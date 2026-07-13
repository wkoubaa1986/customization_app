"""
Backend du Rapport Caisse Journalière (Page custom).

Porte la logique de l'ancien Script Report vers une API whitelisted qui renvoie
du JSON structuré, rendu par une Page Desk (customize_erpnext/page/rapport_caisse_journaliere).
"""

import frappe
from frappe.utils import flt, getdate

COMPANY = "AquaWorld & Servicing"

SO_STATUS_FR = {
    "Draft": "Brouillon",
    "To Bill": "À facturer",
    "To Deliver": "À livrer",
    "To Deliver and Bill": "À livrer et facturer",
    "Completed": "Terminé",
    "Closed": "Fermé",
    "Cancelled": "Annulé",
    "On Hold": "En attente",
}


def _unbilled_sos(user, start_date, end_date):
    conds = ["so.transaction_date BETWEEN %s AND %s"]
    vals = [start_date, end_date]
    if user:
        conds.append("so.owner = %s")
        vals.append(user)
    conds.append(
        "NOT EXISTS (SELECT 1 FROM `tabTache de travail` tt "
        "WHERE tt.commande_client = so.name AND tt.docstatus < 2)"
    )
    where = " AND ".join(conds)
    return frappe.db.sql(
        f"""SELECT DISTINCT so.name, so.customer, so.transaction_date, so.grand_total, so.owner
            FROM `tabSales Order` so WHERE {where} ORDER BY so.transaction_date DESC""",
        tuple(vals), as_dict=True,
    )


def _taches(staff, start_date, end_date):
    conds = []
    vals = []
    if staff:
        conds.append("tt.custom_choix_du_staff = %s")
        vals.append(staff)
    conds.append("tt.starts_on BETWEEN %s AND %s")
    vals.extend([f"{start_date} 00:00:00", f"{end_date} 23:59:59"])
    where = " AND ".join(conds) if conds else "1=1"
    return frappe.db.sql(
        f"""SELECT DISTINCT tt.name, tt.custom_type_dintervention, tt.custom_employé,
                   tt.custom_client, tt.commande_client, tt.status, tt.starts_on
            FROM `tabTache de travail` tt WHERE {where} ORDER BY tt.starts_on DESC""",
        tuple(vals), as_dict=True,
    )


def _links(sales_order):
    sis = frappe.db.sql(
        """SELECT DISTINCT si.name, si.grand_total, si.docstatus FROM `tabSales Invoice` si
           INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
           WHERE sii.sales_order = %s AND si.docstatus < 2""",
        (sales_order,), as_dict=True,
    )
    si_names = [r.name for r in sis]

    dns = frappe.db.sql(
        """SELECT DISTINCT dn.name, dn.grand_total, dn.docstatus FROM `tabDelivery Note` dn
           INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
           WHERE dni.against_sales_order = %s AND dn.docstatus < 2""",
        (sales_order,), as_dict=True,
    )

    pes = []
    if si_names:
        ph = ",".join(["%s"] * len(si_names))
        inv_pes = frappe.db.sql(
            f"""SELECT DISTINCT pe.name, pe.posting_date, pe.paid_amount, pe.mode_of_payment, pe.reference_no
                FROM `tabPayment Entry` pe
                INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
                WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name IN ({ph})
                  AND pe.docstatus = 1 AND pe.payment_type = 'Receive'""",
            tuple(si_names), as_dict=True,
        )
        for pe in inv_pes:
            pe["link_type"] = "Invoice"
            pes.append(pe)
        direct = frappe.db.sql(
            f"""SELECT DISTINCT pe.name, pe.posting_date, pe.paid_amount, pe.mode_of_payment, pe.reference_no
                FROM `tabPayment Entry` pe
                INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
                WHERE per.reference_doctype = 'Sales Order' AND per.reference_name = %s
                  AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
                  AND pe.name NOT IN (
                      SELECT DISTINCT per2.parent FROM `tabPayment Entry Reference` per2
                      WHERE per2.reference_doctype = 'Sales Invoice' AND per2.reference_name IN ({ph}))""",
            (sales_order,) + tuple(si_names), as_dict=True,
        )
    else:
        direct = frappe.db.sql(
            """SELECT DISTINCT pe.name, pe.posting_date, pe.paid_amount, pe.mode_of_payment, pe.reference_no
               FROM `tabPayment Entry` pe
               INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
               WHERE per.reference_doctype = 'Sales Order' AND per.reference_name = %s
                 AND pe.docstatus = 1 AND pe.payment_type = 'Receive'""",
            (sales_order,), as_dict=True,
        )
    for pe in direct:
        pe["link_type"] = "Direct SO"
        pes.append(pe)

    return sis, dns, pes


