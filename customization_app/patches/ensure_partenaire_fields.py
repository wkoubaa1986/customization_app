"""Clients gérés par le partenaire (03/10/2026) : champs sur Customer, reprise de l'existant (fiches créées par le
compte partenaire), et type de liste d'appels « Zone partenaire ». Idempotent."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.api import PARTNER_USER


def execute():
    create_custom_fields({
        "Customer": [
            {"fieldname": "custom_gere_par_partenaire", "label": "Géré par le partenaire", "fieldtype": "Check",
             "insert_after": "custom_envoi_sms", "default": "0", "module": "Customize erpnext",
             "description": "Ses relances d’entretien (SMS, e-mail, appels) sont faites par le partenaire : exclu des nôtres. "
                            "Posé automatiquement quand la fiche est créée par son compte ; corrigeable ici ou dans la page « Clients partenaire »."},
            {"fieldname": "custom_partenaire", "label": "Partenaire", "fieldtype": "Link", "options": "Employee",
             "insert_after": "custom_gere_par_partenaire", "depends_on": "eval:doc.custom_gere_par_partenaire",
             "ignore_user_permissions": 1, "module": "Customize erpnext"},
        ]}, ignore_validate=True, update=True)
    frappe.clear_cache(doctype="Customer")

    emp = frappe.db.get_value("Employee", {"user_id": PARTNER_USER, "status": "Active"}, "name")
    repris = frappe.db.sql("""update tabCustomer set custom_gere_par_partenaire = 1, custom_partenaire = %s
                              where owner = %s and ifnull(custom_gere_par_partenaire, 0) = 0""", (emp, PARTNER_USER))
    print("ensure_partenaire_fields : fiches créées par le compte partenaire reprises =", frappe.db._cursor.rowcount if hasattr(frappe.db, "_cursor") else "?")

    # Liste d'appels : DocType CUSTOM (en base seulement, module Support) → l'option s'ajoute sur son DocField.
    champ = frappe.db.get_value("DocField", {"parent": "Liste Appelle Entretien", "fieldname": "type_liste"}, ["name", "options"], as_dict=True)
    if champ and "Zone partenaire" not in (champ.options or "").splitlines():
        frappe.db.set_value("DocField", champ.name, "options", (champ.options or "").rstrip("\n") + "\nZone partenaire", update_modified=False)
        frappe.clear_cache(doctype="Liste Appelle Entretien")
