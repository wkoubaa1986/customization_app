"""Donne le rôle « Relances » (onglet Relances & partenaire) à UN compte : koubaawassim@gmail.com. Joué une fois ;
retirer le rôle depuis la fiche utilisateur tient ensuite. Les comptes « Workspace Manager » (Administrator,
koubaawassim, agent-ia) voient de toute façon tous les espaces : mécanique Frappe."""
import frappe

from customization_app.patches.ensure_onglet_relances import ROLE, execute as attacher

UTILISATEURS = ("koubaawassim@gmail.com",)


def execute():
    attacher()
    for email in UTILISATEURS:
        if not frappe.db.exists("User", email) or frappe.db.exists("Has Role", {"parent": email, "parenttype": "User", "role": ROLE}):
            continue
        user = frappe.get_doc("User", email)
        user.append("roles", {"role": ROLE})
        user.flags.ignore_permissions = True
        user.save()
    frappe.clear_cache()
