"""Donne le rôle « Tableau des appels » à koubaawassim@gmail.com et à Nejib (aquaworld.servicing@gmail.com) : seuls eux
voient /app/tableau-appels (demande du 13/10/2026). Une fois : retirer le rôle à la main tient ensuite.

Lignes « Has Role » posées directement : un `User.save()` déclenche le Server Script « update_list_tel » (Contact After
Save), qui plante sur certains comptes."""
import frappe

from customization_app.patches.ensure_onglet_appels import ROLE_TABLEAU, execute as attacher

UTILISATEURS = ("koubaawassim@gmail.com", "aquaworld.servicing@gmail.com")


def execute():
    attacher()
    for email in UTILISATEURS:
        if not frappe.db.exists("User", email):
            continue
        if not frappe.db.exists("Has Role", {"parent": email, "parenttype": "User", "role": ROLE_TABLEAU}):
            frappe.get_doc({"doctype": "Has Role", "parent": email, "parenttype": "User", "parentfield": "roles",
                            "role": ROLE_TABLEAU}).insert(ignore_permissions=True)
        frappe.clear_cache(user=email)
    frappe.clear_cache()
