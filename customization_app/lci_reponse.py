"""
Import de la RÉPONSE du fournisseur dans une « Liste Commande Import ».

Le fournisseur renvoie rarement le fichier qu'on lui a envoyé : il répond dans
son propre classeur, avec ses en-têtes, sa langue et ses unités. Ce module lit
n'importe quel .xlsx/.xls et le rapproche des lignes de la LCI :

  1. `_trouver_tableau`  : quelle feuille, quelle ligne d'en-tête ;
  2. `_mapper_colonnes`  : quelle colonne porte le prix, le CBM, le MOQ…
     (mots-clés FR/EN/DE/ES/中文 d'abord, IA en secours) ;
  3. `_apparier`         : quelle ligne fournisseur correspond à quelle ligne
     LCI (numéro de ligne renvoyé → code article → désignation exacte →
     approchante → IA en dernier recours) ;
  4. `analyser_reponse`  : rend un APERÇU — rien n'est écrit ;
  5. `appliquer_reponse` : écrit ce que l'utilisateur a validé dans l'aperçu.

L'aperçu est volontairement séparé de l'écriture : un mauvais appariement de
prix se paie en devises, il doit être relu avant d'entrer dans la base.
"""

import base64
import difflib
import json
import os
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from customization_app.liste_commande_import import (
    DOCTYPE,
    _chat_json,
    _guard,
    _strip_html,
)

# ------------------------------------------------------------------ lecture

MAX_HEADER_SCAN = 40      # lignes explorées à la recherche de l'en-tête
MAX_ROWS = 2000           # garde-fou sur les très gros classeurs
FUZZY_MIN = 0.72          # similarité minimale d'un appariement approchant
FUZZY_MARGE = 0.06        # écart minimal avec le 2e candidat (sinon ambigu)


def _txt(v):
    """Contenu d'une cellule en texte propre (None et NaN compris)."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _num(v):
    """Nombre d'une cellule, quel que soit le format d'écriture du fournisseur.

    Gère « 1,234.56 », « 1 234,56 », « USD 0.45 », « $0,45/pc », « 0.45 ».
    Rend None si la cellule ne porte aucun nombre exploitable.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and v != v) else float(v)

    s = _txt(v)
    if not s:
        return None
    # on ne garde que le premier nombre rencontré (« 0.45 USD/pc » -> 0.45)
    m = re.search(r"[-+]?[\d][\d\s., ']*", s)
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace(" ", "").replace("'", "")
    if not re.search(r"\d", raw):
        return None

    if "," in raw and "." in raw:
        # le séparateur décimal est le dernier des deux
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in raw:
        ent, _, frac = raw.rpartition(",")
        # « 1,234 » = millier ; « 0,45 » = décimal
        raw = (ent + frac) if (len(frac) == 3 and ent and "," not in ent) else raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


_LETTRES = re.compile(r"[^\W\d_]", re.UNICODE)
_NOMBRE_SEUL = re.compile(r"^[^\w]*\d[\d\s.,\u00a0'/-]*[^\w]*$")


def _est_texte(v):
    """Vrai si la cellule porte un LIBELLÉ, pas seulement un nombre.

    `_num` ne suffit pas : il extrait le premier nombre venu, donc il rend 1.0
    pour « MODEL A.1 » et ferait passer une colonne de références pour une
    colonne numérique.
    """
    s = _txt(v)
    if not s or _NOMBRE_SEUL.match(s):
        return False
    return len(_LETTRES.findall(s)) >= 2


DIM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[*x×]\s*(\d+(?:[.,]\d+)?)\s*[*x×]\s*(\d+(?:[.,]\d+)?)\s*(cm|mm|m)?",
    re.I)


def _volume_depuis_dimensions(v):
    """« 54*36*40cm » ou « 0.54 x 0.36 x 0.40 m » -> volume en m³."""
    s = _txt(v)
    m = DIM_RE.search(s)
    if not m:
        return None
    try:
        l, w, h = (float(m.group(i).replace(",", ".")) for i in (1, 2, 3))
    except ValueError:
        return None
    unite = (m.group(4) or "").lower()
    if not unite:
        # pas d'unité : au-delà de 5 la mesure est en cm, en deçà elle est en m
        unite = "cm" if max(l, w, h) > 5 else "m"
    div = {"cm": 1_000_000.0, "mm": 1_000_000_000.0, "m": 1.0}[unite]
    vol = l * w * h / div
    return vol if 0 < vol < 100 else None


def _feuilles(chemin):
    """Classeur -> [(nom de feuille, grille de valeurs)], quel que soit le format.

    Les fournisseurs chinois envoient encore beaucoup de .xls (BIFF, le format
    d'avant 2007) : openpyxl ne sait pas les lire, xlrd si. Les deux back-ends
    rendent la même grille de listes, le reste du module ignore le format.
    """
    ext = os.path.splitext(chemin)[1].lower()
    if ext == ".pdf":
        return lire_pdf(chemin)
    if ext == ".xls":
        try:
            import xlrd
            wb = xlrd.open_workbook(chemin)
        except Exception as e:
            frappe.throw(_("Fichier .xls illisible ({0}).").format(str(e)[:120]))
        out = []
        for ws in wb.sheets():
            if getattr(ws, "visibility", 0):
                continue
            out.append((ws.name, [[ws.cell_value(i, j) for j in range(ws.ncols)]
                                  for i in range(min(ws.nrows, MAX_ROWS))]))
        return out

    from openpyxl import load_workbook
    try:
        wb = load_workbook(chemin, data_only=True, read_only=True)
    except Exception as e:
        frappe.throw(_("Fichier illisible ({0}). Formats acceptés : .xlsx, .xlsm, .xls.")
                     .format(str(e)[:120]))
    out = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        out.append((ws.title, [[c.value for c in row]
                               for row in ws.iter_rows(max_row=min(ws.max_row or 0, MAX_ROWS))]))
    wb.close()
    return out


# ------------------------------------------------- PDF (liste de prix)
# Demande utilisateur 25/09/2026 : « une liste de prix PDF, et l'IA en extrait les colonnes
# (volume unitaire, prix fournisseur…) ». Deux lectures : les tableaux du texte du PDF
# (PyMuPDF find_tables — un PDF exporté d'Excel), sinon chaque page transcrite par le
# modèle vision (`lire_pdf_ia`). Dans les deux cas on rend une grille comme pour un
# classeur : le reste du module (en-tête, rôles des colonnes, appariement) ne change pas.

MAX_PAGES_PDF = 40
MAX_PAGES_IA = 12
DPI_PAGE_IA = 130

PROMPT_PAGE_PRIX = (
    "You read ONE page of a supplier price list / quotation (image attached). Transcribe the main "
    "product table as JSON: {\"header\": [\"col1\", ...], \"rows\": [[\"cell\", ...], ...]}. Keep the column "
    "names EXACTLY as printed (any language), one entry per printed column, in order; one row per "
    "product line, cells as printed (numbers as plain text like \"12.50\", keep units such as USD, CBM, "
    "pcs). Skip page titles, totals, terms and signatures. If the page has no product table, return "
    "{\"header\": [], \"rows\": []}. Never invent values. If the rows show product PICTURES, add one last column named "
    "\"Photo\" with, for each row, a short description of its picture (product type, colour, shape, number of stages, "
    "fittings…), so that the product can be recognised without the picture."
)


def _norm_entete_pdf(cells):
    return tuple(_txt(c).lower() for c in cells)


def _est_entete_repetee(ligne, entete):
    """Une ligne identique à l'en-tête (le PDF le répète à chaque page). Pur."""
    return entete and _norm_entete_pdf(ligne) == _norm_entete_pdf(entete)


class LignePdf(list):
    """Une ligne de tableau lue dans un PDF : ses textes (la liste elle-même) et, pour découper
    les photos, la page et le rectangle de chaque cellule (None = cellule fusionnée avec celle
    du dessus). Se manipule comme une liste ordinaire partout ailleurs."""

    def __init__(self, textes, page=None, rects=None):
        super().__init__(textes)
        self.page = page
        self.rects = list(rects or [])


