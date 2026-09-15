"""Les impressions Aramex de « Ma journée » : tous les BL, toutes les étiquettes, en UN PDF.

Le technicien (ou le magasin) prépare les colis du jour : il lui faut le bon de livraison de
chaque colis Aramex et l'étiquette à coller dessus. Plutôt qu'ouvrir chaque commande, deux
boutons de la barre de « Ma journée » rendent chacun un seul PDF, dans l'ordre des tâches.

- BL : le bon de livraison de la commande (format « Aqua World BL », le même que la tournée),
  créé s'il n'existe pas encore — par le MÊME chemin que « Générer BL » (generer_bl), qui
  soumet la commande en brouillon puisqu'un BL l'exige.
- Étiquettes : le PDF attaché à la commande (custom_etiquette_aramex) ; à défaut, redemandé à
  Aramex (PrintLabel) pour un bordereau de notre compte ; un bordereau sans étiquette
  possible est listé en tête du PDF, jamais passé sous silence.
"""

from __future__ import annotations

import io

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

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
            writer.append(PdfReader(io.BytesIO(contenu)))
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
