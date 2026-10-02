"""Optimisation des tournées (02/10/2026) : coordonnées mémorisées sur l'adresse (le lien Google Maps court
est résolu une fois), et case « Heure et employé fixes » sur la tâche (épingle un rendez-vous promis)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Address": [
                {"fieldname": "custom_latitude", "label": "Latitude", "fieldtype": "Float", "precision": "6",
                 "insert_after": "custom_lien_google_map", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_longitude", "label": "Longitude", "fieldtype": "Float", "precision": "6",
                 "insert_after": "custom_latitude", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_geocode_source", "label": "Position lue depuis", "fieldtype": "Data",
                 "insert_after": "custom_longitude", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
            ],
            "Tache de travail": [
                {"fieldname": "custom_tournee_fixe", "label": "Heure et employé fixes (ne pas déplacer à l’optimisation)",
                 "fieldtype": "Check", "insert_after": "temps", "no_copy": 1, "module": "Customize erpnext",
                 "description": "Rendez-vous promis au client ou technicien imposé : l’optimisation des tournées s’organise autour."},
            ],
        },
        ignore_validate=True,
    )
    frappe.db.commit()
