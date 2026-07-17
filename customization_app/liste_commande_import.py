"""
Backend de l'outil « Liste Commande Import » (demande de cotation fournisseur).

Créée depuis la sélection de la page Prévision Import, la liste est un DocType
persistant (Liste Commande Import + table enfant Article). Ce module fournit :
  - create_from_selection : créer/compléter une liste depuis la sélection ;
  - ai_improve_descriptions / ai_translate : amélioration et traduction des
    descriptions via OpenAI (clé lue dans Raven Settings) ;
  - download_pdf / download_excel : demande de cotation à envoyer au fournisseur.

Réservé à un seul utilisateur (même garde que Prévision Import).
"""

import base64
import html as html_lib
import json
import mimetypes
import os
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

ALLOWED_USER = "koubaawassim@gmail.com"

LANG_NAMES = {
    "Français": "French",
    "English": "English",
    "Deutsch": "German",
    "العربية": "Arabic",
    "Español": "Spanish",
}
RTL_LANGS = {"العربية"}

DOCTYPE = "Liste Commande Import"


def _guard():
    if frappe.session.user != ALLOWED_USER:
        frappe.throw(_("Accès réservé."), frappe.PermissionError)


def _strip_html(html):
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text).strip()


# ---------------------------------------------------------------- création

@frappe.whitelist()
def create_from_selection(items, target=None, titre=None):
    """items = JSON [{item_code, qty}] ; target = liste Brouillon existante à
    compléter, sinon nouvelle liste avec `titre`. Merge : si l'article existe
    déjà dans la liste, la qty transmise remplace l'ancienne."""
    _guard()
    rows = json.loads(items) if isinstance(items, str) else items
    if not rows:
        frappe.throw(_("Aucun article sélectionné."))

    if target:
        doc = frappe.get_doc(DOCTYPE, target)
        if doc.statut != "Brouillon":
            frappe.throw(_("La liste {0} n'est plus en Brouillon.").format(target))
    else:
        doc = frappe.new_doc(DOCTYPE)
        doc.titre = (titre or "").strip() or f"Commande import {nowdate()}"

    existing = {r.item_code: r for r in doc.articles}
    for it in rows:
        code = it.get("item_code")
        qty = flt(it.get("qty"))
        if not code:
            continue
        if code in existing:
            existing[code].qty = qty
            continue
        item = frappe.db.get_value(
            "Item", code,
            ["item_name", "description", "image", "stock_uom", "custom_volume_m3",
             "item_group"],
            as_dict=True) or frappe._dict()
        doc.append("articles", {
            "item_code": code,
            "item_name": item.get("item_name") or code,
            "item_group": item.get("item_group") or "",
            "qty": qty,
            "uom": item.get("stock_uom") or "",
            "description": item.get("description") or "",
            "image": item.get("image") or "",
            "volume_unitaire_m3": flt(item.get("custom_volume_m3")),
        })

    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "nb_articles": doc.nb_articles}


# ---------------------------------------------------------------- IA (OpenAI)

def _ai_setting(fieldname):
    """Lit un champ du Single « AI Settings » (créé par woocommerce_fusion)."""
    for dt in ("AI Settings", "AI settings"):
        if frappe.db.exists("DocType", dt):
            try:
                val = getattr(frappe.get_cached_doc(dt), fieldname, None)
                if val is not None and str(val).strip():
                    return str(val).strip()
            except Exception:
                pass
            return None
    return None


def _openai_client():
    api_key = (_ai_setting("openai_api_key")
               or frappe.conf.get("openai_api_key")
               or frappe.conf.get("lci_openai_api_key"))
    if not api_key:
        frappe.throw(_("Aucune clé OpenAI configurée (AI Settings > openai_api_key, "
                       "ou site_config openai_api_key)."))
    from openai import OpenAI
    return OpenAI(api_key=api_key)


def _model():
    return (frappe.conf.get("lci_openai_model")
            or _ai_setting("open_ai_model") or _ai_setting("openai_model")
            or "gpt-4o-mini")


