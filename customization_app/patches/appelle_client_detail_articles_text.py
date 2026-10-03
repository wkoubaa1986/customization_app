"""Liste d'appels : `Appelle Client.detail_articles` (JSON {article: date}) était un Data de 140 caractères — un client à
plusieurs échéanciers dépassait (revue 03/10/2026). DocType custom en base → on passe le champ en Small Text (la colonne
suit via DocType.on_update). Idempotent."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "Appelle Client"):
        return
    dt = frappe.get_doc("DocType", "Appelle Client")
    champ = next((f for f in dt.fields if f.fieldname == "detail_articles"), None)
    if not champ or champ.fieldtype == "Small Text":
        return
    champ.fieldtype, champ.length = "Small Text", 0
    dt.flags.ignore_permissions = True
    dt.save()
    frappe.clear_cache(doctype="Appelle Client")
