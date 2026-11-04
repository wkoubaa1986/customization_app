"""Ajout compatible des responsables multiples ; conserve les attributions historiques."""
import frappe


def execute():
    frappe.reload_doc("customize_erpnext", "doctype", "activite_etape")
    reprendre_attributions()


def reprendre_attributions():
    frappe.db.sql("""update `tabActivite Etape`
        set responsables_json = JSON_ARRAY(responsable)
        where coalesce(responsables_json, '') = '' and coalesce(responsable, '') != ''""")