def fusionner_tables(tables):
    """[(page, rows)] -> [(nom, grille)] : les tableaux de même en-tête (ou de même largeur
    et sans en-tête propre : suite du précédent sur la page suivante) sont mis bout à bout,
    l'en-tête répété est retiré. Un tableau d'une autre forme ouvre une nouvelle grille. Pur."""
    grilles = []
    for page, rows in tables:
        rows = [r if isinstance(r, LignePdf) else list(r) for r in rows if r and any(_txt(c) for c in r)]
        if not rows:
            continue
        if grilles:
            nom, grille, entete, pages = grilles[-1]
            if _est_entete_repetee(rows[0], entete):
                grille.extend(rows[1:]); pages.append(page); continue
            # une ligne produit peut marquer 1 (un mot comme « port ») : seul un vrai en-tête (≥ 2) ouvre un tableau
            if len(rows[0]) == len(entete) and _score_entete(rows[0]) < 2:
                grille.extend(rows); pages.append(page); continue
        grilles.append(["PDF", rows, rows[0], [page]])
    out = []
    for nom, grille, entete, pages in grilles:
        p0, p1 = min(pages), max(pages)
        out.append(("PDF p.%d" % p0 if p0 == p1 else "PDF p.%d-%d" % (p0, p1), grille[:MAX_ROWS]))
    return out


def lire_pdf(chemin, ia=None):
    """PDF -> [(nom, grille)]. `ia=None` : tableaux du texte s'il y en a, sinon vision ;
    `ia=True` : vision d'office ; `ia=False` : texte seulement."""
    import pymupdf

    doc = pymupdf.open(chemin)
    tables = []
    if ia is not True:
        for no, page in enumerate(doc):
            if no >= MAX_PAGES_PDF:
                break
            trouvees = []
            for strategie in ("lines", "text"):
                try:
                    trouvees = page.find_tables(strategy=strategie).tables
                except Exception:
                    trouvees = []
                if trouvees:
                    break
            for t in trouvees:
                try:
                    rows = t.extract()
                    rects = [r.cells for r in t.rows]
                except Exception:
                    continue
                if rows and len(rows) >= 2 and len(rows[0]) >= 2:
                    # chaque ligne garde sa page et ses cellules : la colonne « Photo » d'un
                    # catalogue est une image, elle se découpe plus tard, pas ici
                    tables.append((no + 1, [LignePdf(r, page=page, rects=rects[k] if k < len(rects) else None)
                                            for k, r in enumerate(rows)]))
        grilles = fusionner_tables(tables)
        if any(len(g) >= 2 for _n, g in grilles):
            return grilles
        if ia is False:
            frappe.throw(_("Aucun tableau lisible dans le texte de ce PDF."))
    return lire_pdf_ia(doc)


def lire_pdf_ia(doc):
    """Chaque page transcrite par le modèle vision -> grilles fusionnées par en-tête."""
    from customization_app.liste_commande_import import _chat_json_image

    tables = []
    for no, page in enumerate(doc):
        if no >= MAX_PAGES_IA:
            break
        png = page.get_pixmap(dpi=DPI_PAGE_IA, alpha=False).tobytes("png")
        sortie = _chat_json_image(PROMPT_PAGE_PRIX, "Page %d of %d." % (no + 1, len(doc)), png) or {}
        rows = grille_ia(sortie)
        if rows:
            tables.append((no + 1, rows))
    grilles = fusionner_tables(tables)
    if not grilles:
        frappe.throw(_("L'IA n'a trouvé aucun tableau de prix dans ce PDF."))
    return grilles


def grille_ia(sortie):
    """La réponse du modèle -> lignes de grille (en-tête + lignes, largeur homogène). Pur."""
    if not isinstance(sortie, dict):
        return []
    entete = [_txt(c) for c in (sortie.get("header") or [])]
    lignes = [[_txt(c) for c in (r or [])] for r in (sortie.get("rows") or []) if isinstance(r, (list, tuple))]
    if not entete or not lignes:
        return []
    larg = max([len(entete)] + [len(r) for r in lignes])
    return [entete + [""] * (larg - len(entete))] + [r + [""] * (larg - len(r)) for r in lignes]


# ------------------------------------------------- appariement par IA, ligne par ligne, avec la photo
# (demande utilisateur 25/09/2026 : « l'identification doit utiliser l'IA, une par une, d'après
# l'image et les caractéristiques »). Pour chaque ligne de la LCI : sa photo, sa désignation et sa
# description sont présentées au modèle vision avec les entrées candidates du fournisseur
# (code, désignation, photo décrite, prix, volumes) ; il désigne l'entrée qui est LE MÊME produit,
# ou rien. Plus lent et plus cher que la cascade texte (≈ 1 à 2 ¢ par ligne), mais il voit.

MAX_CANDIDATS_IMAGE = 8      # candidats (avec leur photo) présentés au modèle vision, par ligne
LOT_PRESELECTION = 10        # lignes LCI par appel de présélection
DESIG_MAX_PRESEL = 220       # caractères de désignation par entrée dans le catalogue compact


def _candidats_pour(row, libres, n=MAX_CANDIDATS_IMAGE):
    """Secours quand la présélection IA n'a pas répondu : les entrées fournisseur les plus proches
    de la ligne par similarité de texte (faible entre un libellé français et un catalogue anglais,
    d'où la présélection par l'IA en premier) ; toutes si elles sont peu nombreuses. Pur."""
    if len(libres) <= n:
        return list(libres)
    ref = _norm("%s %s %s" % (row.item_code or "", row.item_name or "", row.item_name_traduit or ""))
    def score(f):
        t = _norm("%s %s %s" % (f.get("code") or "", f.get("designation") or "", f.get("photo") or ""))
        return difflib.SequenceMatcher(None, ref, t).ratio()
    return sorted(libres, key=score, reverse=True)[:n]


def _description_courte(row, maxlen=600):
    return _strip_html(row.description or "")[:maxlen] if getattr(row, "description", None) else ""


# --- 1) présélection : l'IA (texte) choisit dans TOUTE la liste du fournisseur les entrées qui
# peuvent être notre produit. Vu sur les vrais catalogues (25/09/2026) : la similarité de texte
# entre « Porte filtre, double bleu, 10' » et « 2 stages water filter Housing: Blue housing
# (10" * 2.5") » est nulle, les bons candidats n'étaient jamais présentés au modèle vision.

def catalogue_court(libres):
    """La liste du fournisseur en texte compact : un alias court par entrée (« S12 »), le code, la
    désignation tronquée et le prix. Rend (texte, {alias: id}). Pur."""
    alias, lignes = {}, []
    for k, f in enumerate(libres, start=1):
        a = "S%d" % k
        alias[a] = f["id"]
        prix = f.get("prix_unitaire")
        lignes.append("%s | %s | %s | %s" % (
            a, f.get("code") or "", (f.get("designation") or "")[:DESIG_MAX_PRESEL].replace("\n", " "),
            "" if prix is None else prix))
    return "\n".join(lignes), alias


PROMPT_PRESELECTION = (
    "You shortlist candidates in a supplier's price list for lines of our purchase order (water treatment "
    "equipment: filter housings, cartridges, RO systems, fittings, pumps, softeners, purifiers). Our lines are in "
    "French (sometimes with an English translation); the supplier writes in English. For EACH of our lines, list "
    "the supplier entries (their alias, e.g. \"S12\") that could be THE SAME product, most likely first, at most "
    "%d. Match on product type, size (5\"/10\"/20\", big blue…), number of stages, port/thread size (1/2\", 3/4\", "
    "1\"), colour/transparency, material, capacity. Include every plausible variant (same product, other port "
    "size); an empty list when nothing fits. Answer JSON only: {\"<our line key>\": [\"S3\", \"S4\"], ...}."
    % MAX_CANDIDATS_IMAGE
)


def lire_preselection(rep, cles, alias):
    """La réponse de présélection -> {clé de ligne: [ids fournisseur]} ; alias inconnus ignorés,
    lignes sans réponse absentes (≠ liste vide = « rien ne convient »). Pur."""
    out = {}
    if not isinstance(rep, dict):
        return out
    for cle in cles:
        v = rep.get(cle)
        if not isinstance(v, list):
            continue
        ids = []
        for a in v:
            i = alias.get(str(a).strip())
            if i and i not in ids:
                ids.append(i)
        out[cle] = ids[:MAX_CANDIDATS_IMAGE]
    return out


