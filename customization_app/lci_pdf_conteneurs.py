"""PDF du plan de chargement, conteneur par conteneur, pour le fournisseur (14/10/2026).

Demande : « à partir de la répartition en conteneurs, créer des PDF avec la somme de chaque conteneur, les
photos du fournisseur, sa description, les quantités et le volume dans chaque conteneur » — prix unitaire,
montant, date de départ et remplissage en plus, « clair pour lui ». Dans la langue de la liste.

Deux formes : un conteneur (`no`), ou tous (une page de récapitulatif puis un conteneur par page).
Lit le plan ENREGISTRÉ (`plan_enregistre`) : jamais de recalcul, le PDF montre ce qui a été validé.

Pour qu'il s'y retrouve, chaque ligne parle SA langue : sa photo (tirée de son propre fichier, la nôtre à
défaut), sa ligne (« Your line 11 »), sa description en puces (remise en forme par l'IA), et une ligne scindée dit où
est le reste (« 100 of 150 — rest in C1 (50) »). Pas d'« additionnels » : sa description, acceptée, suffit (14/10).
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import flt, formatdate, nowdate

from customization_app.lci_comparaison import texte_fournisseur
from customization_app.lci_conteneurs import plan_enregistre
from customization_app.lci_observation import prix_retenu, qty_retenue
from customization_app.liste_commande_import import (
    DOCTYPE,
    RTL_LANGS,
    _esc,
    _guard,
    _img_data_uri,
    _strip_html,
    _uom_out,
)
from customization_app.liste_commande_import import _labels as _labels_export

LABELS = {
    "English": {"paiements": "Payment plan", "libelle": "Description", "commande_entiere": "Whole order", "total_planifie": "Total scheduled", "reste_planifier": "Remaining to schedule", "paiements_conteneur": "Payments for this container", "legende_type": "TT = telegraphic transfer (bank transfer) · LC = letter of credit", "incomplet": "Not fully loaded (loaded / ordered)", "titre": "Loading plan", "conteneur": "Container", "type": "Type", "depart": "Departure",
                "a_confirmer": "to be confirmed", "capacite": "Usable capacity", "charge": "Loaded volume",
                "remplissage": "Fill", "lignes": "Lines", "photo": "Photo", "ref": "Your line",
                "description": "Description", "qty": "Qty", "volume": "Volume (m³)", "pu": "Unit price",
                "montant": "Amount", "total": "Container total", "recap": "Summary",
                "total_general": "Grand total", "scinde": "{q} of {t} — rest in {autres}",
                "additionnel": "Additional", "non_charge": "Not loaded (no unit volume)", "ref_doc": "Ref.",
                "date": "Date", "fournisseur": "Supplier", "conteneurs": "Containers"},
    "Français": {"paiements": "Plan de paiement", "libelle": "Libellé", "commande_entiere": "Toute la commande", "total_planifie": "Total planifié", "reste_planifier": "Reste à planifier", "paiements_conteneur": "Paiements de ce conteneur", "legende_type": "TT = virement bancaire · LC = lettre de crédit", "incomplet": "Pas entièrement chargées (chargé / commandé)", "titre": "Plan de chargement", "conteneur": "Conteneur", "type": "Gabarit", "depart": "Départ",
                 "a_confirmer": "à confirmer", "capacite": "Capacité utile", "charge": "Volume chargé",
                 "remplissage": "Remplissage", "lignes": "Lignes", "photo": "Photo", "ref": "Votre ligne",
                 "description": "Description", "qty": "Qté", "volume": "Volume (m³)", "pu": "Prix unitaire",
                 "montant": "Montant", "total": "Total du conteneur", "recap": "Récapitulatif",
                 "total_general": "Total général", "scinde": "{q} sur {t} — reste dans {autres}",
                 "additionnel": "Additionnel", "non_charge": "Non chargées (sans volume unitaire)",
                 "ref_doc": "Réf.", "date": "Date", "fournisseur": "Fournisseur", "conteneurs": "Conteneurs"},
    "Deutsch": {"paiements": "Zahlungsplan", "libelle": "Bezeichnung", "commande_entiere": "Gesamter Auftrag", "total_planifie": "Geplant gesamt", "reste_planifier": "Noch einzuplanen", "paiements_conteneur": "Zahlungen für diesen Container", "legende_type": "TT = Banküberweisung · LC = Akkreditiv", "incomplet": "Nicht vollständig verladen (verladen / bestellt)", "titre": "Ladeplan", "conteneur": "Container", "type": "Typ", "depart": "Abfahrt",
                "a_confirmer": "noch offen", "capacite": "Nutzbares Volumen", "charge": "Geladenes Volumen",
                "remplissage": "Auslastung", "lignes": "Positionen", "photo": "Foto", "ref": "Ihre Zeile",
                "description": "Beschreibung", "qty": "Menge", "volume": "Volumen (m³)", "pu": "Stückpreis",
                "montant": "Betrag", "total": "Summe Container", "recap": "Übersicht",
                "total_general": "Gesamtsumme", "scinde": "{q} von {t} — Rest in {autres}",
                "additionnel": "Zusätzlich", "non_charge": "Nicht verladen (ohne Stückvolumen)", "ref_doc": "Ref.",
                "date": "Datum", "fournisseur": "Lieferant", "conteneurs": "Container"},
    "Español": {"paiements": "Plan de pagos", "libelle": "Concepto", "commande_entiere": "Pedido completo", "total_planifie": "Total programado", "reste_planifier": "Pendiente de programar", "paiements_conteneur": "Pagos de este contenedor", "legende_type": "TT = transferencia bancaria · LC = carta de crédito", "incomplet": "No cargadas por completo (cargado / pedido)", "titre": "Plan de carga", "conteneur": "Contenedor", "type": "Tipo", "depart": "Salida",
                "a_confirmer": "por confirmar", "capacite": "Capacidad útil", "charge": "Volumen cargado",
                "remplissage": "Llenado", "lignes": "Líneas", "photo": "Foto", "ref": "Su línea",
                "description": "Descripción", "qty": "Cant.", "volume": "Volumen (m³)", "pu": "Precio unitario",
                "montant": "Importe", "total": "Total del contenedor", "recap": "Resumen",
                "total_general": "Total general", "scinde": "{q} de {t} — resto en {autres}",
                "additionnel": "Adicional", "non_charge": "No cargadas (sin volumen unitario)", "ref_doc": "Ref.",
                "date": "Fecha", "fournisseur": "Proveedor", "conteneurs": "Contenedores"},
    "العربية": {"paiements": "خطة الدفع", "libelle": "البيان", "commande_entiere": "الطلبية كاملة", "total_planifie": "إجمالي المبرمج", "reste_planifier": "المتبقي للبرمجة", "paiements_conteneur": "دفعات هذه الحاوية", "legende_type": "TT = تحويل بنكي · LC = اعتماد مستندي", "incomplet": "غير محمّلة بالكامل (المحمّل / المطلوب)", "titre": "خطة الشحن", "conteneur": "حاوية", "type": "النوع", "depart": "المغادرة",
                "a_confirmer": "سيتم التأكيد", "capacite": "السعة المفيدة", "charge": "الحجم المحمّل",
                "remplissage": "نسبة الملء", "lignes": "البنود", "photo": "صورة", "ref": "سطركم",
                "description": "الوصف", "qty": "الكمية", "volume": "الحجم (م³)", "pu": "سعر الوحدة",
                "montant": "المبلغ", "total": "مجموع الحاوية", "recap": "ملخص", "total_general": "المجموع العام",
                "scinde": "{q} من {t} — الباقي في {autres}", "additionnel": "إضافي",
                "non_charge": "غير محمّلة (بدون حجم للوحدة)", "ref_doc": "المرجع", "date": "التاريخ",
                "fournisseur": "المورّد", "conteneurs": "الحاويات"},
}


MARQUAGE = {
    "English": {"marquage": "Shipping marks", "marque_principale": "Main mark (every package)", "accessoires": "Accessories — mark each accessory carton",
                "accessoire": "Accessory", "pour": "for", "pcs": "pcs", "marque": "Mark",
                "note": "Accessories packed separately must carry the mark below; if packed inside the unit carton, add “WITH ACCESSORIES” to the main mark."},
    "Français": {"marquage": "Marquage d'expédition", "marque_principale": "Marque principale (chaque colis)", "accessoires": "Accessoires — marquer chaque carton d'accessoires",
                 "accessoire": "Accessoire", "pour": "pour", "pcs": "pcs", "marque": "Marque",
                 "note": "Les accessoires emballés à part portent la marque ci-dessous ; s'ils sont dans le carton de l'unité, ajouter « AVEC ACCESSOIRES » à la marque principale."},
    "Deutsch": {"marquage": "Versandmarkierung", "marque_principale": "Hauptmarkierung (jedes Packstück)", "accessoires": "Zubehör — jeden Zubehörkarton markieren",
                "accessoire": "Zubehör", "pour": "für", "pcs": "Stk.", "marque": "Markierung",
                "note": "Separat verpacktes Zubehör trägt die folgende Markierung; im Gerätekarton verpackt: „MIT ZUBEHÖR“ zur Hauptmarkierung hinzufügen."},
    "Español": {"marquage": "Marcas de envío", "marque_principale": "Marca principal (cada bulto)", "accessoires": "Accesorios — marcar cada caja de accesorios",
                "accessoire": "Accesorio", "pour": "para", "pcs": "uds.", "marque": "Marca",
                "note": "Los accesorios embalados aparte llevan la marca siguiente; si van dentro de la caja de la unidad, añadir «CON ACCESORIOS» a la marca principal."},
    "العربية": {"marquage": "علامات الشحن", "marque_principale": "العلامة الرئيسية (كل طرد)", "accessoires": "الملحقات — وضع العلامة على كل كرتونة ملحقات",
                "accessoire": "ملحق", "pour": "لـ", "pcs": "قطعة", "marque": "العلامة",
                "note": "الملحقات المعبأة على حدة تحمل العلامة أدناه؛ وإذا كانت داخل كرتونة الجهاز تُضاف عبارة «مع الملحقات» إلى العلامة الرئيسية."},
}
MARQUE_DEFAUT = "AQUAWORLD · {ref} · {conteneur}/{total}"


for _langue, _l in LABELS.items():                      # les libellés du marquage rejoignent ceux de chaque langue (même objet)
    _l.update(MARQUAGE.get(_langue) or MARQUAGE["English"])


def labels(langue):
    return LABELS.get(langue or "English") or LABELS["English"]


def marque_principale(modele, ref, no, total) -> str:
    """La marque des colis d'un conteneur : le modèle du document (balises {ref} {conteneur} {total}), sinon le défaut. PURE."""
    m = (modele or "").strip() or MARQUE_DEFAUT
    return m.replace("{ref}", str(ref or "")).replace("{conteneur}", "C%d" % int(no)).replace("{total}", str(int(total or 1)))


