"""Onglet « Appels » (workspace Appels) : le poste de travail de la personne qui appelle les clients (Salma, 03/10/2026) —
listes d'appels ouvertes, suivi d'activité, calendrier. Visible du seul rôle « Appels » (plus Workspace Manager). Joué à chaque
migrate (after_migrate), idempotent : crée le rôle et le pose comme seul rôle du workspace (l'import de la fixture est sauté
quand la fiche en base est plus récente). L'attribution aux comptes = patch attribuer_onglet_appels (une fois)."""
import frappe

ROLE = "Appels"
WORKSPACE = "Appels"


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    ws = frappe.get_doc("Workspace", WORKSPACE)
    if [r.role for r in ws.roles] != [ROLE]:
        ws.set("roles", [{"role": ROLE}])
        ws.flags.ignore_permissions = True
        ws.save()
        frappe.clear_cache()
