"""Arrivée d'un technicien (10/11/2026) : la case « Écran simplifié (téléphone) » sur la fiche Employé et le
rôle « Technicien » (accès Desk, ouvre l'agenda du téléphone). Idempotent."""
from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("Role", "Technicien"):
        frappe.get_doc({"doctype": "Role", "role_name": "Technicien", "desk_access": 1}).insert(ignore_permissions=True)
    create_custom_fields({"Employee": [
        {"fieldname": "custom_ecran_simplifie", "label": "Écran simplifié (téléphone)", "fieldtype": "Check",
         "insert_after": "custom_couleur",
         "description": "Ma journée, agenda, mon stock, ma caisse et clôture mobile — comme un sous-traitant, "
                        "mais la caisse et le stock restent ceux d'un employé."},
    ]}, ignore_validate=True, update=True)