def marques_articles(doc) -> dict:
    """{clé: marque} — clé = nom de ligne (unité) ou « acc:<code> » (accessoire). Les marques mémorisées (lci_marquage),
    complétées par NOTRE CODE ARTICLE pour toute ligne qui n'en a pas (défaut du 16/10/2026 : « AQUAWORLD · REF code »)."""
    from customization_app.lci_marquage import marques_enregistrees
    out = {k: v for k, v in marques_enregistrees(doc).items() if v}
    for r in doc.get("articles") or []:
        if r.item_code and not out.get(r.name):
            out[r.name] = r.item_code
    return out


def prefixe_marque(modele) -> str:
    """Le modèle de marque SANS référence ni conteneur : « AQUAWORLD · {ref} · {conteneur}/{total} » → « AQUAWORLD ». C'est ce
    qui précède la marque d'un article ou d'un accessoire (demande du 16/10 : « enlève LCI-2026-00295 · C1/4 »). PURE."""
    m = (modele or "").strip() or MARQUE_DEFAUT
    m = re.sub(r"\{ref\}|\{conteneur\}|\{total\}", "", m)
    m = re.sub(r"[\s·/\-]{2,}", " · ", m)
    return m.strip(" ·/-") or "AQUAWORLD"


def accessoires_conteneur(c, par_row, adds_de, marques=None) -> list:
    """Les accessoires embarqués dans un conteneur, cumulés par article : quantité = unités chargées × quantité par pack,
    avec les unités qu'ils accompagnent. `adds_de(row)` → la liste JSON des additionnels d'une ligne. PURE."""
    out = {}
    marques = marques or {}
    for l in c.get("lignes") or []:
        r = par_row.get(l.get("row"))
        if not r:
            continue
        for a in adds_de(r) or []:
            code = a.get("item_code") or a.get("item_name") or "?"
            e = out.setdefault(code, {"item_code": code, "nom": a.get("item_name_traduit") or a.get("item_name") or code,
                                      "brand": a.get("brand") or "", "uom": a.get("uom") or "", "qty": 0.0, "pour": [],
                                      "marque": marques.get("acc:" + code) or ""})
            e["qty"] += flt(l.get("qty")) * (flt(a.get("qty_par_pack")) or 1)
            unite = marques.get(r.name) or r.item_code or r.item_name or ""   # l'unité par sa marque lisible si elle en a une
            if unite and unite not in e["pour"]:
                e["pour"].append(unite)
    return sorted(out.values(), key=lambda e: e["item_code"])


