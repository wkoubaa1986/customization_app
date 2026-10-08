"""Optimisation des tournées (08/10/2026) : la case « Heure et employé fixes » devient deux cases.
« Heure fixe » (custom_tournee_fixe, même champ : l'heure promise ne bouge pas, un autre employé peut la faire) et
« Employé fixe » (custom_employe_fixe : le technicien ne change pas, l'heure peut bouger). Les deux = rien ne bouge."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Tache de travail": [
                {"fieldname": "custom_tournee_fixe", "label": "Heure fixe (l’optimisation ne change pas l’heure)",
                 "fieldtype": "Check", "insert_after": "temps", "no_copy": 1, "module": "Customize erpnext",
                 "description": "Rendez-vous promis au client : l’heure reste, un autre employé peut le faire "
                                "(cochez aussi « Employé fixe » pour ne rien bouger)."},
                {"fieldname": "custom_employe_fixe", "label": "Employé fixe (l’optimisation ne change pas l’employé)",
                 "fieldtype": "Check", "insert_after": "custom_tournee_fixe", "no_copy": 1, "module": "Customize erpnext",
                 "description": "Technicien imposé : l’heure peut bouger dans la fenêtre de l’optimisation, pas l’employé."},
            ],
        },
        ignore_validate=True,
    )
    frappe.db.commit()
