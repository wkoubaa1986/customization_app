"""
Backend du Bilan Vente Economiq Aqua Solution (Page custom).

Porte la logique de l'ancien Script Report "Blilan Vente Economic Aqua Solution"
vers une API whitelisted qui renvoie du JSON structuré, rendu par une Page Desk
(customize_erpnext/page/bilan_vente). Toutes les règles métier vivent ici :
le front ne fait qu'afficher.

Règles métier :
- Période = mois sélectionné ; bornée à aujourd'hui si c'est le mois courant.
- Section "Aqua World & Servicing" : commandes créées par le partenaire
  (owner = PARTNER_USER) et livrées (Fully Delivered, ou BL validé avec
  réconciliation stock), plus les commandes partagées via DocShare dont la
  Tâche de travail n'est pas affectée à l'employé partenaire. Les clients
  présents dans une "Liste Appelle Entretien" en brouillon sont exclus.
- Section "Economiq Aqua Solution" : commandes partagées dont la Tâche de
  travail est affectée à l'employé partenaire ; le prix d'achat est majoré
  de PARTNER_MARGIN (10 %).
- Prix d'achat = tarif PURCHASE_PRICE_LIST de l'article, valide à la date de
  livraison quand les dates de validité sont renseignées. Pour les groupes
  PASSTHROUGH_ITEM_GROUPS (Livraison, Main d'œuvre), PA = PV : pas de marge.
- Bénéfice = Qté × (PV − PA).
- Paiements d'une commande = paiements alloués à ses factures validées
  + paiements alloués directement à la commande (sans double compte).
- Solde de section = Espèces encaissées − Total achats TTC ; le signe
  détermine qui doit à qui.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_last_day, getdate, nowdate

PARTNER_USER = "economiqaquasolutions23@gmail.com"
HOST_LABEL = "Aqua World & Servicing"
PARTNER_LABEL = "Economiq Aqua Solution"
PURCHASE_PRICE_LIST = "Compte Pro"
PARTNER_MARGIN = 0.10
PASSTHROUGH_ITEM_GROUPS = ("Livraison", "Main d’œuvre")
PRECISION = 3
FIRST_MONTH = (2024, 10)  # premier mois proposé dans le filtre

FR_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


# ---------------------------------------------------------------- période

def _period(month):
    today = getdate(nowdate())
    if month:
        parts = str(month).split("-")
        year, mon = cint(parts[0]), cint(parts[1])
    else:
        year, mon = today.year, today.month
    start = getdate(f"{year:04d}-{mon:02d}-01")
    end = today if (year, mon) == (today.year, today.month) else get_last_day(start)
    return year, mon, start, end


def _month_options():
    today = getdate(nowdate())
    options = []
    year, mon = FIRST_MONTH
    while (year, mon) <= (today.year, today.month):
        options.append({
            "value": f"{year:04d}-{mon:02d}",
            "label": f"{FR_MONTHS[mon - 1]} {year}",
        })
        mon += 1
        if mon > 12:
            mon, year = 1, year + 1
    options.reverse()
    return options


# ---------------------------------------------------------------- requêtes

def _clients_partage():
    return set(frappe.db.sql(
        """SELECT DISTINCT c.client
           FROM `tabListe Appelle Entretien` lac
           INNER JOIN `tabAppelle Client` c ON c.parent = lac.name
           WHERE lac.docstatus = 0""",
        pluck="client",
    ))


def _owned_orders(start, end):
    return frappe.db.sql(
        """SELECT so.name, so.customer, so.delivery_date, so.grand_total
           FROM `tabSales Order` so
           WHERE so.docstatus = 1
             AND so.owner = %s
             AND so.delivery_date BETWEEN %s AND %s
             AND (
                 so.delivery_status = 'Fully Delivered'
                 OR EXISTS (
                     SELECT 1
                     FROM `tabDelivery Note Item` dni
                     INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
                     WHERE dni.against_sales_order = so.name
                       AND dn.docstatus = 1
                       AND dn.status != 'Closed'
                       AND dn.custom_reconciliation_stock IS NOT NULL
                 )
             )
           ORDER BY so.delivery_date, so.name""",
        (PARTNER_USER, start, end), as_dict=True,
    )


def _shared_orders(start, end):
    return frappe.db.sql(
        """SELECT DISTINCT so.name, so.customer, so.delivery_date, so.grand_total
           FROM `tabDocShare` ds
           INNER JOIN `tabSales Order` so ON so.name = ds.share_name
           WHERE ds.share_doctype = 'Sales Order'
             AND ds.user = %s
             AND so.docstatus = 1
             AND so.delivery_date BETWEEN %s AND %s
           ORDER BY so.delivery_date, so.name""",
        (PARTNER_USER, start, end), as_dict=True,
    )


def _split_shared(shared):
    """Sépare les commandes partagées : réalisées PAR le partenaire vs POUR lui."""
    if not shared:
        return [], []
    partner_employee = frappe.db.get_value(
        "Employee", {"user_id": PARTNER_USER}, "employee_name"
    )
    names = [o.name for o in shared]
    ph = ",".join(["%s"] * len(names))
    rows = frappe.db.sql(
        f"""SELECT commande_client, custom_employé AS employee
            FROM `tabTache de travail`
            WHERE commande_client IN ({ph})
            ORDER BY modified DESC""",
        tuple(names), as_dict=True,
    )
    task_employee = {}
    for r in rows:
        task_employee.setdefault(r.commande_client, r.employee)

    done_by, done_for = [], []
    for order in shared:
        if partner_employee and task_employee.get(order.name) == partner_employee:
            done_by.append(order)
        else:
            done_for.append(order)
    return done_by, done_for


def _items_by_order(so_names):
    if not so_names:
        return {}
    ph = ",".join(["%s"] * len(so_names))
    rows = frappe.db.sql(
        f"""SELECT soi.parent, soi.item_code, soi.item_name, soi.qty, soi.rate,
                   i.item_group
            FROM `tabSales Order Item` soi
            LEFT JOIN `tabItem` i ON i.name = soi.item_code
            WHERE soi.parent IN ({ph})
            ORDER BY soi.parent, soi.idx""",
        tuple(so_names), as_dict=True,
    )
    out = {}
    for r in rows:
        out.setdefault(r.parent, []).append(r)
    return out


def _purchase_price_resolver(item_codes):
    """Renvoie resolve(item_code, date) -> tarif PURCHASE_PRICE_LIST.

    Choisit le tarif valide à la date (valid_from/valid_upto), sinon le plus
    récent connu, sinon 0.
    """
    prices = {}
    codes = [c for c in set(item_codes) if c]
    if codes:
        ph = ",".join(["%s"] * len(codes))
        rows = frappe.db.sql(
            f"""SELECT item_code, price_list_rate, valid_from, valid_upto
                FROM `tabItem Price`
                WHERE price_list = %s AND item_code IN ({ph})
                ORDER BY valid_from""",
            (PURCHASE_PRICE_LIST, *codes), as_dict=True,
        )
        for r in rows:
            prices.setdefault(r.item_code, []).append(r)

    def resolve(item_code, on_date):
        candidates = prices.get(item_code)
        if not candidates:
            return 0.0
        on_date = getdate(on_date) if on_date else None
        valid = [
            r for r in candidates
            if on_date
            and (not r.valid_from or getdate(r.valid_from) <= on_date)
            and (not r.valid_upto or getdate(r.valid_upto) >= on_date)
        ]
        pool = valid or candidates
        best = max(pool, key=lambda r: getdate(r.valid_from) if r.valid_from else getdate("1900-01-01"))
        return flt(best.price_list_rate)

    return resolve


def _tasks_by_order(so_names):
    """Tâches de travail (non annulées) liées aux commandes."""
    if not so_names:
        return {}
    ph = ",".join(["%s"] * len(so_names))
    rows = frappe.db.sql(
        f"""SELECT name, commande_client, status,
                   custom_type_dintervention AS intervention_type,
                   custom_employé AS employee, starts_on
            FROM `tabTache de travail`
            WHERE commande_client IN ({ph}) AND docstatus < 2
            ORDER BY starts_on""",
        tuple(so_names), as_dict=True,
    )
    out = {}
    for r in rows:
        out.setdefault(r.commande_client, []).append({
            "name": r.name,
            "status": r.status,
            "intervention_type": r.intervention_type,
            "employee": r.employee,
            "starts_on": str(r.starts_on) if r.starts_on else None,
        })
    return out


def _payments_by_order(so_names):
    """Paiements validés par commande : via factures + directs (dédupliqués)."""
    if not so_names:
        return {}
    ph = ",".join(["%s"] * len(so_names))

    inv_rows = frappe.db.sql(
        f"""SELECT DISTINCT sii.sales_order, si.name
            FROM `tabSales Invoice` si
            INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
            WHERE si.docstatus = 1 AND sii.sales_order IN ({ph})""",
        tuple(so_names), as_dict=True,
    )
    inv_to_so = {r.name: r.sales_order for r in inv_rows}

    out = {name: [] for name in so_names}
    seen = {name: set() for name in so_names}

    if inv_to_so:
        iph = ",".join(["%s"] * len(inv_to_so))
        rows = frappe.db.sql(
            f"""SELECT per.reference_name AS ref, per.allocated_amount AS amount,
                       pe.name, pe.mode_of_payment, pe.reference_no, pe.posting_date,
                       pe.paid_to
                FROM `tabPayment Entry Reference` per
                INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
                WHERE pe.docstatus = 1
                  AND per.reference_doctype = 'Sales Invoice'
                  AND per.reference_name IN ({iph})
                ORDER BY pe.posting_date, pe.name""",
            tuple(inv_to_so), as_dict=True,
        )
        for r in rows:
            so = inv_to_so[r.ref]
            out[so].append(r)
            seen[so].add(r.name)

    rows = frappe.db.sql(
        f"""SELECT per.reference_name AS ref, per.allocated_amount AS amount,
                   pe.name, pe.mode_of_payment, pe.reference_no, pe.posting_date,
                   pe.paid_to
            FROM `tabPayment Entry Reference` per
            INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
            WHERE pe.docstatus = 1
              AND per.reference_doctype = 'Sales Order'
              AND per.reference_name IN ({ph})
            ORDER BY pe.posting_date, pe.name""",
        tuple(so_names), as_dict=True,
    )
    for r in rows:
        if r.name not in seen[r.ref]:
            out[r.ref].append(r)
            seen[r.ref].add(r.name)

    return out


# ---------------------------------------------------------------- assemblage

def _classify(mode):
    if mode == "Espèces":
        return "especes"
    if mode == "Chèque":
        return "cheque"
    return "autre"


def _build_order(so, items, payments, tasks, resolve_price, margin):
    order_items = []
    vente = achat = benefice = 0.0
    for it in items:
        qty = flt(it.qty)
        pv = flt(it.rate, PRECISION)
        base = resolve_price(it.item_code, so.delivery_date)
        pa = flt((1 + margin) * base, PRECISION) if margin else flt(base, PRECISION)
        if (it.item_group or "") in PASSTHROUGH_ITEM_GROUPS:
            pa = pv
        ben = flt(qty * (pv - pa), PRECISION)
        vente += qty * pv
        achat += qty * pa
        benefice += ben
        order_items.append({
            "item_code": it.item_code,
            "item_name": it.item_name or it.item_code,
            "item_group": it.item_group,
            "qty": qty,
            "pv": pv,
            "pa": pa,
            "benefice": ben,
        })

    order_payments = []
    especes = cheques = autres = paid = 0.0
    cheques_list = []
    for p in payments:
        amount = flt(p.amount, PRECISION)
        kind = _classify(p.mode_of_payment)
        if kind == "especes":
            especes += amount
        elif kind == "cheque":
            cheques += amount
            cheques_list.append({"reference_no": p.reference_no, "amount": amount})
        else:
            autres += amount
        paid += amount
        order_payments.append({
            "payment_entry": p.name,
            "mode": p.mode_of_payment,
            "kind": kind,
            "amount": amount,
            "reference_no": p.reference_no,
            "posting_date": str(p.posting_date) if p.posting_date else None,
            "paid_to": p.paid_to,
        })

    total_ttc = flt(so.grand_total, PRECISION)
    return {
        "name": so.name,
        "customer": so.customer,
        "delivery_date": str(so.delivery_date) if so.delivery_date else None,
        "total_ttc": total_ttc,
        "items": order_items,
        "payments": order_payments,
        "tasks": tasks,
        "totals": {
            "vente": flt(vente, PRECISION),
            "achat": flt(achat, PRECISION),
            "benefice": flt(benefice, PRECISION),
            "especes": flt(especes, PRECISION),
            "cheques": flt(cheques, PRECISION),
            "autres": flt(autres, PRECISION),
            "paid": flt(paid, PRECISION),
            "reste": flt(total_ttc - paid, PRECISION),
        },
        "cheques": cheques_list,
    }


def _build_section(key, company, orders, margin, margin_note, due_pos, due_neg):
    totals = {"vente": 0.0, "achat": 0.0, "benefice": 0.0,
              "especes": 0.0, "cheques": 0.0, "autres": 0.0, "reste": 0.0}
    cheques = []
    payments_detail = []
    for o in orders:
        for field in ("vente", "achat", "benefice", "especes", "cheques", "autres"):
            totals[field] += o["totals"][field]
        totals["reste"] += max(0.0, o["totals"]["reste"])
        cheques.extend(o["cheques"])
        for p in o["payments"]:
            payments_detail.append({**p, "order": o["name"], "customer": o["customer"]})
    totals = {k: flt(v, PRECISION) for k, v in totals.items()}
    payments_detail.sort(key=lambda p: (p["posting_date"] or "", p["payment_entry"]))

    solde = flt(totals["especes"] - totals["achat"], PRECISION)
    return {
        "key": key,
        "company": company,
        "margin_note": margin_note,
        "orders": orders,
        "totals": totals,
        "cheques": cheques,
        "payments_detail": payments_detail,
        "solde": {
            "due_by": due_pos if solde >= 0 else due_neg,
            "amount": abs(solde),
        },
    }


# ---------------------------------------------------------------- export

def _excel_rows(data):
    def money(v):
        return flt(v, PRECISION)

    rows = [
        [f"Bilan Vente Economiq Aqua Solution — {data['label']}",
         f"du {data['period']['from']} au {data['period']['to']}"],
        [],
    ]
    for s in data["sections"]:
        title = f"Travaux effectués par {s['company']}"
        if s.get("margin_note"):
            title += f" ({s['margin_note']})"
        rows.append([title])
        rows.append([
            "Commande", "Date livraison", "Client", "Total TTC",
            "Article", "Qté", "PV TTC", "PA TTC", "Bénéfice",
            "Mode de paiement", "Montant payé", "Référence",
            "Tâche de travail", "Statut tâche", "Type d'intervention",
            "Effectuée par", "Début tâche",
        ])
        for o in s["orders"]:
            count = max(len(o["items"]), len(o["payments"]), len(o["tasks"]), 1)
            for i in range(count):
                it = o["items"][i] if i < len(o["items"]) else None
                p = o["payments"][i] if i < len(o["payments"]) else None
                tk = o["tasks"][i] if i < len(o["tasks"]) else None
                rows.append([
                    o["name"] if i == 0 else "",
                    o["delivery_date"] if i == 0 else "",
                    o["customer"] if i == 0 else "",
                    money(o["total_ttc"]) if i == 0 else "",
                    it["item_name"] if it else "",
                    it["qty"] if it else "",
                    money(it["pv"]) if it else "",
                    money(it["pa"]) if it else "",
                    money(it["benefice"]) if it else "",
                    p["mode"] if p else "",
                    money(p["amount"]) if p else "",
                    (p["reference_no"] or "") if p else "",
                    tk["name"] if tk else "",
                    (tk["status"] or "") if tk else "",
                    (tk["intervention_type"] or "") if tk else "",
                    (tk["employee"] or "") if tk else "",
                    (tk["starts_on"] or "") if tk else "",
                ])
        t = s["totals"]
        rows.append(["", "", "Totaux", "", "", "",
                     money(t["vente"]), money(t["achat"]), money(t["benefice"])])
        rows.append(["Espèces", money(t["especes"]), "Chèques", money(t["cheques"]),
                     "Autres modes", money(t["autres"])])
        rows.append([s["solde"]["due_by"], money(s["solde"]["amount"])])
        rows.append([])
        rows.append([f"Détail des paiements — {s['company']}"])
        rows.append(["Date", "Écriture", "Commande", "Client",
                     "Mode de paiement", "Compte encaissé", "Référence", "Montant"])
        for p in s["payments_detail"]:
            rows.append([
                p["posting_date"] or "", p["payment_entry"], p["order"], p["customer"],
                p["mode"] or "", p["paid_to"] or "", p["reference_no"] or "",
                money(p["amount"]),
            ])
        rows.append([])
    return rows


@frappe.whitelist()
def download_excel(month=None):
    from frappe.utils.xlsxutils import make_xlsx

    data = get_data(month)
    xlsx = make_xlsx(_excel_rows(data), "Bilan Vente")
    frappe.response["filename"] = f"bilan-vente-{data['month']}.xlsx"
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"


# ---------------------------------------------------------------- API

@frappe.whitelist()
def get_data(month=None):
    if not frappe.has_permission("Sales Order", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)

    year, mon, start, end = _period(month)

    clients_partage = _clients_partage()
    owned = _owned_orders(start, end)
    done_by_partner, done_for_partner = _split_shared(_shared_orders(start, end))

    # Section Aqua World : commandes du partenaire + partagées "pour lui",
    # hors clients "Liste Appelle Entretien", sans doublon.
    #
    # ⚠️ LE DÉDOUBLONNAGE DOIT AUSSI REGARDER L'AUTRE SECTION. `seen` ne dédoublonnait
    # qu'à l'intérieur de `owned + done_for_partner` : une commande CRÉÉE par le partenaire et
    # EXÉCUTÉE par lui est dans `owned` ET dans `done_by_partner`, donc comptée des deux côtés.
    # Sur juillet 2026, trois commandes dans ce cas gonflaient les ventes de 118 DT sur 1 048,
    # et faussaient le solde net du bilan — donc l'ajustement Economiq et sa première échéance.
    #
    # C'est l'EXÉCUTANT qui décide de la section, jamais le créateur : `_split_shared` est là
    # pour ça. Une commande faite par le partenaire est son travail, elle est à lui.
    faites_par_le_partenaire = {o.name for o in done_by_partner}
    aw_orders, seen = [], set()
    for order in sorted(owned + done_for_partner, key=lambda o: (o.delivery_date, o.name)):
        if (order.name in seen
                or order.name in faites_par_le_partenaire
                or order.customer in clients_partage):
            continue
        seen.add(order.name)
        aw_orders.append(order)

    all_orders = aw_orders + done_by_partner
    so_names = [o.name for o in all_orders]
    items_map = _items_by_order(so_names)
    payments_map = _payments_by_order(so_names)
    tasks_map = _tasks_by_order(so_names)
    resolve_price = _purchase_price_resolver(
        it.item_code for rows in items_map.values() for it in rows
    )

    def build(orders, margin):
        return [
            _build_order(o, items_map.get(o.name, []), payments_map.get(o.name, []),
                         tasks_map.get(o.name, []), resolve_price, margin)
            for o in orders
        ]

    sections = [
        _build_section(
            "aqua_world", HOST_LABEL, build(aw_orders, 0.0),
            0.0, None,
            due_pos=f"Total dû par {HOST_LABEL}",
            due_neg=f"Total dû par {PARTNER_LABEL}",
        ),
        _build_section(
            "economiq", PARTNER_LABEL, build(done_by_partner, PARTNER_MARGIN),
            PARTNER_MARGIN, f"Marge à la vente : {PARTNER_MARGIN:.0%}",
            due_pos=f"Total dû par {PARTNER_LABEL}",
            due_neg=f"Total dû par {HOST_LABEL}",
        ),
    ]

    kpis = {k: flt(sum(s["totals"][k] for s in sections), PRECISION)
            for k in ("vente", "achat", "benefice", "especes", "cheques", "autres", "reste")}

    return {
        "month": f"{year:04d}-{mon:02d}",
        "label": f"{FR_MONTHS[mon - 1]} {year}",
        "period": {"from": str(start), "to": str(end)},
        "months": _month_options(),
        "currency": frappe.defaults.get_global_default("currency") or "TND",
        "kpis": kpis,
        "sections": sections,
    }
