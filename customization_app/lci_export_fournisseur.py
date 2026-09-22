"""
Renvoi au fournisseur de SON PROPRE classeur, RECONSTRUIT dans l'ordre de notre liste et
annoté de nos ajustements.

Le fournisseur a bâti sa cotation dans son fichier, avec ses références, ses colonnes, ses
photos et sa langue. Lui répondre dans un classeur maison l'oblige à tout réapparier à la
main — c'est là que se perdent les remises et que reviennent les mauvaises quantités. On
lui renvoie donc SES colonnes, SES valeurs et SES photos, mais :

  - dans l'ORDRE DE NOTRE DOCUMENT (groupes d'articles puis lignes), demande de
    l'utilisateur du 22/09/2026 : c'est notre liste qui fait foi, son tableau est réordonné ;
  - une ligne AJOUTÉE (absente de sa cotation) est insérée à sa place, avec notre photo
    (fiche Article) posée dans sa colonne photo, comme dans « Excel à envoyer » ;
  - une ligne ABANDONNÉE n'apparaît plus dans le tableau et est listée nommément en bas ;
  - une ligne ajoutée ou modifiée (quantité, prix cible, additionnels) est entièrement en
    orange clair, la cellule qui change en orange soutenu ;
  - nos colonnes à droite : quantité retenue, prix cible, décision, observation (avec les
    articles ADDITIONNELS embarqués, qu'il ne voyait pas), conteneur ;
  - le plan de chargement : l'onglet récapitulatif « Conteneurs » ET un onglet par conteneur
    (C1, C2…) avec ses lignes et leurs photos.

Lecture du fichier reçu :
  - .xlsx : openpyxl, en VALEURS (data_only) — le tableau étant réordonné, ses formules ne
    peuvent pas survivre ; ses images sont relues avec leurs ancrages ;
  - .xls : xlrd ne lit que les valeurs ; xls_images.py extrait les photos du flux BIFF avec
    leurs ancrages, et on reprend hauteurs de lignes, largeurs de colonnes et fusions.
Les lignes au-dessus de son en-tête (titres, coordonnées) et sous son tableau (conditions)
sont recopiées ; les NOMBRES des lignes de queue sont effacés (ses totaux ne valent plus).
"""

