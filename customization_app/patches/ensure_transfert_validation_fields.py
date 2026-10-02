"""Double validation des transferts Magasin → stock d'un employé (page Stock par entrepôt, 02/10/2026).

Quatre champs sur Stock Entry : l'employé qui doit confirmer la réception (posé tant que l'écriture
est en brouillon), puis qui a validé, quand, et les écarts constatés à la réception.
Voir customization_app/stock_entrepots.py (valider_transfert).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": "custom_validation_employe",
                    "label": "Validation attendue de",
                    "fieldtype": "Link",
                    "options": "Employee",
                    "insert_after": "remarks",
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "module": "Customize erpnext",
                    "description": "Transfert Magasin → stock d'un employé : reste en brouillon jusqu'à sa confirmation.",
                },
                {
                    "fieldname": "custom_valide_par",
                    "label": "Réception confirmée par",
                    "fieldtype": "Link",
                    "options": "User",
                    "insert_after": "custom_validation_employe",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                },
                {
                    "fieldname": "custom_valide_le",
                    "label": "Réception confirmée le",
                    "fieldtype": "Datetime",
                    "insert_after": "custom_valide_par",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                },
                {
                    "fieldname": "custom_ecart_reception",
                    "label": "Écarts à la réception",
                    "fieldtype": "Small Text",
                    "insert_after": "custom_valide_le",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                },
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
