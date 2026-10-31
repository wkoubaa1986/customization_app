"""Payment Schedule.mode_of_payment : plus obligatoire sur les achats (31/10/2026).

L'ancien Property Setter `reqd = 1` valait pour tous les parents ; une facture d'achat dont l'échéancier est
généré par ERPNext échouait. Désormais obligatoire à l'écran sur les ventes seulement (mandatory_depends_on sur
le parent) et contrôlé côté serveur par echeancier.exiger_mode_de_paiement. Idempotent.
"""
import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    make_property_setter("Payment Schedule", "mode_of_payment", "reqd", 0, "Check", validate_fields_for_doctype=False)
    make_property_setter("Payment Schedule", "mode_of_payment", "mandatory_depends_on",
                         'eval:["Sales Order", "Sales Invoice"].includes(parent.doctype)', "Code",
                         validate_fields_for_doctype=False)
    frappe.clear_cache(doctype="Payment Schedule")