import copy
import json
import os

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.lci_observation import qty_retenue
from customization_app.liste_commande_import import (
    DOCTYPE,
    RTL_LANGS,
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
        "legende": "Tableau réordonné selon notre liste. Lignes vertes : acceptées telles quelles. "
                   "Lignes orange : ajoutées ou modifiées de notre côté (cellule foncée = la valeur qui change). Lignes retirées de la "
                   "commande : listées en bas. Vos totaux d'origine ne sont pas repris.",
        "code": "Code", "designation": "Désignation", "titre": "Contre-proposition",
        "note_xls": "Fichier .xls converti en .xlsx : valeurs, dimensions et photos conservées ; le reste de la mise en forme d'origine peut différer.",
        "note_masquees": "",
    },
    "English": {
        "qty": "Target qty", "prix": "Target price", "decision": "Decision",
        "observation": "Remark", "conteneur": "Container", "photo": "Photo",
        "ajoutees": "LINES NOT QUOTED — please price them",
        "retirees": "LINES REMOVED FROM THE ORDER",
        "additionnels": "Additional items",
        "legende": "Table reordered to follow our list. Green rows: accepted as quoted. Orange rows: added or changed on our side "
                   "(darker cell = the changed value). Lines removed from the order: listed below. "
                   "Your original totals are not carried over.",
        "code": "Code", "designation": "Description", "titre": "Counter-offer",
        "note_xls": ".xls file converted to .xlsx: values, dimensions and pictures kept; other original formatting may differ.",
        "note_masquees": "",
    },
    "Deutsch": {
        "qty": "Zielmenge", "prix": "Zielpreis", "decision": "Entscheidung",
        "observation": "Bemerkung", "conteneur": "Container", "photo": "Foto",
        "ajoutees": "NICHT ANGEBOTENE POSITIONEN — bitte bepreisen",
        "retirees": "AUS DER BESTELLUNG GESTRICHENE POSITIONEN",
        "additionnels": "Zusätzliche Artikel",
        "legende": "Tabelle nach unserer Liste neu geordnet. Grüne Zeilen: wie angeboten angenommen. Orange Zeilen: von uns hinzugefügt oder "
                   "geändert (dunklere Zelle = geänderter Wert). Gestrichene Positionen: unten "
                   "aufgeführt. Ihre ursprünglichen Summen werden nicht übernommen.",
        "code": "Code", "designation": "Bezeichnung", "titre": "Gegenangebot",
        "note_xls": ".xls-Datei in .xlsx umgewandelt: Werte, Abmessungen und Bilder erhalten; sonstige Formatierung kann abweichen.",
        "note_masquees": "",
    },
    "Español": {
        "qty": "Cant. objetivo", "prix": "Precio objetivo", "decision": "Decisión",
        "observation": "Observación", "conteneur": "Contenedor", "photo": "Foto",
        "ajoutees": "LÍNEAS NO COTIZADAS — rogamos cotizarlas",
        "retirees": "LÍNEAS RETIRADAS DEL PEDIDO",
        "additionnels": "Artículos adicionales",
        "legende": "Tabla reordenada según nuestra lista. Filas verdes: aceptadas tal cual. Filas naranjas: añadidas o modificadas por "
                   "nosotros (celda más oscura = valor modificado). Líneas retiradas del pedido: "
                   "listadas abajo. Sus totales originales no se conservan.",
        "code": "Código", "designation": "Descripción", "titre": "Contraoferta",
        "note_xls": "Archivo .xls convertido a .xlsx: valores, dimensiones y fotos conservados; el resto del formato puede diferir.",
        "note_masquees": "",
    },
    "العربية": {
        "qty": "الكمية المعتمدة", "prix": "السعر المستهدف", "decision": "القرار",
        "observation": "ملاحظة", "conteneur": "الحاوية", "photo": "صورة",
        "ajoutees": "بنود غير مسعّرة — يرجى تسعيرها",
        "retirees": "بنود مستبعدة من الطلب",
        "additionnels": "بنود إضافية",
        "legende": "أُعيد ترتيب الجدول وفق قائمتنا. الأسطر الخضراء: بنود مقبولة كما عُرضت. الأسطر البرتقالية: بنود أضفناها أو عدّلناها "
                   "(الخلية الأغمق = القيمة المعدّلة). البنود المستبعدة من الطلب: مدرجة في الأسفل. "
                   "مجاميعكم الأصلية غير منقولة.",
        "code": "المرجع", "designation": "التسمية", "titre": "عرض مضاد",
        "note_xls": "تم تحويل ملف .xls إلى .xlsx: القيم والأبعاد والصور محفوظة؛ قد يختلف باقي التنسيق.",
        "note_masquees": "",
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
EMU = 9525  # EMU par pixel


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


def _charger(chemin, valeurs=False):
    """Classeur ouvert EN LECTURE. Rend (workbook, converti?).

    `valeurs=True` : un .xlsx est lu en valeurs (data_only) — nécessaire dès qu'on
    réordonne son tableau, ses formules ne pouvant plus pointer au bon endroit.
    """
    from openpyxl import Workbook, load_workbook

    ext = os.path.splitext(chemin)[1].lower()
    if ext != ".xls":
        # rich_text=True : les segments colorés d'une description restent colorés
        return load_workbook(chemin, data_only=valeurs, rich_text=True), False

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
        _styles_xls(ws, sh, src)
        _poser_images_xls(ws, images[index] if index < len(images) else [], largeurs, hauteurs)
    return wb, True


_LIGNES_XLS = {1: "thin", 2: "medium", 3: "dashed", 4: "dotted", 5: "thick", 6: "double", 7: "hair",
               8: "mediumDashed", 9: "dashDot", 10: "mediumDashDot", 11: "dashDotDot",
               12: "mediumDashDotDot", 13: "slantDashDot"}
_HOR_XLS = {1: "left", 2: "center", 3: "right", 4: "fill", 5: "justify", 6: "centerContinuous", 7: "distributed"}
_VERT_XLS = {0: "top", 1: "center", 2: "bottom", 3: "justify", 4: "distributed"}


def _styles_xls(ws, sh, bk):
    """Reprend, cellule par cellule, la mise en forme du .xls : police (nom, taille, gras,
    italique, souligné, couleur), alignement (dont le centrage vertical de ses descriptions),
    fond, bordures — et le TEXTE ENRICHI (segments rouges/verts/soulignés d'une description),
    que xlrd expose via `rich_text_runlist_map`. Demande utilisateur du 22/09/2026 : « garder
    les mêmes couleurs et le même format que sa facture »."""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.styles.colors import Color

    def couleur(idx):
        rgb = bk.colour_map.get(idx) if idx is not None else None
        return "FF%02X%02X%02X" % tuple(rgb) if rgb else None

    def police(fo):
        return Font(name=fo.name or None, size=(fo.height / 20.0) if fo.height else None, bold=bool(fo.bold),
                    italic=bool(fo.italic), underline=("single" if fo.underline_type else None),
                    color=couleur(fo.colour_index))

    def police_segment(fo):
        c = couleur(fo.colour_index)
        return InlineFont(rFont=fo.name or None, sz=(fo.height / 20.0) if fo.height else None, b=bool(fo.bold),
                          i=bool(fo.italic), u=("single" if fo.underline_type else None),
                          color=Color(rgb=c) if c else None)

    def cote(style, idx):
        nom = _LIGNES_XLS.get(style)
        return Side(style=nom, color=couleur(idx) or "FF000000") if nom else Side()

    cache = {}
    for i in range(sh.nrows):
        for j in range(sh.ncols):
            try:
                xfi = sh.cell_xf_index(i, j)
            except Exception:
                continue
            if xfi not in cache:
                xf = bk.xf_list[xfi]
                fo = bk.font_list[xf.font_index]
                al = xf.alignment
                bd = xf.border
                fond = None
                if xf.background.fill_pattern == 1:
                    c = couleur(xf.background.pattern_colour_index)
                    if c:
                        fond = PatternFill("solid", fgColor=c)
                cache[xfi] = (
                    police(fo),
                    Alignment(horizontal=_HOR_XLS.get(al.hor_align), vertical=_VERT_XLS.get(al.vert_align, "center"),
                              wrap_text=bool(al.text_wrapped), shrink_to_fit=bool(al.shrink_to_fit),
                              text_rotation=al.rotation if al.rotation in range(0, 181) else 0),
                    fond,
                    Border(left=cote(bd.left_line_style, bd.left_colour_index), right=cote(bd.right_line_style, bd.right_colour_index),
                           top=cote(bd.top_line_style, bd.top_colour_index), bottom=cote(bd.bottom_line_style, bd.bottom_colour_index)),
                )
            font, align, fond, bord = cache[xfi]
            cell = ws.cell(row=i + 1, column=j + 1)
            cell.font, cell.alignment, cell.border = font, align, bord
            if fond is not None:
                cell.fill = fond
            runs = sh.rich_text_runlist_map.get((i, j))
            texte = cell.value
            if runs and isinstance(texte, str) and texte:
                bornes = [0] + [min(len(texte), max(0, k)) for k, _f in runs] + [len(texte)]
                fontes = [bk.xf_list[xfi].font_index] + [f for _k, f in runs]
                blocs = []
                for k in range(len(fontes)):
                    seg = texte[bornes[k]:bornes[k + 1]]
                    if seg:
                        blocs.append(TextBlock(police_segment(bk.font_list[fontes[k]]), seg))
                if blocs:
                    cell.value = CellRichText(*blocs)


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


def _vignette(octets, cible):
    """Octets d'image -> BytesIO réduit à `cible` (w, h) px, ou None. Une photo de 155 Mpx
    dans une case de 200 px n'est pas une attaque : `draft` la décode déjà réduite."""
    import io as _io
    import warnings

    from PIL import Image as PILImage

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", PILImage.DecompressionBombWarning)
            pil = PILImage.open(_io.BytesIO(octets))
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
        return buf, pil.width, pil.height
    except Exception:
        return None


def _poser_images_xls(ws, images, largeurs, hauteurs):
    """Ses images, à leur cellule d'origine, réduites à la taille de leur boîte."""
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D

    from customization_app.xls_images import boite_pixels

    for im in images:
        x, y, w, h = boite_pixels(im, largeurs, hauteurs)
        res = _vignette(im["data"], (max(16, int(w * 2)), max(16, int(h * 2))))
        if not res:
            continue
        buf, pw, ph = res
        img = XLImage(buf)
        ratio = min(w / float(pw or 1), h / float(ph or 1))
        img.width, img.height = max(1, int(pw * ratio)), max(1, int(ph * ratio))
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


def ordre_export(articles):
    """L'ordre du fichier renvoyé = l'ordre de NOTRE document : [("groupe", nom) | ("ligne", r)],
    une ligne de groupe à chaque changement de groupe d'articles ; les abandonnées n'y sont pas."""
    sorties, courant = [], object()
    for r in articles:
        if (getattr(r, "decision", "") or "") == "Abandonné":
            continue
        groupe = getattr(r, "item_group", "") or ""
        if groupe != courant:
            courant = groupe
            if groupe:
                sorties.append(("groupe", groupe))
        sorties.append(("ligne", r))
    return sorties


def colonne_frequente(colonnes, defaut=None):
    """La colonne où le fournisseur met ses photos = celle qui en porte le plus."""
    if not colonnes:
        return defaut
    return max(set(colonnes), key=lambda c: (colonnes.count(c), -c))


def hauteur_type(hauteurs, defaut=HAUTEUR_LIGNE_PHOTO):
    """La hauteur (médiane) de ses lignes de données : celle qu'on donne aux lignes ajoutées."""
    valeurs = sorted(float(h) for h in hauteurs if h)
    return valeurs[len(valeurs) // 2] if valeurs else defaut


def est_modifiee(qty, qty_fournisseur, prix, prix_fournisseur, additionnels=None):
    """Une ligne est « modifiée » si notre quantité retenue diffère de la sienne, si on
    renvoie un prix cible différent du sien, ou si on y embarque des additionnels."""
    if additionnels:
        return True
    if qty > 0 and abs(flt(qty) - flt(qty_fournisseur)) > 0.001:
        return True
    return prix > 0 and abs(flt(prix) - flt(prix_fournisseur)) > 0.00001


def texte_additionnels(adds, qty_ret, labels):
    """« Additional items: + 150 × Membrane 1812-80 GPD (Vontron) ; + 150 × Faucet »."""
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
    parties = [(observation or "").strip(), texte_additionnels(adds, qty_ret, labels)]
    return "\n".join(p for p in parties if p)


# ------------------------------------------------------------ écriture


def _copier_image(ws, img, delta_lignes, XLImage, colonne=None):
    """Recopie une image d'openpyxl à `delta_lignes` plus bas (ou plus haut), même colonne
    (ou `colonne`), mêmes décalages, même taille."""
    import io as _io

    try:
        nouveau = XLImage(_io.BytesIO(img._data()))
        ancre = copy.deepcopy(img.anchor)
        ancre._from.row = int(ancre._from.row) + delta_lignes
        if colonne is not None:
            ancre._from.col = colonne
        fin = getattr(ancre, "to", None)
        if fin is not None:
            fin.row = int(fin.row) + delta_lignes
            if colonne is not None:
                fin.col = colonne
        ext = getattr(ancre, "ext", None)
        if ext is not None:
            nouveau.width, nouveau.height = ext.width / EMU, ext.height / EMU
        nouveau.anchor = ancre
        ws.add_image(nouveau)
        return True
    except Exception:
        return False


def _poser_vignette(ws, ligne, colonne, buf, pw, ph, max_px=VIGNETTE_PX, dx=2, dy=2):
    """Une vignette (BytesIO) ancrée dans la cellule (ligne, colonne 1-based)."""
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D

    img = XLImage(buf)
    ratio = min(max_px / float(pw or max_px), max_px / float(ph or max_px), 1.0)
    img.width, img.height = max(1, int(pw * ratio)), max(1, int(ph * ratio))
    img.anchor = OneCellAnchor(_from=AnchorMarker(col=colonne - 1, colOff=dx * EMU, row=ligne - 1, rowOff=dy * EMU),
                               ext=XDRPositiveSize2D(cx=img.width * EMU, cy=img.height * EMU))
    ws.add_image(img)
    return img.height + dy


def _poser_photo_article(ws, ligne, colonne, file_url, max_px=VIGNETTE_PX, dx=2, dy=2):
    """Notre photo (fiche Article) dans une cellule — même logique que « Excel à envoyer »."""
    buf = _img_thumb(file_url, max_px=max_px * 2)
    if not buf:
        return False
    from PIL import Image as PILImage

    try:
        pil = PILImage.open(buf)
        pw, ph = pil.size
        buf.seek(0)
    except Exception:
        return False
    _poser_vignette(ws, ligne, colonne, buf, pw, ph, max_px, dx, dy)
    return True


def _copier_style(src, dst):
    try:
        if src.has_style:
            dst.font = copy.copy(src.font)
            dst.fill = copy.copy(src.fill)
            dst.border = copy.copy(src.border)
            dst.alignment = copy.copy(src.alignment)
            dst.number_format = src.number_format
    except Exception:
        pass


@frappe.whitelist()
def download_reponse_annotee(docname):
    """Rend le classeur du fournisseur, réordonné selon notre liste et enrichi de nos ajustements."""
    import io

    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    plan = plan_lecture(doc)
    L = _labels(doc)
    devise = doc.devise or "USD"

    wb_src, converti = _charger(_fichier_local(doc.fichier_fournisseur), valeurs=True)
    feuille = plan.get("feuille")
    if feuille not in wb_src.sheetnames:
        frappe.throw(_("La feuille « {0} » n'existe plus dans le fichier joint.").format(feuille))
    ws_src = wb_src[feuille]

    corresp = {k: int(v) for k, v in (plan.get("correspondances") or {}).items()}
    mapping = plan.get("mapping") or {}
    ligne_entete = int(plan.get("ligne_entete") or 0) + 1      # 1-based
    adds_map = _adds_map(doc)
    max_col = ws_src.max_column or 1

    # ses images, indexées par ligne source ; sa colonne photo = la plus fournie
    images_src = {}
    for img in getattr(ws_src, "_images", []) or []:
        images_src.setdefault(int(img.anchor._from.row) + 1, []).append(img)
    lignes_tableau = sorted(set(corresp.values()))
    fin_tableau = max(lignes_tableau) if lignes_tableau else ligne_entete
    col_photo = colonne_frequente([int(i.anchor._from.col) + 1 for xl in lignes_tableau for i in images_src.get(xl, [])])
    hauteur_donnees = hauteur_type([ws_src.row_dimensions[xl].height for xl in lignes_tableau])
    fusions_par_ligne = {}
    for rng in ws_src.merged_cells.ranges:
        if rng.min_row == rng.max_row:
            fusions_par_ligne.setdefault(rng.min_row, []).append((rng.min_col, rng.max_col))

    ORANGE = PatternFill("solid", fgColor="FFE0B2")        # ligne ajoutée ou modifiée
    ORANGE_FORT = PatternFill("solid", fgColor="FFB74D")   # la valeur qui change
    VERT = PatternFill("solid", fgColor="DCEDC8")          # ligne acceptée telle quelle
    GRIS = PatternFill("solid", fgColor="EDEDED")          # bloc des lignes retirées
    GROUPE = PatternFill("solid", fgColor="D9E7FF")        # ligne de groupe d'articles
    ENTETE = PatternFill("solid", fgColor="D9E7FF")
    gras = Font(bold=True)

    from openpyxl.styles import Border, Side

    from customization_app.liste_commande_import import _labels as _labels_export

    LX = _labels_export(doc)
    out = Workbook()
    ws = out.active
    ws.title = feuille[:31]
    if doc.langue_cible in RTL_LANGS:
        ws.sheet_view.rightToLeft = True
    for lettre, dim in ws_src.column_dimensions.items():
        if dim.width:
            ws.column_dimensions[lettre].width = dim.width
    # la date de notre réponse se lit EN TÊTE du fichier (demande 22/09), ses lignes descendent d'un cran
    DECALAGE = 1
    titre = ws.cell(row=1, column=1, value=f"{L['titre']} — {doc.titre or doc.name} ({doc.name}) — "
                                            f"{LX['date']} : {frappe.utils.formatdate(doc.date_commande or nowdate())}"
                                            + (f" — {LX['supplier']} : {doc.fournisseur}" if doc.fournisseur else ""))
    titre.font = Font(bold=True, size=12)
    titre.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 22
    NOIR = Side(style="thin", color="FF000000")
    CADRE = Border(left=NOIR, right=NOIR, top=NOIR, bottom=NOIR)

    # nos colonnes à droite ; « Photo » seulement s'il n'a pas de colonne photo à lui
    col0 = max_col + 2
    avec_conteneurs = any((r.repartition_conteneurs or "") for r in doc.articles)
    colonnes = []
    if col_photo is None and any(getattr(r, "image", None) for r in doc.articles):
        colonnes.append(("photo", L["photo"], 12))
    colonnes += [("qty", L["qty"], 13), ("prix", f'{L["prix"]} ({devise})', 15),
                 ("decision", L["decision"], 14), ("observation", L["observation"], 56)]
    if avec_conteneurs:
        colonnes.append(("conteneur", L["conteneur"], 14))
    pos = {cle: col0 + i for i, (cle, _lbl, _w) in enumerate(colonnes)}
    derniere_col = col0 + len(colonnes) - 1

    def copier_ligne(xl_src, xl_dst, effacer_nombres=False):
        for c in range(1, max_col + 1):
            src = ws_src.cell(row=xl_src, column=c)
            v = src.value
            if effacer_nombres and isinstance(v, (int, float)) and not isinstance(v, bool):
                v = None
            dst = ws.cell(row=xl_dst, column=c, value=v)
            _copier_style(src, dst)
        h = ws_src.row_dimensions[xl_src].height
        if h:
            ws.row_dimensions[xl_dst].height = h
        for c1, c2 in fusions_par_ligne.get(xl_src, []):
            try:
                ws.merge_cells(start_row=xl_dst, end_row=xl_dst, start_column=c1, end_column=c2)
            except Exception:
                pass
        for img in images_src.get(xl_src, []):
            _copier_image(ws, img, xl_dst - xl_src, XLImage)

    def colorer_ligne(xl, fond):
        for c in range(1, derniere_col + 1):
            ws.cell(row=xl, column=c).fill = fond

    def ecrire_nos_colonnes(xl, r, qty, prix, adds):
        cq = ws.cell(row=xl, column=pos["qty"], value=(qty if qty > 0 else None))
        cp = ws.cell(row=xl, column=pos["prix"], value=prix or None)
        ws.cell(row=xl, column=pos["decision"], value=_decision(doc, r.decision))
        co = ws.cell(row=xl, column=pos["observation"], value=texte_observation(r.observation, adds, qty, L))
        for c in range(col0, derniere_col + 1):   # nos colonnes centrées verticalement, comme ses cellules
            ws.cell(row=xl, column=c).alignment = Alignment(wrap_text=(c == pos["observation"]), vertical="center")
        if avec_conteneurs:
            ws.cell(row=xl, column=pos["conteneur"], value=_conteneurs_ligne(r))
        if "photo" in pos and getattr(r, "image", None):
            _poser_photo_article(ws, xl, pos["photo"], r.image)
            ws.row_dimensions[xl].height = max(ws.row_dimensions[xl].height or 0, HAUTEUR_LIGNE_PHOTO)
        return cq, cp

    # 1. tout ce qui précède son en-tête (titres, coordonnées), puis l'en-tête lui-même
    for xl in range(1, ligne_entete + 1):
        copier_ligne(xl, xl + DECALAGE)
    for rng in ws_src.merged_cells.ranges:
        if rng.min_row != rng.max_row and rng.max_row <= ligne_entete:
            try:
                ws.merge_cells(start_row=rng.min_row + DECALAGE, end_row=rng.max_row + DECALAGE,
                               start_column=rng.min_col, end_column=rng.max_col)
            except Exception:
                pass
    ligne_entete += DECALAGE
    for i, (_cle, lbl, w) in enumerate(colonnes):
        c = ws.cell(row=ligne_entete, column=col0 + i, value=lbl)
        c.font = gras
        c.fill = ENTETE
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(col0 + i)].width = w

    # 2. le corps, dans NOTRE ordre
    c_code = (mapping.get("code") + 1) if mapping.get("code") is not None else None
    c_des = (mapping.get("designation") + 1) if mapping.get("designation") is not None else None
    c_qty = (mapping.get("qty") + 1) if mapping.get("qty") is not None else None
    c_vol = (mapping.get("volume_total_m3") + 1) if mapping.get("volume_total_m3") is not None else None
    ligne = ligne_entete
    for genre, valeur in ordre_export(doc.articles):
        ligne += 1
        if genre == "groupe":
            cell = ws.cell(row=ligne, column=1, value=valeur)
            cell.font = gras
            for c in range(1, derniere_col + 1):
                ws.cell(row=ligne, column=c).fill = GROUPE
            try:
                ws.merge_cells(start_row=ligne, end_row=ligne, start_column=1, end_column=max_col)
            except Exception:
                pass
            ws.row_dimensions[ligne].height = 18
            continue
        r = valeur
        qty = qty_retenue(r)
        prix = _prix_cible_export(r)
        adds = adds_map.get(r.name, [])
        xl = corresp.get(r.name)
        if xl:
            copier_ligne(xl, ligne)
            qty_frn = flt(r.qty_fournisseur) or flt(r.qty)
            cq, cp = ecrire_nos_colonnes(ligne, r, qty, prix, adds)
            acceptee = (r.decision or "") == "Accepté"
            if acceptee:
                colorer_ligne(ligne, VERT)          # demande utilisateur 22/09 : accepté = vert
            if est_modifiee(qty, qty_frn, prix, flt(r.prix_fournisseur), adds):
                if not acceptee:
                    colorer_ligne(ligne, ORANGE)
                if qty > 0 and abs(qty - qty_frn) > 0.001:
                    cq.fill = ORANGE_FORT
                if prix > 0 and abs(prix - flt(r.prix_fournisseur)) > 0.00001:
                    cp.fill = ORANGE_FORT
                if adds:
                    ws.cell(row=ligne, column=pos["observation"]).fill = ORANGE_FORT
        else:
            # ligne AJOUTÉE : nos valeurs dans ses colonnes, notre photo dans sa colonne photo
            if c_code:
                ws.cell(row=ligne, column=c_code, value=r.item_code or "")
            if c_des:
                d = ws.cell(row=ligne, column=c_des, value=r.item_name_traduit or r.item_name or "")
                d.alignment = Alignment(wrap_text=True, vertical="center")
            if c_qty:
                ws.cell(row=ligne, column=c_qty, value=qty or None)
            if c_vol and flt(r.volume_ligne_m3):
                ws.cell(row=ligne, column=c_vol, value=round(flt(r.volume_ligne_m3), 4))
            ws.row_dimensions[ligne].height = hauteur_donnees
            if col_photo is not None:
                if not c_code or c_code != col_photo:
                    ws.cell(row=ligne, column=col_photo, value=r.item_code or "").alignment = Alignment(vertical="top", horizontal="center")
                if getattr(r, "image", None):
                    _poser_photo_article(ws, ligne, col_photo, r.image, max_px=min(120, int(hauteur_donnees * 96 / 72) - 30), dx=4, dy=22)
            ecrire_nos_colonnes(ligne, r, qty, prix, adds)
            colorer_ligne(ligne, ORANGE)
    fin_corps = ligne
    # bordures noires fines sur tout le tableau (ses colonnes ET les nôtres), en-tête compris
    for xl in range(ligne_entete, fin_corps + 1):
        for c in range(1, derniere_col + 1):
            ws.cell(row=xl, column=c).border = CADRE

    # 3. ce qu'il avait sous son tableau (conditions, signature) — sans ses nombres (totaux caducs)
    ligne += 1
    for xl in range(fin_tableau + 1, (ws_src.max_row or 0) + 1):
        if all(ws_src.cell(row=xl, column=c).value in (None, "") for c in range(1, max_col + 1)):
            continue
        ligne += 1
        copier_ligne(xl, ligne, effacer_nombres=True)

    # 4. les lignes retirées, nommées, avec le motif
    retirees = [r for r in doc.articles if (r.decision or "") == "Abandonné"]
    if retirees:
        ligne += 2
        t = ws.cell(row=ligne, column=1, value=L["retirees"])
        t.font = Font(bold=True, color="FF6B7280")
        for r in retirees:
            ligne += 1
            ws.cell(row=ligne, column=(c_code or 1), value=r.item_code or "")
            ws.cell(row=ligne, column=(c_des or 2), value=r.item_name_traduit or r.item_name or "")
            ws.cell(row=ligne, column=pos["decision"], value=_decision(doc, r.decision))
            co = ws.cell(row=ligne, column=pos["observation"], value=(r.observation or "").strip())
            co.alignment = Alignment(wrap_text=True, vertical="top")
            colorer_ligne(ligne, GRIS)

    # 5. légende
    ligne += 2
    lg = ws.cell(row=ligne, column=col0,
                 value=f"{L['titre']} — {doc.name} — {doc.date_commande or nowdate()} · {L['legende']}")
    lg.font = Font(italic=True, size=9, color="FF6B7280")
    if converti:
        ws.cell(row=ligne + 1, column=col0, value=L["note_xls"]).font = Font(italic=True, size=9)

    # 6. le plan de chargement : récapitulatif + un onglet par conteneur, avec les photos
    from customization_app.lci_conteneurs import ecrire_feuille
    ecrire_feuille(out, doc)
    _onglets_par_conteneur(out, doc, images_src, corresp, col_photo, L)

    _centrer_verticalement(out)   # demande 22/09 : TOUT centré verticalement, prix, remarque, conteneur…

    buf = io.BytesIO()
    out.save(buf)
    base = os.path.splitext(os.path.basename(doc.fichier_fournisseur))[0][:60]
    frappe.response["filename"] = f"{base}-{doc.name}-annote.xlsx"
    frappe.response["filecontent"] = buf.getvalue()
    frappe.response["type"] = "binary"
    return {"lignes": fin_corps - ligne_entete, "photos": len(ws._images)}


def _centrer_verticalement(wb):
    """Centre verticalement toutes les cellules de tous les onglets, en gardant l'alignement
    horizontal, le retour à la ligne et la rotation de chacune."""
    from openpyxl.styles import Alignment

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None and not cell.has_style:
                    continue
                a = cell.alignment
                cell.alignment = Alignment(horizontal=a.horizontal, vertical="center", wrap_text=a.wrap_text,
                                           shrink_to_fit=a.shrink_to_fit, text_rotation=a.text_rotation or 0,
                                           indent=a.indent or 0)


def _onglets_par_conteneur(wb, doc, images_src, corresp, col_photo, L):
    """Un onglet par conteneur (C1, C2…) : ses lignes, quantités chargées, volumes, montants —
    et la photo de chaque article (la sienne si sa cotation en avait une, sinon la nôtre)."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from customization_app.lci_conteneurs import LABELS_CONT, plan_enregistre
    from customization_app.liste_commande_import import _labels as _labels_export
    from customization_app.liste_commande_import import _uom_out

    plan = plan_enregistre(doc)
    if not plan.get("conteneurs"):
        return
    LC = LABELS_CONT.get(doc.langue_cible) or LABELS_CONT["Français"]
    LX = _labels_export(doc)
    devise = doc.devise or "USD"
    par_row = {r.name: r for r in doc.articles}
    gras = Font(bold=True)
    fond_th = PatternFill("solid", fgColor="F0F2F5")
    fond_ct = PatternFill("solid", fgColor="F9F0FF")

    for c in plan["conteneurs"]:
        titre = f'C{c["no"]} {c.get("type") or ""}'.strip()[:31]
        if titre in wb.sheetnames:
            del wb[titre]
        ws = wb.create_sheet(title=titre)
        if doc.langue_cible in RTL_LANGS:
            ws.sheet_view.rightToLeft = True
        for col, w in enumerate([5, 18, 14, 48, 10, 8, 12, 14], 1):
            ws.column_dimensions[get_column_letter(col)].width = w
        cap = flt(c["capacite"])
        pct = (flt(c["volume"]) / cap * 100) if cap else 0
        ws.append([f'{LC["conteneur"]} C{c["no"]} — {c.get("type") or ""}'
                   + (f' — {LC["depart"]} : {frappe.utils.formatdate(c["date"])}' if c.get("date") else "")
                   + f' — {LC["capacite"]} : {cap:.2f} m³ — {LC["remplissage"]} : {pct:.0f} %'])
        ws["A1"].font = Font(bold=True, size=12)
        ws["A1"].fill = fond_ct
        ws.append([])
        ws.append(["#", LC["code"], L["photo"], LC["designation"], LC["qty"], LC["uom"], LC["volume"], LC["montant"]])
        for col in range(1, 9):
            cell = ws.cell(row=3, column=col)
            cell.font = Font(bold=True, size=9)
            cell.fill = fond_th
        for i, l in enumerate(c["lignes"], 1):
            r = par_row.get(l["row"])
            ws.append([i, l["item_code"], "", l["libelle"] + (f' ({LC["scindee"]})' if l["scinde"] else ""),
                       flt(l["qty"]), _uom_out(l["uom"], LX), round(flt(l["volume"]), 4), round(flt(l["montant"]), 2)])
            ligne = ws.max_row
            ws.row_dimensions[ligne].height = HAUTEUR_LIGNE_PHOTO
            ws.cell(row=ligne, column=4).alignment = Alignment(wrap_text=True, vertical="center")
            ws.cell(row=ligne, column=8).number_format = f'#,##0.00 "{devise}"'
            _photo_conteneur(ws, ligne, r, images_src, corresp, col_photo)
        ws.append(["", "", "", LC["total"], "", "", round(flt(c["volume"]), 3), round(flt(c["montant"]), 2)])
        for col in (4, 7, 8):
            ws.cell(row=ws.max_row, column=col).font = gras
        ws.cell(row=ws.max_row, column=8).number_format = f'#,##0.00 "{devise}"'


def _photo_conteneur(ws, ligne, r, images_src, corresp, col_photo):
    """La photo d'une ligne d'onglet conteneur : la sienne (colonne photo de sa cotation), sinon la nôtre."""
    if not r:
        return
    xl = corresp.get(r.name)
    if xl and col_photo is not None:
        siennes = [i for i in images_src.get(xl, []) if int(i.anchor._from.col) + 1 == col_photo]
        for img in siennes[:1]:
            try:
                res = _vignette(img._data(), (VIGNETTE_PX * 2, VIGNETTE_PX * 2))
            except Exception:
                res = None
            if res:
                buf, pw, ph = res
                _poser_vignette(ws, ligne, 3, buf, pw, ph)
                return
    if getattr(r, "image", None):
        _poser_photo_article(ws, ligne, 3, r.image)
