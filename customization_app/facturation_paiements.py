"""Récapitulatif des montants affectés à une facture, par moyen de paiement."""
from collections import defaultdict
from decimal import Decimal
from html import escape
import frappe


def amount(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.001'))


def summarize(payments, outstanding, adjustments=0):
    grouped = defaultdict(Decimal)
    debt = Decimal('0')
    for row in payments:
        value = amount(row.get('allocated_amount'))
        if row.get('payment_type') == 'Pay':
            value = -value
        if row.get('mode_of_payment') == 'Dette non payée' or row.get('paid_to') == 'Dettes - A&S':
            debt += value
        else:
            grouped[row.get('mode_of_payment') or 'Autre paiement'] += value
    return {'payments': dict(sorted(grouped.items())), 'received': sum(grouped.values(), Decimal('0')),
            'debt': debt, 'outstanding': amount(outstanding),
            'remaining': debt + amount(outstanding), 'adjustments': amount(adjustments)}


def render_summary(reference, total, summary, prior_credit=0):
    def money(value):
        return f'{amount(value):,.3f}'.replace(',', ' ').replace('.', ',') + ' TND'
    rows = [(mode, value) for mode, value in summary['payments'].items()]
    rows += [('Total des paiements reçus affectés à cette facture', summary['received'])]
    if amount(prior_credit):
        rows.append(('Dont crédit antérieur utilisé (déjà inclus ci-dessus)', amount(prior_credit)))
    if summary['adjustments']:
        rows.append(('Autres écritures de règlement', summary['adjustments']))
    if summary['debt']:
        rows.append(('Dette non payée', summary['debt']))
    if summary['outstanding']:
        rows.append(('Solde de facture non couvert', summary['outstanding']))
    rows.append(('Total restant à régler', summary['remaining']))
    body = ''.join(f'<tr><td>{escape(label)}</td><td style="text-align:right">{money(value)}</td></tr>' for label, value in rows)
    return (f'<h3>Récapitulatif des paiements — {escape(reference)}</h3>'
            f'<p>Total TTC de la facture : <strong>{money(total)}</strong></p>'
            '<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">'
            f'{body}</table><p>Seuls les montants affectés à cette facture sont présentés. '
            'La dette non payée est exclue des paiements reçus.</p>')


@frappe.whitelist()
def invoice_payment_summary(invoice, prior_credit=0):
    doc = frappe.get_doc('Sales Invoice', invoice)
    doc.check_permission('read')
    payments = frappe.db.sql('''
        SELECT pe.mode_of_payment, pe.payment_type, pe.paid_to, per.allocated_amount
        FROM `tabPayment Entry Reference` per
        JOIN `tabPayment Entry` pe ON pe.name=per.parent
        WHERE pe.docstatus=1 AND per.reference_doctype='Sales Invoice'
          AND per.reference_name=%s
    ''', invoice, as_dict=True)
    adjustments = frappe.db.sql('''
        SELECT COALESCE(SUM(jea.credit_in_account_currency-jea.debit_in_account_currency),0)
        FROM `tabJournal Entry Account` jea
        JOIN `tabJournal Entry` je ON je.name=jea.parent
        WHERE je.docstatus=1 AND jea.reference_type='Sales Invoice'
          AND jea.reference_name=%s AND jea.party_type='Customer' AND jea.party=%s
    ''', (invoice, doc.customer))[0][0]
    date = frappe.utils.getdate(doc.posting_date)
    reference = f'FAC-{date:%m-%Y}-{str(doc.custom_numero_facture).zfill(5)}'
    return render_summary(reference, doc.grand_total, summarize(payments, doc.outstanding_amount, adjustments), prior_credit=prior_credit)
