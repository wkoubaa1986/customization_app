"""
Champ `custom_reglement_caisse` sur Payment Entry.

Posé par le règlement des factures d'achat depuis la caisse journalière
(`caisse_depenses.payer_factures`). Le rapport de caisse le relit pour faire
entrer ces paiements dans les dépenses du jour — leur part espèces pèse sur le
solde théorique de la clôture. Sans ce drapeau, il faudrait deviner quels
règlements fournisseurs sont sortis de la caisse, et on réécrirait des années
d'historique.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Payment Entry": [
                {
                    "fieldname": "custom_reglement_caisse",
                    "fieldtype": "Check",
                    "label": "Règlement saisi en caisse journalière",
                    "default": "0",
                    "read_only": 1,
                    "print_hide": 1,
                    "insert_after": "custom_exclu_caisse",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