def _payload_preselection(rows):
    return json.dumps({"nos_lignes": [
        {"cle": r.name, "code": r.item_code or "", "designation": r.item_name or "",
         "designation_traduite": r.item_name_traduit or "", "description": _description_courte(r, 300),
         "quantite": flt(r.qty)} for r in rows]}, ensure_ascii=False)


def _preselection_ia(rows, libres, contexte):
    """{nom de ligne: [entrées candidates]} : par lots de lignes en parallèle, le modèle (texte)
    présélectionne dans tout le catalogue. Une ligne sans réponse retombe sur la similarité de
    texte ; une ligne à liste vide n'a aucun candidat (pas d'appel vision pour elle)."""
    from concurrent.futures import ThreadPoolExecutor

    from customization_app.liste_commande_import import _completion_parts

    texte, alias = catalogue_court(libres)
    lots = list(_lots(list(rows), LOT_PRESELECTION))

    def un(lot):
        user = ("SUPPLIER PRICE LIST (alias | code | description | unit price):\n%s\n\nOUR LINES:\n%s"
                % (texte, _payload_preselection(lot)))
        try:
            rep = _completion_parts(contexte, PROMPT_PRESELECTION, user)
        except Exception as e:
            return {}, str(e)[:300]
        return lire_preselection(rep, [r.name for r in lot], alias), None

    with ThreadPoolExecutor(max_workers=PARALLELE_IMAGES) as pool:
        resultats = list(pool.map(un, lots))
    choisis, erreurs = {}, []
    for res, err in resultats:
        choisis.update(res)
        if err:
            erreurs.append(err)
    if erreurs:
        frappe.log_error(title="LCI réponse — présélection IA", message="\n".join(erreurs[:10]))
    par_id = {f["id"]: f for f in libres}
    out = {}
    for row in rows:
        if row.name in choisis:
            out[row.name] = [par_id[i] for i in choisis[row.name] if i in par_id]
        else:
            out[row.name] = _candidats_pour(row, libres)
    return out


# --- 2) identification : pour chaque ligne, le modèle vision voit NOTRE photo et celles des
# candidats présélectionnés (découpées dans le PDF), avec les caractéristiques de chacun.


def _photo_ligne(row):
    """La vignette JPEG de l'article (octets) ou None."""
    from customization_app.liste_commande_import import _img_thumb

    if not getattr(row, "image", None):
        return None
    buf = _img_thumb(row.image, max_px=512)
    return buf.getvalue() if buf else None


PROMPT_APPARIEMENT_IMAGE = (
    "You identify, in a supplier's price list, the entry that is THE SAME product as one line of our purchase "
    "order (water treatment equipment: filter housings, cartridges, RO systems, fittings, pumps, softeners). "
    "You get our line (code, French name, English translation, description, and its photo when attached) and the "
    "candidate entries of the supplier (alias C1, C2…: code, description, variant, price, volumes) followed by the "
    "photo of each candidate that has one. Compare shapes, colours, sizes (5\"/10\"/20\", big blue…), number of "
    "stages, fittings/threads (1/2\", 3/4\", 1\"), transparency, materials, accessories (bracket, wrench). "
    "A different size, thread, capacity, stage count or transparency means a different product: a clear/transparent "
    "(PET) housing never matches an opaque (blue/white) one, whatever else matches. When candidates are variants of "
    "one product (other port size), pick the variant matching our line's specification (« In/Out 3/4\" »), or the "
    "base variant when our line does not say. Answer JSON only: {\"id\": \"<candidate alias>\" or null, "
    "\"confiance\": 0..1, \"raison\": \"<15 words>\"}. Prefer null over a guess."
)


PARALLELE_IMAGES = 4


def _payload_image(row, cands):
    return json.dumps({
        "notre_ligne": {"code": row.item_code or "", "designation": row.item_name or "",
                        "designation_traduite": row.item_name_traduit or "", "description": _description_courte(row),
                        "quantite": flt(row.qty)},
        "candidats": [{"id": "C%d" % k, "code": f.get("code") or "", "designation": f.get("designation_base") or f.get("designation") or "",
                       "variante": f.get("variante") or "", "photo_decrite": f.get("photo") or "",
                       "prix": f.get("prix_unitaire"), "volume_unitaire_m3": f.get("volume_unitaire_m3"),
                       "remarque": f.get("remarque") or ""}
                      for k, f in enumerate(cands, start=1)],
    }, ensure_ascii=False)


def _contenu_vision(row, cands, photo, photos):
    """Le message utilisateur de l'identification : les caractéristiques, notre photo, puis la
    photo de chaque candidat (étiquetée par son alias)."""
    from customization_app.liste_commande_import import _part_image

    parts = [{"type": "text", "text": _payload_image(row, cands)}]
    if photo:
        parts += [{"type": "text", "text": "Photo of OUR product:"}, _part_image(photo, "high", "image/jpeg")]
    for k, f in enumerate(cands, start=1):
        p = photos.get(f["id"]) or f.get("_photo")
        if p:
            parts += [{"type": "text", "text": "Photo of candidate C%d:" % k}, _part_image(p, "low", "image/jpeg")]
    return parts


def resoudre_choix(choix):
    """[(nom de ligne, id source, confiance)] -> {nom: (id, confiance)} : une source ne sert
    qu'une fois, la meilleure confiance l'emporte ; sous 0,5 on ne retient rien. Pur."""
    out, pris = {}, {}
    for nom, ident, conf in sorted(choix, key=lambda c: -c[2]):
        if not ident or conf < 0.5 or ident in pris:
            continue
        out[nom] = (ident, round(min(1.0, conf), 2))
        pris[ident] = nom
    return out


def alias_choisi(rep, cands):
    """La réponse du modèle vision -> (id fournisseur ou None, confiance) : l'alias « C3 » (ou
    l'id brut) doit désigner un des candidats. Pur."""
    if not isinstance(rep, dict):
        return None, 0
    ident = rep.get("id")
    conf = flt(rep.get("confiance"))
    if not isinstance(ident, str):
        return None, conf
    ident = ident.strip()
    alias = {"C%d" % k: f["id"] for k, f in enumerate(cands, start=1)}
    if ident in alias:
        return alias[ident], conf
    if any(f["id"] == ident for f in cands):
        return ident, conf
    return None, conf


