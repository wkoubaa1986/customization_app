"""Onglet « Clients Pro » (tableau de bord clients-b2b) : visible du seul rôle « Responsable B2B » et de System Manager
(demande utilisateur 04/11/2026 : « le responsable B2B et moi-même »). Joué à chaque migrate (after_migrate), idempotent :
crée le rôle s'il manque, crée l'espace de travail depuis la fixture s'il manque, et garantit ses rôles — l'import de la
fixture est sauté quand la fiche en base est plus récente. L'attribution du rôle aux comptes se fait à la main.

⚠️ L'ESPACE S'APPELLE « Clients Pro », PAS « Clients B2B » : Frappe refuse un espace dont le nom en minuscules-tirets est
celui d'une page (validate_route_conflict), et la page est clients-b2b."""
import json
import os

import frappe

ROLE = "Responsable B2B"
ROLES = [ROLE, "System Manager"]
WORKSPACE = "Clients Pro"


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1, "is_custom": 1}).insert(
            ignore_permissions=True)
    if not frappe.db.exists("Workspace", WORKSPACE):
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "workspace.json")
        with open(chemin, encoding="utf-8") as f:
            entree = next((w for w in json.load(f) if w.get("name") == WORKSPACE), None)
        if not entree:
            return
        doc = frappe.get_doc(entree)
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    ws = frappe.get_doc("Workspace", WORKSPACE)
    if sorted(r.role for r in ws.roles) == sorted(ROLES):
        return
    ws.set("roles", [{"role": r} for r in ROLES])
    ws.flags.ignore_permissions = True
    ws.save()
    # La liste des espaces est mise en cache PAR UTILISATEUR : sans vidage, l'onglet resterait invisible / visible.
    frappe.clear_cache()
