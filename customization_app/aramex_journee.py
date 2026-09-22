"""Les impressions Aramex de « Ma journée » : tous les BL, toutes les étiquettes, en UN PDF.

Le technicien (ou le magasin) prépare les colis du jour : il lui faut le bon de livraison de
chaque colis Aramex et l'étiquette à coller dessus. Plutôt qu'ouvrir chaque commande, deux
boutons de la barre de « Ma journée » rendent chacun un seul PDF, dans l'ordre des tâches.

- BL : le bon de livraison de la commande (format « Aqua World BL », le même que la tournée),
  créé s'il n'existe pas encore — par le MÊME chemin que « Générer BL » (generer_bl), qui
  soumet la commande en brouillon puisqu'un BL l'exige.
- Étiquettes : le PDF attaché à la commande (custom_etiquette_aramex) ; à défaut, redemandé à
  Aramex (PrintLabel) pour un bordereau de notre compte ; un bordereau sans étiquette
  possible est listé en tête du PDF, jamais passé sous silence. UNE ÉTIQUETTE PAR CARTON :
  un colis de N pièces en sort avec N étiquettes « Pièce i / N » (voir etiquettes_par_piece).
"""

from __future__ import annotations

import io

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from customization_app.planning_employe import TYPE_LIVRAISON, _employe_demande, ma_journee


def _livraisons_aramex(date, employe) -> list:
    """Les lignes de « Ma journée » qui sont des livraisons Aramex, dans l'ordre du jour."""
    m = ma_journee(date, employe)
    return [l for l in m["lignes"] if l.get("aramex") and l.get("type") == TYPE_LIVRAISON]


def _repondre_pdf(contenu, nom):
    frappe.local.response.filename = nom
    frappe.local.response.filecontent = contenu
    frappe.local.response.type = "pdf"


@frappe.whitelist()
def bl_aramex_pdf(date=None, employe=None):
    """Un PDF avec le BL de chaque livraison Aramex du jour (créé s'il manque)."""
    from customization_app.generer_bl import _construire_pdf, _get_dn_for_so, _traiter_avec_commande

    jour = getdate(date or nowdate())
    cible = _employe_demande(employe)
    lignes = _livraisons_aramex(str(jour), employe)
    noms, sans = [], []
    for l in lignes:
        if not l.get("commande"):
            sans.append(l["tache"])
            continue
        dn = _get_dn_for_so(l["commande"])
        if not dn:
            t = frappe.get_doc("Tache de travail", l["tache"])
            try:
                dn = _traiter_avec_commande(t, str(jour), None, None)
            except Exception as e:
                frappe.log_error(title="BL Aramex : %s" % l["commande"],
                                 message=frappe.get_traceback())
                frappe.throw(_("Impossible de créer le BL de {0} : {1}").format(
                    l["commande"], str(e)[:200]))
        if dn and dn not in noms:
            noms.append(dn)
    if not noms:
        frappe.throw(_("Aucune livraison Aramex avec commande ce jour-là."))
    _repondre_pdf(_construire_pdf(noms), "BL-Aramex-%s-%s.pdf" % (jour, cible or "tous"))


def _page_avis(lignes_texte) -> bytes:
    """Une page de garde qui dit ce qui manque — un PDF où il manque une étiquette sans le
    dire ferait partir un colis nu."""
    html = "<h3>Étiquettes Aramex — colis sans étiquette</h3><ul>%s</ul>" % "".join(
        "<li>%s</li>" % frappe.utils.escape_html(t) for t in lignes_texte)
    from frappe.utils.pdf import get_pdf

    return get_pdf(html)


@frappe.whitelist()
def etiquettes_aramex_pdf(date=None, employe=None):
    """Un PDF avec l'étiquette de chaque colis Aramex du jour."""
    from pypdf import PdfReader, PdfWriter

    from customization_app import aramex_api
    from customization_app.livraison_aramex import DOCTYPE_SUIVI

    jour = getdate(date or nowdate())
    cible = _employe_demande(employe)
    lignes = _livraisons_aramex(str(jour), employe)
    writer = PdfWriter()
    manquants, nb = [], 0
    for l in lignes:
        bordereau = l.get("bordereau")
        if not bordereau:
            manquants.append("%s — %s : bordereau manquant" % (l["tache"], l.get("client") or ""))
            continue
        contenu = None
        fichier = l.get("etiquette")
        if fichier:
            try:
                nom_f = frappe.db.get_value("File", {"file_url": fichier}, "name")
                contenu = frappe.get_doc("File", nom_f).get_content() if nom_f else None
            except Exception:
                contenu = None
        if not contenu:
            url = frappe.db.get_value(DOCTYPE_SUIVI, bordereau, "etiquette_url") if \
                frappe.db.exists(DOCTYPE_SUIVI, bordereau) else None
            if not url:
                try:
                    res = aramex_api.print_label(bordereau)
                    url = res.get("etiquette_url")
                except Exception:
                    url = None
            contenu = aramex_api.telecharger(url) if url else None
        if not contenu:
            manquants.append("%s — %s : bordereau %s sans étiquette disponible"
                             % (l["tache"], l.get("client") or "", bordereau))
            continue
        try:
            for page in etiquettes_par_piece(contenu, (l.get("colis") or {}).get("pieces")):
                writer.add_page(page)
            nb += 1
        except Exception:
            manquants.append("%s — étiquette %s illisible" % (l["tache"], bordereau))
    if not nb and not manquants:
        frappe.throw(_("Aucune livraison Aramex ce jour-là."))
    if not nb:
        frappe.throw(_("Aucune étiquette disponible : {0}").format(" ; ".join(manquants)))
    sortie = PdfWriter()
    if manquants:
        sortie.append(PdfReader(io.BytesIO(_page_avis(manquants))))
    for page in en_grille_a4(PdfReader(io.BytesIO(_octets(writer))).pages):
        sortie.add_page(page)
    _repondre_pdf(_octets(sortie), "Etiquettes-Aramex-%s-%s.pdf" % (jour, cible or "tous"))


