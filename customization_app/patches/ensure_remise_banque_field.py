"""
Champ `custom_remise_en_banque` (Date) sur Payment Entry.

Trace la SORTIE d'un chèque / d'une traite du portefeuille de la caisse : la
date à laquelle la pièce a été physiquement remise en banque. C'est le maillon
qui rend possible l'avant/après du rapprochement de la clôture globale :
portefeuille ouverture + reçus − remis = attendus en portefeuille.
Marqué depuis le bouton « Remise en banque » de la caisse (direction).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Payment Entry": [
                {
                    "fieldname": "custom_remise_en_banque",
                    "fieldtype": "Date",
                    "label": "Remis en banque le",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "print_hide": 1,
                    "insert_after": "custom_exclu_caisse",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
