"""
Raccourci « Factures à comptabiliser » dans l'espace de travail Achats (Buying) :
la file des factures capturées en caisse (« Facture Achat a Saisir », statut
À saisir) que le comptable transforme en vraies Purchase Invoice.

Le raccourci doit exister DANS LE CONTENU du workspace en plus de la table des
shortcuts : une ligne de shortcut absente du `content` ne s'affiche pas.
Idempotent : ne touche rien si le raccourci est déjà là.
"""

import json

import frappe

LABEL = "Factures à comptabiliser"
DOCTYPE_CIBLE = "Facture Achat a Saisir"
WORKSPACE = "Buying"


def execute():
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    doc = frappe.get_doc("Workspace", WORKSPACE)
    if any((s.link_to or "") == DOCTYPE_CIBLE for s in doc.shortcuts):
        return
    doc.append("shortcuts", {
        "type": "DocType", "link_to": DOCTYPE_CIBLE, "label": LABEL,
        "stats_filter": json.dumps({"statut": "À saisir"}, ensure_ascii=False),
        "color": "Orange", "doc_view": "List",
    })
    contenu = json.loads(doc.content or "[]")
    bloc = {"id": "fas_caisse_sc", "type": "shortcut",
            "data": {"shortcut_name": LABEL, "col": 3}}
    # Au milieu des autres raccourcis (avant le premier), sinon en tête de page.
    positions = [i for i, b in enumerate(contenu) if b.get("type") == "shortcut"]
    contenu.insert(positions[0] if positions else 0, bloc)
    doc.content = json.dumps(contenu, ensure_ascii=False)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
