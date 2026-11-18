"""Plusieurs paiements LE MÊME JOUR sur un document de vente (demande du 18/11/2026).

« Les échéances ne servent à rien » : dans ce système, l'échéancier d'une commande n'est pas un plan de
paiement mais le RELEVÉ des règlements — une ligne par moyen de paiement (espèces + carte, deux chèques…),
presque toujours du jour même. Or ERPNext refuse deux lignes à la même date (« Rows with duplicate due dates
in other rows were found »). Jusqu'ici chaque écran contournait ce refus en DÉCALANT la date d'un jour par
ligne (J, J+1, J+2…), ce qui donnait des échéances fausses.

Ici, le contrôle d'ERPNext (`AccountsController.validate_payment_schedule_dates`) est remplacé pour les
documents de VENTE : la seule vérification abandonnée est celle des dates en double. Sont gardées, à
l'identique : la cohérence date d'escompte / date d'échéance, et le refus d'une échéance antérieure à la date
d'une commande ou d'un devis. Les documents d'achat gardent le contrôle d'origine, sans changement.

Rien, dans ce système, ne distingue deux lignes par leur date : les paiements sont rattachés à leur ligne par
son identifiant (`custom_row_uid`, Server Script « fill payment schedule row uid »).

Appliqué au chargement de l'application (`customization_app/__init__.py`), comme la surcharge de
`get_item_details` ; idempotent.
"""
from __future__ import annotations

DOCUMENTS_DE_VENTE = ("Sales Order", "Sales Invoice", "Quotation")
MARQUE = "_origine_avant_echeances_meme_jour"


def dates_refusees(doctype: str, date_document, echeances: list) -> list:
    """Les numéros (idx) des lignes dont l'échéance tombe AVANT la date d'une commande ou d'un devis — le seul
    refus de date gardé sur un document de vente. Deux lignes du même jour ne sont pas une erreur. PURE.
    `echeances` : [{"idx", "due_date"}], dates comparables entre elles (date ou texte AAAA-MM-JJ)."""
    if doctype not in ("Sales Order", "Quotation") or not date_document:
        return []
    return [e.get("idx") for e in (echeances or [])
            if e.get("due_date") and str(e["due_date"])[:10] < str(date_document)[:10]]


def _controle_vente(self):
    """Le contrôle d'ERPNext, sans le refus des dates en double."""
    import frappe
    from frappe import _
    from frappe.utils import getdate

    if self.doctype == "Sales Invoice" and self.get("is_pos"):
        return
    lignes = self.get("payment_schedule") or []
    for d in lignes:
        d.validate_from_to_dates("discount_date", "due_date")
    avant = dates_refusees(self.doctype, getdate(self.get("transaction_date")) if self.get("transaction_date") else None,
                           [{"idx": d.idx, "due_date": getdate(d.due_date) if d.due_date else None} for d in lignes])
    if avant:
        frappe.throw(_("Row {0}: Due Date in the Payment Terms table cannot be before Posting Date").format(avant[0]))


def appliquer() -> bool:
    """Remplace le contrôle sur `AccountsController` (toutes les classes de documents en héritent, y compris
    celles que d'autres applications surchargent). -> True si la surcharge est en place."""
    from erpnext.controllers.accounts_controller import AccountsController

    if getattr(AccountsController, MARQUE, None):
        return True
    origine = AccountsController.validate_payment_schedule_dates

    def validate_payment_schedule_dates(self):
        if self.doctype in DOCUMENTS_DE_VENTE:
            return _controle_vente(self)
        return origine(self)

    setattr(AccountsController, MARQUE, origine)
    AccountsController.validate_payment_schedule_dates = validate_payment_schedule_dates
    return True