def _temperature():
    try:
        return float(_ai_setting("open_ai_temperature") or 0.2)
    except Exception:
        return 0.2


def _chat_json(system, user):
    """Appel completion → JSON object. Lève une erreur claire en cas d'échec."""
    client = _openai_client()
    params = {
        "model": _model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    try:
        resp = client.chat.completions.create(temperature=_temperature(), **params)
    except Exception as e:
        # certains modèles (raisonneurs) refusent une température custom
        if "temperature" in str(e):
            resp = client.chat.completions.create(**params)
        else:
            raise
    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except Exception:
        frappe.throw(_("Réponse IA illisible : {0}").format(content[:300]))


def _target_rows(doc, row_names):
    names = None
    if row_names:
        names = set(json.loads(row_names) if isinstance(row_names, str) else row_names)
    return [r for r in doc.articles if (names is None or r.name in names)]


AI_CHUNK = 8  # lignes par appel IA — les grosses listes sont traitées par lots


def _chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


@frappe.whitelist()
def ai_improve_descriptions(docname, row_names=None):
    """Réécrit la description technique de chaque ligne ciblée (batch, 1 appel)."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    rows = [r for r in _target_rows(doc, row_names)
            if _strip_html(r.description) or r.item_name]
    if not rows:
        frappe.throw(_("Aucune ligne avec description à améliorer."))

    system = ("Tu es un rédacteur technique pour une société de traitement d'eau "
              "(osmoseurs, filtres, adoucisseurs, pompes). Pour chaque article : "
              "1) réécris une DÉSIGNATION courte et claire (max 60 caractères, "
              "nom commercial précis) ; 2) réécris une DESCRIPTION produit "
              "professionnelle, factuelle et concise (2 à 4 phrases), en "
              "français, adaptée à une demande de cotation fournisseur : "
              "caractéristiques techniques, matériaux, dimensions/capacités si "
              "présentes. N'invente aucune caractéristique absente. Réponds en "
              'JSON: {"0": {"designation": "...", "description": "..."}, "1": {...}}')

    updated = []
    for chunk in _chunks(rows, AI_CHUNK):
        payload = {str(i): {"designation": r.item_name or r.item_code or "",
                            "description": _strip_html(r.description) or "(aucune description)"}
                   for i, r in enumerate(chunk)}
        result = _chat_json(system, json.dumps(payload, ensure_ascii=False))
        for i, r in enumerate(chunk):
            entry = result.get(str(i)) or {}
            if isinstance(entry, str):
                entry = {"description": entry}
            new_name = (entry.get("designation") or "").strip()
            new_desc = (entry.get("description") or "").strip()
            if new_name:
                r.item_name = new_name
            if new_desc:
                r.description = new_desc
            if new_name or new_desc:
                updated.append(r.item_code or r.item_name)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"updated": updated}


@frappe.whitelist()
def ai_translate(docname, row_names=None):
    """Traduit la description de chaque ligne vers la langue cible du document."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    lang = LANG_NAMES.get(doc.langue_cible or "English", "English")
    rows = [r for r in _target_rows(doc, row_names)
            if _strip_html(r.description) or (r.item_name or "").strip()]
    if not rows:
        frappe.throw(_("Aucune ligne avec désignation ou description à traduire."))

    system = (f"Translate each product designation and description to {lang}. "
              "Keep technical terms, units and product codes accurate. "
              "Professional tone for a supplier quotation request. Respond in "
              'JSON: {"0": {"designation": "...", "description": "..."}, "1": {...}}')

    updated = []
    for chunk in _chunks(rows, AI_CHUNK):
        payload = {str(i): {"designation": r.item_name or r.item_code or "",
                            "description": _strip_html(r.description)}
                   for i, r in enumerate(chunk)}
        result = _chat_json(system, json.dumps(payload, ensure_ascii=False))
        for i, r in enumerate(chunk):
            entry = result.get(str(i)) or {}
            if isinstance(entry, str):
                entry = {"description": entry}
            tr_name = (entry.get("designation") or "").strip()
            tr_desc = (entry.get("description") or "").strip()
            if tr_name:
                r.item_name_traduit = tr_name
            if tr_desc:
                r.description_traduite = tr_desc
            if tr_name or tr_desc:
                updated.append(r.item_code or r.item_name)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"updated": updated, "langue": doc.langue_cible}


