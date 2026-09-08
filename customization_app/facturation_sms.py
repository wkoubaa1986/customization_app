"""Notification transactionnelle de disponibilité d'une facture mensuelle."""
import frappe
from frappe.utils import getdate


def message_facture(doc):
    date = getdate(doc.posting_date)
    numero = f"FAC-{date:%m-%Y}-{str(doc.custom_numero_facture).zfill(5)}"
    return (
        f"Bonjour, votre facture Aqua World {numero} pour {date:%m/%Y} "
        f"est prete. Montant TTC : {doc.grand_total:.3f} TND. "
        "Consultez votre e-mail pour la facture et le detail des livraisons. "
        "Aqua World & Servicing."
    )


@frappe.whitelist()
def enqueue_invoice_ready(invoice):
    frappe.only_for('System Manager')
    doc = frappe.get_doc('Sales Invoice', invoice)
    doc.check_permission('read')
    if doc.docstatus != 1:
        frappe.throw('La facture doit etre validee avant notification.')
    frappe.enqueue(
        'customization_app.facturation_sms.send_invoice_ready',
        invoice=invoice, enqueue_after_commit=True,
    )


def send_invoice_ready(invoice):
    # Un worker dev ne doit jamais contacter la passerelle de production.
    if frappe.conf.developer_mode:
        return {'skipped': 'developer_mode'}
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        traiter_numero_tel, envoyer_sms_verifie,
    )
    doc = frappe.get_doc('Sales Invoice', invoice)
    if doc.docstatus != 1:
        return {'skipped': 'invoice_not_submitted'}
    phones = frappe.db.sql('''
        SELECT DISTINCT cp.phone FROM `tabContact Phone` cp
        JOIN `tabDynamic Link` dl ON dl.parent=cp.parent AND dl.parenttype='Contact'
        WHERE dl.link_doctype='Customer' AND dl.link_name=%s
        ORDER BY cp.phone
    ''', doc.customer, pluck=True)
    numbers = traiter_numero_tel(','.join(phones))
    if not numbers:
        frappe.throw(f'Aucun mobile valide pour la facture {invoice}')
    message = message_facture(doc)
    for number in numbers:
        marker = f'SMS facture mensuelle {invoice} / {number}'
        # Le verrou empêche deux workers de notifier simultanément le même destinataire.
        with frappe.cache.lock(f'invoice-ready:{invoice}:{number}', timeout=90):
            if frappe.db.exists('Comment', {'reference_doctype': 'Sales Invoice',
                    'reference_name': invoice, 'content': ['like', marker + '%']}):
                continue
            # Pas de relance réseau automatique : un timeout peut cacher un envoi accepté.
            response = envoyer_sms_verifie('216' + number, message, tentatives=1)
            doc.add_comment('Info', marker + '<br>' + frappe.utils.escape_html(message))
            frappe.db.commit()
    return {'recipients': len(numbers)}
