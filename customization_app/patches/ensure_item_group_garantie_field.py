"""
Champ « Sous garantie » sur le Groupe d'Articles, et amorçage des groupes
d'appareils couverts par la garantie d'un an.

Le print format « Aqua World BL » s'appuie sur ce champ pour décider d'imprimer
ou non la mention de garantie (voir customization_app/jinja_methods.py). Un
groupe coché couvre aussi tous ses descendants : « Pompes de surface & puits »
et « Pompes multicellulaires » ne portent aucun article en direct, leurs 26
articles vivent dans des sous-groupes.

L'amorçage n'a lieu QUE si aucun groupe n'est encore coché. Sans cette garde,
chaque migrate réactiverait un groupe décoché depuis le Desk.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Customize erpnext"
CHAMP = "custom_sous_garantie"

GROUPES_INITIAUX = (
    "Pompes multicellulaires",
    "Pompes de surface & puits",
    "Pompes doseuses",
    "Osmoseurs Industriels",
    "Appareils commerciaux",
    "Fontaines",
    "Bi-osmose",
    "Filtres UV",
    "Électrovannes",
    "Adoucisseurs Commerciaux",
    "Adoucisseurs Domestiques",
    "Vannes adoucisseurs automatiques",
    "Contrôleurs",
)


def execute():
    create_custom_fields(
        {
            "Item Group": [
                {
                    "fieldname": CHAMP,
                    "label": "Sous garantie",
                    "fieldtype": "Check",
                    "insert_after": "is_group",
                    "module": MODULE,
                    "description": (
                        "Les articles de ce groupe et de ses sous-groupes font apparaître "
                        "la mention de garantie en bas du bon de livraison."
                    ),
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()

    deja_coches = frappe.db.count("Item Group", {CHAMP: 1})
    if deja_coches:
        print(f"[ensure_item_group_garantie_field] {deja_coches} groupe(s) déjà coché(s), amorçage ignoré.")
        return

    coches, absents = [], []
    for nom in GROUPES_INITIAUX:
        if frappe.db.exists("Item Group", nom):
            frappe.db.set_value("Item Group", nom, CHAMP, 1, update_modified=False)
            coches.append(nom)
        else:
            absents.append(nom)

    frappe.db.commit()
    frappe.clear_cache()

    if absents:
        print(f"[ensure_item_group_garantie_field] groupes introuvables, ignorés : {', '.join(absents)}")
    print(f"[ensure_item_group_garantie_field] {len(coches)} groupe(s) marqué(s) sous garantie.")
