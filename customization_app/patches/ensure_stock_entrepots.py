"""Page « Stock par entrepôt » : raccourci dans l'espace Stock juste après « Zones & sorties d’articles »,
et premier réglage (Magasin = « Magasins - A&S », « Articles defectueux » jamais remis à zéro).

Rejoué à chaque migrate (after_migrate), idempotent : le réglage n'est posé que s'il n'a jamais été
enregistré — une fois modifié à la main, il n'est plus touché.
"""

import json

import frappe

ESPACE = "Stock"
PAGE = "stock-entrepots"
LIBELLE = "Stock par entrepôt"
APRES = "Zones & sorties d’articles"
CONFIG = "Config Stock Entrepot"
MAGASIN = "Magasins - A&S"
EXCLUS = ["Articles defectueux - A&S"]


def execute():
    _reglage()
    _raccourci()


def _reglage():
    if not frappe.db.exists("DocType", CONFIG):
        return
    if frappe.db.sql("select 1 from tabSingles where doctype = %s limit 1", CONFIG):
        return
    if not frappe.db.exists("Warehouse", MAGASIN):
        return
    doc = frappe.get_single(CONFIG)
    doc.entrepot_magasin = MAGASIN
    for e in EXCLUS:
        if frappe.db.exists("Warehouse", e):
            doc.append("entrepots_exclus", {"entrepot": e})
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()


def _raccourci():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False

    if not any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        espace.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Blue"})
        change = True

    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE
               for b in blocs):
        bloc = {"id": "stockEntrepotsRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
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
