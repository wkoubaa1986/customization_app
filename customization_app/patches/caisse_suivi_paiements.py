"""Chaque paiement pris par UNE clôture de caisse (25/11/2026, caisse_paiements.py).

- champ `custom_cloture_caisse` sur Payment Entry (le numéro de la clôture qui l'a compté) ;
- Config Caisse « paiements_suivis_depuis » = maintenant, s'il est vide : seuls les paiements saisis depuis la mise
  en route peuvent être repris par un comptage suivant (l'historique n'est pas rebalayé).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import now_datetime


def execute():
    create_custom_fields(
        {
            "Payment Entry": [
                {
                    "fieldname": "custom_cloture_caisse",
                    "fieldtype": "Link",
                    "options": "Cloture Caisse",
                    "label": "Compté dans la clôture de caisse",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "no_copy": 1,
                    "print_hide": 1,
                    "search_index": 1,
                    "insert_after": "custom_exclu_caisse",
                }
            ]
        },
        ignore_validate=True,
    )
    deja = frappe.db.sql("""SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse'
                            AND field = 'paiements_suivis_depuis'""")
    if not deja or not deja[0][0]:
        frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'paiements_suivis_depuis'")
        frappe.db.sql("""INSERT INTO `tabSingles` (doctype, field, value)
                         VALUES ('Config Caisse', 'paiements_suivis_depuis', %s)""", (str(now_datetime()),))
    frappe.clear_cache(doctype="Config Caisse")
    frappe.db.commit()