def _apparier_images(rows, libres, photos=None, contexte=None, progres=None):
    """{nom de ligne LCI: (id source, confiance)} par IA : présélection dans tout le catalogue,
    puis une ligne à la fois avec sa photo et celles des candidats — PARALLELE_IMAGES lignes en
    même temps (appels réseau), le client OpenAI préparé dans le thread principal. Seul ce thread
    touche à Frappe (progression, journal)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from customization_app.liste_commande_import import _completion_parts, _contexte_ia

    photos = photos or {}
    # un titre de section, une ligne sans prix, n'est pas un candidat
    libres = [f for f in libres if f.get("prix_unitaire") is not None]
    if not rows or not libres:
        return {}
    contexte = contexte or _contexte_ia()
    if progres:
        progres("preselection", 0, len(rows))
    cands_par_ligne = _preselection_ia(rows, libres, contexte)
    taches = []
    for row in rows:
        cands = cands_par_ligne.get(row.name) or []
        if cands:
            taches.append((row.name, cands, _contenu_vision(row, cands, _photo_ligne(row), photos)))
    if not taches:
        return {}

    def une(t):
        nom, cands, contenu = t
        try:
            rep = _completion_parts(contexte, PROMPT_APPARIEMENT_IMAGE, contenu) or {}
        except Exception as e:
            return (nom, None, 0, str(e)[:300])
        ident, conf = alias_choisi(rep, cands)
        return (nom, ident, conf, None)

    def lancer(taches, deja=0, total=None):
        resultats = []
        with ThreadPoolExecutor(max_workers=PARALLELE_IMAGES) as pool:
            futurs = [pool.submit(une, t) for t in taches]
            for k, fut in enumerate(as_completed(futurs), start=1):
                resultats.append(fut.result())
                if progres:
                    progres("identification", deja + k, total or len(taches))
        erreurs = [r[3] for r in resultats if r[3]]
        if erreurs:
            frappe.log_error(title="LCI réponse — appariement IA photo", message="\n".join(erreurs[:20]))
        return resultats

    resultats = lancer(taches)
    retenus = resoudre_choix([(nom, ident, conf) for nom, ident, conf, _e in resultats])
    # Seconde passe pour les lignes qui ont perdu leur entrée au profit d'une autre ligne (trois
    # lignes « porte-filtre double bleu » en 1/2", 3/4" et 1" : le modèle a pu désigner la même
    # variante pour les trois) : on les représente sans les entrées déjà prises.
    pris = {ident for ident, _c in retenus.values()}
    perdants = [t for t in taches if t[0] not in retenus
                and any(r[0] == t[0] and r[1] in pris for r in resultats)]
    if perdants:
        rows_par_nom = {r.name: r for r in rows}
        rejouees = []
        for nom, cands, _contenu in perdants:
            restes = [f for f in cands if f["id"] not in pris]
            if restes:
                rejouees.append((nom, restes, _contenu_vision(rows_par_nom[nom], restes, _photo_ligne(rows_par_nom[nom]), photos)))
        if rejouees:
            seconds = lancer(rejouees, deja=len(taches), total=len(taches) + len(rejouees))
            for nom, (ident, conf) in resoudre_choix(
                    [(nom, ident, conf) for nom, ident, conf, _e in seconds if ident not in pris]).items():
                retenus[nom] = (ident, conf)
    return retenus


# ------------------------------------------------- repérage de l'en-tête

MOTS = {
    "ref_ligne": ["#", "n°", "no", "no.", "item no", "line", "ligne", "s/n", "sn",
                  "序号", "nr", "num", "numero", "numéro"],
    # le code DOUANIER n'est pas une référence article : sans ce rôle, « HS
    # code » serait pris pour le code produit et toutes les lignes porteraient
    # le même, rendant l'appariement par code inopérant.
    "hs_code": ["hs code", "h.s code", "hscode", "code hs", "customs code",
                "tariff code", "海关编码", "hs编码"],
    "code": ["code", "item code", "model", "model no", "modèle", "ref", "réf",
             "reference", "référence", "article", "art.", "part no", "sku",
             "型号", "货号", "artikelnummer", "codigo", "código"],
    "designation": ["designation", "désignation", "description", "item name",
                    "specification", "specifications", "spec", "product", "produit", "name", "nom", "item", "goods",
                    "品名", "名称", "描述", "bezeichnung", "descripcion",
                    "descripción", "nombre"],
    "qty": ["qty", "quantity", "quantité", "quantite", "qte", "qté", "menge",
            "cantidad", "数量", "order qty", "q'ty", "qty.", "pieces", "pcs",
            "pieza", "stück", "piece"],
    # NOTRE colonne de prix cible, quand le fournisseur nous renvoie notre
    # propre fichier. Elle doit capter la colonne pour que « Target price » ne
    # soit pas lu comme le prix du fournisseur — on importerait notre propre
    # cible comme sa réponse, et l'écart serait nul partout.
    "prix_cible_envoye": ["target price", "prix cible", "zielpreis",
                          "precio objetivo", "السعر المستهدف", "目标价"],
    "prix_unitaire": ["unit price", "price", "prix", "prix unitaire", "u/price",
                      "fob", "fob price", "exw", "unitprice", "u.price",
                      "单价", "价格", "stückpreis", "preis", "precio",
                      "usd", "usd/pc", "price usd", "unit cost", "cost"],
    "total": ["amount", "total", "total amount", "montant", "line total",
              "总价", "金额", "gesamt", "importe", "sum"],
    "moq": ["moq", "min order", "minimum order", "min qty", "mindestmenge",
            "起订量", "最小起订量", "minimo", "mínimo"],
    "pcs_carton": ["pcs/ctn", "pcs per carton", "pcs/carton", "qty/ctn",
                   "pcs/box", "pc/ctn", "per carton", "packing", "pcs per box",
                   "装箱数", "每箱", "stk/karton", "pzs/caja", "qty per carton"],
    "volume_carton_m3": ["cbm/ctn", "cbm per carton", "carton cbm", "volume/ctn",
                         "每箱体积", "cbm/carton", "vol/ctn"],
    "volume_total_m3": ["cbm", "total cbm", "volume", "m3", "m³", "volumen",
                        "体积", "总体积", "volume total"],
    "volume_unitaire_m3": ["cbm/pc", "cbm per pc", "unit cbm", "volume unitaire",
                           "unit volume", "单个体积"],
    "dimensions_carton": ["carton size", "ctn size", "dimension", "dimensions",
                          "size", "taille", "外箱尺寸", "尺寸", "maße", "medidas",
                          "measurement", "meas."],
    "poids": ["weight", "poids", "gross weight", "net weight", "gewicht",
              "peso", "重量", "毛重", "净重"],
    "photo": ["photo", "picture", "image", "图片", "foto", "bild"],
    "remarque": ["remark", "remarks", "note", "notes", "comment", "commentaire",
                 "备注", "bemerkung", "observacion", "observación", "delivery",
                 "lead time", "délai", "交期"],
}

# une seule colonne peut légitimement porter ces rôles
UNIQUES = set(MOTS)


def _norm(s):
    """Texte comparable : sans accents, sans ponctuation, en minuscules."""
    s = unicodedata.normalize("NFKD", _txt(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w一-鿿]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score_entete(cells):
    """Combien de cellules de cette ligne ressemblent à un en-tête connu."""
    score = 0
    for c in cells:
        n = _norm(c)
        if not n or len(n) > 40:
            continue
        for mots in MOTS.values():
            # `_norm("#")` est vide et « "" in n » est toujours vrai : sans ce
            # garde-fou, chaque cellule remplie marquerait un point et la
            # première ligne de DONNÉES serait prise pour l'en-tête.
            if any(_norm(m) and (n == _norm(m) or _norm(m) in n) for m in mots):
                score += 1
                break
    return score


def _trouver_tableau(feuilles):
    """Feuille et ligne d'en-tête les plus plausibles de tout le classeur."""
    best = None
    for nom, grille in feuilles:
        for i, cells in enumerate(grille[:MAX_HEADER_SCAN]):
            score = _score_entete(cells)
            # une ligne d'en-tête est suivie de données
            suite = sum(1 for r in grille[i + 1:i + 6] if any(_txt(c) for c in r))
            if score >= 3 and suite >= 1:
                poids = (score, suite, -i)
                if best is None or poids > best[0]:
                    best = (poids, nom, i, grille)
    if not best:
        # aucun en-tête reconnu : on garde la feuille la plus remplie, ligne 0
        nom, grille = max(feuilles, key=lambda f: sum(len(r) for r in f[1]))
        return nom, 0, grille
    return best[1], best[2], best[3]


def _mapper_colonnes(entete, echantillon):
    """En-tête -> {champ canonique: index de colonne}. Mots-clés, puis IA."""
    mapping = {}
    pris = set()

    def poser(champ, idx):
        if champ in UNIQUES and champ in mapping:
            return
        if idx in pris:
            return
        mapping[champ] = idx
        pris.add(idx)

    # « # » et « № » ne survivent pas à la normalisation (aucun caractère de
    # mot) : ce sont pourtant les en-têtes de numéro de ligne les plus courants,
    # dont celui de NOTRE propre fichier de cotation.
    for idx, cell in enumerate(entete):
        if _txt(cell) in {"#", "№", "N°", "n°", "Nº"}:
            poser("ref_ligne", idx)
            break

    # 1) en-tête strictement égal à un mot-clé : le cas le plus sûr
    for idx, cell in enumerate(entete):
        if idx in pris:
            continue
        n = _norm(cell)
        if not n:
            continue
        for champ, mots in MOTS.items():
            if champ not in mapping and any(n == _norm(m) for m in mots):
                poser(champ, idx)
                break

    # 2) en-tête CONTENANT un mot-clé : c'est le mot-clé le plus LONG qui
    #    l'emporte, pas le premier rôle testé. « Unit volume (m³) » contient
    #    « volume » (volume total) et « unit volume » (volume unitaire) : sans
    #    cette règle le premier gagnerait et le volume serait divisé par la
    #    quantité. Même piège pour « Total CBM » contre « Total ».
    for idx, cell in enumerate(entete):
        if idx in pris:
            continue
        n = _norm(cell)
        if not n:
            continue
        meilleur, longueur = None, 0
        for champ, mots in MOTS.items():
            if champ in mapping:
                continue
            for m in mots:
                nm = _norm(m)
                if nm and nm in n and len(nm) > longueur:
                    meilleur, longueur = champ, len(nm)
        if meilleur:
            poser(meilleur, idx)

    # 2) l'IA tranche s'il manque le prix — sans prix, l'import n'a pas d'objet
    if "prix_unitaire" not in mapping:
        ia = _mapper_colonnes_ia(entete, echantillon)
        for champ, idx in ia.items():
            if champ in MOTS and idx is not None and champ not in mapping:
                poser(champ, cint(idx))
    return mapping