def _build_order(so_name, task, so_meta):
    """Assemble un dict commande prêt pour le front."""
    sis, dns, pes = _links(so_name)

    ptt = so_meta.get("payment_terms_template")
    is_aramex = (ptt == "Livraison Aramex")
    has_dette = any((p.get("mode_of_payment") == "Dette non payée") for p in pes)

    intervention = task.get("custom_type_dintervention") if task else None
    if is_aramex and has_dette:
        intervention = "Livraison Aramex"

    total_paid = sum(flt(p.get("paid_amount")) for p in pes)
    gt = flt(so_meta.get("grand_total"))
    if total_paid < gt - 0.5:
        alert = "underpaid"
    elif total_paid > gt + 0.5:
        alert = "overpaid"
    elif not sis:
        alert = "unbilled"
    else:
        alert = None

    tache_status = task.get("status") if task else None
    task_open = (tache_status == "Open")

    # Avertissements de cohérence
    is_validated = (so_meta.get("docstatus") == 1)
    warnings = []
    if is_validated:
        draft_invoices = [s.name for s in sis if s.docstatus == 0]
        draft_bls = [d.name for d in dns if d.docstatus == 0]
        inv_valid = sum(flt(s.grand_total) for s in sis if s.docstatus == 1)
        dn_valid = sum(flt(d.grand_total) for d in dns if d.docstatus == 1)
        has_valid_invoice = inv_valid > 0.5

        # 1) Contrôle des paiements : basé sur la facture si elle existe (validée), sinon sur la commande.
        if has_valid_invoice:
            if abs(total_paid - inv_valid) > 0.5:
                warnings.append({
                    "type": "payment",
                    "message": "Total des paiements (%.3f) ≠ Total des factures TTC (%.3f)" % (total_paid, inv_valid),
                })
        else:
            if abs(total_paid - gt) > 0.5:
                warnings.append({
                    "type": "payment",
                    "message": "Total des paiements (%.3f) ≠ Total TTC de la commande (%.3f)" % (total_paid, gt),
                })

        # 2) Facture(s) liée(s) mais non validée(s) (brouillon)
        if draft_invoices:
            warnings.append({
                "type": "invoice_draft",
                "message": "Facture(s) non validée(s) : " + ", ".join(draft_invoices),
            })

        # 3) Bon(s) de livraison non validé(s) (brouillon)
        if draft_bls:
            warnings.append({
                "type": "delivery_draft",
                "message": "Bon(s) de livraison non validé(s) : " + ", ".join(draft_bls),
            })

        # 4) Total des BL validés ≠ Total des factures TTC
        if has_valid_invoice and dn_valid > 0.5 and abs(dn_valid - inv_valid) > 0.5:
            warnings.append({
                "type": "delivery",
                "message": "Total des BL validés (%.3f) ≠ Total des factures TTC (%.3f)" % (dn_valid, inv_valid),
            })

        # 5) Paiement en « Dette non payée » hors Livraison Aramex
        if has_dette and not is_aramex:
            warnings.append({
                "type": "dette",
                "message": "Commande validée avec paiement en « Dette non payée » (hors Livraison Aramex)",
            })

    # 6) Tâche de travail ouverte (indépendant de la validation)
    if task_open:
        warnings.append({
            "type": "task_open",
            "message": "Tâche de travail ouverte : à fermer, ou à déplacer si le client souhaite un autre rendez-vous.",
        })

    return {
        "sales_order": so_name,
        "customer": so_meta.get("customer"),
        "date": str(so_meta.get("transaction_date") or ""),
        "grand_total": gt,
        "status": SO_STATUS_FR.get(so_meta.get("status"), so_meta.get("status")),
        "status_raw": so_meta.get("status"),
        "intervention": intervention,
        "tache_reference": task.get("name") if task else None,
        "tache_employee": task.get("custom_employé") if task else None,
        "tache_status": tache_status,
        "task_open": task_open,
        "total_paid": total_paid,
        "alert": alert,
        "is_validated": is_validated,
        "warnings": warnings,
        "payments": [
            {
                "name": p.get("name"),
                "date": str(p.get("posting_date") or ""),
                "mode": p.get("mode_of_payment") or "",
                "amount": flt(p.get("paid_amount")),
                "reference_no": p.get("reference_no") or "",
                "link_type": p.get("link_type"),
            }
            for p in pes
        ],
        "delivery_notes": [{"name": d.name, "grand_total": flt(d.grand_total)} for d in dns],
        "sales_invoices": [{"name": s.name, "grand_total": flt(s.grand_total)} for s in sis],
    }


