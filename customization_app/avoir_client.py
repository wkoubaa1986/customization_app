"""
Création d'un avoir client à partir d'un Sales Order.

Reproduit exactement l'écriture d'avoir saisie manuellement :
  - CRÉDIT  « Débiteurs - A&S » (party = client)  → le client gagne un crédit (avoir).
  - DÉBIT   « Compte temporaire - compte  d'overture - A&S »
  - Journal Entry simple (voucher_type "Journal Entry"), référence « Avoir <client> ».

C'est un CRÉDIT CLIENT FLOTTANT (aucun lien commande) : il est ensuite appliqué comme
paiement par la logique existante lorsqu'on ajoute une ligne d'échéancier au mode
« Avoir client » du même montant (détection par montant dans
`backfill_selective_payment.py` et le script « re-generate payment after sales order »).
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

RECEIVABLE_ACCOUNT = "Débiteurs - A&S"
# Nom exact tel qu'en base (double espace volontaire dans « compte  d'overture »).
DEFAULT_DEBIT_ACCOUNT = "Compte temporaire - compte  d'overture - A&S"


@frappe.whitelist()
def create_avoir_from_sales_order(sales_order, amount, debit_account=None,
                                  reference=None, remark=None, posting_date=None):
    """Crée (et soumet) un Journal Entry d'avoir pour le client de la commande."""
    if not sales_order:
        frappe.throw(_("Commande manquante."))

    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus != 1:
        frappe.throw(_("La commande doit être validée pour créer un avoir."))

    amount = flt(amount, 3)
    if amount <= 0:
        frappe.throw(_("Le montant de l'avoir doit être supérieur à 0."))

    customer = so.customer
    debit_account = debit_account or DEFAULT_DEBIT_ACCOUNT
    posting_date = posting_date or nowdate()
    reference = (reference or ("Avoir " + (customer or ""))).strip()

    if not frappe.db.exists("Account", debit_account):
        frappe.throw(_("Compte de contrepartie introuvable : {0}").format(debit_account))

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = so.company
    je.posting_date = posting_date
    je.cheque_no = reference           # « Numéro de référence » (ex. « Avoir Technolab »)
    je.cheque_date = posting_date
    je.user_remark = (remark or reference)
    je.is_opening = "No"

    # Ligne 1 : crédit du client → il obtient un avoir réutilisable.
    je.append("accounts", {
        "account": RECEIVABLE_ACCOUNT,
        "party_type": "Customer",
        "party": customer,
        "credit_in_account_currency": amount,
    })
    # Ligne 2 : débit du compte de contrepartie (compte temporaire).
    je.append("accounts", {
        "account": debit_account,
        "debit_in_account_currency": amount,
    })

    je.insert(ignore_permissions=True)
    je.submit()

    return {
        "journal_entry": je.name,
        "customer": customer,
        "amount": amount,
        "advance_paid": flt(so.advance_paid, 3),
        "over_paid": amount > flt(so.advance_paid, 3),
    }