def _mapper_colonnes_ia(entete, echantillon):
    """Demande à l'IA quelle colonne porte quoi (en-tête + lignes d'exemple)."""
    payload = {
        "colonnes": {str(i): _txt(c) for i, c in enumerate(entete)},
        "exemples": [{str(i): _txt(c) for i, c in enumerate(r)} for r in echantillon[:4]],
    }
    system = (
        "Tu analyses un tableau de cotation envoyé par un fournisseur "
        "(souvent chinois). On te donne les en-têtes de colonnes indexés et "
        "quelques lignes d'exemple. Identifie l'index de colonne de chaque "
        "rôle, en t'appuyant AUSSI sur le contenu des exemples quand l'en-tête "
        "est vide ou ambigu. Rôles possibles : ref_ligne (numéro de ligne), "
        "code (référence article), designation, qty, prix_unitaire (prix "
        "unitaire, jamais le montant total), total, moq, pcs_carton, "
        "volume_carton_m3, volume_unitaire_m3, volume_total_m3, "
        "dimensions_carton, remarque. Mets null pour un rôle absent. Ne "
        "réutilise jamais le même index pour deux rôles. Réponds en JSON : "
        '{"prix_unitaire": 4, "designation": 2, "moq": null, ...}')
    try:
        return _chat_json(system, json.dumps(payload, ensure_ascii=False)) or {}
    except Exception as e:
        frappe.log_error(title="LCI réponse — mapping IA",
                         message=f"{e}\n{json.dumps(payload, ensure_ascii=False)[:2000]}")
        return {}


# ------------------------------------------------- extraction des lignes

def _colonnes_texte_libres(grille, ligne_entete, mapping):
    """Colonnes de texte qu'aucun rôle n'a réclamées.

    Sur une vraie facture, le libellé qui distingue deux articles n'est pas
    toujours dans la colonne « Description » : dix osmoseurs peuvent partager
    « Reverse Osmosis System » et ne se distinguer que par un « MODEL B.2 »
    rangé sous un en-tête inattendu (« Picture »). Ces colonnes viennent donc
    compléter la désignation. On n'admet que du texte court et majoritaire,
    pour ne pas polluer l'appariement avec des dates ou des numéros.
    """
    pris = set(mapping.values())
    data = grille[ligne_entete + 1:]
    nb_col = max((len(r) for r in data), default=0)
    libres = []
    for j in range(nb_col):
        if j in pris:
            continue
        vals = [_txt(r[j]) for r in data if j < len(r) and _txt(r[j])]
        textes = [v for v in vals if _est_texte(v)]
        if len(vals) < 3 or len(textes) / len(vals) < 0.6:
            continue
        if sum(len(v) for v in textes) / len(textes) > 80:
            continue
        libres.append(j)
    return libres


def _extraire(grille, ligne_entete, mapping, feuille):
    """Lignes de données du fournisseur -> dictionnaires normalisés."""
    appoint = _colonnes_texte_libres(grille, ligne_entete, mapping)
    out = []
    for i, cells in enumerate(grille[ligne_entete + 1:], start=ligne_entete + 2):
        def col(champ):
            idx = mapping.get(champ)
            return cells[idx] if idx is not None and idx < len(cells) else None

        code = _txt(col("code"))
        desig_base = desig = _txt(col("designation"))
        # seules les valeurs vraiment textuelles sont reprises : sur la ligne
        # de total, ces colonnes portent souvent un « / » qui suffirait à faire
        # passer le pied de tableau pour un article.
        extras = [_txt(cells[j])[:80] for j in appoint
                  if j < len(cells) and _est_texte(cells[j])]
        if extras:
            desig = " — ".join(([desig] if desig else []) + extras)
        prix = _num(col("prix_unitaire"))
        qty = _num(col("qty"))
        if not code and not desig and prix is None:
            continue  # ligne vide, séparateur ou pied de tableau
        if prix is None and not (code and desig):
            continue  # un titre de section (« Water Softener »), pas un article

        # une ligne « TOTAL » n'est pas un article
        if not code and _norm(desig) in {"total", "grand total", "sub total",
                                         "subtotal", "amount", "sum", "合计", "总计"}:
            continue
        photo_txt = _txt(col("photo"))

        pcs_ctn = _num(col("pcs_carton"))
        vol_ctn = _num(col("volume_carton_m3"))
        vol_unit = _num(col("volume_unitaire_m3"))
        if vol_ctn is None:
            vol_ctn = _volume_depuis_dimensions(col("dimensions_carton"))
        if vol_ctn is None and vol_unit is None:
            # « CBM » seul : total de la ligne si une quantité est connue,
            # sinon on ne devine pas et on laisse vide.
            vt = _num(col("volume_total_m3"))
            if vt is not None and qty and qty > 0:
                vol_unit = vt / qty

        out.append({
            "id": f"{feuille}:{i}",
            "feuille": feuille,
            "ligne": i,
            "ref_ligne": _num(col("ref_ligne")),
            "code": code,
            "designation": desig,
            "qty": qty,
            "prix_unitaire": prix,
            "total": _num(col("total")),
            "moq": _num(col("moq")),
            "pcs_carton": pcs_ctn,
            "volume_carton_m3": vol_ctn,
            "volume_unitaire_m3": vol_unit,
            "remarque": _txt(col("remarque")),
            "photo": photo_txt,   # description de la photo (colonne écrite par l'IA)
            # la photo elle-même quand la colonne est une image dans un PDF (octets JPEG,
            # retirés de l'aperçu avant envoi au navigateur)
            "_photo": None if photo_txt else _photo_cellule(cells, mapping.get("photo")),
            "designation_base": desig_base,
            "complement": " — ".join(extras),
        })
    return heriter_fusion(out)


PHOTO_PX = 260            # grand côté de la photo fournisseur découpée dans le PDF


