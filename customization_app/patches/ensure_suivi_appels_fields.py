"""
Champs d'horodatage des appels de confirmation sur les commandes WEB.

Renseignés par les boutons du formulaire via
customization_app.suivi_appels.enregistrer_appel. En lecture seule : leur
valeur ne doit venir que du bouton, pour que l'horodatage reste fiable.

allow_on_submit est indispensable — les commandes WEB sont soumises au moment
où les appels ont lieu.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.suivi_appels import CHAMPS


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": CHAMPS[1],
                    "label": "1er appel sans réponse",
                    "fieldtype": "Datetime",
                    "insert_after": "custom_anomalie",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                    "depends_on": "eval:doc.woocommerce_id",
                    "description": "Horodaté par le bouton du formulaire, une seule fois.",
                },
                {
                    "fieldname": CHAMPS[2],
                    "label": "2e appel sans réponse",
                    "fieldtype": "Datetime",
                    "insert_after": CHAMPS[1],
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                    "depends_on": f"eval:doc.{CHAMPS[1]}",
                    "description": "Disponible une fois le 1er appel enregistré.",
                },
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
    print("[ensure_suivi_appels_fields] champs de suivi des appels en place.")
