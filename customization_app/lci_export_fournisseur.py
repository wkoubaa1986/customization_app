"""
Renvoi au fournisseur de SON PROPRE classeur, annoté de nos ajustements.

Le fournisseur a bâti sa cotation dans son fichier, avec ses références, son
ordre et sa langue. Lui répondre dans un classeur maison l'oblige à tout
réapparier à la main — c'est là que se perdent les remises et que reviennent
les mauvaises quantités. On lui renvoie donc le fichier qu'il a envoyé, avec
des colonnes ajoutées à droite (photo si son fichier n'en a plus, quantité
retenue, prix cible, décision, observation, conteneur), et trois conventions
demandées par l'utilisateur (22/09/2026) :

  - une ligne AJOUTÉE ou MODIFIÉE par nous est entièrement en orange clair,
    la cellule dont la valeur change en orange plus soutenu ;
  - une ligne ABANDONNÉE est RETIRÉE de son tableau et listée nommément en
    bas (« lignes retirées de la commande »), avec le motif ;
  - ses photos sont conservées quand son fichier en a (xlsx, ou xls via
    xls_images) ; sinon on ajoute les nôtres (fiche Article) dans une colonne
    « Photo ».

Les articles ADDITIONNELS embarqués sur une ligne (membrane, robinet…)
sont décrits dans l'observation de la ligne : sans cela, le fournisseur ne
voyait pas qu'on attend 150 membranes en plus des 150 osmoseurs.

Deux limites assumées, dites à l'écran :
  - un .xls (format d'avant 2007) ne se réécrit pas : on le convertit en .xlsx.
    Valeurs, hauteurs/largeurs, fusions et PHOTOS sont reprises (xls_images.py
    lit les images et leurs ancrages dans le flux BIFF) ; le reste de la mise en
    forme (couleurs, bordures, polices) n'est pas conservé ;
  - dans un .xlsx qui porte des formules ou des photos, retirer physiquement
    des lignes casserait ses totaux (openpyxl ne recale ni formules ni
    ancrages) : la ligne est alors VIDÉE et MASQUÉE — même résultat à l'écran,
    ses formules restent justes. Sans formule ni photo, la ligne est supprimée.
"""

import bisect
import json
import os

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.lci_observation import qty_retenue
from customization_app.liste_commande_import import (
    DOCTYPE,
    _add_label,
    _adds_map,
    _guard,
    _img_thumb,
)

