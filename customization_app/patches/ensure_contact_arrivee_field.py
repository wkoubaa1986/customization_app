"""
Champ « Personne à contacter à l'arrivée » sur la Tache de travail.

Saisi par le client lors d'une réservation en ligne (/rdv) : le technicien
sait qui demander en arrivant, et sur quel numéro appeler si ce n'est pas le
titulaire du compte (gardien, conjoint, responsable de site…).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Tache de travail": [
                {
                    "fieldname": "custom_contact_arrivee",
                    "fieldtype": "Data",
                    "label": "Personne à contacter à l'arrivée",
                    "insert_after": "tel",
                    "module": "Customize erpnext",
                    "description": "Nom et téléphone de la personne à demander sur place.",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
