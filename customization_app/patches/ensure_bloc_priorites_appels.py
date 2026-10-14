"""Bloc « Mes appels par priorité » tout en haut de l'onglet Appels (13/10/2026).

Un Custom HTML Block dont le HTML, le CSS et le JS vivent dans `blocs_html/priorites_appels.*` : ce patch les recopie
dans la fiche à chaque migrate (after_migrate) si elle a changé, puis pose le bloc en tête de l'onglet. Idempotent.
Visible des rôles « Appels » et « Tableau des appels » ; chacun y voit ses propres actions (tableau_appels.get_priorites).
"""

import json
import os

import frappe

BLOC = "Mes appels par priorité"
ESPACE = "Appels"
ROLES = ("Appels", "Tableau des appels")
ID_BLOC = "prioritesAppelsBloc"


def _source(extension):
    chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blocs_html",
                          "priorites_appels." + extension)
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def _bloc():
    valeurs = {"html": _source("html"), "style": _source("css"), "script": _source("js"), "private": 0}
    if frappe.db.exists("Custom HTML Block", BLOC):
        doc = frappe.get_doc("Custom HTML Block", BLOC)
        change = any((doc.get(k) or "") != (v or "") for k, v in valeurs.items() if k != "private") or doc.private
        if sorted(r.role for r in doc.roles) != sorted(ROLES):
            doc.set("roles", [{"role": r} for r in ROLES])
            change = True
        if not change:
            return False
        doc.update(valeurs)
    else:
        doc = frappe.new_doc("Custom HTML Block")
        doc.name = BLOC
        doc.update(valeurs)
        doc.set("roles", [{"role": r} for r in ROLES])
    doc.flags.ignore_permissions = True
    doc.save() if not doc.is_new() else doc.insert(set_name=BLOC)
    return True


def _dans_l_onglet():
    if not frappe.db.exists("Workspace", ESPACE):
        return False
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False
    if not any(b.custom_block_name == BLOC for b in (espace.custom_blocks or [])):
        espace.append("custom_blocks", {"custom_block_name": BLOC, "label": BLOC})
        change = True
    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "custom_block" and (b.get("data") or {}).get("custom_block_name") == BLOC for b in blocs):
        blocs.insert(0, {"id": ID_BLOC, "type": "custom_block", "data": {"custom_block_name": BLOC, "col": 12}})
        espace.content = json.dumps(blocs)
        change = True
    if change:
        espace.flags.ignore_permissions = True
        espace.save()
    return change


def execute():
    if not frappe.db.exists("DocType", "Custom HTML Block"):
        return
    if _bloc() | _dans_l_onglet():
        frappe.clear_cache()
        frappe.db.commit()