LABELS = {
    "Français": {
        "qty": "Qté retenue", "prix": "Prix cible", "decision": "Décision",
        "observation": "Observation", "conteneur": "Conteneur", "photo": "Photo",
        "ajoutees": "LIGNES NON COTÉES — merci de les chiffrer",
        "retirees": "LIGNES RETIRÉES DE LA COMMANDE",
        "additionnels": "Articles additionnels",
        "legende": "Lignes orange : ajoutées ou modifiées de notre côté (cellule foncée = "
                   "la valeur qui change). Lignes retirées de la commande : listées en bas.",
        "code": "Code", "designation": "Désignation", "titre": "Contre-proposition",
        "note_xls": "Fichier .xls converti en .xlsx : valeurs, dimensions et photos conservées ; le reste de la mise en forme d'origine peut différer.",
        "note_masquees": "Lignes retirées : vidées et masquées (pour préserver vos formules et vos photos).",
    },
    "English": {
        "qty": "Target qty", "prix": "Target price", "decision": "Decision",
        "observation": "Remark", "conteneur": "Container", "photo": "Photo",
        "ajoutees": "LINES NOT QUOTED — please price them",
        "retirees": "LINES REMOVED FROM THE ORDER",
        "additionnels": "Additional items",
        "legende": "Orange rows: added or changed on our side (darker cell = the changed "
                   "value). Lines removed from the order: listed below.",
        "code": "Code", "designation": "Description", "titre": "Counter-offer",
        "note_xls": ".xls file converted to .xlsx: values, dimensions and pictures kept; other original formatting may differ.",
        "note_masquees": "Removed lines: cleared and hidden (to keep your formulas and pictures intact).",
    },
    "Deutsch": {
        "qty": "Zielmenge", "prix": "Zielpreis", "decision": "Entscheidung",
        "observation": "Bemerkung", "conteneur": "Container", "photo": "Foto",
        "ajoutees": "NICHT ANGEBOTENE POSITIONEN — bitte bepreisen",
        "retirees": "AUS DER BESTELLUNG GESTRICHENE POSITIONEN",
        "additionnels": "Zusätzliche Artikel",
        "legende": "Orange Zeilen: von uns hinzugefügt oder geändert (dunklere Zelle = "
                   "geänderter Wert). Gestrichene Positionen: unten aufgeführt.",
        "code": "Code", "designation": "Bezeichnung", "titre": "Gegenangebot",
        "note_xls": ".xls-Datei in .xlsx umgewandelt: Werte, Abmessungen und Bilder erhalten; sonstige Formatierung kann abweichen.",
        "note_masquees": "Gestrichene Zeilen: geleert und ausgeblendet (Formeln und Bilder bleiben erhalten).",
    },
    "Español": {
        "qty": "Cant. objetivo", "prix": "Precio objetivo", "decision": "Decisión",
        "observation": "Observación", "conteneur": "Contenedor", "photo": "Foto",
        "ajoutees": "LÍNEAS NO COTIZADAS — rogamos cotizarlas",
        "retirees": "LÍNEAS RETIRADAS DEL PEDIDO",
        "additionnels": "Artículos adicionales",
        "legende": "Filas naranjas: añadidas o modificadas por nosotros (celda más oscura = "
                   "valor modificado). Líneas retiradas del pedido: listadas abajo.",
        "code": "Código", "designation": "Descripción", "titre": "Contraoferta",
        "note_xls": "Archivo .xls convertido a .xlsx: valores, dimensiones y fotos conservados; el resto del formato puede diferir.",
        "note_masquees": "Líneas retiradas: vaciadas y ocultas (para conservar sus fórmulas y fotos).",
    },
    "العربية": {
        "qty": "الكمية المعتمدة", "prix": "السعر المستهدف", "decision": "القرار",
        "observation": "ملاحظة", "conteneur": "الحاوية", "photo": "صورة",
        "ajoutees": "بنود غير مسعّرة — يرجى تسعيرها",
        "retirees": "بنود مستبعدة من الطلب",
        "additionnels": "بنود إضافية",
        "legende": "الأسطر البرتقالية: بنود أضفناها أو عدّلناها (الخلية الأغمق = القيمة "
                   "المعدّلة). البنود المستبعدة من الطلب: مدرجة في الأسفل.",
        "code": "المرجع", "designation": "التسمية", "titre": "عرض مضاد",
        "note_xls": "تم تحويل ملف .xls إلى .xlsx: القيم والأبعاد والصور محفوظة؛ قد يختلف باقي التنسيق.",
        "note_masquees": "البنود المستبعدة: أُفرغت وأُخفيت (للحفاظ على معادلاتكم وصوركم).",
    },
}

DECISIONS = {
    "English": {"À négocier": "To negotiate", "Accepté": "Accepted", "Abandonné": "Dropped"},
    "Deutsch": {"À négocier": "Zu verhandeln", "Accepté": "Angenommen", "Abandonné": "Gestrichen"},
    "Español": {"À négocier": "A negociar", "Accepté": "Aceptado", "Abandonné": "Descartado"},
    "العربية": {"À négocier": "قيد التفاوض", "Accepté": "مقبول", "Abandonné": "مستبعد"},
}

# hauteur (points) d'une ligne qui porte une vignette, et taille de la vignette (pixels)
HAUTEUR_LIGNE_PHOTO = 52
VIGNETTE_PX = 64


def _labels(doc):
    return LABELS.get(doc.langue_cible) or LABELS["Français"]


def _decision(doc, valeur):
    return (DECISIONS.get(doc.langue_cible) or {}).get(valeur, valeur or "")


def plan_lecture(doc):
    """Plan de lecture du dernier fichier importé, ou throw explicite."""
    if not doc.fichier_fournisseur or not doc.reponse_plan:
        frappe.throw(_("Aucun fichier fournisseur n'a été importé sur cette liste : "
                       "importez d'abord la réponse du fournisseur, le fichier reçu "
                       "sert de base au renvoi."))
    try:
        return json.loads(doc.reponse_plan)
    except Exception:
        frappe.throw(_("Le plan de lecture du fichier fournisseur est illisible : "
                       "réimportez la réponse du fournisseur."))


def _fichier_local(file_url):
    return frappe.get_doc("File", {"file_url": file_url}).get_full_path()


