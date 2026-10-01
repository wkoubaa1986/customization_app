"""Raccourci « Réparation osmoseurs » dans l'espace Ventes, juste après « Commandes à traiter ».

Rejoué à chaque migrate (after_migrate) : la fixture Workspace réécrit l'espace
Selling, puis les raccourcis ajoutés par patch sont réinjectés — même mécanique
que ensure_raccourci_commandes_a_traiter. Idempotent.
"""

import json

import frappe

ESPACE = "Selling"
PAGE = "reparation-osmoseurs"
LIBELLE = "Réparation osmoseurs"
APRES = "Commandes à traiter"


def execute():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False

    if not any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        espace.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Cyan"})
        change = True

    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE
               for b in blocs):
        bloc = {"id": "reparationOsmoseursRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
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
