"""Rôle « Inventaire Magasin » (bouton Inventaire du Magasin de la page Stock par entrepôt) pour koubaawassim,
Nejib Koubaa et Hedi ibidhii (responsable principal du magasin), et personne d'autre — System Manager y a
toujours accès.

Demande du 27/10/2026. Attribué UNE fois (patch) : ensuite c'est la fiche User qui fait foi, comme « Banque »,
« Congés RH » et « Sortie Circuit ».
"""
import frappe

ROLE = "Inventaire Magasin"
COMPTES = ("koubaawassim@gmail.com", "aquaworld.servicing@gmail.com", "hedi.ibidhi@gmail.com")


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