def _charger(chemin):
    """Classeur ouvert EN ÉCRITURE. Rend (workbook, converti?)."""
    from openpyxl import Workbook, load_workbook

    ext = os.path.splitext(chemin)[1].lower()
    if ext != ".xls":
        # data_only=False : les formules du fournisseur restent des formules,
        # sinon son fichier revient figé sur les valeurs du jour de l'envoi.
        return load_workbook(chemin), False

    import xlrd
    from openpyxl.utils import get_column_letter

    from customization_app import xls_images

    # formatting_info=True : hauteurs de lignes, largeurs de colonnes et fusions — sans elles,
    # ses photos retomberaient dans des cases de 20 pixels.
    src = xlrd.open_workbook(chemin, formatting_info=True)
    with open(chemin, "rb") as fh:
        images = xls_images.images_du_classeur(fh.read())
    wb = Workbook()
    wb.remove(wb.active)
    for index, sh in enumerate(src.sheets()):
        ws = wb.create_sheet(title=sh.name[:31])
        for i in range(sh.nrows):
            ws.append([sh.cell_value(i, j) for j in range(sh.ncols)])
        largeurs, hauteurs = _mise_en_forme_xls(ws, sh, get_column_letter)
        _poser_images_xls(ws, images[index] if index < len(images) else [], largeurs, hauteurs)
    return wb, True


def _mise_en_forme_xls(ws, sh, get_column_letter):
    """Recopie largeurs de colonnes, hauteurs de lignes et fusions du .xls.
    -> (largeurs px par colonne, hauteurs px par ligne) pour ancrer les images."""
    from customization_app.xls_images import hauteur_ligne_px, largeur_colonne_px

    largeurs = []
    for j in range(max(sh.ncols, 1)):
        info = sh.colinfo_map.get(j)
        if info and info.width:
            ws.column_dimensions[get_column_letter(j + 1)].width = round(info.width / 256.0, 2)
        largeurs.append(largeur_colonne_px(info.width if info else None))
    hauteurs = []
    for i in range(sh.nrows):
        info = sh.rowinfo_map.get(i)
        if info and info.height:
            ws.row_dimensions[i + 1].height = round(info.height / 20.0, 2)
        hauteurs.append(hauteur_ligne_px(info.height if info else None))
    for rlo, rhi, clo, chi in sh.merged_cells:
        try:
            ws.merge_cells(start_row=rlo + 1, end_row=rhi, start_column=clo + 1, end_column=chi)
        except Exception:
            pass
    return largeurs, hauteurs


def _poser_images_xls(ws, images, largeurs, hauteurs):
    """Ses images, à leur cellule d'origine, réduites à la taille de leur boîte (une photo
    de 4 Mo dans une case de 200 px n'a pas besoin de ses 4 Mo)."""
    import io as _io

    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from PIL import Image as PILImage

    from customization_app.xls_images import boite_pixels

    import warnings

    EMU = 9525  # EMU par pixel
    for im in images:
        x, y, w, h = boite_pixels(im, largeurs, hauteurs)
        try:
            with warnings.catch_warnings():
                # une photo de 155 Mpx dans son fichier n'est pas une attaque : on la réduit
                warnings.simplefilter("ignore", PILImage.DecompressionBombWarning)
                pil = PILImage.open(_io.BytesIO(im["data"]))
            # certains fournisseurs collent des photos de 150 Mpx dans une case de 200 px :
            # `draft` demande au décodeur JPEG une version réduite, sans charger l'original
            cible = (max(16, int(w * 2)), max(16, int(h * 2)))
            if pil.format == "JPEG":
                pil.draft("RGB", cible)
            if pil.mode not in ("RGB", "RGBA", "L"):
                pil = pil.convert("RGB")
            pil.thumbnail(cible)
            buf = _io.BytesIO()
            if pil.mode == "RGBA":
                pil.save(buf, format="PNG")
            else:
                pil.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            img = XLImage(buf)
        except Exception:
            continue
        ratio = min(w / float(pil.width or 1), h / float(pil.height or 1))
        img.width, img.height = max(1, int(pil.width * ratio)), max(1, int(pil.height * ratio))
        img.anchor = OneCellAnchor(_from=AnchorMarker(col=im["col"], colOff=int(x * EMU), row=im["row"], rowOff=int(y * EMU)),
                                   ext=XDRPositiveSize2D(cx=int(img.width * EMU), cy=int(img.height * EMU)))
        ws.add_image(img)


