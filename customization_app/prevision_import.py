"""
Backend de l'outil « Prévision Import » (page Desk prevision-import).

Pour chaque article actif : moyenne glissante des ventes réelles (Bons de
Livraison soumis) sur une fenêtre W mois, tendance automatique (MG récente vs
MG précédente), prévision de demande pour la prochaine période d'import P mois
avec hypothèse de croissance (manuelle si saisie, sinon tendance auto), et
quantité à importer = max(0, prévision − stock actuel).

Indicateurs : couverture (mois de stock restants au rythme MG) et risque de
rupture (couverture < délai d'appro Item.lead_time_days/30, défaut 1 mois).

Le mois courant (incomplet) est exclu de l'historique.
Réservé à un seul utilisateur (même garde qu'Analyse Articles).
"""

import json

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

ALLOWED_USER = "koubaawassim@gmail.com"
PAGE_LENGTH = 50

DEFAULT_PERIODE = 3        # P : période d'import (mois)
DEFAULT_HISTORIQUE = 12    # H : fenêtre d'historique (mois)
DEFAULT_MOYENNE = 3        # W : fenêtre de moyenne glissante (mois)
TREND_CAP = 1.0            # tendance auto bornée à ±100 %

SORTABLE_FIELDS = {
    "item_code": "item_code", "item_name": "item_name", "item_group": "item_group",
    "mg": "mg", "tendance": "tendance", "croissance": "croissance",
    "prevision": "prevision", "stock": "stock", "a_importer": "a_importer",
    "a_importer_2": "a_importer_2", "couverture": "couverture",
}


def _guard():
    if frappe.session.user != ALLOWED_USER:
        frappe.throw(_("Accès réservé."), frappe.PermissionError)


# ---------------------------------------------------------------- helpers

def _month_key(d):
    return getdate(d).strftime("%Y-%m")


def _month_range(months):
    """Liste des clés YYYY-MM des `months` derniers mois COMPLETS
    (mois courant exclu), de la plus ancienne à la plus récente."""
    first_of_current = getdate(nowdate()).replace(day=1)
    keys = []
    for i in range(months, 0, -1):
        keys.append(_month_key(add_months(first_of_current, -i)))
    return keys


def _expand_item_group(item_group):
    lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (None, None)
    if lft is None:
        return [item_group]
    return frappe.get_all("Item Group", pluck="name",
                          filters={"lft": [">=", lft], "rgt": ["<=", rgt]})


def _fetch_items(search=None, item_group=None):
    """Articles actifs stockables (la prévision d'import ne concerne que le stock)."""
    filters = {"disabled": 0, "is_stock_item": 1}
    if item_group:
        filters["item_group"] = ["in", _expand_item_group(item_group)]
    or_filters = None
    if search:
        like = f"%{search}%"
        or_filters = [["Item", "name", "like", like],
                      ["Item", "item_name", "like", like],
                      ["Item", "description", "like", like]]
    return frappe.get_all(
        "Item",
        fields=["name", "item_name", "item_group", "stock_uom", "lead_time_days"],
        filters=filters, or_filters=or_filters,
    )


