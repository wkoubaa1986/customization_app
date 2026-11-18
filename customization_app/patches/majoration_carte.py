"""Majoration « paiement par carte » sur la commande (18/11/2026) — voir customization_app.majoration_carte.

Crée les champs que le calcul remplit : sur la commande, le montant et le taux de la majoration (sous
l'échéancier) ; sur chaque ligne, le prix d'avant majoration et le prix majoré posé. Idempotent.

La mention imprimée en bas de « Aqua World BC » et « Aqua World BL » n'est PAS posée ici : ces modèles sont des
fixtures de booking_ristourne (fixtures/print_format.json), réimportées après les patchs à chaque migration —
une modification faite en base par un patch serait effacée aussitôt (constaté le 18/11/2026).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def champs() -> dict:
    apres_ligne = "custom_prix_avant_marge" if frappe.get_meta("Sales Order Item").has_field("custom_prix_avant_marge") \
        else "rate"
    return {
        "Sales Order": [
            {"fieldname": "custom_majoration_carte", "label": "Majoration pour paiement par carte",
             "fieldtype": "Currency", "options": "currency", "insert_after": "payment_schedule", "read_only": 1,
             # Caché : le bandeau posé au-dessus de l'échéancier (sales_order_majoration_carte.js) dit déjà
             # le montant ; le champ sert aux rapports et à l'impression.
             "print_hide": 1, "hidden": 1},
            {"fieldname": "custom_majoration_carte_taux", "label": "Taux de la majoration carte (%)",
             "fieldtype": "Percent", "insert_after": "custom_majoration_carte", "read_only": 1, "hidden": 1,
             "print_hide": 1},
        ],
        "Sales Order Item": [
            {"fieldname": "custom_prix_avant_carte", "label": "Prix avant majoration carte",
             "fieldtype": "Currency", "options": "currency", "insert_after": apres_ligne, "read_only": 1,
             "print_hide": 1, "depends_on": "eval:doc.custom_prix_avant_carte"},
            {"fieldname": "custom_prix_avec_carte", "label": "Prix majoré (carte)",
             "fieldtype": "Currency", "options": "currency", "insert_after": "custom_prix_avant_carte",
             "read_only": 1, "hidden": 1, "print_hide": 1},
        ],
    }


def execute():
    create_custom_fields(champs(), ignore_validate=True)
    frappe.db.commit()
    frappe.clear_cache(doctype="Sales Order")