@frappe.whitelist()
def get_group_paths():
    """{groupe: "Parent > Sous-groupe > Groupe"} — hiérarchie complète des
    groupes d'articles (racine exclue), pour organiser les listes."""
    _guard()
    groups = frappe.get_all("Item Group", fields=["name", "parent_item_group"],
                            order_by="lft")
    parent = {g.name: g.parent_item_group for g in groups}
    roots = {g.name for g in groups if not g.parent_item_group}

    def path(n):
        parts = [n]
        cur = parent.get(n)
        while cur and cur in parent:
            parts.append(cur)
            cur = parent.get(cur)
        return " > ".join(p for p in reversed(parts) if p not in roots) or n

    return {g.name: path(g.name) for g in groups}


# ---------------------------------------------------------------- exports

def _img_path(file_url):
    """Chemin disque d'un fichier /files/... ou /private/files/..., sinon None.

    Les URLs sont souvent encodées (ex. %22 pour « " » dans les articles en
    pouces : C-10"-PP) alors que le fichier sur disque porte le caractère
    littéral — on essaie donc les deux variantes."""
    if not file_url:
        return None
    from urllib.parse import unquote
    try:
        if file_url.startswith("/private/files/"):
            base = ("private", "files")
        elif file_url.startswith("/files/"):
            base = ("public", "files")
        else:
            return None
        raw = os.path.basename(file_url)
        for candidate in (unquote(raw), raw):
            path = frappe.get_site_path(*base, candidate)
            if os.path.exists(path):
                return path
        return None
    except Exception:
        return None


def _img_thumb(file_url, max_px=220):
    """Vignette JPEG compressée (BytesIO) — allège fortement PDF et Excel."""
    import io as _io

    path = _img_path(file_url)
    if not path:
        return None
    try:
        from PIL import Image as PILImage
        img = PILImage.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_px, max_px))
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        buf.seek(0)
        return buf
    except Exception:
        return None


def _img_data_uri(file_url):
    """Vignette en data URI base64 (fiable pour wkhtmltopdf)."""
    buf = _img_thumb(file_url)
    if not buf:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _esc(v):
    return frappe.utils.escape_html(v or "")


@frappe.whitelist()
def download_pdf(docname):
    from frappe.utils.pdf import get_pdf

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    rtl = doc.langue_cible in RTL_LANGS
    dir_attr = ' dir="rtl"' if rtl else ""

    supplier = ""
    if doc.fournisseur:
        supplier = frappe.db.get_value("Supplier", doc.fournisseur, "supplier_name") or doc.fournisseur

    rows_html = ""
    for i, r in enumerate(doc.articles, 1):
        img = _img_data_uri(r.image)
        img_html = f'<img src="{img}" style="max-width:90px;max-height:90px;">' if img else ""
        name = r.item_name_traduit or r.item_name or ""
        desc = r.description_traduite or _strip_html(r.description)
        rows_html += f"""
        <tr>
          <td style="text-align:center;">{i}</td>
          <td style="text-align:center;">{img_html}</td>
          <td><b>{_esc(r.item_code)}</b><br><span{dir_attr}>{_esc(name)}</span></td>
          <td{dir_attr}>{_esc(desc).replace(chr(10), '<br>')}</td>
          <td style="text-align:right;">{flt(r.qty):g} {_esc(r.uom)}</td>
          <td style="text-align:right;">{flt(r.volume_ligne_m3):.3f}</td>
        </tr>"""

    html = f"""
    <html><head><meta charset="utf-8"><style>
      body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #222; }}
      h1 {{ font-size: 18px; margin-bottom: 2px; }}
      .meta {{ color: #555; margin-bottom: 14px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th {{ background: #f0f2f5; text-align: left; padding: 6px 8px; font-size: 10px;
            text-transform: uppercase; border: 1px solid #d5dae1; }}
      td {{ padding: 6px 8px; border: 1px solid #d5dae1; vertical-align: top; }}
      .tot {{ margin-top: 12px; font-size: 12px; font-weight: bold; text-align: right; }}
    </style></head><body>
      <h1>Demande de cotation — {_esc(doc.titre)}</h1>
      <div class="meta">
        Référence : {_esc(doc.name)} &nbsp;·&nbsp; Date : {_esc(str(doc.date_commande or nowdate()))}
        {"&nbsp;·&nbsp; Fournisseur : <b>" + _esc(supplier) + "</b>" if supplier else ""}
        &nbsp;·&nbsp; Langue : {_esc(doc.langue_cible or "")}
      </div>
      <table>
        <thead><tr>
          <th style="width:24px;">#</th><th style="width:100px;">Image</th>
          <th style="width:130px;">Article</th><th>Description</th>
          <th style="width:80px;text-align:right;">Quantité</th>
          <th style="width:70px;text-align:right;">Volume (m³)</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div class="tot">
        {cint(doc.nb_articles)} articles &nbsp;·&nbsp; Volume total estimé :
        {flt(doc.volume_total_m3):.3f} m³
      </div>
    </body></html>"""

    pdf = get_pdf(html)
    frappe.local.response.filename = f"{doc.name}-cotation.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"