def _monthly_sales(item_codes, months, warehouse=None):
    """{item_code: {"YYYY-MM": qty}} depuis les BL soumis sur `months` mois complets.

    Trois sources cumulées (choix métier : les corrections d'inventaire sont de
    la consommation réelle à réapprovisionner — casse, pertes, écarts) :
      - lignes directes du BL (`Delivery Note Item`), y compris les BL de
        régularisation (custom_reconciliation_stock) ;
      - composants sortis via Product Bundle (`Packed Item`) — c'est cette table
        qui décrémente le stock des composants quand on vend un bundle. Pas de
        double comptage : l'article parent d'un bundle est non-stock, il est
        hors périmètre (is_stock_item=1) ;
      - ajustements NETS des Stock Reconciliation natifs. Attention : un SLE de
        réconciliation ne porte PAS de delta (actual_qty=0), il RÉINITIALISE le
        niveau (qty_after_transaction). Le delta implicite est donc recalculé
        par fenêtrage (qty_after − balance précédente) ; une perte ajoute de la
        demande, un gain la réduit. La toute première écriture d'un article
        (stock d'ouverture par réco) a un delta NULL → exclue."""
    if not item_codes:
        return {}
    first_of_current = getdate(nowdate()).replace(day=1)
    date_from = add_months(first_of_current, -months)
    conds_dni, conds_pki, conds_sle = "", "", ""
    params = {"codes": tuple(item_codes), "date_from": date_from,
              "date_to": first_of_current}
    if warehouse:
        conds_dni = " AND dni.warehouse = %(warehouse)s"
        conds_pki = " AND pki.warehouse = %(warehouse)s"
        conds_sle = " AND sle.warehouse = %(warehouse)s"
        params["warehouse"] = warehouse
    rows = frappe.db.sql(f"""
        SELECT item_code, mois, SUM(qty) AS qty FROM (
            SELECT dni.item_code AS item_code,
                   DATE_FORMAT(dn.posting_date, '%%Y-%%m') AS mois,
                   dni.qty AS qty
            FROM `tabDelivery Note` dn
            JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
            WHERE dn.docstatus = 1
              AND dni.item_code IN %(codes)s
              AND dn.posting_date >= %(date_from)s
              AND dn.posting_date < %(date_to)s{conds_dni}
            UNION ALL
            SELECT pki.item_code AS item_code,
                   DATE_FORMAT(dn.posting_date, '%%Y-%%m') AS mois,
                   pki.qty AS qty
            FROM `tabDelivery Note` dn
            JOIN `tabPacked Item` pki ON pki.parent = dn.name
                 AND pki.parenttype = 'Delivery Note'
            WHERE dn.docstatus = 1
              AND pki.item_code IN %(codes)s
              AND dn.posting_date >= %(date_from)s
              AND dn.posting_date < %(date_to)s{conds_pki}
            UNION ALL
            SELECT rc.item_code AS item_code,
                   DATE_FORMAT(rc.posting_date, '%%Y-%%m') AS mois,
                   -rc.delta AS qty
            FROM (
                SELECT sle.item_code, sle.posting_date, sle.voucher_type,
                       sle.qty_after_transaction
                       - LAG(sle.qty_after_transaction) OVER (
                           PARTITION BY sle.item_code, sle.warehouse
                           ORDER BY sle.posting_datetime, sle.creation) AS delta
                FROM `tabStock Ledger Entry` sle
                WHERE sle.is_cancelled = 0
                  AND sle.item_code IN %(codes)s{conds_sle}
            ) rc
            WHERE rc.voucher_type = 'Stock Reconciliation'
              AND rc.delta IS NOT NULL AND rc.delta != 0
              AND rc.posting_date >= %(date_from)s
              AND rc.posting_date < %(date_to)s
        ) t
        GROUP BY item_code, mois
    """, params, as_dict=True)
    out = {}
    for r in rows:
        out.setdefault(r.item_code, {})[r.mois] = flt(r.qty)
    return out


def _stock_map(item_codes, warehouse=None):
    """{item_code: stock actuel} — SUM(Bin.actual_qty), filtré entrepôt si fourni."""
    if not item_codes:
        return {}
    filters = {"item_code": ["in", item_codes]}
    if warehouse:
        filters["warehouse"] = warehouse
    rows = frappe.get_all("Bin", filters=filters,
                          fields=["item_code", "sum(actual_qty) as qty"],
                          group_by="item_code")
    return {r.item_code: flt(r.qty) for r in rows}


def _avg(values):
    return (sum(values) / len(values)) if values else 0.0


def build_row(item, sales_by_month, month_keys, stock, periode, fenetre_moy,
              croissance_manuelle):
    """Calcule une ligne de prévision pour un article. Point d'entrée unique."""
    series = [flt(sales_by_month.get(k, 0)) for k in month_keys]

    recent = series[-fenetre_moy:] if fenetre_moy else []
    previous = series[-2 * fenetre_moy:-fenetre_moy] if fenetre_moy else []

    mg = _avg(recent)
    mg_prev = _avg(previous)

    # tendance auto : besoin d'un historique complet sur les 2 fenêtres
    if len(series) >= 2 * fenetre_moy and mg_prev > 0:
        tendance = (mg - mg_prev) / mg_prev
        tendance = max(-TREND_CAP, min(TREND_CAP, tendance))
    else:
        tendance = 0.0

    croissance = (flt(croissance_manuelle) / 100.0
                  if croissance_manuelle not in (None, "")
                  else tendance)

    prevision = mg * periode * (1 + croissance)
    a_importer = max(0.0, prevision - stock)
    # quantité pour couvrir 2 cycles d'import (cohérent avec le seuil « OK »)
    prevision_2 = mg * 2 * periode * (1 + croissance)
    a_importer_2 = max(0.0, prevision_2 - stock)

    couverture = (stock / mg) if mg > 0 else None  # mois de stock restants
    delai_mois = max(flt(item.get("lead_time_days")) / 30.0, 1.0)
    # OK exige de couvrir 2 cycles d'import : si on n'importe pas maintenant,
    # la prochaine reception n'arrive qu'au cycle suivant (+ delai). Un article
    # qui ne tient pas 2 cycles doit etre commande maintenant ou surveille.
    seuil_ok = max(2 * periode, delai_mois + periode)
    if mg <= 0:
        risque = "aucun"          # article sans rotation récente
    elif couverture is not None and couverture < delai_mois:
        risque = "rupture"        # stock épuisé avant le prochain réappro
    elif couverture is not None and couverture < seuil_ok:
        risque = "attention"      # ne couvre pas 2 cycles : à commander maintenant
    else:
        risque = "ok"             # couvre ≥ 2 cycles : peut sauter ce cycle

    return {
        "item_code": item["name"],
        "item_name": item.get("item_name") or "",
        "item_group": item.get("item_group") or "",
        "stock_uom": item.get("stock_uom") or "",
        "serie": series,
        "mg": round(mg, 3),
        "mg_prev": round(mg_prev, 3),
        "tendance": round(tendance * 100, 1),
        "croissance": round(croissance * 100, 1),
        "prevision": round(prevision, 3),
        "stock": round(stock, 3),
        "a_importer": round(a_importer, 3),
        "prevision_2": round(prevision_2, 3),
        "a_importer_2": round(a_importer_2, 3),
        "couverture": round(couverture, 1) if couverture is not None else None,
        "risque": risque,
    }


