"""Champ « Livraison par notre équipe » sur la commande client.

Certaines commandes sont livrées par nos propres techniciens plutôt que par
Aramex. Pour celles-là — et seulement celles-là — le client peut réserver un
créneau de LIVRAISON sur le portail de rendez-vous (20 minutes), au même titre
qu'une installation.

C'est une autorisation posée à la main depuis l'écran « Commandes à traiter » :
aucune machine ne la coche ni ne la décoche.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_livraison_equipe",
                    "fieldtype": "Check",
                    "label": "Livraison par notre équipe",
                    "default": "0",
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "in_standard_filter": 1,
                    "insert_after": "custom_commande_traitee",
                    "module": "Customize erpnext",
                    "description": "Autorise le client à réserver un créneau de "
                                   "livraison (20 min) sur le portail de rendez-vous.",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