def bloc_marquage(doc, c, L, par_row, nb_conteneurs, adds_de, LX=None, note=None) -> str:
    """Le bloc « Shipping marks » d'un conteneur : la marque principale, puis une ligne par accessoire avec sa marque,
    et la remarque libre saisie au moment du téléchargement (`note`)."""
    marque = marque_principale(doc.get("marquage_expedition"), doc.name, c["no"], nb_conteneurs)
    html = (f'<h2 style="font-size:13px;margin:12px 0 4px">{L["marquage"]} — C{c["no"]}</h2>'
            f'<div style="margin:0 0 6px"><span style="color:#6b7280">{L["marque_principale"]} :</span> '
            f'<b style="font-size:12px;letter-spacing:.5px">{_esc(marque)}</b></div>')
    acc = accessoires_conteneur(c, par_row, adds_de, marques_articles(doc))
    if acc:
        html += (f'<div style="font-size:10px;margin:2px 0 4px"><b>{L["accessoires"]}</b></div>'
                 f'<table class="l"><thead><tr><th style="width:70px">{L["accessoire"]}</th><th>{L["description"]}</th>'
                 f'<th class="n" style="width:60px">{L["qty"]}</th><th>{L["pour"]}</th><th>{L["marque"]}</th></tr></thead><tbody>')
        for e in acc:
            html += (f'<tr><td><b>{_esc(e["item_code"])}</b></td><td>{_esc(e["nom"])}{(" — " + _esc(e["brand"])) if e["brand"] else ""}</td>'
                     f'<td class="n"><b>{_n(e["qty"])}</b> {_esc(_uom_out(e["uom"], LX) if LX else e["uom"])}</td><td>{_esc(", ".join(e["pour"]))}</td>'
                     f'<td><b>{_esc(prefixe_marque(doc.get("marquage_expedition")))} · ACC · {_esc(e.get("marque") or e["item_code"])} · {_n(e["qty"])} {L["pcs"]}</b></td></tr>')
        html += f'</tbody></table><div style="font-size:9px;color:#6b7280;margin-top:3px">{L["note"]}</div>'
    if (note or "").strip():
        html += f'<div style="font-size:10px;margin-top:4px;padding:4px 6px;border:1px solid #d1d5db;border-radius:3px">{_esc(note.strip())}</div>'
    return html


