"""
Champ drapeau `custom_allocation_manuelle` sur Encaissement Paiement.

Posé par la caisse quand l'employé a SÉLECTIONNÉ lui-même les dettes à
consommer : le Server Script « generartion_list dette » ne doit alors PAS
régénérer l'allocation FIFO à l'enregistrement (sa garde lit ce champ).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Encaissement Paiement": [
                {
                    "fieldname": "custom_allocation_manuelle",
                    "fieldtype": "Check",
                    "label": "Allocation manuelle (caisse)",
                    "hidden": 1,
                    "default": "0",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
