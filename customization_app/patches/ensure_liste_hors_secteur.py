"""Type de liste d'appels « Hors secteur » (15/10/2026) : les clients hors de nos secteurs et sans partenaire recevaient leurs
SMS puis n'étaient jamais appelés (217 en attente). Ils ont leur liste, dernière priorité. Idempotent."""
import frappe


def execute():
    champ = frappe.db.get_value("DocField", {"parent": "Liste Appelle Entretien", "fieldname": "type_liste"}, ["name", "options"], as_dict=True)
    if champ and "Hors secteur" not in (champ.options or "").splitlines():
        frappe.db.set_value("DocField", champ.name, "options", (champ.options or "").rstrip("\n") + "\nHors secteur", update_modified=False)
        frappe.clear_cache(doctype="Liste Appelle Entretien")
        print("ensure_liste_hors_secteur : option ajoutée")