# ----------------------------------------------------------------- fonctions pures

def ref_ligne(reponse_source):
    """« L11 · Reverse Osmosis… » → 11 (le numéro de SA ligne), sinon None. PURE."""
    m = re.match(r"\s*L(\d+)\b", str(reponse_source or ""))
    return int(m.group(1)) if m else None


def parts_de(repartition):
    """Le JSON `repartition_conteneurs` d'une ligne → [{"no", "qty"}]. PURE."""
    try:
        parts = json.loads(repartition or "[]") or []
    except Exception:
        return []
    return [{"no": int(p.get("no") or 0), "qty": flt(p.get("qty"))} for p in parts if p.get("no")]


def texte_scinde(parts, no, total, L):
    """Pour une ligne répartie sur plusieurs conteneurs, vue depuis le conteneur `no` :
    « 100 of 150 — rest in C1 (50) ». None si la ligne n'est pas scindée. PURE."""
    if len(parts) < 2:
        return None
    ici = sum(p["qty"] for p in parts if p["no"] == no)
    autres = ", ".join("C%d (%s)" % (p["no"], _n(p["qty"])) for p in sorted(parts, key=lambda p: p["no"]) if p["no"] != no)
    return L["scinde"].format(q=_n(ici), t=_n(total), autres=autres)


def description_pour_lui(row):
    """Ce qui le décrit le mieux À SES YEUX : ses puces, sinon son texte, sinon notre description traduite. PURE
    (row = dict ou Document)."""
    puces = (row.get("description_fournisseur_puces") or "").strip()
    if puces:
        return puces
    sien = texte_fournisseur(row.get("description_fournisseur"))
    if sien:
        return sien
    nom = row.get("item_name_traduit") or row.get("item_name") or row.get("item_code") or ""
    desc = row.get("description_traduite") or _strip_html(row.get("description") or "")
    return (nom + ("\n" + desc if desc else "")).strip()


def description_html(texte):
    """Sa description en PUCES, une puce par ligne (demandes du 14/10/2026 : « just the fournisseur description …
    in bullet point », « one line per bullet point ») : la première en titre (le produit), les autres en liste.
    Un texte sans puces reste tel quel. PURE."""
    from customization_app.lci_comparaison import puces_de

    t = str(texte or "").strip()
    if not t:
        return ""
    esc = frappe.utils.escape_html
    if "•" not in t:
        return esc(t).replace("\n", "<br>")
    puces = puces_de(t)
    tete, reste = puces[0], puces[1:]
    liste = ('<ul class="pu">' + "".join(f"<li>{esc(p)}</li>" for p in reste) + "</ul>") if reste else ""
    return f'<div class="pu-t">{esc(tete)}</div>{liste}'


def _n(v):
    v = flt(v)
    return ("%d" % v) if abs(v - round(v)) < 1e-9 else ("%g" % v)


def remplissage(c):
    cap = flt(c.get("capacite"))
    return flt(c.get("volume")) / cap * 100 if cap else 0.0


# ----------------------------------------------------------------- ses photos

