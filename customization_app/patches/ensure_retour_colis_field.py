"""
Champ « Retour colis » sur la commande client.

Coché par retour_aramex.constater_retour quand le colis Aramex revenu est
constaté (photo + BL de retour + paiement dette supprimé). Porte la pastille
« 📦 Retour colis » en vue liste et le bandeau sur la fiche — même mécanique
que l'anomalie, mais indépendant d'elle : l'anomalie se recalcule, le retour
est un FAIT qui ne doit jamais s'effacer.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_retour_colis",
                    "fieldtype": "Check",
                    "label": "Retour colis",
                    "default": "0",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "in_standard_filter": 1,
                    "insert_after": "custom_anomalie",
                    "module": "Customize erpnext",
                    "description": "Colis Aramex revenu — constaté depuis l'écran Livraisons Aramex.",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