@frappe.whitelist()
def download_excel(docname):
    """Excel de cotation : images embarquées, désignation/description TRADUITES
    (repli sur l'original), colonnes prix à remplir par le fournisseur."""
    import io

    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)

    wb = Workbook()
    ws = wb.active
    ws.title = "Cotation"

    header = ["#", "Image", "Code article", "Désignation", "Description",
              "Quantité", "UDM", "Volume unitaire (m³)", "Volume ligne (m³)",
              "Prix unitaire", "Total"]
    widths = [4, 12, 16, 34, 52, 10, 8, 12, 12, 12, 12]
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w

    ws.append([f"Demande de cotation — {doc.titre} ({doc.name})"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Date : {doc.date_commande or nowdate()}"
               + (f" — Fournisseur : {doc.fournisseur}" if doc.fournisseur else "")
               + f" — Langue : {doc.langue_cible or ''}"])
    ws.append([])
    ws.append(header)
    hrow = ws.max_row
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=hrow, column=c)
        cell.font = Font(bold=True, size=9)
        cell.fill = PatternFill("solid", fgColor="F0F2F5")

    wrap = Alignment(wrap_text=True, vertical="top")
    for i, r in enumerate(doc.articles, 1):
        name = r.item_name_traduit or r.item_name or ""
        desc = r.description_traduite or _strip_html(r.description)
        ws.append([i, "", r.item_code or "", name, desc, flt(r.qty), r.uom or "",
                   flt(r.volume_unitaire_m3), flt(r.volume_ligne_m3), "", ""])
        row_no = ws.max_row
        ws.row_dimensions[row_no].height = 52
        ws.cell(row=row_no, column=4).alignment = wrap
        ws.cell(row=row_no, column=5).alignment = wrap
        buf = _img_thumb(r.image, max_px=160)
        if buf:
            try:
                img = XLImage(buf)
                ratio = min(64.0 / (img.width or 64), 64.0 / (img.height or 64), 1.0)
                img.width = int((img.width or 64) * ratio)
                img.height = int((img.height or 64) * ratio)
                ws.add_image(img, f"B{row_no}")
            except Exception:
                pass

    ws.append([])
    ws.append(["", "", "", "TOTAL", "", "", "", "", flt(doc.volume_total_m3), "", ""])
    ws.cell(row=ws.max_row, column=4).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=9).font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    frappe.response["filename"] = f"{doc.name}-cotation.xlsx"
    frappe.response["filecontent"] = buf.getvalue()
    frappe.response["type"] = "binary"