# Comptes de caisse surveillés pour les paiements « hors périmètre »
# (règlements d'anciennes commandes saisis pendant la période de vérification).
# « Chèques - % » exclut volontairement « Chèques sans provision - … » (impayés).
CAISSE_ACCOUNTS_LIKE = ("Espèces%", "Chèques - %", "Traite Bancaire%")


def _paiements_anciennes_commandes(d1, d2, exclude_names):
    """Payment Entries validés encaissés sur un compte de caisse pendant la
    période (date comptable OU date de création — attrape les saisies
    antidatées), et qui n'appartiennent à aucune commande du rapport :
    ce sont des règlements d'anciennes commandes à tracer dans la caisse."""
    like_conds = " OR ".join(["pe.paid_to LIKE %s"] * len(CAISSE_ACCOUNTS_LIKE))
    vals = [d1, d2, d1, d2, *CAISSE_ACCOUNTS_LIKE]
    exclude_sql = ""
    if exclude_names:
        exclude_sql = "AND pe.name NOT IN ({})".format(",".join(["%s"] * len(exclude_names)))
        vals += list(exclude_names)

    pes = frappe.db.sql(
        f"""SELECT pe.name, pe.posting_date, DATE(pe.creation) AS creation_date,
                   pe.owner, pe.party, pe.party_name, pe.mode_of_payment,
                   pe.paid_to, pe.paid_amount, pe.reference_no
            FROM `tabPayment Entry` pe
            WHERE pe.docstatus = 1
              AND pe.payment_type = 'Receive'
              AND (pe.posting_date BETWEEN %s AND %s OR DATE(pe.creation) BETWEEN %s AND %s)
              AND ({like_conds})
              {exclude_sql}
            ORDER BY pe.posting_date, pe.name""",
        tuple(vals), as_dict=True,
    )
    if not pes:
        return {"paiements": [], "par_mode": {}, "total": 0.0}

    # pièces référencées (commandes / factures) avec leur date d'origine
    names = [p.name for p in pes]
    ph = ",".join(["%s"] * len(names))
    refs = frappe.db.sql(
        f"""SELECT per.parent, per.reference_doctype, per.reference_name,
                   so.transaction_date AS so_date, si.posting_date AS si_date
            FROM `tabPayment Entry Reference` per
            LEFT JOIN `tabSales Order` so
                   ON per.reference_doctype = 'Sales Order' AND so.name = per.reference_name
            LEFT JOIN `tabSales Invoice` si
                   ON per.reference_doctype = 'Sales Invoice' AND si.name = per.reference_name
            WHERE per.parent IN ({ph})""",
        tuple(names), as_dict=True,
    )
    refs_by_pe = {}
    for r in refs:
        refs_by_pe.setdefault(r.parent, []).append({
            "doctype": r.reference_doctype,
            "name": r.reference_name,
            "date": str(r.so_date or r.si_date or ""),
        })

    # qui a saisi le paiement (nom lisible)
    owners = {p.owner for p in pes}
    owner_names = {u.name: u.full_name for u in frappe.get_all(
        "User", filters={"name": ("in", list(owners))}, fields=["name", "full_name"])}

    paiements, par_mode = [], {}
    for p in pes:
        par_mode[p.mode_of_payment or "—"] = flt(par_mode.get(p.mode_of_payment or "—", 0)) \
            + flt(p.paid_amount)
        paiements.append({
            "name": p.name,
            "date": str(p.posting_date or ""),
            "creation_date": str(p.creation_date or ""),
            "antidate": bool(p.posting_date and p.creation_date
                             and str(p.posting_date) != str(p.creation_date)),
            "saisi_par": owner_names.get(p.owner) or p.owner,
            "customer": p.party,
            "customer_name": p.party_name or p.party,
            "mode": p.mode_of_payment or "",
            "compte": p.paid_to,
            "amount": flt(p.paid_amount),
            "reference_no": p.reference_no or "",
            "pieces": refs_by_pe.get(p.name, []),
        })
    return {
        "paiements": paiements,
        "par_mode": {m: flt(v) for m, v in par_mode.items()},
        "total": flt(sum(x["amount"] for x in paiements)),
    }


