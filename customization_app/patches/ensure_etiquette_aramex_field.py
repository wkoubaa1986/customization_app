"""
Champs de la creation de bordereau par l'API Aramex, sur la commande client.

- `custom_etiquette_aramex` : l'etiquette PDF rendue par Aramex a la creation du bordereau,
  attachee a la commande. Sa presence dit « bordereau cree par l'API » : la cloture de la
  tache Livraison n'exige alors plus la photo du bordereau (c'est l'etiquette qui prouve).
- `custom_aramex_cheque_autorise` : le contre-remboursement est encaisse en ESPECES par
  Aramex, sauf mention explicite ; la case garde la trace de cette autorisation, transmise en
  instruction au transporteur.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_etiquette_aramex",
                    "fieldtype": "Attach",
                    "label": "Étiquette Aramex",
                    "no_copy": 1,
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "depends_on": "eval:doc.custom_etiquette_aramex",
                    "insert_after": "custom_statut_aramex",
                    "module": "Customize erpnext",
                    "description": "Étiquette PDF rendue par l'API Aramex à la création du bordereau.",
                },
                {
                    "fieldname": "custom_aramex_cheque_autorise",
                    "fieldtype": "Check",
                    "label": "Chèque autorisé pour le contre-remboursement Aramex",
                    "no_copy": 1,
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "depends_on": "eval:doc.custom_aramex_cheque_autorise",
                    "insert_after": "custom_etiquette_aramex",
                    "module": "Customize erpnext",
                    "description": "Par défaut Aramex n'encaisse qu'en espèces. Coché depuis le dialogue de création du bordereau.",
                },
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
