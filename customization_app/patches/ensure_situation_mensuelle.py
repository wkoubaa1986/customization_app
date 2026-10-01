"""Situation mensuelle : le graphe « Situation Mensuelle » de l'onglet Comptabilité montre les 12
derniers mois (même calcul que la page, omissions comprises) au lieu du rapport figé sur un mois,
et un raccourci « Situation mensuelle (détail) » rejoint l'onglet. Rejoué à chaque migrate (la
fixture Workspace réécrit l'onglet), idempotent.

⚠️ Le type d'un Dashboard Chart ne se change pas (« Value cannot be changed for Chart Type ») :
on crée un NOUVEAU graphe et la ligne « Situation Mensuelle » de l'onglet pointe dessus (le bloc de
mise en page référence le libellé, pas le graphe : il reste tel quel).
"""

import json

import frappe

ANCIEN = "Situation Mensuelle3"            # graphe sur rapport, figé sur un mois
GRAPHE = "Situation Mensuelle 12 mois"
SOURCE = "Situation Mensuelle 12 mois"
ESPACE = "Accounting"
PAGE = "situation-mensuelle"
LIBELLE = "Situation mensuelle (détail)"
OPTIONS = {"colors": ["#2563eb", "#f59e0b", "#ef4444", "#ec4899", "#22c55e", "#7f1d1d"], "barOptions": {"spaceRatio": 0.35}}


def execute():
    if frappe.db.exists("Dashboard Chart Source", SOURCE) and not frappe.db.exists("Dashboard Chart", GRAPHE):
        frappe.get_doc({"doctype": "Dashboard Chart", "chart_name": GRAPHE, "chart_type": "Custom", "source": SOURCE,
                        "type": "Bar", "is_public": 1, "timeseries": 0, "filters_json": "{}",
                        "custom_options": json.dumps(OPTIONS)}).insert(ignore_permissions=True)
    elif frappe.db.exists("Dashboard Chart", GRAPHE) and frappe.parse_json(
            frappe.db.get_value("Dashboard Chart", GRAPHE, "custom_options") or "{}") != OPTIONS:
        # Couleurs à jour (6e série « Perte » ajoutée le 01/10/2026).
        frappe.db.set_value("Dashboard Chart", GRAPHE, "custom_options", json.dumps(OPTIONS))

    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    ws = frappe.get_doc("Workspace", ESPACE)
    change = False
    if frappe.db.exists("Dashboard Chart", GRAPHE):
        for c in ws.charts or []:
            if c.chart_name == ANCIEN:
                c.chart_name = GRAPHE
                change = True
    if not any(s.link_to == PAGE for s in ws.shortcuts or []):
        ws.append("shortcuts", {"type": "Page", "label": LIBELLE, "link_to": PAGE, "color": "Green"})
        change = True
    blocs = json.loads(ws.content or "[]")
    if not any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == LIBELLE for b in blocs):
        bloc = {"id": "situationMensuelleRac", "type": "shortcut", "data": {"shortcut_name": LIBELLE, "col": 3}}
        derniers = [i for i, b in enumerate(blocs) if b.get("type") == "shortcut"]
        # En tête des raccourcis : c'est l'écran de décision du mois.
        blocs.insert(derniers[0] if derniers else len(blocs), bloc)
        ws.content = json.dumps(blocs)
        change = True
    if change:
        ws.flags.ignore_permissions = True
        ws.save()
    frappe.clear_cache()
    frappe.db.commit()
