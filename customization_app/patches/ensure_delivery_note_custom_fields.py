"""
Garantit la présence des Custom Field du Delivery Note utilisés par la
génération des BL et par le print format « Aqua World BL ».

Ces champs existent en base mais avec module = NULL : le filtre
["module", "=", "Customize erpnext"] du bloc `fixtures` de hooks.py ne les
exporte donc pas, et une installation neuve les perdrait — generer_bl.py
(custom_livré_par / custom_véhicle) comme le print format échoueraient.

create_custom_fields() est idempotent et ne requiert pas developer_mode :
il crée le champ s'il manque et se contente de mettre à jour les propriétés
divergentes sinon. Le rattachement au module rend en prime l'export de
fixtures complet pour les prochaines fois.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Customize erpnext"

CUSTOM_FIELDS = {
    "Delivery Note": [
        {
            "fieldname": "custom_livré_par",
            "label": "Livré par",
            "fieldtype": "Link",
            "options": "Employee",
            "insert_after": "amended_from",
            "module": MODULE,
        },
        {
            "fieldname": "custom_véhicle",
            "label": "Véhicle",
            "fieldtype": "Link",
            "options": "Vehicle",
            "insert_after": "custom_livré_par",
            "module": MODULE,
        },
        {
            "fieldname": "custom_garantie",
            "label": "Garantie",
            "fieldtype": "Check",
            "insert_after": "custom_véhicle",
            "module": MODULE,
        },
        {
            "fieldname": "custom_bl_envoye",
            "label": "BL_envoye",
            "fieldtype": "Check",
            "insert_after": "custom_garantie",
            "module": MODULE,
        },
    ]
}


def execute():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)

    # Rattache au module les champs préexistants créés à la main (module NULL),
    # sans quoi ils resteraient hors du périmètre des fixtures.
    for champ in CUSTOM_FIELDS["Delivery Note"]:
        nom = f"Delivery Note-{champ['fieldname']}"
        if frappe.db.exists("Custom Field", nom):
            frappe.db.set_value("Custom Field", nom, "module", MODULE, update_modified=False)

    frappe.db.commit()
    print("[ensure_delivery_note_custom_fields] champs Delivery Note vérifiés et rattachés au module.")