def _prix_cible_export(row):
    """Le prix qu'on AFFICHE dans la colonne « prix cible » du fichier renvoyé.

    Notre contre-proposition si on en a une, sinon notre cible initiale — pas
    son prix à lui : une colonne « Target price » qui recopie sa cotation
    contredirait l'observation qui, juste à côté, dit qu'elle est trop chère.
    Vide quand on n'a aucune cible : on accepte son prix tel quel.
    """
    return flt(row.prix_cible_negocie) or flt(row.prix_cible) or 0.0


def _conteneurs_ligne(row):
    try:
        parts = json.loads(row.repartition_conteneurs or "[]") or []
    except Exception:
        return ""
    if not parts:
        return ""
    if len(parts) == 1:
        return f"C{parts[0].get('no')}"
    return " + ".join(f"C{p.get('no')} ({flt(p.get('qty')):g})" for p in parts)


# ------------------------------------------------------------ règles pures


def lignes_supprimees(articles, corresp):
    """Les lignes Excel (1-based, triées) des articles abandonnés qui ont une
    place dans son tableau : celles qu'on retire."""
    return sorted({int(corresp[r.name]) for r in articles
                   if r.name in corresp and (r.decision or "") == "Abandonné"})


def remapper_correspondances(corresp, supprimees):
    """Après suppression physique de `supprimees` (lignes 1-based), chaque
    ligne restante remonte d'autant de lignes supprimées au-dessus d'elle.
    Les lignes supprimées disparaissent de la correspondance."""
    supprimees = sorted(set(int(x) for x in supprimees))
    out = {}
    for k, xl in corresp.items():
        xl = int(xl)
        if xl in supprimees:
            continue
        out[k] = xl - bisect.bisect_left(supprimees, xl)
    return out


def est_modifiee(qty, qty_fournisseur, prix, prix_fournisseur, additionnels=None):
    """Une ligne est « modifiée » si notre quantité retenue diffère de la
    sienne, si on renvoie un prix cible différent du sien, ou si on y embarque
    des articles additionnels qu'il ne voit pas dans sa cotation."""
    if additionnels:
        return True
    if qty > 0 and abs(flt(qty) - flt(qty_fournisseur)) > 0.001:
        return True
    return prix > 0 and abs(flt(prix) - flt(prix_fournisseur)) > 0.00001


def texte_additionnels(adds, qty_ret, labels):
    """« Articles additionnels : + 150 × Membrane 1812-80 GPD (Vontron) ; + 150 × Robinet »
    — quantité = quantité par pack × quantité retenue, comme dans nos documents."""
    morceaux = []
    for a in adds or []:
        tot = flt(a.get("qty_par_pack")) * flt(qty_ret)
        libelle = _add_label(a)
        if not libelle:
            continue
        morceaux.append(f"+ {tot:g} × {libelle}" if tot > 0 else f"+ {libelle}")
    if not morceaux:
        return ""
    return f"{labels['additionnels']}: " + " ; ".join(morceaux)


def texte_observation(observation, adds, qty_ret, labels):
    """L'observation envoyée = la sienne + la description des additionnels."""
    parties = [(observation or "").strip(), texte_additionnels(adds, qty_ret, labels)]
    return "\n".join(p for p in parties if p)


def a_formules_ou_images(ws):
    """Vrai si la feuille porte des formules ou des images : on ne supprime
    alors pas de lignes physiquement (openpyxl ne recale ni les unes ni les
    autres), on vide et on masque."""
    if getattr(ws, "_images", None):
        return True
    for ligne in ws.iter_rows(values_only=True):
        for v in ligne:
            if isinstance(v, str) and v.startswith("="):
                return True
    return False


# ------------------------------------------------------------ écriture


def _poser_photo(ws, cellule, file_url, XLImage):
    """Vignette de la fiche Article dans la cellule. Rend True si posée."""
    buf = _img_thumb(file_url, max_px=VIGNETTE_PX * 2)
    if not buf:
        return False
    try:
        img = XLImage(buf)
        ratio = (img.width or 1) / float(img.height or 1)
        img.height = VIGNETTE_PX
        img.width = int(VIGNETTE_PX * ratio)
        img.anchor = cellule.coordinate
        ws.add_image(img)
        ws.row_dimensions[cellule.row].height = HAUTEUR_LIGNE_PHOTO
        return True
    except Exception:
        return False


