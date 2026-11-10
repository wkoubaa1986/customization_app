"""Départ d'un employé (10/11/2026) : les champs de suivi sur la fiche Employé.

Section « Départ » (repliée) : date du départ traité, vérification finale du stock, date de clôture du
stock, journal de ce qui a été fait. Tous en lecture seule : c'est `depart_employe.executer` qui écrit.
Idempotent.
"""
from __future__ import annotations

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({"Employee": [
        {"fieldname": "custom_sec_depart", "label": "Départ", "fieldtype": "Section Break", "collapsible": 1,
         "insert_after": "custom_couleur", "depends_on": "eval:doc.custom_depart_le"},
        {"fieldname": "custom_depart_le", "label": "Départ traité le", "fieldtype": "Date", "read_only": 1,
         "insert_after": "custom_sec_depart"},
        {"fieldname": "custom_depart_verification", "label": "Vérification finale du stock", "fieldtype": "Link",
         "options": "Verification Stock", "read_only": 1, "insert_after": "custom_depart_le"},
        {"fieldname": "custom_depart_stock_clos", "label": "Stock clos le", "fieldtype": "Datetime", "read_only": 1,
         "insert_after": "custom_depart_verification"},
        {"fieldname": "custom_col_depart", "fieldtype": "Column Break", "insert_after": "custom_depart_stock_clos"},
        {"fieldname": "custom_depart_journal", "label": "Journal du départ", "fieldtype": "Small Text", "read_only": 1,
         "insert_after": "custom_col_depart"},
    ]}, ignore_validate=True, update=True)
