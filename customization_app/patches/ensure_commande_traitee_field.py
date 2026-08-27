"""
Champ « Commande traitée » sur la commande client.

Coché depuis l'écran Traitement des commandes (page traitement-commandes) quand
quelqu'un a fini de s'occuper de la commande : appel passé, tâche planifiée,
livraison suivie… Un FAIT posé à la main — contrairement à custom_anomalie qui
se recalcule en cron, celui-ci n'est jamais requalifié par une machine.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_commande_traitee",
                    "fieldtype": "Check",
                    "label": "Commande traitée",
                    "default": "0",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "insert_after": "custom_appel_2_sans_reponse",
                    "module": "Customize erpnext",
                    "description": "Traitement terminé — coché depuis l'écran Traitement des commandes.",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
