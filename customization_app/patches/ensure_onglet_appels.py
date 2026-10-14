"""Onglet « Appels » (workspace Appels) : le poste de travail de la personne qui appelle les clients (Salma, 03/10/2026) —
listes d'appels ouvertes, suivi d'activité, calendrier. Visible des rôles « Appels » et « Tableau des appels » (plus Workspace
Manager). Joué à chaque migrate (after_migrate), idempotent : crée les rôles et les pose comme seuls rôles du workspace
(l'import de la fixture est sauté quand la fiche en base est plus récente). L'attribution aux comptes = patches
attribuer_onglet_appels et attribuer_tableau_appels (une fois).

« Tableau des appels » (13/10/2026) : la page /app/tableau-appels est réservée à la direction (koubaawassim, Nejib) ; son
raccourci vit dans cet onglet, qui doit donc s'ouvrir aussi pour eux. Frappe masque le raccourci à qui n'a pas la page."""
import frappe

ROLE = "Appels"
ROLE_TABLEAU = "Tableau des appels"
WORKSPACE = "Appels"


def execute():
    for role in (ROLE, ROLE_TABLEAU):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    ws = frappe.get_doc("Workspace", WORKSPACE)
    if [r.role for r in ws.roles] != [ROLE, ROLE_TABLEAU]:
        ws.set("roles", [{"role": ROLE}, {"role": ROLE_TABLEAU}])
        ws.flags.ignore_permissions = True
        ws.save()
        frappe.clear_cache()
