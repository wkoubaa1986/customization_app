"""Raccourci « Rapport Prime » dans l'onglet Banque (Workspace de bank_retenue_sync).

Même mécanique que ensure_raccourci_commandes_a_traiter : la table `shortcuts`
déclare le raccourci, le champ `content` le place à l'écran ; il faut les deux.
Rejoué à chaque migrate (after_migrate) car banque.json est réimporté par
l'autre app et pourrait écraser la fiche. Idempotent.
"""

import json

import frappe

ESPACE = "Banque"
PAGE = "rapport-prime"
LIBELLE = "Rapport Prime"


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
        bloc = {"id": "rapportPrimeRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
        derniers = [i for i, b in enumerate(blocs) if b.get("type") == "shortcut"]
        blocs.insert(derniers[-1] + 1 if derniers else len(blocs), bloc)
        espace.content = json.dumps(blocs)
        change = True

    if not change:
        return
    espace.flags.ignore_permissions = True
    espace.save()
    frappe.clear_cache()
    frappe.db.commit()
