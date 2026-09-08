import frappe
from customization_app.facturation_mensuelle import SCRIPT_NAME, script_source


def execute():
    # Ne lance aucune facturation pendant le déploiement.
    if not frappe.db.exists("Server Script", SCRIPT_NAME):
        return
    doc = frappe.get_doc("Server Script", SCRIPT_NAME)
    doc.script = script_source()
    doc.save(ignore_permissions=True)
