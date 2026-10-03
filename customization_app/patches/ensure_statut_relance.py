"""Statut de relance sur le client (« À requalifier » / « Perdu ») et type de liste d'appels « Requalification » (règle de
lassitude, 03/10/2026). Idempotent."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({"Customer": [
        {"fieldname": "custom_statut_relance", "label": "Statut de relance", "fieldtype": "Select", "options": "\nÀ requalifier\nPerdu",
         "insert_after": "custom_gere_par_partenaire", "module": "Customize erpnext", "in_standard_filter": 1,
         "description": "Vide = relancé normalement. « À requalifier » : posé automatiquement après plusieurs cycles sans réponse, "
                        "plus de SMS ni d’appel d’entretien, un dernier appel via la liste « Requalification » ; un achat le remet à vide. "
                        "« Perdu » : plus jamais relancé."},
    ]}, ignore_validate=True, update=True)
    frappe.clear_cache(doctype="Customer")
    champ = frappe.db.get_value("DocField", {"parent": "Liste Appelle Entretien", "fieldname": "type_liste"}, ["name", "options"], as_dict=True)
    if champ and "Requalification" not in (champ.options or "").splitlines():
        frappe.db.set_value("DocField", champ.name, "options", (champ.options or "").rstrip("\n") + "\nRequalification", update_modified=False)
        frappe.clear_cache(doctype="Liste Appelle Entretien")
