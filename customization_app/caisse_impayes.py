"""Règlement en espèces d'un chèque impayé, sans modifier le paiement d'origine.

reference_no désigne la Payment Entry d'origine. ERPNext efface party sur les
transferts internes : Relance et le rapport résolvent le client via cette référence.
"""
import math

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.caisse_encaissement_dettes import ROLES

IMPAYES = "Chèques sans provision - A&S"
ESPECES = "Espèces - A&S"


def _montant(montant, restant):
    try:
        valeur = float(montant)
    except (TypeError, ValueError):
        valeur = 0
    if not math.isfinite(valeur) or round(valeur, 3) <= 0:
        frappe.throw(_("Saisissez un montant positif."))
    valeur = round(valeur, 3)
    if valeur > round(restant, 3):
        frappe.throw(_("Le montant dépasse le solde restant. Actualisez la liste."))
    return valeur


def _soldes(client, piece=None):
    return frappe.db.sql(
        """SELECT pe.name, pe.company, pe.party AS customer, pe.party_name,
                  pe.posting_date, pe.reference_no,
                  ROUND(SUM(gl.debit - gl.credit) - COALESCE(reg.montant, 0), 3) AS restant
           FROM `tabPayment Entry` pe
           JOIN `tabGL Entry` gl ON gl.voucher_type = 'Payment Entry'
                AND gl.voucher_no = pe.name AND gl.is_cancelled = 0
                AND gl.account = %(impayes)s
           LEFT JOIN (
               SELECT tr.reference_no, SUM(g.credit - g.debit) AS montant
               FROM `tabPayment Entry` tr
               JOIN `tabGL Entry` g ON g.voucher_type = 'Payment Entry'
                    AND g.voucher_no = tr.name AND g.is_cancelled = 0
                    AND g.account = %(impayes)s
               WHERE tr.docstatus = 1 AND tr.payment_type = 'Internal Transfer'
                 AND tr.paid_from = %(impayes)s AND tr.paid_to = %(especes)s
               GROUP BY tr.reference_no
           ) reg ON reg.reference_no = pe.name
           WHERE pe.docstatus = 1 AND pe.party_type = 'Customer' AND pe.party = %(client)s
             AND (%(piece)s IS NULL OR pe.name = %(piece)s)
           GROUP BY pe.name
           HAVING restant > 0
           ORDER BY pe.posting_date, pe.name""",
        dict(client=client, piece=piece, impayes=IMPAYES, especes=ESPECES), as_dict=True)


def _restant_verrouille(piece):
    # Lectures courantes : même sous REPEATABLE READ, une requête ayant attendu
    # le verrou doit voir les règlements validés entre-temps, pas son snapshot.
    origine = frappe.db.sql(
        """SELECT debit, credit FROM `tabGL Entry`
           WHERE voucher_type = 'Payment Entry' AND voucher_no = %s
             AND account = %s AND is_cancelled = 0 FOR UPDATE""", (piece, IMPAYES))
    reglements = frappe.db.sql(
        """SELECT gl.debit, gl.credit FROM `tabGL Entry` gl
           JOIN `tabPayment Entry` pe ON gl.voucher_no = pe.name
           WHERE gl.voucher_type = 'Payment Entry' AND gl.account = %s
             AND gl.is_cancelled = 0 AND pe.docstatus = 1
             AND pe.payment_type = 'Internal Transfer' AND pe.paid_from = %s
             AND pe.paid_to = %s AND pe.reference_no = %s FOR UPDATE""",
        (IMPAYES, IMPAYES, ESPECES, piece))
    return round(sum(flt(d) - flt(c) for d, c in origine + reglements), 3)


@frappe.whitelist()
def pieces(client):
    frappe.only_for(ROLES)
    frappe.get_doc("Customer", client).check_permission("read")
    return _soldes(client)


@frappe.whitelist()
def encaisser(client, piece, montant):
    frappe.only_for(ROLES)
    # Sérialiser deux validations de la même pièce avant de relire son solde.
    frappe.db.sql("SELECT name FROM `tabPayment Entry` WHERE name = %s FOR UPDATE", piece)
    origine = frappe.get_doc("Payment Entry", piece)
    origine.check_permission("read")
    if origine.docstatus != 1 or origine.party_type != "Customer" or origine.party != client:
        frappe.throw(_("La pièce ne correspond pas à un impayé validé de ce client."))
    restant = _restant_verrouille(piece)
    if restant <= 0:
        frappe.throw(_("Cette pièce n'a plus de solde à encaisser. Actualisez la liste."))
    valeur = _montant(montant, restant)
    devise = frappe.get_cached_value("Company", origine.company, "default_currency")
    for compte in (IMPAYES, ESPECES):
        meta = frappe.get_doc("Account", compte)
        if meta.company != origine.company or meta.account_currency != devise or meta.is_group or meta.disabled:
            frappe.throw(_("Le compte {0} doit être actif, dans la société et sa devise.").format(compte))
    paiement = frappe.get_doc({
        "doctype": "Payment Entry", "payment_type": "Internal Transfer",
        "company": origine.company, "posting_date": nowdate(),
        "paid_from": IMPAYES, "paid_to": ESPECES,
        "paid_amount": valeur, "received_amount": valeur,
        "source_exchange_rate": 1, "target_exchange_rate": 1,
        "mode_of_payment": "Espèces", "reference_no": piece,
        "reference_date": nowdate(), "custom_remarks": 1,
        "remarks": _("Règlement en espèces du chèque impayé {0} — client {1}").format(piece, client),
    })
    paiement.insert()
    paiement.submit()
    return {"name": paiement.name, "restant": round(restant - valeur, 3)}