def _data_uri(octets, cible=(260, 260)):
    from customization_app.lci_export_fournisseur import _vignette

    res = _vignette(octets, cible)
    if not res:
        return None
    buf = res[0].getvalue()
    mime = "image/png" if buf[:4] == b"\x89PNG" else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(buf).decode())


def _photos_pdf(chemin, plan, corresp):
    """Son PDF relu (tableaux du texte, sans IA) avec le plan de lecture enregistré : la cellule « photo » de
    chacune de SES lignes appariées, découpée comme à l'import."""
    from customization_app.lci_reponse import _photo_cellule, lire_pdf

    idx = (plan.get("mapping") or {}).get("photo")
    if idx is None:
        return {}
    grilles = dict(lire_pdf(chemin, ia=False))
    grille = grilles.get(plan.get("feuille")) or (next(iter(grilles.values())) if len(grilles) == 1 else None)
    if not grille:
        return {}
    out = {}
    for nom, i in corresp.items():
        if 1 <= i <= len(grille):
            octets = _photo_cellule(grille[i - 1], idx)
            uri = _data_uri(octets) if octets else None
            if uri:
                out[nom] = uri
    return out


def _photos_classeur(chemin, plan, corresp):
    """Son classeur (.xls/.xlsx) : les images de sa colonne photo, à la ligne de chacune de SES lignes appariées."""
    from customization_app.lci_export_fournisseur import _charger, colonne_frequente

    wb, _converti = _charger(chemin, valeurs=True)
    ws = wb[plan.get("feuille")] if plan.get("feuille") in wb.sheetnames else wb.active
    images = {}
    for img in getattr(ws, "_images", []) or []:
        images.setdefault(int(img.anchor._from.row) + 1, []).append(img)
    col = colonne_frequente([int(i.anchor._from.col) + 1 for xl in set(corresp.values()) for i in images.get(xl, [])])
    out = {}
    for nom, xl in corresp.items():
        siennes = [i for i in images.get(xl, []) if col is None or int(i.anchor._from.col) + 1 == col]
        uri = _data_uri(siennes[0]._data()) if siennes else None
        if uri:
            out[nom] = uri
    return out


def photos_fournisseur(doc):
    """{nom de ligne: data URI} — SES photos, tirées de son fichier (PDF ou classeur). Vide si la liste n'a pas
    de fichier importé ou s'il n'en contient pas : on prendra les nôtres."""
    if not doc.fichier_fournisseur or not doc.reponse_plan:
        return {}
    try:
        from customization_app.lci_export_fournisseur import _fichier_local

        plan = json.loads(doc.reponse_plan)
        corresp = {k: int(v) for k, v in (plan.get("correspondances") or {}).items()}
        chemin = _fichier_local(doc.fichier_fournisseur)
        if chemin.lower().endswith(".pdf"):
            return _photos_pdf(chemin, plan, corresp)
        return _photos_classeur(chemin, plan, corresp)
    except Exception:
        frappe.log_error(title="LCI — PDF conteneurs : photos du fournisseur", message=frappe.get_traceback())
        return {}


# ----------------------------------------------------------------- rendu

CSS = """
  body { font-family: Helvetica, Arial, sans-serif; font-size: 10px; color: #222; }
  h1 { font-size: 17px; margin: 0 0 2px; }
  .meta { color: #555; margin-bottom: 10px; }
  .ct { border: 2px solid #722ed1; border-radius: 6px; padding: 8px 10px; margin: 6px 0 10px; background: #f9f0ff; }
  .ct-titre { font-size: 15px; font-weight: bold; color: #391085; }
  .ct-grille { width: 100%; margin-top: 6px; border-collapse: collapse; }
  .ct-grille td { border: none; padding: 2px 8px 2px 0; font-size: 11px; }
  .ct-grille b { font-size: 12.5px; }
  .ko { color: #a8071a; }
  table.l { width: 100%; border-collapse: collapse; }
  table.l th { background: #f0f2f5; text-align: left; padding: 4px 5px; font-size: 8px; text-transform: uppercase;
               border: 1px solid #d5dae1; }
  table.l td { padding: 3px 5px; border: 1px solid #d5dae1; vertical-align: top; font-size: 8px; line-height: 1.22; }
  table.l td.n { font-size: 9.5px; }
  table.l td.ph { vertical-align: middle; text-align: center; padding: 2px; }
  table.l tr { page-break-inside: avoid; }
  .n { text-align: right; white-space: nowrap; }
  .ref { font-size: 8.5px; color: #0958d9; font-weight: bold; }
  .split { white-space: nowrap; font-size: 8px; color: #391085; background: #f9f0ff; border-radius: 3px; padding: 1px 4px; display: inline-block;
           margin-top: 3px; }
  .pu-t { font-weight: bold; font-size: 8.5px; }
  ul.pu { margin: 1px 0 0; padding-left: 10px; }
  ul.pu li { margin: 0; padding: 0; }
  .tot td { font-weight: bold; background: #f9f0ff; font-size: 12px; }
  .saut { page-break-before: always; }
"""


