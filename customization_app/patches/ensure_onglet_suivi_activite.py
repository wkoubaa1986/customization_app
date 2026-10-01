"""Onglet « Suivi d'activité » + ses deux rôles. Rejoué à chaque migrate (after_migrate), idempotent.

L'onglet n'est visible que des rôles « Suivi Activité » et « Responsable Activité » (et des
Workspace Manager, comme tout onglet). Les rôles sont donnés aux comptes par le réglage
« Config Suivi Activite », jamais ici : la liste des employés est une donnée, pas du code.
"""

import json

import frappe

ESPACE = "Suivi d'activité"
PAGE = "suivi-activite"
ROLES = ("Suivi Activité", "Responsable Activité")


def _assurer_roles():
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def execute():
    _assurer_roles()
    if not frappe.db.exists("Page", PAGE):
        return
    if frappe.db.exists("Workspace", ESPACE):
        ws = frappe.get_doc("Workspace", ESPACE)
    else:
        ws = frappe.new_doc("Workspace")
        ws.update({"name": ESPACE, "label": ESPACE, "title": ESPACE, "public": 1, "icon": "edit",
                   "sequence_id": 2.5, "module": "Customize erpnext"})
    change = ws.is_new()

    raccourcis = [("Mes activités", "Page", PAGE, "Blue"),
                  ("Toutes les activités (liste)", "DocType", "Activite Employe", "Grey")]
    for libelle, type_, cible, couleur in raccourcis:
        if not any(s.link_to == cible for s in ws.shortcuts or []):
            ws.append("shortcuts", {"type": type_, "label": libelle, "link_to": cible, "color": couleur})
            change = True

    blocs = json.loads(ws.content or "[]")
    voulus = [{"id": "suiviActEntete", "type": "header",
               "data": {"text": "<span class=\"h4\"><b>Suivi d’activité</b></span>", "col": 12}}]
    voulus += [{"id": "suiviActRac%d" % i, "type": "shortcut", "data": {"shortcut_name": lib, "col": 3}}
               for i, (lib, *_r) in enumerate(raccourcis)]
    presents = {b.get("id") for b in blocs}
    for b in voulus:
        if b["id"] not in presents:
            blocs.append(b)
            change = True
    ws.content = json.dumps(blocs)

    roles_ws = {r.role for r in ws.roles or []}
    for role in ROLES:
        if role not in roles_ws:
            ws.append("roles", {"role": role})
            change = True

    if not change:
        return
    ws.flags.ignore_permissions = True
    ws.save()
    frappe.clear_cache()
    frappe.db.commit()
