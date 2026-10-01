"""Raccourci « Zones & sorties d’articles » dans l'espace Stock, juste après « Transformation d’articles ».

Rejoué à chaque migrate (after_migrate), idempotent — même mécanique que le raccourci de la transformation.
"""

import json

import frappe

ESPACE = "Stock"
PAGE = "zones-magasin"
LIBELLE = "Zones & sorties d’articles"
APRES = "Transformation d’articles"


def execute():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False

    if not any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        espace.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Green"})
        change = True

    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE
               for b in blocs):
        bloc = {"id": "zonesMagasinRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
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
