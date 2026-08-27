"""
Champ « N° bordereau Aramex » sur la commande client.

Saisi depuis l'écran Traitement des commandes quand la commande part en
livraison Aramex et qu'aucun paiement ne porte encore le bordereau : le numéro
vit alors sur la COMMANDE, et le suivi (Suivi Aramex, interrogation, SMS)
fonctionne sans attendre le paiement. Quand un paiement sur le compte Aramex
existe, son reference_no (« Aramex N: … ») reste la référence maîtresse —
l'écran écrit alors dans les deux.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "custom_bordereau_aramex",
                    "fieldtype": "Data",
                    "label": "N° bordereau Aramex",
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "depends_on": "eval:doc.payment_terms_template=='Livraison Aramex' || doc.custom_bordereau_aramex",
                    "insert_after": "custom_commande_traitee",
                    "module": "Customize erpnext",
                    "description": "Numéro de suivi Aramex (8 à 20 chiffres) — saisi depuis l'écran Traitement des commandes.",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
