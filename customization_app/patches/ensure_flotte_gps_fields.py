"""Flotte GPS (08/10/2026) : le passage réel chez le client, lu sur le journal des véhicules (plateforme unidev),
écrit sur la tâche — arrivée, départ, durée sur place, écart avec l'heure annoncée."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Tache de travail": [
                {"fieldname": "custom_gps_section", "label": "Passage GPS (véhicule)", "fieldtype": "Section Break",
                 "insert_after": "custom_reservation_app", "collapsible": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_statut", "label": "Passage", "fieldtype": "Select",
                 "options": "\nPassage confirmé\nAucun passage\nSans position\nPas de véhicule\nPas de données GPS",
                 "insert_after": "custom_gps_section", "read_only": 1, "no_copy": 1, "in_standard_filter": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_vehicule", "label": "Véhicule", "fieldtype": "Data",
                 "insert_after": "custom_gps_statut", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_distance", "label": "Distance à l’adresse (m)", "fieldtype": "Int",
                 "insert_after": "custom_gps_vehicule", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_col", "fieldtype": "Column Break", "insert_after": "custom_gps_distance", "module": "Customize erpnext"},
                {"fieldname": "custom_gps_arrivee", "label": "Arrivée réelle", "fieldtype": "Datetime",
                 "insert_after": "custom_gps_col", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_depart", "label": "Départ réel", "fieldtype": "Datetime",
                 "insert_after": "custom_gps_arrivee", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_duree", "label": "Durée sur place (min)", "fieldtype": "Int",
                 "insert_after": "custom_gps_depart", "read_only": 1, "no_copy": 1, "module": "Customize erpnext"},
                {"fieldname": "custom_gps_ecart", "label": "Écart arrivée − heure annoncée (min)", "fieldtype": "Int",
                 "insert_after": "custom_gps_duree", "read_only": 1, "no_copy": 1, "module": "Customize erpnext",
                 "description": "Négatif = en avance sur l’heure annoncée au client, positif = en retard."},
                {"fieldname": "custom_gps_code", "label": "Code du lien de suivi client", "fieldtype": "Data",
                 "insert_after": "custom_gps_ecart", "read_only": 1, "no_copy": 1, "hidden": 1, "module": "Customize erpnext"},
            ],
        },
        ignore_validate=True,
    )
    frappe.db.commit()