def compute_prevision(search=None, item_group=None, warehouse=None,
                      periode=DEFAULT_PERIODE, fenetre_hist=DEFAULT_HISTORIQUE,
                      fenetre_moy=DEFAULT_MOYENNE, croissance=None,
                      start=0, page_length=None, order_by="a_importer",
                      order_dir="desc", only_active=1, risque=None,
                      only_a_importer=0):
    periode = max(cint(periode) or DEFAULT_PERIODE, 1)
    fenetre_moy = max(cint(fenetre_moy) or DEFAULT_MOYENNE, 1)
    # l'historique doit couvrir 2 fenêtres de moyenne pour la tendance
    fenetre_hist = max(cint(fenetre_hist) or DEFAULT_HISTORIQUE, 2 * fenetre_moy)

    items = _fetch_items(search=search, item_group=item_group)
    codes = [i["name"] for i in items]
    month_keys = _month_range(fenetre_hist)
    sales = _monthly_sales(codes, fenetre_hist, warehouse=warehouse)
    stocks = _stock_map(codes, warehouse=warehouse)

    rows = []
    for item in items:
        code = item["name"]
        item_sales = sales.get(code, {})
        # only_active : masquer les articles dormants (aucune vente sur H mois
        # ET stock exactement à zéro). Un stock NÉGATIF reste toujours visible :
        # c'est un déficit réel à combler, même sans vente récente.
        if cint(only_active) and not item_sales and flt(stocks.get(code, 0)) == 0:
            continue
        rows.append(build_row(item, item_sales, month_keys,
                              flt(stocks.get(code, 0)), periode, fenetre_moy,
                              croissance))

    # totaux calculés AVANT le filtre risque (les KPI restent globaux)
    totaux = {
        "a_importer_articles": sum(1 for r in rows if r["a_importer"] > 0),
        "rupture": sum(1 for r in rows if r["risque"] == "rupture"),
        "attention": sum(1 for r in rows if r["risque"] == "attention"),
    }

    # filtre risque MULTI : liste JSON d'états (union) — [] ou absent = tous.
    # Rétro-compatible avec les anciennes valeurs chaîne.
    states = None
    if risque:
        if isinstance(risque, str) and risque.strip().startswith("["):
            try:
                states = [s for s in json.loads(risque) if s]
            except Exception:
                states = None
        elif risque == "rupture_attention":
            states = ["rupture", "attention"]
        elif risque == "a_importer":
            only_a_importer = 1
        elif risque in ("rupture", "attention", "ok", "aucun"):
            states = [risque]
    if states:
        rows = [r for r in rows if r["risque"] in states]

    # critère quantité, combinable avec les états (ET logique)
    if cint(only_a_importer):
        rows = [r for r in rows if r["a_importer"] > 0]

    field = SORTABLE_FIELDS.get(order_by, "a_importer")
    reverse = str(order_dir).lower() != "asc"
    rows.sort(key=lambda r: ((r.get(field) is None),
                             r.get(field) if r.get(field) is not None else 0,
                             r["item_code"]),
              reverse=reverse)

    total = len(rows)
    start = cint(start)
    if page_length:
        rows = rows[start:start + cint(page_length)]

    return {
        "total": total, "start": start, "page_length": page_length,
        "months": month_keys, "totaux": totaux, "rows": rows,
        "params": {"periode": periode, "fenetre_hist": fenetre_hist,
                   "fenetre_moy": fenetre_moy,
                   "croissance": croissance if croissance not in (None, "") else None},
    }


