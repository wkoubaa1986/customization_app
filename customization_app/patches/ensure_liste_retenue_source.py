"""Liste d'appels « Retenue à la source » (15/10/2026) : option du type de liste, et l'échéancier d'entretien
n'est plus obligatoire sur une ligne (un certificat manquant n'a pas d'échéancier). Idempotent."""
import frappe


def execute():
    champ = frappe.db.get_value("DocField", {"parent": "Liste Appelle Entretien", "fieldname": "type_liste"}, ["name", "options"], as_dict=True)
    if champ and "Retenue à la source" not in (champ.options or "").splitlines():
        frappe.db.set_value("DocField", champ.name, "options", (champ.options or "").rstrip("\n") + "\nRetenue à la source", update_modified=False)
    ech = frappe.db.get_value("DocField", {"parent": "Appelle Client", "fieldname": "échéancier_dentretien"}, ["name", "reqd"], as_dict=True)
    if ech and ech.reqd:
        frappe.db.set_value("DocField", ech.name, "reqd", 0, update_modified=False)
    frappe.clear_cache(doctype="Liste Appelle Entretien")
    frappe.clear_cache(doctype="Appelle Client")
    print("ensure_liste_retenue_source : ok")
