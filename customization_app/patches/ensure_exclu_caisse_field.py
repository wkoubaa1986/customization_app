"""
Champ `custom_exclu_caisse` sur Payment Entry.

Coché depuis le rapport de caisse (« paiements sur anciennes commandes ») pour
écarter des totaux une CORRECTION DE SAISIE qui n'est pas de l'argent entré ce
jour. La ligne reste listée, grisée, et réintégrable.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Payment Entry": [
                {
                    "fieldname": "custom_exclu_caisse",
                    "fieldtype": "Check",
                    "label": "Exclu de la caisse journalière",
                    "default": "0",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "print_hide": 1,
                    "insert_after": "remarks",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
