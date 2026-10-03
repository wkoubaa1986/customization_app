"""Donne le rôle « Appels » à Salma (salmaaquaworldservicing@gmail.com) et à koubaawassim@gmail.com, et fait de l'onglet
« Appels » la page d'accueil de Salma (User.default_workspace). Une fois : retirer le rôle à la main tient ensuite."""
import frappe

from customization_app.patches.ensure_onglet_appels import ROLE, WORKSPACE, execute as attacher

UTILISATEURS = ("salmaaquaworldservicing@gmail.com", "koubaawassim@gmail.com")
ACCUEIL = ("salmaaquaworldservicing@gmail.com",)


def execute():
    attacher()
    for email in UTILISATEURS:
        if not frappe.db.exists("User", email):
            continue
        if not frappe.db.exists("Has Role", {"parent": email, "parenttype": "User", "role": ROLE}):
            user = frappe.get_doc("User", email)
            user.append("roles", {"role": ROLE})
            user.flags.ignore_permissions = True
            user.save()
        if email in ACCUEIL and frappe.db.exists("Workspace", WORKSPACE) and not frappe.db.get_value("User", email, "default_workspace"):
            frappe.db.set_value("User", email, "default_workspace", WORKSPACE)
    frappe.clear_cache()