def _entete_doc(doc, L, fournisseur):
    return (f'<div class="meta">{L["ref_doc"]} : {_esc(doc.name)} &nbsp;·&nbsp; {L["date"]} : '
            f'{_esc(formatdate(nowdate()))}'
            + (f' &nbsp;·&nbsp; {L["fournisseur"]} : <b>{_esc(fournisseur)}</b>' if fournisseur else "")
            + "</div>")


def _bloc_conteneur(doc, c, L, LX, par_row, photos, devise, nb_conteneurs, echeances=(), marquage=True, note=None):
    pct = remplissage(c)
    ko = flt(c["volume"]) > flt(c["capacite"]) + 1e-4
    depart = _esc(formatdate(c["date"])) if c.get("date") else f'<i>{L["a_confirmer"]}</i>'
    html = f"""
    <div class="ct">
      <div class="ct-titre">{L["conteneur"]} C{c["no"]} / {nb_conteneurs} — {_esc(c.get("type") or "")}</div>
      <table class="ct-grille"><tr>
        <td>{L["depart"]}<br><b>{depart}</b></td>
        <td>{L["capacite"]}<br><b>{flt(c["capacite"]):.2f} m³</b></td>
        <td>{L["charge"]}<br><b class="{'ko' if ko else ''}">{flt(c["volume"]):.3f} m³</b></td>
        <td>{L["remplissage"]}<br><b class="{'ko' if ko else ''}">{pct:.0f} %</b></td>
        <td>{L["lignes"]}<br><b>{len(c["lignes"])}</b></td>
        <td style="text-align:right">{L["total"]}<br><b>{_esc(frappe.utils.fmt_money(flt(c["montant"]), currency=devise))}</b></td>
      </tr></table>
    </div>
    {bloc_paiements([e for e in echeances if int(e.get("conteneur") or 0) == int(c["no"])], L, devise,
                    L["paiements_conteneur"], avec_conteneur=False, bases={int(c["no"]): flt(c["montant"])})}"""
    # Colonne « Mark » (16/10/2026 : « is it better to add column for mark » — oui : l'emballeur la lit d'un coup d'œil),
    # seulement si au moins un article du conteneur a sa marque ; le préfixe = la marque principale sans référence ni conteneur.
    marques = marques_articles(doc)
    col_marque = bool(marquage and any(marques.get(l.get("row")) for l in c["lignes"]))
    prefixe = prefixe_marque(doc.get("marquage_expedition"))
    html += f"""
    <table class="l" style="margin-top:8px"><thead><tr>
      <th style="width:16px">#</th><th style="width:66px">{L["photo"]}</th><th>{L["description"]}</th>
      {f'<th style="width:118px">{L["marque"]}</th>' if col_marque else ""}
      <th class="n" style="width:50px">{L["qty"]}</th><th class="n" style="width:50px">{L["volume"]}</th>
      <th class="n" style="width:56px">{L["pu"]} ({devise})</th><th class="n" style="width:66px">{L["montant"]} ({devise})</th>
    </tr></thead><tbody>"""
    for i, l in enumerate(c["lignes"], 1):
        r = par_row.get(l["row"])
        if not r:
            continue
        photo = photos.get(r.name) or _img_data_uri(r.image)
        scinde = texte_scinde(parts_de(r.repartition_conteneurs), c["no"], qty_retenue(r), L)
        desc = description_html(description_pour_lui(r))
        m_art = marques.get(r.name) if marquage else None
        html += f"""
      <tr>
        <td>{i}</td>
        <td class="ph">{f'<img src="{photo}" style="max-width:60px;max-height:60px">' if photo else ""}</td>
        <td>{desc}
          {f'<div class="split">{_esc(scinde)}</div>' if scinde else ""}</td>
        {(f'<td><b>{_esc(prefixe)} · {_esc(m_art)}</b></td>' if m_art else "<td></td>") if col_marque else ""}
        <td class="n"><b>{_n(l["qty"])}</b> {_esc(_uom_out(r.uom, LX))}</td>
        <td class="n">{flt(l["volume"]):.3f}</td>
        <td class="n">{prix_retenu(r):,.2f}</td>
        <td class="n"><b>{flt(l["montant"]):,.2f}</b></td>
      </tr>"""
    html += f"""
      <tr class="tot"><td colspan="{4 if col_marque else 3}">{L["total"]} C{c["no"]}</td>
        <td></td><td class="n">{flt(c["volume"]):.3f}</td><td></td>
        <td class="n">{flt(c["montant"]):,.2f}</td></tr>
    </tbody></table>"""
    # Marquage d'expédition (16/10/2026 : le fournisseur demande le shipping mark des accessoires pour la LC)
    from customization_app.customize_erpnext.doctype.liste_commande_import.liste_commande_import import _adds
    if marquage:
        html += bloc_marquage(doc, c, L, par_row, nb_conteneurs, _adds, LX, note=note)
    return html


