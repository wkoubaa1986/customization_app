"""Reprendre une seule fois la ristourne des commandes sur leur facture."""
import json

import frappe
from frappe import _
from frappe.utils import flt

STATE_FIELD = "custom_ristourne_commandes"


def apply_order_discounts(doc, method=None):
    if doc.get("is_return") or doc.get("is_pos") or doc.docstatus == 2:
        return

    # Une valeur envoyée par le navigateur ne doit pas transformer une remise
    # manuelle en remise automatique : lire l'état enregistré.
    previous = doc.get_doc_before_save()
    state = json.loads(previous.get(STATE_FIELD) or "{}") if previous else {}
    precision = doc.precision("discount_amount") or 3
    automatic = bool(state) and (
        flt(doc.discount_amount, precision) == flt(state["amount"], precision)
        and not flt(doc.additional_discount_percentage)
        and doc.apply_discount_on == "Grand Total"
    )
    orders = sorted({row.sales_order for row in doc.get("items") if row.get("sales_order")})
    eligible = []
    for order in orders:
        # Verrou commun aux factures concurrentes d'une même commande.
        rows = frappe.db.sql(
            """SELECT discount_amount, currency, company FROM `tabSales Order`
               WHERE name=%s AND docstatus=1 FOR UPDATE""", (order,), as_dict=True
        )
        if not rows or not flt(rows[0].discount_amount):
            continue
        # Un brouillon ultérieur sans ristourne ne dépossède pas celui qui
        # la porte déjà. Une commande nouvellement ajoutée vérifie toutes
        # les autres factures, même si notre brouillon est plus ancien.
        owned_since = doc.creation if automatic and order in state.get("orders", []) else None
        used = frappe.db.sql(
            """SELECT si.name FROM `tabSales Invoice Item` sii
               JOIN `tabSales Invoice` si ON si.name=sii.parent
               WHERE sii.sales_order=%s AND si.docstatus<2 AND si.name!=%s
               AND si.is_return=0
               AND (%s IS NULL OR si.creation<%s
                    OR (si.creation=%s AND si.name<%s))
               LIMIT 1 FOR UPDATE""",
            (order, doc.name or "", owned_since,
             doc.creation, doc.creation, doc.name or "")
        )
        if used:
            continue
        if rows[0].currency != doc.currency or rows[0].company != doc.company:
            frappe.throw(_("La ristourne de la commande {0} nécessite la même devise et société.").format(order))
        eligible.append((order, flt(rows[0].discount_amount)))

    amount = flt(sum(value for _, value in eligible), precision)
    manual = not automatic and (
        flt(doc.discount_amount) or flt(doc.additional_discount_percentage)
    )
    doc.set(STATE_FIELD, None)
    if manual:
        if amount > 0:
            frappe.msgprint(
                _("Ristourne des commandes : {0} {1}. La remise saisie est conservée.")
                .format(amount, doc.currency), indicator="blue", alert=True
            )
        return
    if amount > 0 or automatic:
        doc.apply_discount_on = "Grand Total"
        doc.additional_discount_percentage = 0
        doc.discount_amount = amount
        if amount > 0:
            doc.set(STATE_FIELD, json.dumps({"amount": amount, "orders": [x[0] for x in eligible]}))
        doc.calculate_taxes_and_totals()
        # Le contrôleur a calculé les avances avant ce hook validate.
        if doc.get("allocate_advances_automatically"):
            doc.set_advances()
            doc.calculate_taxes_and_totals()
        doc.set_payment_schedule()
