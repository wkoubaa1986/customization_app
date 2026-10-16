"""Domicile sur la fiche Employé (demande du 16/10/2026 : « la corrélation sur les domiciles… je parle du domicile des
employés »). Lien Google Maps + position, tenus à jour dans les deux sens avec la table « Points de départ particuliers »
de Config Optimisation Tournees — la seule source du suivi GPS et de l'optimisation (voir domicile_employe.py)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Employee": [
                {"fieldname": "custom_sec_domicile", "fieldtype": "Section Break", "label": "Domicile (suivi GPS et tournées)",
                 "insert_after": "permanent_address", "module": "Customize erpnext", "collapsible": 0},
                {"fieldname": "custom_domicile_lien", "fieldtype": "Data", "label": "Lien Google Maps du domicile",
                 "insert_after": "custom_sec_domicile", "module": "Customize erpnext",
                 "description": "Même valeur que « Points de départ particuliers » du réglage des tournées : modifier l'un met l'autre à jour. "
                                "Sert au suivi GPS (nuits au domicile, véhicule attribué s'il dort à moins de 500 m) et à l'optimisation des tournées."},
                {"fieldname": "custom_col_domicile", "fieldtype": "Column Break", "insert_after": "custom_domicile_lien", "module": "Customize erpnext"},
                {"fieldname": "custom_domicile_latitude", "fieldtype": "Float", "label": "Domicile : latitude", "precision": "6",
                 "insert_after": "custom_col_domicile", "module": "Customize erpnext"},
                {"fieldname": "custom_domicile_longitude", "fieldtype": "Float", "label": "Domicile : longitude", "precision": "6",
                 "insert_after": "custom_domicile_latitude", "module": "Customize erpnext"},
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
    from customization_app.domicile_employe import depuis_config
    depuis_config(frappe.get_single("Config Optimisation Tournees"), "on_update")       # amorçage : la table vers les fiches
    frappe.db.commit()