def bloc_paiements(echeances, L, devise, titre, avec_conteneur=True, base=None, bases=None):
    """Le tableau des échéances (déjà triées) : libellé, conteneur, type, date, %, montant ; total, et le reste à
    planifier quand `base` (montant de la commande) est donné. "" sans échéance. PURE (hors formatdate).

    `bases` : {n° de conteneur (0 = commande): montant} — le % d'un montant saisi (ou du reste) s'en déduit, pour
    qu'il se lise aussi (« 20 000 » sur 62 770,95 → 31,9 %)."""
    if not echeances:
        return ""
    lignes = ""
    for e in echeances:
        no = int(e.get("conteneur") or 0)
        pct = flt(e.get("pourcentage"))
        if not pct and bases and flt(bases.get(no)) and flt(e.get("montant")):
            pct = round(100 * flt(e.get("montant")) / flt(bases.get(no)), 1)
        lignes += (f'<tr><td>{_esc(e.get("libelle") or "")}</td>'
                   + (f'<td>{("C%d" % no) if no else L["commande_entiere"]}</td>' if avec_conteneur else "")
                   + f'<td><b>{_esc(e.get("type_paiement") or "")}</b></td>'
                   f'<td>{_esc(formatdate(e.get("date_execution"))) if e.get("date_execution") else "<i>" + L["a_confirmer"] + "</i>"}</td>'
                   f'<td class="n">{(_n(pct) + " %") if pct else ""}</td>'
                   f'<td class="n"><b>{flt(e.get("montant")):,.2f}</b></td></tr>')
    total = sum(flt(e.get("montant")) for e in echeances)
    reste = (flt(base) - total) if base is not None else 0
    pied = (f'<tr class="tot"><td colspan="{5 if avec_conteneur else 4}">{L["total_planifie"]}</td>'
            f'<td class="n">{total:,.2f}</td></tr>')
    if base is not None and abs(reste) > 0.005:
        pied += (f'<tr><td colspan="{5 if avec_conteneur else 4}" class="ko">{L["reste_planifier"]}</td>'
                 f'<td class="n ko"><b>{reste:,.2f}</b></td></tr>')
    return (f'<h2 style="font-size:13px;margin:12px 0 4px">{titre}</h2>'
            f'<table class="l"><thead><tr><th>{L["libelle"]}</th>'
            + (f'<th style="width:110px">{L["conteneur"]}</th>' if avec_conteneur else "")
            + f'<th style="width:50px">{L["type"]}</th><th style="width:90px">{L["date"]}</th>'
            f'<th class="n" style="width:50px">%</th><th class="n" style="width:110px">{L["montant"]} ({devise})</th>'
            f'</tr></thead><tbody>{lignes}{pied}</tbody></table>'
            f'<div style="font-size:9px;color:#6b7280;margin-top:3px">{L["legende_type"]}</div>')


def _echeances(doc):
    from customization_app.lci_paiement import tri

    return tri([e.as_dict() if hasattr(e, "as_dict") else dict(e) for e in (doc.get("plan_paiement") or [])])


def _recap(doc, plan, L, devise, hors_plan):
    lignes = ""
    for c in plan["conteneurs"]:
        ko = flt(c["volume"]) > flt(c["capacite"]) + 1e-4
        lignes += f"""<tr><td><b>C{c["no"]}</b></td><td>{_esc(c.get("type") or "")}</td>
          <td>{_esc(formatdate(c["date"])) if c.get("date") else f'<i>{L["a_confirmer"]}</i>'}</td>
          <td class="n">{len(c["lignes"])}</td>
          <td class="n {'ko' if ko else ''}">{flt(c["volume"]):.3f} / {flt(c["capacite"]):.2f}</td>
          <td class="n {'ko' if ko else ''}">{remplissage(c):.0f} %</td>
          <td class="n"><b>{flt(c["montant"]):,.2f}</b></td></tr>"""
    vol = sum(flt(c["volume"]) for c in plan["conteneurs"])
    mnt = sum(flt(c["montant"]) for c in plan["conteneurs"])
    html = f"""
    <h2 style="font-size:14px;margin:8px 0 6px">{L["recap"]} — {len(plan["conteneurs"])} {L["conteneurs"].lower()}</h2>
    <table class="l"><thead><tr><th>{L["conteneur"]}</th><th>{L["type"]}</th><th>{L["depart"]}</th>
      <th class="n">{L["lignes"]}</th><th class="n">{L["volume"]}</th><th class="n">{L["remplissage"]}</th>
      <th class="n">{L["montant"]} ({devise})</th></tr></thead>
      <tbody>{lignes}
      <tr class="tot"><td colspan="4">{L["total_general"]}</td><td class="n">{vol:.3f}</td><td></td>
        <td class="n">{mnt:,.2f}</td></tr></tbody></table>"""
    bases = {int(c["no"]): flt(c["montant"]) for c in plan["conteneurs"]}
    bases[0] = flt(doc.montant_retenu)
    html += bloc_paiements(_echeances(doc), L, devise, L["paiements"], base=round(mnt, 2), bases=bases)
    if hors_plan:
        html += (f'<p style="margin-top:12px;color:#ad6800"><b>{L["non_charge"]}</b> : '
                 + " · ".join(_esc(r.item_code or r.item_name or "") + f" ({_n(qty_retenue(r))})" for r in hors_plan)
                 + "</p>")
    return html


