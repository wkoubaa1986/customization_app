"""Congés & récupérations : type de congé « Récupération (quinzaine) » + raccourci dans l'onglet HR après « Leave Application ».

Rejoué à chaque migrate (after_migrate), idempotent.
"""

import json

import frappe

ESPACE = "HR"
PAGE = "conges-recuperations"
LIBELLE = "Congés & récupérations"
APRES = "Leave Application"


SERVER_SCRIPT_REMPLACE = "Generer"   # Attendance After Save → tâche 08:30-17:00 jamais supprimée


ROLE = "Congés RH"   # qui le porte = donnée (patch attribuer_role_conges_rh), géré ensuite dans la fiche User


def execute():
    from customization_app.conges_recuperations import _assurer_type_recup
    _assurer_type_recup()
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    # Le hook Python sur Leave Application fait mieux (toute la journée, type dédié, suppression
    # à l'annulation) : le Server Script est éteint, pas supprimé (réversible d'une case).
    if frappe.db.exists("Server Script", SERVER_SCRIPT_REMPLACE) and \
            not frappe.db.get_value("Server Script", SERVER_SCRIPT_REMPLACE, "disabled"):
        frappe.db.set_value("Server Script", SERVER_SCRIPT_REMPLACE, "disabled", 1)
        frappe.clear_cache()
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False
    if not any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        espace.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Green"})
        change = True
    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE for b in blocs):
        bloc = {"id": "congesRecupRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
        pos = next((i for i, b in enumerate(blocs) if b.get("type") == "shortcut"
                    and (b.get("data") or {}).get("shortcut_name") == APRES), None)
        if pos is None:
            derniers = [i for i, b in enumerate(blocs) if b.get("type") == "shortcut"]
            pos = derniers[-1] if derniers else len(blocs) - 1
        blocs.insert(pos + 1, bloc)
        espace.content = json.dumps(blocs)
        change = True
    if not change:
        return
    espace.flags.ignore_permissions = True
    espace.save()
    frappe.clear_cache()
    frappe.db.commit()
