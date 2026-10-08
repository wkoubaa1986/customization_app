"""Onglet « Suivi des techniciens » dans la barre de gauche (flotte GPS). Rejoué à chaque migrate (after_migrate), idempotent.
Visible de tous les employés (rôle Employee) ; les statistiques, journées par véhicule et réglages sont réservés au rôle
« Direction Suivi Terrain » (créé ici, donné à Wassim et Néjib)."""

import json

import frappe

ESPACE = "Suivi des techniciens"
PAGE = "suivi-terrain"
ROLES = ("Employee", "Appels", "Responsable magasin", "System Manager")
DIRECTION = "Direction Suivi Terrain"
# Qui voit les statistiques, les journées par véhicule et les réglages (demande du 08/10/2026 : Wassim et Néjib).
DIRECTION_USERS = ("koubaawassim@gmail.com", "aquaworld.servicing@gmail.com")


def _assurer_role_direction():
    if not frappe.db.exists("Role", DIRECTION):
        frappe.get_doc({"doctype": "Role", "role_name": DIRECTION, "desk_access": 1}).insert(ignore_permissions=True)
    for email in DIRECTION_USERS:
        if frappe.db.exists("User", email) and not frappe.db.exists("Has Role", {"parent": email, "parenttype": "User", "role": DIRECTION}):
            u = frappe.get_doc("User", email)
            u.append("roles", {"role": DIRECTION})
            u.flags.ignore_permissions = True
            u.save()


def execute():
    _assurer_role_direction()
    if not frappe.db.exists("Page", PAGE):
        return
    if frappe.db.exists("Workspace", ESPACE):
        ws = frappe.get_doc("Workspace", ESPACE)
    else:
        ws = frappe.new_doc("Workspace")
        ws.update({"name": ESPACE, "label": ESPACE, "title": ESPACE, "public": 1, "icon": "map",
                   "sequence_id": 2.6, "module": "Customize erpnext"})
    change = ws.is_new()

    raccourcis = [("Où sont les techniciens (live + statistiques)", "Page", PAGE, "Blue"),
                  ("Journées par véhicule", "DocType", "Journee Flotte GPS", "Grey"),
                  ("Réglages (plateforme GPS, véhicules)", "DocType", "Config Flotte GPS", "Grey")]
    for libelle, type_, cible, couleur in raccourcis:
        if not any(s.link_to == cible for s in ws.shortcuts or []):
            ws.append("shortcuts", {"type": type_, "label": libelle, "link_to": cible, "color": couleur})
            change = True

    blocs = json.loads(ws.content or "[]")
    voulus = [{"id": "suiviTerEntete", "type": "header",
               "data": {"text": "<span class=\"h4\"><b>Suivi terrain — flotte GPS</b></span>", "col": 12}}]
    voulus += [{"id": "suiviTerRac%d" % i, "type": "shortcut", "data": {"shortcut_name": lib, "col": 4}}
               for i, (lib, *_r) in enumerate(raccourcis)]
    presents = {b.get("id") for b in blocs}
    for b in voulus:
        if b["id"] not in presents:
            blocs.append(b)
            change = True
    ws.content = json.dumps(blocs)

    roles_ws = {r.role for r in ws.roles or []}
    for role in ROLES:
        if frappe.db.exists("Role", role) and role not in roles_ws:
            ws.append("roles", {"role": role})
            change = True

    if not change:
        return
    ws.flags.ignore_permissions = True
    ws.save()
    frappe.clear_cache()
    frappe.db.commit()