def _photo_cellule(cells, idx):
    """La photo d'une cellule de tableau PDF (rendu du rectangle en JPEG) ; None hors PDF,
    cellule fusionnée, colonne inconnue ou rectangle trop petit pour être une image."""
    page, rects = getattr(cells, "page", None), getattr(cells, "rects", None)
    if idx is None or page is None or not rects or idx >= len(rects) or not rects[idx]:
        return None
    try:
        import pymupdf
        r = pymupdf.Rect(rects[idx]) + (2, 2, -2, -2)   # sans les traits de bordure
        if r.width < 20 or r.height < 20:
            return None
        zoom = min(2.0, PHOTO_PX / max(r.width, r.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r)
        if _uniforme(pix):
            return None  # cellule blanche : pas de photo
        return pix.tobytes("jpeg", jpg_quality=80)
    except Exception:
        return None


def _uniforme(pix, part=0.995):
    """Vrai si une seule teinte couvre (presque) toute l'image : une cellule vide, avec au plus
    un bout de trait de bordure ; un logo plat comme une photo passent."""
    from collections import Counter
    import pymupdf
    gris = pymupdf.Pixmap(pymupdf.csGRAY, pix) if pix.n > 1 else pix
    comptes = Counter(bytes(gris.samples))
    return max(comptes.values()) / max(1, len(gris.samples)) >= part


VARIANTE_MAX = 40         # au-delà, une ligne sans code est un produit à part, pas une variante


def heriter_fusion(lignes):
    """Cellules fusionnées : dans un catalogue, un produit occupe une cellule (code, description,
    photo) et ses variantes de raccord (« 1/2" Brass Port », « 3/4" Brass Port »…) autant de
    sous-lignes avec un prix chacune ; lues ligne à ligne, ces sous-lignes n'ont ni code ni
    description ni photo. Elles héritent de la ligne pleine au-dessus et gardent leur variante,
    pour être reconnaissables par l'IA comme par l'utilisateur. Pur."""
    parent = None
    for l in lignes:
        if l.get("code"):
            l.setdefault("variante", l.get("complement") or "")
            parent = l if l.get("designation_base") else None
            continue
        variante = (l.get("designation") or "").strip()
        if parent and l.get("prix_unitaire") is not None and len(variante) <= VARIANTE_MAX:
            l["code"] = parent["code"]
            l["variante"] = variante
            l["designation"] = " — ".join([parent["designation_base"]] + ([variante] if variante else []))
            l["designation_base"] = parent["designation_base"]
            for k in ("photo", "_photo", "moq", "pcs_carton", "volume_carton_m3", "volume_unitaire_m3"):
                if l.get(k) in (None, "") and parent.get(k) not in (None, ""):
                    l[k] = parent[k]
        else:
            parent = None  # une ligne d'une autre nature ferme le groupe
    return lignes


# ------------------------------------------------------- appariement

def _textes_ligne(r):
    """Tous les libellés sous lesquels une ligne LCI peut être reconnue."""
    return [t for t in (r.item_name, r.item_name_traduit, r.item_code,
                        _strip_html(r.description)[:120]) if _txt(t)]


def _apparier(rows, fournisseur, mode="cascade", photos=None, progres=None):
    """Rend {nom de ligne LCI: (ligne fournisseur, méthode, confiance)}.

    Chaque ligne fournisseur ne sert qu'une fois : un prix apparié deux fois
    est presque toujours une erreur de lecture, pas une intention.
    `photos` : {id fournisseur: octets JPEG} pour le mode « images » ; `progres(etape, fait, total)`
    est appelé au fil de l'identification.
    """
    res = {}
    libres = list(fournisseur)

    def prendre(row, src, methode, conf):
        res[row.name] = (src, methode, conf)
        libres.remove(src)

    restants = list(rows)

    # 1) notre propre numéro de ligne, quand le fournisseur nous a renvoyé
    #    NOTRE fichier. Le numéro seul ne prouve rien : un fournisseur qui
    #    numérote son propre tableau donnerait un alignement par position, donc
    #    des prix collés sur les mauvais articles. On ne suit la numérotation
    #    que si le CONTENU la corrobore sur une nette majorité des paires.
    refs = [f for f in libres if f.get("ref_ligne")]
    if len(refs) >= 3:
        par_ref = {}
        for f in refs:
            par_ref.setdefault(int(f["ref_ligne"]), []).append(f)
        paires = [(row, par_ref[cint(row.idx)][0]) for row in restants
                  if len(par_ref.get(cint(row.idx), [])) == 1]

        testables = confirmees = 0
        for row, f in paires:
            code_frn, code_lci = _norm(f.get("code")), _norm(row.item_code)
            if code_frn and code_lci:
                testables += 1
                confirmees += 1 if code_frn == code_lci else 0
            elif _norm(f.get("designation")):
                cible = _norm(f["designation"])
                best = max((difflib.SequenceMatcher(None, _norm(t), cible).ratio()
                            for t in _textes_ligne(row)), default=0)
                testables += 1
                confirmees += 1 if best >= 0.6 else 0

        if testables >= 3 and confirmees / testables >= 0.6:
            pris_ref = {row.name for row, _f in paires}
            for row, f in paires:
                prendre(row, f, "numéro de ligne", 1.0)
            restants = [r for r in restants if r.name not in pris_ref]

    # 2) code article exact
    par_code = {}
    for f in libres:
        n = _norm(f.get("code"))
        if n:
            par_code.setdefault(n, []).append(f)
    encore = []
    for row in restants:
        n = _norm(row.item_code)
        cand = par_code.get(n) if n else None
        if cand and len(cand) == 1 and cand[0] in libres:
            prendre(row, cand[0], "code article", 1.0)
        else:
            encore.append(row)
    restants = encore

    # 3) désignation identique (au bruit de ponctuation près)
    par_desig = {}
    for f in libres:
        n = _norm(f.get("designation"))
        if n:
            par_desig.setdefault(n, []).append(f)
    encore = []
    for row in restants:
        trouve = None
        for t in _textes_ligne(row):
            cand = par_desig.get(_norm(t))
            if cand and len(cand) == 1 and cand[0] in libres:
                trouve = cand[0]
                break
        if trouve:
            prendre(row, trouve, "désignation exacte", 0.97)
        else:
            encore.append(row)
    restants = encore

    # 4) désignation approchante — refusée si deux candidats se valent
    encore = []
    for row in restants:
        notes = []
        for f in libres:
            cible = _norm(f.get("designation")) or _norm(f.get("code"))
            if not cible:
                continue
            best = max((difflib.SequenceMatcher(None, _norm(t), cible).ratio()
                        for t in _textes_ligne(row)), default=0)
            notes.append((best, f))
        notes.sort(key=lambda x: x[0], reverse=True)
        if notes and notes[0][0] >= FUZZY_MIN and (
                len(notes) == 1 or notes[0][0] - notes[1][0] >= FUZZY_MARGE):
            prendre(row, notes[0][1], "désignation approchante", round(notes[0][0], 2))
        else:
            encore.append(row)
    restants = encore

    # 5) IA sur le reliquat, par lots
    if restants and libres and mode == "images":
        for row, (src_id, conf) in _apparier_images(restants, libres, photos=photos, progres=progres).items():
            src = next((f for f in libres if f["id"] == src_id), None)
            r = next((x for x in restants if x.name == row), None)
            if src and r:
                prendre(r, src, "IA photo", conf)
        return res, libres
    if restants and libres:
        for chunk in _lots(restants, 20):
            if not libres:
                break
            paires = _apparier_ia(chunk, libres)
            for nom_ligne, src_id in paires.items():
                row = next((r for r in chunk if r.name == nom_ligne), None)
                src = next((f for f in libres if f["id"] == src_id), None)
                if row and src:
                    prendre(row, src, "IA", 0.8)
    return res, libres


def _lots(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _apparier_ia(rows, libres):
    """Appariement du reliquat par l'IA. Rend {nom de ligne LCI: id source}."""
    payload = {
        "lignes_a_apparier": [
            {"cle": r.name, "code": r.item_code or "", "designation": r.item_name or "",
             "designation_traduite": r.item_name_traduit or "",
             "quantite": flt(r.qty)}
            for r in rows],
        "lignes_fournisseur": [
            {"id": f["id"], "code": f.get("code") or "",
             "designation": f.get("designation") or "",
             "quantite": f.get("qty"), "prix": f.get("prix_unitaire")}
            for f in libres[:120]],
    }
    system = (
        "Tu rapproches les lignes d'une demande de cotation (matériel de "
        "traitement d'eau : osmoseurs, filtres, raccords, pompes) de la "
        "réponse chiffrée du fournisseur, dont les libellés sont souvent en "
        "anglais approximatif ou en chinois. Pour chaque entrée de "
        "lignes_a_apparier, donne l'id de la ligne fournisseur qui désigne LE "
        "MÊME produit. N'apparie que si tu es sûr : un diamètre, un filetage "
        "ou une capacité différents font deux produits distincts — dans le "
        "doute, réponds null. Chaque id fournisseur ne peut servir qu'une "
        'fois. Réponds en JSON : {"<cle>": "<id ou null>", ...}')
    try:
        out = _chat_json(system, json.dumps(payload, ensure_ascii=False)) or {}
    except Exception as e:
        frappe.log_error(title="LCI réponse — appariement IA", message=str(e)[:2000])
        return {}
    vus = set()
    propre = {}
    for k, v in out.items():
        if isinstance(v, str) and v and v not in vus:
            propre[k] = v
            vus.add(v)
    return propre


# --------------------------------------------------------------- API

def _fichier_local(file_url):
    doc = frappe.get_doc("File", {"file_url": file_url})
    return doc.get_full_path()


def _lire_grilles_pdf(grilles):
    """Un catalogue PDF porte souvent PLUSIEURS tableaux (une catégorie de produits chacun, des
    en-têtes différents) : chaque grille qui a une colonne de prix est lue, et leurs lignes sont
    mises bout à bout. -> (feuille, ligne_entete, entete, mapping, lignes) de la première grille
    lue + toutes les lignes, ou None si aucune grille n'a de prix."""
    premiere, lignes, precedent = None, [], None
    for feuille, grille in grilles:
        f, le, g, entete, mapping = feuille, 0, grille, [], {}
        try:
            f, le, g = _trouver_tableau([(feuille, grille)])
            entete = g[le] if le < len(g) else []
            mapping = _mapper_colonnes(entete, g[le + 1:le + 6])
        except Exception:
            pass
        if "prix_unitaire" not in mapping:
            # un tableau SANS en-tête (la suite du précédent sur la page suivante, un catalogue
            # qui ne répète pas ses titres) : on reprend l'en-tête et les rôles du tableau
            # précédent s'il a la même largeur
            if precedent and grille and len(grille[0]) == len(precedent[0]):
                entete, mapping, le, g = precedent[0], precedent[1], -1, grille
            else:
                continue
        lues = _extraire(g, le, mapping, f)
        if not lues:
            continue
        precedent = (entete, mapping)
        if premiere is None:
            premiere = (f, max(le, 0), entete, mapping)
        lignes.extend(lues)
    if premiere is None:
        return None
    return premiere + (lignes,)


def _lire_un_fichier(file_url):
    """Un fichier fournisseur (Excel ou PDF) -> (feuille, ligne_entete, entete, mapping, lignes).
    Pour un PDF : tous les tableaux à prix du texte ; s'il n'y en a aucun, les pages relues par l'IA."""
    chemin = _fichier_local(file_url)
    nom = file_url.split("/")[-1]
    if chemin.lower().endswith(".pdf"):
        lu = _lire_grilles_pdf(lire_pdf(chemin))
        if lu is None:
            lu = _lire_grilles_pdf(lire_pdf(chemin, ia=True))
        if lu is None:
            frappe.throw(_("Aucune colonne de prix unitaire reconnue dans « {0} », ni dans son texte "
                           "ni par l'IA.").format(nom))
        return lu
    feuille, ligne_entete, grille = _trouver_tableau(_feuilles(chemin))
    entete = grille[ligne_entete] if ligne_entete < len(grille) else []
    mapping = _mapper_colonnes(entete, grille[ligne_entete + 1:ligne_entete + 6])
    if "prix_unitaire" not in mapping:
        frappe.throw(_("Aucune colonne de prix unitaire reconnue dans « {0} » "
                       "(feuille « {1} »). Vérifiez que le fichier contient bien "
                       "la cotation chiffrée.").format(nom, feuille))
    lignes = _extraire(grille, ligne_entete, mapping, feuille)
    if not lignes:
        frappe.throw(_("Aucune ligne de cotation lue dans « {0} » (feuille « {1} »).").format(nom, feuille))
    return feuille, ligne_entete, entete, mapping, lignes


def _urls(file_url, file_urls):
    urls = json.loads(file_urls) if isinstance(file_urls, str) else list(file_urls or [])
    if file_url and file_url not in urls:
        urls.insert(0, file_url)
    if not urls:
        frappe.throw(_("Aucun fichier."))
    return urls


@frappe.whitelist()
def analyser_reponse(docname, file_url=None, file_urls=None, mode="cascade"):
    """Lit le ou les fichiers du fournisseur (Excel, PDF — plusieurs listes de prix à la fois,
    demande utilisateur 25/09/2026) et rend un APERÇU. N'écrit rien. Synchrone : pour le mode
    « images » (des dizaines d'appels IA), passer par `lancer_analyse` + `etat_analyse`."""
    _guard()
    return _analyser(frappe.get_doc(DOCTYPE, docname), _urls(file_url, file_urls), mode or "cascade")


PREFIXE_ANALYSE = "lci_rep:"
DUREE_ETAT = 3600


@frappe.whitelist()
def lancer_analyse(docname, file_urls=None, mode="cascade", file_url=None):
    """Lance l'analyse en tâche de fond et rend sa clé de suivi : l'identification par IA d'une
    liste de 40 lignes dépasse le délai d'une requête web. Le navigateur interroge `etat_analyse`."""
    _guard()
    urls = _urls(file_url, file_urls)
    frappe.get_doc(DOCTYPE, docname)  # existe ?
    cle = "%s%s:%s" % (PREFIXE_ANALYSE, docname, frappe.generate_hash(length=10))
    frappe.cache().set_value(cle, {"etat": "en attente"}, expires_in_sec=DUREE_ETAT)
    frappe.enqueue("customization_app.lci_reponse.job_analyse", queue="long", timeout=1800,
                   docname=docname, urls=urls, mode=mode or "cascade", cle=cle)
    return {"cle": cle}


@frappe.whitelist()
def etat_analyse(cle):
    """{etat: en attente | en cours | termine | echec, etape, fait, total, resultat, erreur}."""
    _guard()
    if not str(cle or "").startswith(PREFIXE_ANALYSE):
        frappe.throw(_("Clé de suivi inconnue."))
    return frappe.cache().get_value(cle) or {"etat": "inconnu"}


def job_analyse(docname, urls, mode, cle):
    """La tâche de fond : même travail que `analyser_reponse`, le résultat (ou l'erreur) déposé
    sous la clé de suivi. Tourne sous l'identité de l'utilisateur qui l'a lancée."""
    _guard()

    def progres(etape, fait, total):
        frappe.cache().set_value(cle, {"etat": "en cours", "etape": etape, "fait": fait, "total": total},
                                 expires_in_sec=DUREE_ETAT)

    try:
        progres("lecture", 0, 0)
        res = _analyser(frappe.get_doc(DOCTYPE, docname), urls, mode, progres=progres)
        frappe.cache().set_value(cle, {"etat": "termine", "resultat": res}, expires_in_sec=DUREE_ETAT)
    except Exception as e:
        frappe.cache().set_value(cle, {"etat": "echec", "erreur": str(e)[:1000]}, expires_in_sec=DUREE_ETAT)
        frappe.log_error(title="LCI réponse — analyse en tâche de fond", message=frappe.get_traceback())


def _cle_id(ident):
    """Tri des identifiants « fichier › feuille:ligne » par fichier puis numéro de ligne (pas L10 avant L3)."""
    tete, _sep, fin = str(ident).rpartition(":")
    return (tete, cint(fin) if fin.isdigit() else 0, str(ident))


def _analyser(doc, urls, mode, progres=None):
    """Lecture des fichiers, appariement, aperçu (dict prêt pour le navigateur)."""
    fournisseur, feuilles, plans = [], [], []
    entete, mapping, ligne_entete, feuille = [], {}, 0, ""
    for url in urls:
        feuille, ligne_entete, entete, mapping, lignes = _lire_un_fichier(url)
        nom = url.split("/")[-1]
        if len(urls) > 1:
            # plusieurs fichiers : l'identifiant de chaque ligne dit de quel fichier elle vient
            for l in lignes:
                l["id"] = "%s › %s" % (nom, l["id"])
                l["feuille"] = "%s › %s" % (nom, l.get("feuille") or "")
        fournisseur.extend(lignes)
        feuilles.append(feuille if len(urls) == 1 else "%s › %s" % (nom, feuille))
        plans.append({"file_url": url, "feuille": feuille, "ligne_entete": ligne_entete, "mapping": mapping, "nb_lignes": len(lignes)})
    file_url = urls[0]
    feuille = feuilles[0] if len(feuilles) == 1 else _("{0} fichiers : {1}").format(len(feuilles), " ; ".join(feuilles))

    # les photos découpées dans le PDF : en octets pour l'IA, en data URI (vignette) pour l'aperçu
    photos = {}
    for l in fournisseur:
        p = l.pop("_photo", None)
        if p:
            photos[l["id"]] = p
            l["photo_b64"] = base64.b64encode(p).decode()

    apparies, libres = _apparier(doc.articles, fournisseur, mode=mode or "cascade", photos=photos, progres=progres)
    orphelines = {f["id"] for f in libres}

    lignes = [{
        "row": r.name,
        "idx": r.idx,
        "item_code": r.item_code,
        "item_name": r.item_name,
        "image": r.image,
        "qty": flt(r.qty),
        "prix_cible": flt(r.prix_cible),
        "prix_actuel": flt(r.prix_fournisseur),
        "source": apparies[r.name][0]["id"] if r.name in apparies else None,
        "methode": apparies[r.name][1] if r.name in apparies else None,
        "confiance": apparies[r.name][2] if r.name in apparies else 0,
    } for r in doc.articles]

    # ce que le fichier additionne, LUI : sa colonne « Amount » quand il en a
    # une, sinon sa quantité × son prix. C'est ce total qui doit se retrouver
    # dans la liste, pas notre quantité multipliée par son prix.
    montant_fichier = 0.0
    for f in fournisseur:
        if f.get("total") is not None:
            montant_fichier += flt(f["total"])
        elif f.get("prix_unitaire") and f.get("qty"):
            montant_fichier += flt(f["prix_unitaire"]) * flt(f["qty"])

    return {
        "feuille": feuille,
        "ligne_entete": ligne_entete + 1,
        "colonnes": {c: _txt(entete[i]) if i < len(entete) else ""
                     for c, i in sorted(mapping.items(), key=lambda kv: kv[1])},
        "devise": doc.devise or "USD",
        "sources": fournisseur,
        "lignes": lignes,
        "orphelines": sorted(orphelines, key=_cle_id),
        "montant_fichier": montant_fichier,
        # plan de lecture : c'est lui qui permettra de RENVOYER ce même fichier
        # annoté (quelle feuille, quelle ligne d'en-tête, quelle colonne porte quoi).
        "plan": {
            "file_url": file_url,
            "feuille": plans[0]["feuille"],
            "ligne_entete": plans[0]["ligne_entete"],
            "mapping": plans[0]["mapping"],
            "montant_fichier": montant_fichier,
            "nb_lignes_fichier": len(fournisseur),
            "fichiers": plans,
        },
        "fichiers": urls,
    }


CHAMPS_APPLICABLES = {"prix_fournisseur", "qty_fournisseur", "moq",
                      "qty_par_carton", "volume_carton_m3",
                      "volume_unitaire_m3", "remarque_fournisseur",
                      "total_fichier"}


@frappe.whitelist()
def appliquer_reponse(docname, lignes, file_url=None, plan=None):
    """Écrit dans la LCI les valeurs validées dans l'aperçu.

    `lignes` : [{"row": <nom de ligne>, "valeurs": {...}, "source_label": "..."}]
    Seuls les champs de CHAMPS_APPLICABLES sont écrits — l'aperçu ne peut pas
    servir à modifier autre chose que la réponse du fournisseur.
    """
    _guard()
    if isinstance(lignes, str):
        lignes = json.loads(lignes)
    if isinstance(plan, str):
        plan = json.loads(plan or "null")
    doc = frappe.get_doc(DOCTYPE, docname)
    par_nom = {r.name: r for r in doc.articles}

    corresp = {}
    maj = 0
    for entree in lignes or []:
        row = par_nom.get(entree.get("row"))
        if not row:
            continue
        valeurs = entree.get("valeurs") or {}
        touche = False
        for champ, val in valeurs.items():
            if champ not in CHAMPS_APPLICABLES or val is None or val == "":
                continue
            setattr(row, champ, val if champ == "remarque_fournisseur" else flt(val))
            touche = True
        if touche:
            row.reponse_source = entree.get("source_label") or ""
            if not row.decision:
                row.decision = "À négocier"
            # « feuille:ligne » -> numéro de ligne Excel, pour savoir plus tard
            # OÙ réécrire nos ajustements dans le fichier du fournisseur.
            src_id = entree.get("source_id") or ""
            if ":" in src_id:
                try:
                    corresp[row.name] = int(src_id.rsplit(":", 1)[1])
                except ValueError:
                    pass
            maj += 1

    if maj:
        doc.date_reponse = nowdate()
        if doc.statut in ("Brouillon", "Envoyée"):
            doc.statut = "Réponse reçue"
        url = (plan or {}).get("file_url") or file_url
        if url:
            doc.fichier_fournisseur = url
            doc.reponse_plan = json.dumps({
                "file_url": url,
                "feuille": (plan or {}).get("feuille"),
                "ligne_entete": (plan or {}).get("ligne_entete"),
                "mapping": (plan or {}).get("mapping") or {},
                "correspondances": corresp,
                "importe_le": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False)
        if (plan or {}).get("montant_fichier"):
            doc.montant_fichier = flt(plan["montant_fichier"])
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {
        "maj": maj,
        "montant_propose": flt(doc.montant_propose),
        "montant_cible": flt(doc.montant_cible),
        "ecart_global_pct": flt(doc.ecart_global_pct),
        "volume_total_m3": flt(doc.volume_total_m3),
        "nb_lignes_cotees": cint(doc.nb_lignes_cotees),
        "montant_fichier": flt(doc.montant_fichier),
        "montant_retenu": flt(doc.montant_retenu),
        "nb_lignes_divergentes": cint(doc.nb_lignes_divergentes),
    }


@frappe.whitelist()
def prix_cible_suggere(docname, row_names=None):
    """Propose un prix cible par ligne, DANS LA DEVISE DE LA LISTE.

    On ne cherche que des références payées dans la même devise que la
    cotation en cours : un prix d'achat en TND n'apprend rien sur ce qu'il
    faut viser en USD chez un fournisseur chinois. Par ordre de préférence :
    la dernière cotation reçue du même fournisseur, puis la dernière de
    n'importe quel fournisseur, puis le dernier achat (facture puis commande)
    libellé dans cette devise.

    Aucune écriture : la proposition revient à l'écran, l'utilisateur tranche.
    """
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    devise = doc.devise or "USD"
    noms = set(json.loads(row_names) if isinstance(row_names, str) else (row_names or []))
    rows = [r for r in doc.articles if r.item_code and (not noms or r.name in noms)]
    if not rows:
        return {"lignes": [], "devise": devise}

    codes = list({r.item_code for r in rows})

    def _index(recs):
        out = {}
        for rec in recs:
            out.setdefault(rec.item_code, rec)
        return out

    # cotations déjà reçues sur d'autres listes, même devise
    lci_frn = _index(frappe.db.sql("""
        SELECT a.item_code, a.prix_fournisseur AS prix, p.name AS ref, p.date_commande AS d
        FROM `tabListe Commande Import Article` a
        JOIN `tabListe Commande Import` p ON p.name = a.parent
        WHERE a.item_code IN %(codes)s AND a.prix_fournisseur > 0
          AND p.name != %(self)s AND IFNULL(p.devise, 'USD') = %(devise)s
          AND IFNULL(p.fournisseur, '') = %(frn)s
        ORDER BY p.date_commande DESC, p.modified DESC
    """, {"codes": codes, "self": doc.name, "devise": devise,
          "frn": doc.fournisseur or ""}, as_dict=True)) if doc.fournisseur else {}

    lci_tous = _index(frappe.db.sql("""
        SELECT a.item_code, a.prix_fournisseur AS prix, p.name AS ref, p.date_commande AS d
        FROM `tabListe Commande Import Article` a
        JOIN `tabListe Commande Import` p ON p.name = a.parent
        WHERE a.item_code IN %(codes)s AND a.prix_fournisseur > 0
          AND p.name != %(self)s AND IFNULL(p.devise, 'USD') = %(devise)s
        ORDER BY p.date_commande DESC, p.modified DESC
    """, {"codes": codes, "self": doc.name, "devise": devise}, as_dict=True))

    achats = {}
    for dt, date_field, label in (("Purchase Invoice", "posting_date", "Facture d'achat"),
                                  ("Purchase Order", "transaction_date", "Commande d'achat")):
        recs = frappe.db.sql("""
            SELECT it.item_code, it.rate AS prix, p.name AS ref, p.{df} AS d
            FROM `tab{dt} Item` it
            JOIN `tab{dt}` p ON p.name = it.parent
            WHERE it.item_code IN %(codes)s AND it.rate > 0
              AND p.docstatus = 1 AND p.currency = %(devise)s
            ORDER BY p.{df} DESC
        """.format(dt=dt, df=date_field),
            {"codes": codes, "devise": devise}, as_dict=True)
        for rec in recs:
            achats.setdefault(rec.item_code, (label, rec))

    out = []
    for r in rows:
        code = r.item_code
        if code in lci_frn:
            rec = lci_frn[code]
            src = _("Cotation {0} ({1})").format(rec.ref, doc.fournisseur)
            prix = flt(rec.prix)
        elif code in lci_tous:
            rec = lci_tous[code]
            src, prix = _("Cotation {0}").format(rec.ref), flt(rec.prix)
        elif code in achats:
            label, rec = achats[code]
            src, prix = f"{label} {rec.ref}", flt(rec.prix)
        else:
            continue
        if prix > 0:
            out.append({"row": r.name, "item_code": code, "prix": prix, "source": src})
    return {"lignes": out, "devise": devise}
