"""Rôle « Congés RH » (page Congés & récupérations) pour koubaawassim et Nejib Koubaa, et personne d'autre.

Demande du 01/10/2026. Attribué UNE fois (patch) : ensuite c'est la fiche User qui fait foi,
comme pour le rôle « Banque ».
"""
import frappe

ROLE = "Congés RH"
COMPTES = ("koubaawassim@gmail.com", "aquaworld.servicing@gmail.com")


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    for user in COMPTES:
        if not frappe.db.exists("User", user):
            continue
        doc = frappe.get_doc("User", user)
        if any(r.role == ROLE for r in doc.roles):
            continue
        doc.append("roles", {"role": ROLE})
        doc.flags.ignore_permissions = True
        doc.save()
    frappe.clear_cache()
