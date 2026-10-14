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
    "English": {"incomplet": "Not fully loaded (loaded / ordered)", "titre": "Loading plan", "conteneur": "Container", "type": "Type", "depart": "Departure",
                "a_confirmer": "to be confirmed", "capacite": "Usable capacity", "charge": "Loaded volume",
                "remplissage": "Fill", "lignes": "Lines", "photo": "Photo", "ref": "Your line",
                "description": "Description", "qty": "Qty", "volume": "Volume (m³)", "pu": "Unit price",
                "montant": "Amount", "total": "Container total", "recap": "Summary",
                "total_general": "Grand total", "scinde": "{q} of {t} — rest in {autres}",
                "additionnel": "Additional", "non_charge": "Not loaded (no unit volume)", "ref_doc": "Ref.",
                "date": "Date", "fournisseur": "Supplier", "conteneurs": "Containers"},
    "Français": {"incomplet": "Pas entièrement chargées (chargé / commandé)", "titre": "Plan de chargement", "conteneur": "Conteneur", "type": "Gabarit", "depart": "Départ",
                 "a_confirmer": "à confirmer", "capacite": "Capacité utile", "charge": "Volume chargé",
                 "remplissage": "Remplissage", "lignes": "Lignes", "photo": "Photo", "ref": "Votre ligne",
                 "description": "Description", "qty": "Qté", "volume": "Volume (m³)", "pu": "Prix unitaire",
                 "montant": "Montant", "total": "Total du conteneur", "recap": "Récapitulatif",
                 "total_general": "Total général", "scinde": "{q} sur {t} — reste dans {autres}",
                 "additionnel": "Additionnel", "non_charge": "Non chargées (sans volume unitaire)",
                 "ref_doc": "Réf.", "date": "Date", "fournisseur": "Fournisseur", "conteneurs": "Conteneurs"},
    "Deutsch": {"incomplet": "Nicht vollständig verladen (verladen / bestellt)", "titre": "Ladeplan", "conteneur": "Container", "type": "Typ", "depart": "Abfahrt",
                "a_confirmer": "noch offen", "capacite": "Nutzbares Volumen", "charge": "Geladenes Volumen",
                "remplissage": "Auslastung", "lignes": "Positionen", "photo": "Foto", "ref": "Ihre Zeile",
                "description": "Beschreibung", "qty": "Menge", "volume": "Volumen (m³)", "pu": "Stückpreis",
                "montant": "Betrag", "total": "Summe Container", "recap": "Übersicht",
                "total_general": "Gesamtsumme", "scinde": "{q} von {t} — Rest in {autres}",
                "additionnel": "Zusätzlich", "non_charge": "Nicht verladen (ohne Stückvolumen)", "ref_doc": "Ref.",
                "date": "Datum", "fournisseur": "Lieferant", "conteneurs": "Container"},
    "Español": {"incomplet": "No cargadas por completo (cargado / pedido)", "titre": "Plan de carga", "conteneur": "Contenedor", "type": "Tipo", "depart": "Salida",
                "a_confirmer": "por confirmar", "capacite": "Capacidad útil", "charge": "Volumen cargado",
                "remplissage": "Llenado", "lignes": "Líneas", "photo": "Foto", "ref": "Su línea",
                "description": "Descripción", "qty": "Cant.", "volume": "Volumen (m³)", "pu": "Precio unitario",
                "montant": "Importe", "total": "Total del contenedor", "recap": "Resumen",
                "total_general": "Total general", "scinde": "{q} de {t} — resto en {autres}",
                "additionnel": "Adicional", "non_charge": "No cargadas (sin volumen unitario)", "ref_doc": "Ref.",
                "date": "Fecha", "fournisseur": "Proveedor", "conteneurs": "Contenedores"},
    "العربية": {"incomplet": "غير محمّلة بالكامل (المحمّل / المطلوب)", "titre": "خطة الشحن", "conteneur": "حاوية", "type": "النوع", "depart": "المغادرة",
                "a_confirmer": "سيتم التأكيد", "capacite": "السعة المفيدة", "charge": "الحجم المحمّل",
                "remplissage": "نسبة الملء", "lignes": "البنود", "photo": "صورة", "ref": "سطركم",
                "description": "الوصف", "qty": "الكمية", "volume": "الحجم (م³)", "pu": "سعر الوحدة",
                "montant": "المبلغ", "total": "مجموع الحاوية", "recap": "ملخص", "total_general": "المجموع العام",
                "scinde": "{q} من {t} — الباقي في {autres}", "additionnel": "إضافي",
                "non_charge": "غير محمّلة (بدون حجم للوحدة)", "ref_doc": "المرجع", "date": "التاريخ",
                "fournisseur": "المورّد", "conteneurs": "الحاويات"},
}


