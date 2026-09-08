"""Compléter les avances des commandes avec le crédit disponible du client."""
import frappe
from frappe.utils import flt


def plan_credits(entries, remaining, precision=3):
    result = []
    remaining = flt(remaining, precision)
    for entry in entries:
        allocated = min(remaining, flt(entry.amount, precision))
        if allocated > 0:
            result.append((entry, allocated))
            remaining = flt(remaining - allocated, precision)
        if remaining <= 0:
            break
    return result


@frappe.whitelist()
def apply_available_credits(invoice):
    frappe.only_for('System Manager')
    doc = frappe.get_doc('Sales Invoice', invoice)
    doc.check_permission('write')
    if doc.docstatus != 0:
        frappe.throw('Les crédits doivent être affectés avant validation de la facture.')
    precision = doc.precision('total_advance') or 3
    remaining = flt(doc.grand_total - doc.total_advance, precision)
    if remaining <= 0:
        return 0
    # Même périmètre comptable que la facture, aucun crédit de dette ni change implicite.
    eligible = frappe.db.sql('''
        SELECT name, posting_date FROM `tabPayment Entry`
        WHERE docstatus=1 AND payment_type='Receive' AND party_type='Customer'
          AND party=%s AND company=%s AND paid_from=%s AND paid_from_account_currency=%s
          AND paid_to_account_currency=%s AND unallocated_amount>0
          AND COALESCE(mode_of_payment,'')!='Dette non payée' AND paid_to!='Dettes - A&S'
        ORDER BY posting_date, creation, name FOR UPDATE
    ''', (doc.customer, doc.company, doc.debit_to, doc.currency, doc.currency), as_dict=True)
    rank = {row.name: index for index, row in enumerate(eligible)}
    # ERPNext fournit les références et taux nécessaires à la réconciliation native.
    existing = {(row.reference_name, row.reference_row or '') for row in doc.advances}
    entries = [row for row in doc.get_advance_entries(include_unallocated=True)
               if row.reference_type == 'Payment Entry' and not row.reference_row
               and row.reference_name in rank and (row.reference_name, '') not in existing]
    entries.sort(key=lambda row: rank[row.reference_name])
    plan = plan_credits(entries, remaining, precision)
    if not plan:
        return 0
    # Préserver la première allocation et empêcher validate de remplacer les crédits ajoutés.
    doc.allocate_advances_automatically = 0
    for entry, allocated in plan:
        doc.append('advances', {
            'reference_type': entry.reference_type, 'reference_name': entry.reference_name,
            'reference_row': entry.reference_row, 'remarks': entry.remarks,
            'advance_amount': entry.amount, 'allocated_amount': allocated,
            'ref_exchange_rate': entry.exchange_rate, 'difference_posting_date': doc.posting_date,
            'account': entry.get('paid_to') or entry.get('paid_from'),
        })
    doc.calculate_taxes_and_totals()
    doc.save()
    return flt(sum(value for _, value in plan), precision)