A4 = (595.28, 841.89)
MARGE = 8.0


def etiquettes_par_piece(contenu, pieces) -> list:
    """Une étiquette PAR CARTON (demande utilisateur 22/09/2026).

    Un colis de N pièces porte UN numéro de bordereau, mais chaque carton doit porter sa
    propre étiquette, numérotée, pour que le livreur les compte. Si Aramex a déjà rendu une
    page par pièce, on les garde telles quelles (il les numérote lui-même) ; s'il n'en a
    rendu qu'une, on la répète N fois et on tamponne « Pièce i / N » dans la case vide en
    haut à droite (au-dessus du COD) — y compris « 1 / 1 » pour un colis d'une seule pièce
    (demande utilisateur : le livreur voit ainsi que la série est complète). Le PDF est relu
    pour chaque copie : pypdf ne sait pas dupliquer une page sans partager ses objets.
    `contenu` : les octets du PDF d'étiquette ; -> liste de PageObject.
    """
    from pypdf import PdfReader

    pieces = max(1, cint(pieces))
    pages = list(PdfReader(io.BytesIO(contenu)).pages)
    if len(pages) != 1:
        return pages
    sorties = []
    for i in range(1, pieces + 1):
        page = PdfReader(io.BytesIO(contenu)).pages[0]
        page.merge_page(_tampon_piece(i, pieces, float(page.mediabox.width),
                                      float(page.mediabox.height)))
        sorties.append(page)
    return sorties


def _tampon_piece(i, n, largeur, hauteur):
    """Une page transparente de la taille de l'étiquette, avec « Pièce » puis « i / N » en gros,
    centrés DANS la case vide de l'étiquette 9729 (à droite de « Date / Ref1 », au-dessus du
    COD). La case a été mesurée sur un vrai bordereau (50920351075, 295 × 432 pt) : x 183 → 283,
    y 65 → 147 depuis le haut — exprimée ici en fractions pour suivre une éventuelle autre
    taille. Helvetica-Bold est une police de base du PDF : rien à embarquer, aucune dépendance."""
    from pypdf import PageObject
    from pypdf.generic import DictionaryObject, NameObject, StreamObject

    page = PageObject.create_blank_page(width=largeur, height=hauteur)
    police = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): police})})
    # La case, en coordonnées PDF (origine en bas à gauche).
    x0, x1 = largeur * (183 / 295), largeur * (283 / 295)
    y_haut, y_bas = hauteur * (1 - 65 / 432), hauteur * (1 - 147 / 432)
    cx = (x0 + x1) / 2
    ligne1, taille1 = "Pi\xe8ce", 12
    ligne2, taille2 = "%d / %d" % (i, n), 30
    flux = StreamObject()
    flux.set_data(("BT /F1 %d Tf 0 0 0 rg %.1f %.1f Td (%s) Tj ET\n"
                   "BT /F1 %d Tf 0 0 0 rg %.1f %.1f Td (%s) Tj ET\n" % (
                       taille1, cx - _largeur_helvetica_bold(ligne1, taille1) / 2, y_haut - 20, ligne1,
                       taille2, cx - _largeur_helvetica_bold(ligne2, taille2) / 2, y_bas + 18, ligne2,
                   )).encode("latin-1"))
    page[NameObject("/Contents")] = flux
    return page


# Chasses Helvetica-Bold (AFM, pour 1000 unités) des seuls caractères du tampon — pour centrer.
_CHASSES = {"P": 667, "i": 278, "\xe8": 556, "c": 556, "e": 556, " ": 278, "/": 278}


def _largeur_helvetica_bold(texte, taille) -> float:
    return sum(_CHASSES.get(c, 556) for c in texte) / 1000.0 * taille


def en_grille_a4(pages, colonnes=2, rangees=2):
    """Plusieurs étiquettes par feuille A4 (demande utilisateur 15/09/2026).

    L'étiquette Aramex 9729 fait 10,4 × 15,2 cm (295 × 432 pt) : quatre tiennent sur un A4
    en 2 × 2, à peine réduites (0,97). Chaque étiquette est centrée dans sa case, dans
    l'ordre de lecture ; la dernière feuille reste partiellement vide plutôt que d'étirer.
    -> liste de PageObject A4.
    """
    from pypdf import PageObject, Transformation

    largeur, hauteur = A4
    cw = (largeur - 2 * MARGE) / colonnes
    ch = (hauteur - 2 * MARGE) / rangees
    par_page = colonnes * rangees
    sorties = []
    feuille = None
    for i, src in enumerate(pages):
        if i % par_page == 0:
            feuille = PageObject.create_blank_page(width=largeur, height=hauteur)
            sorties.append(feuille)
        k = i % par_page
        col, rang = k % colonnes, k // colonnes
        w, h = float(src.mediabox.width), float(src.mediabox.height)
        s = min(cw / w, ch / h, 1.0) * 0.99
        tx = MARGE + col * cw + (cw - w * s) / 2 - float(src.mediabox.left) * s
        ty = hauteur - MARGE - (rang + 1) * ch + (ch - h * s) / 2 - float(src.mediabox.bottom) * s
        feuille.merge_transformed_page(src, Transformation().scale(s).translate(tx, ty))
    return sorties


def _octets(writer) -> bytes:
    tampon = io.BytesIO()
    writer.write(tampon)
    return tampon.getvalue()
