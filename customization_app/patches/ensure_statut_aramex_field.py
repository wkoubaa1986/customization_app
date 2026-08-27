"""
Champ « Statut Aramex » sur la commande client.

Le statut du colis vit dans Suivi Aramex, hors de portée des filtres de la vue
liste : ce champ le MATÉRIALISE sur la commande (écrit par
traitement_commandes.actualiser_statuts_aramex — bouton « 🚚 Actualiser
Aramex » de la liste, et synchro du soir). in_standard_filter : c'est lui qui
donne le filtre « Statut Aramex » dans la barre des filtres.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_statut_aramex",
                    "fieldtype": "Data",
                    "label": "Statut Aramex",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "in_standard_filter": 1,
                    "insert_after": "custom_bordereau_aramex",
                    "module": "Customize erpnext",
                    "description": "Dernier statut connu du colis Aramex — matérialisé pour filtrer la liste (Livré, Créé, Statut en transit, Returned, Introuvable chez Aramex…).",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
