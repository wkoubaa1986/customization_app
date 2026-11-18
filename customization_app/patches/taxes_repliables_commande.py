"""Commande client : la partie « Taxes » devient repliable (demande du 18/11/2026).

La section « Taxes » (catégorie de taxe, modèle de taxes, règle de livraison, incoterm) reçoit la propriété
« repliable » : Frappe l'ouvre fermée, un clic sur son titre la déplie. Le tableau des taxes et le total des
taxes vivent dans deux sections SANS titre placées juste après — elles ne peuvent pas porter de bouton de repli :
c'est public/js/sales_order_taxes_repliables.js qui les replie et les déplie avec la section « Taxes ».
Idempotent.
"""
import frappe

SECTION = "taxes_section"


def execute():
    if not frappe.get_meta("Sales Order").has_field(SECTION):
        return "section absente"
    frappe.make_property_setter({"doctype": "Sales Order", "doctype_or_field": "DocField", "fieldname": SECTION,
                                 "property": "collapsible", "value": "1", "property_type": "Check"},
                                validate_fields_for_doctype=False)
    frappe.db.commit()
    frappe.clear_cache(doctype="Sales Order")
    return "posé"