def _retirer(ws, supprimees, physique):
    """Retire les lignes abandonnées du tableau : suppression physique, ou
    vidage + masquage quand la feuille porte des formules ou des photos."""
    if physique:
        for xl in sorted(supprimees, reverse=True):
            ws.delete_rows(xl)
        return
    lignes = set(supprimees)
    for xl in lignes:
        for c in range(1, (ws.max_column or 1) + 1):
            ws.cell(row=xl, column=c).value = None
        ws.row_dimensions[xl].hidden = True
    # une image ancrée sur une ligne masquée resterait visible par-dessus la suivante
    restantes = []
    for img in getattr(ws, "_images", []) or []:
        marqueur = getattr(img.anchor, "_from", None)
        if marqueur is not None and (int(marqueur.row) + 1) in lignes:
            continue
        restantes.append(img)
    ws._images = restantes


@frappe.whitelist()
def download_reponse_annotee(docname):
    """Rend le classeur du fournisseur enrichi de nos ajustements."""
    import io

    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    plan = plan_lecture(doc)
    L = _labels(doc)
    devise = doc.devise or "USD"

    wb, converti = _charger(_fichier_local(doc.fichier_fournisseur))
    feuille = plan.get("feuille")
    if feuille not in wb.sheetnames:
        frappe.throw(_("La feuille « {0} » n'existe plus dans le fichier joint.").format(feuille))
    ws = wb[feuille]

    corresp = {k: int(v) for k, v in (plan.get("correspondances") or {}).items()}
    mapping = plan.get("mapping") or {}
    ligne_entete = int(plan.get("ligne_entete") or 0) + 1      # 1-based
    adds_map = _adds_map(doc)

    ORANGE = PatternFill("solid", fgColor="FFE0B2")        # ligne ajoutée ou modifiée
    ORANGE_FORT = PatternFill("solid", fgColor="FFB74D")   # la valeur qui change
    GRIS = PatternFill("solid", fgColor="EDEDED")          # bloc des lignes retirées
    ENTETE = PatternFill("solid", fgColor="D9E7FF")
    gras = Font(bold=True)

    # 1. les lignes abandonnées sortent de SON tableau (avant tout calcul de position)
    supprimees = lignes_supprimees(doc.articles, corresp)
    # suppression physique seulement si rien ne dépend des numéros de ligne (ni formule, ni
    # image ancrée) : openpyxl ne recale ni les unes ni les autres. Un .xls converti porte
    # désormais ses photos, donc il passe lui aussi par le vidage + masquage.
    physique = not a_formules_ou_images(ws)
    retirees = [r for r in doc.articles if (r.decision or "") == "Abandonné"]
    if supprimees:
        _retirer(ws, supprimees, physique)
        if physique:
            corresp = remapper_correspondances(corresp, supprimees)

    # 2. ses photos restent si son fichier en a encore ; sinon les nôtres
    garder_photos = bool(getattr(ws, "_images", None))
    col0 = (ws.max_column or 1) + 2                  # une colonne vide de respiration
    avec_conteneurs = any((r.repartition_conteneurs or "") for r in doc.articles)
    colonnes = []
    if not garder_photos:
        colonnes.append(("photo", L["photo"], 12))
    colonnes += [("qty", L["qty"], 13), ("prix", f'{L["prix"]} ({devise})', 15),
                 ("decision", L["decision"], 14), ("observation", L["observation"], 56)]
    if avec_conteneurs:
        colonnes.append(("conteneur", L["conteneur"], 14))
    pos = {cle: col0 + i for i, (cle, _lbl, _w) in enumerate(colonnes)}
    derniere_col = col0 + len(colonnes) - 1

    for i, (_cle, lbl, w) in enumerate(colonnes):
        c = ws.cell(row=ligne_entete, column=col0 + i, value=lbl)
        c.font = gras
        c.fill = ENTETE
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(col0 + i)].width = w

    def colorer_ligne(xl, fond):
        for c in range(1, derniere_col + 1):
            ws.cell(row=xl, column=c).fill = fond

    def ecrire_nos_colonnes(xl, r, qty, prix, adds):
        cq = ws.cell(row=xl, column=pos["qty"], value=(qty if qty > 0 else None))
        cp = ws.cell(row=xl, column=pos["prix"], value=prix or None)
        ws.cell(row=xl, column=pos["decision"], value=_decision(doc, r.decision))
        co = ws.cell(row=xl, column=pos["observation"],
                     value=texte_observation(r.observation, adds, qty, L))
        co.alignment = Alignment(wrap_text=True, vertical="top")
        if avec_conteneurs:
            ws.cell(row=xl, column=pos["conteneur"], value=_conteneurs_ligne(r))
        if "photo" in pos and getattr(r, "image", None):
            _poser_photo(ws, ws.cell(row=xl, column=pos["photo"]), r.image, XLImage)
        return cq, cp

    # 3. ses lignes, annotées
    dernier = ligne_entete
    non_appariees = []
    for r in doc.articles:
        if (r.decision or "") == "Abandonné":
            continue
        xl = corresp.get(r.name)
        if not xl:
            non_appariees.append(r)
            continue
        dernier = max(dernier, xl)
        qty = qty_retenue(r)
        prix = _prix_cible_export(r)
        adds = adds_map.get(r.name, [])
        qty_frn = flt(r.qty_fournisseur) or flt(r.qty)
        cq, cp = ecrire_nos_colonnes(xl, r, qty, prix, adds)
        if est_modifiee(qty, qty_frn, prix, flt(r.prix_fournisseur), adds):
            colorer_ligne(xl, ORANGE)
            if qty > 0 and abs(qty - qty_frn) > 0.001:
                cq.fill = ORANGE_FORT
            if prix > 0 and abs(prix - flt(r.prix_fournisseur)) > 0.00001:
                cp.fill = ORANGE_FORT
            if adds:
                ws.cell(row=xl, column=pos["observation"]).fill = ORANGE_FORT

    # 4. nos lignes que sa cotation ne contient pas : ajoutées sous le tableau,
    # dans SES colonnes quand on sait où elles sont — en orange, comme toute ligne ajoutée.
    ligne = dernier + 3
    c_code = (mapping.get("code") or 0) + 1
    c_des = (mapping.get("designation") or 1) + 1
    c_qty = (mapping.get("qty") or 2) + 1
    if non_appariees:
        t = ws.cell(row=ligne, column=1, value=L["ajoutees"])
        t.font = Font(bold=True, color="FFB00020")
        ligne += 1
        for r in non_appariees:
            ws.cell(row=ligne, column=c_code, value=r.item_code or "")
            ws.cell(row=ligne, column=c_des, value=r.item_name_traduit or r.item_name or "")
            ws.cell(row=ligne, column=c_qty, value=qty_retenue(r))
            ecrire_nos_colonnes(ligne, r, qty_retenue(r), _prix_cible_export(r), adds_map.get(r.name, []))
            colorer_ligne(ligne, ORANGE)
            ligne += 1

    # 5. les lignes retirées, nommées, avec le motif
    if retirees:
        ligne += 1
        t = ws.cell(row=ligne, column=1, value=L["retirees"])
        t.font = Font(bold=True, color="FF6B7280")
        ligne += 1
        for r in retirees:
            ws.cell(row=ligne, column=c_code, value=r.item_code or "")
            ws.cell(row=ligne, column=c_des, value=r.item_name_traduit or r.item_name or "")
            ws.cell(row=ligne, column=pos["decision"], value=_decision(doc, r.decision))
            co = ws.cell(row=ligne, column=pos["observation"], value=(r.observation or "").strip())
            co.alignment = Alignment(wrap_text=True, vertical="top")
            colorer_ligne(ligne, GRIS)
            ligne += 1

    ligne += 1
    lg = ws.cell(row=ligne, column=col0,
                 value=f"{L['titre']} — {doc.name} — {doc.date_commande or nowdate()} · {L['legende']}")
    lg.font = Font(italic=True, size=9, color="FF6B7280")
    if converti:
        ws.cell(row=ligne + 1, column=col0, value=L["note_xls"]).font = Font(italic=True, size=9)
    elif supprimees and not physique:
        ws.cell(row=ligne + 1, column=col0, value=L["note_masquees"]).font = Font(italic=True, size=9)

    # le plan de chargement part dans un onglet à lui : le fournisseur charge
    # les conteneurs, il lui faut la même feuille que nous.
    from customization_app.lci_conteneurs import ecrire_feuille
    ecrire_feuille(wb, doc)

    buf = io.BytesIO()
    wb.save(buf)
    base = os.path.splitext(os.path.basename(doc.fichier_fournisseur))[0][:60]
    frappe.response["filename"] = f"{base}-{doc.name}-annote.xlsx"
    frappe.response["filecontent"] = buf.getvalue()
    frappe.response["type"] = "binary"