def construire_html(doc, no=None, marquage=True, note=None):
    """Le HTML du PDF : un conteneur (`no`) ou tous (récapitulatif d'abord). `marquage` : avec le bloc shipping marks ;
    `note` : remarque libre imprimée sous ce bloc (saisie dans la fenêtre de téléchargement)."""
    plan = plan_enregistre(doc)
    if not plan.get("conteneurs"):
        frappe.throw(_("Aucun plan de chargement enregistré : « 🚢 Répartir en conteneurs » puis « Enregistrer »."))
    conteneurs = plan["conteneurs"]
    if no:
        conteneurs = [c for c in conteneurs if int(c["no"]) == int(no)]
        if not conteneurs:
            frappe.throw(_("Le conteneur C{0} n'existe pas dans le plan enregistré.").format(no))
    L, LX = labels(doc.langue_cible), _labels_export(doc)
    devise = doc.devise or "USD"
    fournisseur = (frappe.db.get_value("Supplier", doc.fournisseur, "supplier_name") or doc.fournisseur) if doc.fournisseur else ""
    par_row = {r.name: r for r in doc.articles}
    photos = photos_fournisseur(doc)
    echeances = _echeances(doc)
    dir_attr = ' dir="rtl"' if doc.langue_cible in RTL_LANGS else ""

    corps = f'<h1>{L["titre"]} — {_esc(doc.titre or doc.name)}</h1>' + _entete_doc(doc, L, fournisseur)
    if not no:
        hors_plan = [r for r in doc.articles if (r.decision or "") != "Abandonné" and qty_retenue(r) > 0
                     and not parts_de(r.repartition_conteneurs)]
        corps += _recap(doc, plan, L, devise, hors_plan)
    for k, c in enumerate(conteneurs):
        saut = ' class="saut"' if (not no or k) else ""
        corps += f"<div{saut}>" + _bloc_conteneur(doc, c, L, LX, par_row, photos, devise, len(plan["conteneurs"]),
                                                  echeances, marquage=marquage, note=note) + "</div>"
    # le doctype n'est pas décoratif : sans lui (mode quirks), les tableaux n'héritent pas la taille du texte
    return (f'<!DOCTYPE html><html{dir_attr}><head><meta charset="utf-8"><style>{CSS}</style></head>'
            f'<body>{corps}</body></html>')


# En PORTRAIT (demande du 14/10 après un essai à l'italienne) : une puce tient sur une ligne grâce à une description
# en petit corps (8 px) et à des colonnes de chiffres étroites ; plus d'articles par page.
OPTIONS_PDF = {"orientation": "Portrait", "margin-top": "8mm", "margin-bottom": "8mm",
               "margin-left": "7mm", "margin-right": "7mm"}


@frappe.whitelist()
def download(docname, no=None, marquage=1, marque=None, note=None):
    """PDF d'un conteneur (`no`) ou de tous. `marque` : la marque principale choisie au téléchargement — mémorisée sur la
    commande (champ marquage_expedition) pour les prochains PDF ; `marquage` = 0 sans le bloc shipping marks ; `note` :
    remarque libre sous le bloc (16/10/2026 : « lorsque je sors le PDF, est-ce que je peux ajouter les marks ? »)."""
    from frappe.utils.pdf import get_pdf
    from frappe.utils import cint

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    no = int(no) if no not in (None, "", "0", 0) else None
    if marque is not None and (marque or "").strip() != (doc.get("marquage_expedition") or "").strip():
        doc.db_set("marquage_expedition", (marque or "").strip(), update_modified=False)
    pdf = get_pdf(construire_html(doc, no, marquage=bool(cint(marquage)), note=note), OPTIONS_PDF)
    frappe.local.response.filename = f"{doc.name}-{'C%d' % no if no else 'containers'}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"