@frappe.whitelist()
def get_data(d1, d2):
    """Renvoie le rapport de caisse journalière groupé par employé, pour [d1, d2]."""
    start_date, end_date = d1, d2

    employees = frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email
           FROM `tabEmployee` e WHERE e.company = %s""",
        COMPANY, as_dict=True,
    )
    users = frappe.get_all(
        "User",
        filters={"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
        fields=["name", "full_name"],
    )
    modes = [m.name for m in frappe.get_all("Mode of Payment", fields=["name"], order_by="name")]

    seen_so = set()
    processed_users = set()
    result_employees = []
    grand_par_mode = {}

    def process(display_name, employee_id, user_id):
        tasks = _taches(employee_id, start_date, end_date) if employee_id else []
        tasks_by_so = {}
        for t in tasks:
            if t.get("commande_client"):
                tasks_by_so.setdefault(t["commande_client"], t)

        unbilled = _unbilled_sos(user_id, start_date, end_date)
        so_names = list(tasks_by_so.keys()) + [
            so["name"] for so in unbilled if so["name"] not in tasks_by_so
        ]

        orders = []
        totaux = {}
        for so_name in so_names:
            if so_name in seen_so:
                continue
            seen_so.add(so_name)
            meta = frappe.db.get_value(
                "Sales Order", so_name,
                ["customer", "transaction_date", "grand_total", "status", "payment_terms_template", "docstatus"],
                as_dict=True,
            )
            if not meta:
                continue
            order = _build_order(so_name, tasks_by_so.get(so_name), meta)
            orders.append(order)
            for p in order["payments"]:
                totaux[p["mode"]] = flt(totaux.get(p["mode"], 0)) + flt(p["amount"])

        if not orders:
            return

        for m, v in totaux.items():
            grand_par_mode[m] = flt(grand_par_mode.get(m, 0)) + flt(v)

        # tri : commandes en alerte / tâche open d'abord, puis par date décroissante
        orders.sort(key=lambda o: (0 if (o["task_open"] or o["alert"]) else 1, o["date"]), reverse=False)

        result_employees.append({
            "employe": display_name,
            "orders": orders,
            "totaux_par_mode": totaux,
            "total": flt(sum(totaux.values())),
            "nb_commandes": len(orders),
        })

    for emp in employees:
        process(emp.employee_name, emp.employee_id, emp.user_email)
        if emp.user_email:
            processed_users.add(emp.user_email)

    for u in users:
        if u.name in processed_users:
            continue
        processed_users.add(u.name)
        process(u.full_name, None, u.name)

    # récap : modes réellement utilisés
    used_modes = [m for m in modes if flt(grand_par_mode.get(m, 0)) != 0]
    # inclure d'éventuels modes hors liste standard
    for m in grand_par_mode:
        if m and m not in used_modes and flt(grand_par_mode[m]) != 0:
            used_modes.append(m)

    recap_par_employe = []
    for e in result_employees:
        if flt(e["total"]) != 0:
            recap_par_employe.append({
                "employe": e["employe"],
                "total": e["total"],
                "par_mode": {m: flt(e["totaux_par_mode"].get(m, 0)) for m in used_modes},
            })
    recap_par_employe.sort(key=lambda x: x["total"], reverse=True)

    # paiements encaissés sur la période mais hors commandes du rapport
    # (= règlements d'anciennes commandes à tracer dans la caisse du jour)
    seen_pes = set()
    for e in result_employees:
        for o in e["orders"]:
            for p in o["payments"]:
                seen_pes.add(p["name"])
    anciens = _paiements_anciennes_commandes(start_date, end_date, seen_pes)

    return {
        "periode": {"d1": start_date, "d2": end_date},
        "anciens": anciens,
        "modes": used_modes,
        "employees": result_employees,
        "recap": {
            "modes": used_modes,
            "par_employe": recap_par_employe,
            "par_mode": {m: flt(grand_par_mode.get(m, 0)) for m in used_modes},
            "grand_total": flt(sum(grand_par_mode.values())),
        },
    }
