"""Champs « dernier commentaire » sur Sales Order + reprise de l'existant (03/10/2026). Idempotent."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({"Sales Order": [
        {"fieldname": "custom_sec_commentaire", "fieldtype": "Section Break", "label": "Dernier commentaire", "insert_after": "custom_anomalie",
         "collapsible": 1, "module": "Customize erpnext"},
        {"fieldname": "custom_dernier_commentaire", "label": "Dernier commentaire", "fieldtype": "Data", "length": 140, "read_only": 1,
         "allow_on_submit": 1, "in_standard_filter": 1, "insert_after": "custom_sec_commentaire", "module": "Customize erpnext",
         "description": "Recopié automatiquement du dernier commentaire écrit sur la commande (les traces automatiques sont ignorées)."},
        {"fieldname": "custom_commentaire_le", "label": "Commentaire le", "fieldtype": "Datetime", "read_only": 1, "allow_on_submit": 1,
         "in_standard_filter": 1, "insert_after": "custom_dernier_commentaire", "module": "Customize erpnext"},
        {"fieldname": "custom_commentaire_par", "label": "Commentaire par", "fieldtype": "Data", "read_only": 1, "allow_on_submit": 1,
         "insert_after": "custom_commentaire_le", "module": "Customize erpnext"},
        {"fieldname": "custom_avec_commentaire", "label": "Avec commentaire", "fieldtype": "Check", "read_only": 1, "allow_on_submit": 1,
         "in_standard_filter": 1, "insert_after": "custom_commentaire_par", "module": "Customize erpnext"},
    ]}, ignore_validate=True, update=True)
    frappe.clear_cache(doctype="Sales Order")
    from customization_app.commentaires_commande import resynchroniser_tout
    print("ensure_commentaire_commande_fields : commandes avec commentaire =", resynchroniser_tout())
