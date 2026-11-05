"""Raccourci « Pertes et Profits (détail) » dans l'onglet Comptabilité, vers la page /app/pertes-et-profits où l'on
choisit ce qui compte dans le graphique Pertes et Profits (05/11/2026). Rejoué à chaque migrate (la fixture Workspace
réécrit l'onglet), idempotent — même mécanique que ensure_situation_mensuelle."""
import json

import frappe

ESPACE = "Accounting"
PAGE = "pertes-et-profits"
LIBELLE = "Pertes et Profits (détail)"


def execute():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    ws = frappe.get_doc("Workspace", ESPACE)
    change = False
    if not any(s.link_to == PAGE for s in ws.shortcuts or []):
        ws.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Blue"})
        change = True
    blocs = json.loads(ws.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE for b in blocs):
        bloc = {"id": "pertesProfitsRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
        # Juste après « Situation mensuelle (détail) » quand il est là, sinon en tête des raccourcis.
        voisin = [i for i, b in enumerate(blocs) if b.get("type") == "shortcut"
                  and (b.get("data") or {}).get("shortcut_name") == "Situation mensuelle (détail)"]
        raccourcis = [i for i, b in enumerate(blocs) if b.get("type") == "shortcut"]
        blocs.insert(voisin[0] + 1 if voisin else (raccourcis[0] if raccourcis else len(blocs)), bloc)
        ws.content = json.dumps(blocs)
        change = True
    if change:
        ws.flags.ignore_permissions = True
        ws.save()
        frappe.clear_cache()
    frappe.db.commit()