# ---------------------------------------------------------------- API

@frappe.whitelist()
def get_filters():
    _guard()
    groups = frappe.get_all("Item Group",
                            fields=["name", "parent_item_group", "is_group"],
                            order_by="lft")
    parent = {g.name: g.parent_item_group for g in groups}
    out = []
    for g in groups:
        depth, cur = 0, parent.get(g.name)
        while cur and cur in parent:
            depth += 1
            cur = parent.get(cur)
        out.append({"name": g.name, "is_group": g.is_group, "depth": depth,
                    "label": ("  " * depth) + g.name})
    warehouses = frappe.get_all("Warehouse", filters={"disabled": 0, "is_group": 0},
                                pluck="name", order_by="name")
    return {"item_groups": out, "warehouses": warehouses,
            "defaults": {"periode": DEFAULT_PERIODE,
                         "fenetre_hist": DEFAULT_HISTORIQUE,
                         "fenetre_moy": DEFAULT_MOYENNE}}


@frappe.whitelist()
def get_prevision(search=None, item_group=None, warehouse=None,
                  periode=DEFAULT_PERIODE, fenetre_hist=DEFAULT_HISTORIQUE,
                  fenetre_moy=DEFAULT_MOYENNE, croissance=None,
                  start=0, page_length=PAGE_LENGTH,
                  order_by="a_importer", order_dir="desc", only_active=1,
                  risque=None, only_a_importer=0):
    _guard()
    return compute_prevision(
        search=search or None, item_group=item_group or None,
        warehouse=warehouse or None, periode=periode, fenetre_hist=fenetre_hist,
        fenetre_moy=fenetre_moy, croissance=croissance, start=start,
        page_length=cint(page_length) or PAGE_LENGTH,
        order_by=order_by, order_dir=order_dir, only_active=only_active,
        risque=risque or None, only_a_importer=only_a_importer)


@frappe.whitelist()
def get_item_history(item_code, months=24, warehouse=None):
    """Série mensuelle détaillée d'un article (pour le graphique)."""
    _guard()
    months = max(cint(months) or 24, 1)
    keys = _month_range(months)
    sales = _monthly_sales([item_code], months, warehouse=warehouse or None)
    serie = [flt((sales.get(item_code) or {}).get(k, 0)) for k in keys]
    return {"months": keys, "qty": serie}


@frappe.whitelist()
def download_excel(search=None, item_group=None, warehouse=None,
                   periode=DEFAULT_PERIODE, fenetre_hist=DEFAULT_HISTORIQUE,
                   fenetre_moy=DEFAULT_MOYENNE, croissance=None,
                   order_by="a_importer", order_dir="desc", only_active=1,
                   risque=None, only_a_importer=0):
    from frappe.utils.xlsxutils import make_xlsx

    _guard()
    data = compute_prevision(
        search=search or None, item_group=item_group or None,
        warehouse=warehouse or None, periode=periode, fenetre_hist=fenetre_hist,
        fenetre_moy=fenetre_moy, croissance=croissance,
        order_by=order_by, order_dir=order_dir, only_active=only_active,
        risque=risque or None, only_a_importer=only_a_importer)

    p = data["params"]
    header = ["Code article", "Désignation", "Groupe", "UDM",
              f"MG ({p['fenetre_moy']} mois)", "Tendance auto %",
              "Croissance appliquée %", f"Prévision {p['periode']} mois",
              "Stock actuel", "À importer (1 cycle)",
              f"À importer (2 cycles = {2 * p['periode']} mois)",
              "Couverture (mois)", "Risque"]
    rows = [[f"Prévision Import — {nowdate()} — période {p['periode']} mois, "
             f"moyenne {p['fenetre_moy']} mois, historique {p['fenetre_hist']} mois"
             + (f", croissance manuelle {p['croissance']}%" if p['croissance'] is not None else ", croissance auto")],
            [], header]
    risk_label = {"rupture": "RUPTURE", "attention": "Attention", "ok": "OK", "aucun": "—"}
    for r in data["rows"]:
        rows.append([r["item_code"], r["item_name"], r["item_group"], r["stock_uom"],
                     r["mg"], r["tendance"], r["croissance"], r["prevision"],
                     r["stock"], r["a_importer"], r["a_importer_2"],
                     r["couverture"] if r["couverture"] is not None else "",
                     risk_label.get(r["risque"], r["risque"])])

    xlsx = make_xlsx(rows, "Prevision Import")
    frappe.response["filename"] = f"prevision-import-{nowdate()}.xlsx"
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"
