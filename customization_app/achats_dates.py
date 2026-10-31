"""Facture d'achat : la date de comptabilisation SUIT la date de la facture du fournisseur (demande 31/10/2026).

ERPNext laisse la date de comptabilisation à aujourd'hui, quelle que soit la date portée par la facture. Ici,
tant que l'utilisateur n'a pas coché « Modifier la date et l'heure » (set_posting_time), la comptabilisation
prend la date de la facture. L'écran fait pareil au changement de « Date de la facture du fournisseur »
(public/js/purchase_invoice_dates.js) ; le serveur couvre les factures nées de la caisse ou d'un import.
"""
import frappe
from frappe.utils import getdate


def date_comptabilisation(bill_date, posting_date, set_posting_time, docstatus=0):
    """La date de comptabilisation à poser, ou None si rien à changer. Fonction pure."""
    if docstatus != 0 or not bill_date or set_posting_time:
        return None
    if posting_date and getdate(posting_date) == getdate(bill_date):
        return None
    return getdate(bill_date)


def suivre_date_facture(doc, method=None):
    date = date_comptabilisation(doc.get("bill_date"), doc.get("posting_date"), doc.get("set_posting_time"), doc.docstatus)
    if date:
        doc.set_posting_time = 1
        doc.posting_date = date
