"""Onglet « Relances & partenaire » (workspace Relances) : visible du SEUL rôle « Relances » (demande utilisateur
03/10/2026 : « visible que par moi »). Joué à chaque migrate (after_migrate), idempotent : crée le rôle s'il manque et
remplace les rôles du workspace par celui-là — l'import de la fixture est sauté quand la fiche en base est plus récente,
donc c'est ici que la restriction est garantie. L'attribution du rôle aux comptes est un patch à part (une fois)."""
import frappe

ROLE = "Relances"
WORKSPACE = "Relances et partenaire"


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    ws = frappe.get_doc("Workspace", WORKSPACE)
    if [r.role for r in ws.roles] == [ROLE]:
        return
    ws.set("roles", [{"role": ROLE}])
    ws.flags.ignore_permissions = True
    ws.save()
    # La liste des espaces est mise en cache PAR UTILISATEUR : sans vidage, l'onglet resterait visible.
    frappe.clear_cache()