def labels(langue):
    return LABELS.get(langue or "English") or LABELS["English"]


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


def manques(doc):
    """Lignes dont la quantité retenue n'est pas entièrement dans les conteneurs : [(ligne, chargé, retenu)]. PURE
    sur les champs de la ligne."""
    out = []
    for r in doc.articles:
        if (r.decision or "") == "Abandonné" or qty_retenue(r) <= 0:
            continue
        parts = parts_de(r.repartition_conteneurs)
        charge = sum(p["qty"] for p in parts)
        if parts and abs(charge - qty_retenue(r)) > 1e-6:
            out.append((r, charge, qty_retenue(r)))
    return out


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
  table.l th { background: #f0f2f5; text-align: left; padding: 5px 6px; font-size: 9.5px; text-transform: uppercase;
               border: 1px solid #d5dae1; }
  table.l td { padding: 4px 6px; border: 1px solid #d5dae1; vertical-align: top; font-size: 9.5px; line-height: 1.35; }
  table.l td.n { font-size: 10.5px; }
  table.l tr { page-break-inside: avoid; }
  .n { text-align: right; white-space: nowrap; }
  .ref { font-size: 9.5px; color: #0958d9; font-weight: bold; }
  .split { font-size: 9.5px; color: #391085; background: #f9f0ff; border-radius: 3px; padding: 1px 4px; display: inline-block;
           margin-top: 3px; }
  .pu-t { font-weight: bold; }
  ul.pu { margin: 2px 0 0; padding-left: 12px; }
  ul.pu li { margin: 0; }
  .tot td { font-weight: bold; background: #f9f0ff; font-size: 12px; }
  .saut { page-break-before: always; }
"""


def _entete_doc(doc, L, fournisseur):
    return (f'<div class="meta">{L["ref_doc"]} : {_esc(doc.name)} &nbsp;·&nbsp; {L["date"]} : '
            f'{_esc(formatdate(nowdate()))}'
            + (f' &nbsp;·&nbsp; {L["fournisseur"]} : <b>{_esc(fournisseur)}</b>' if fournisseur else "")
            + "</div>")


def _bloc_conteneur(doc, c, L, LX, par_row, photos, devise, nb_conteneurs):
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
    <table class="l"><thead><tr>
      <th style="width:18px">#</th><th style="width:86px">{L["photo"]}</th><th>{L["description"]}</th>
      <th class="n" style="width:70px">{L["qty"]}</th><th class="n" style="width:62px">{L["volume"]}</th>
      <th class="n" style="width:66px">{L["pu"]} ({devise})</th><th class="n" style="width:80px">{L["montant"]} ({devise})</th>
    </tr></thead><tbody>"""
    for i, l in enumerate(c["lignes"], 1):
        r = par_row.get(l["row"])
        if not r:
            continue
        photo = photos.get(r.name) or _img_data_uri(r.image)
        sa_ligne = ref_ligne(r.reponse_source)
        scinde = texte_scinde(parts_de(r.repartition_conteneurs), c["no"], qty_retenue(r), L)
        desc = description_html(description_pour_lui(r))
        html += f"""
      <tr>
        <td>{i}</td>
        <td style="text-align:center">{f'<img src="{photo}" style="max-width:80px;max-height:80px">' if photo else ""}</td>
        <td>{f'<div class="ref">{L["ref"]} {sa_ligne}</div>' if sa_ligne else ""}{desc}
          {f'<div class="split">{_esc(scinde)}</div>' if scinde else ""}</td>
        <td class="n"><b>{_n(l["qty"])}</b> {_esc(_uom_out(r.uom, LX))}</td>
        <td class="n">{flt(l["volume"]):.3f}</td>
        <td class="n">{prix_retenu(r):,.2f}</td>
        <td class="n"><b>{flt(l["montant"]):,.2f}</b></td>
      </tr>"""
    html += f"""
      <tr class="tot"><td colspan="3">{L["total"]} C{c["no"]}</td>
        <td></td><td class="n">{flt(c["volume"]):.3f}</td><td></td>
        <td class="n">{flt(c["montant"]):,.2f}</td></tr>
    </tbody></table>"""
    return html


def _recap(doc, plan, L, devise, hors_plan, incomplets=()):
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
    if incomplets:
        html += (f'<p style="margin-top:12px;color:#a8071a"><b>{L["incomplet"]}</b> : '
                 + " · ".join(f'{_esc(r.item_code or r.item_name or "")} ({_n(charge)} / {_n(total)})'
                              for r, charge, total in incomplets) + "</p>")
    if hors_plan:
        html += (f'<p style="margin-top:12px;color:#ad6800"><b>{L["non_charge"]}</b> : '
                 + " · ".join(_esc(r.item_code or r.item_name or "") + f" ({_n(qty_retenue(r))})" for r in hors_plan)
                 + "</p>")
    return html


def construire_html(doc, no=None):
    """Le HTML du PDF : un conteneur (`no`) ou tous (récapitulatif d'abord)."""
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
    dir_attr = ' dir="rtl"' if doc.langue_cible in RTL_LANGS else ""

    corps = f'<h1>{L["titre"]} — {_esc(doc.titre or doc.name)}</h1>' + _entete_doc(doc, L, fournisseur)
    if not no:
        hors_plan = [r for r in doc.articles if (r.decision or "") != "Abandonné" and qty_retenue(r) > 0
                     and not parts_de(r.repartition_conteneurs)]
        corps += _recap(doc, plan, L, devise, hors_plan, manques(doc))
    for k, c in enumerate(conteneurs):
        saut = ' class="saut"' if (not no or k) else ""
        corps += f"<div{saut}>" + _bloc_conteneur(doc, c, L, LX, par_row, photos, devise, len(plan["conteneurs"])) + "</div>"
    # le doctype n'est pas décoratif : sans lui (mode quirks), les tableaux n'héritent pas la taille du texte
    return (f'<!DOCTYPE html><html{dir_attr}><head><meta charset="utf-8"><style>{CSS}</style></head>'
            f'<body>{corps}</body></html>')


# À l'italienne : la colonne description est assez large pour qu'une puce tienne sur UNE ligne (en portrait, elle
# faisait 300 px et chaque puce passait sur deux ou trois lignes).
OPTIONS_PDF = {"orientation": "Landscape", "margin-top": "8mm", "margin-bottom": "8mm",
               "margin-left": "8mm", "margin-right": "8mm"}


@frappe.whitelist()
def download(docname, no=None):
    """PDF d'un conteneur (`no`) ou de tous."""
    from frappe.utils.pdf import get_pdf

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    no = int(no) if no not in (None, "", "0", 0) else None
    pdf = get_pdf(construire_html(doc, no), OPTIONS_PDF)
    frappe.local.response.filename = f"{doc.name}-{'C%d' % no if no else 'containers'}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"
