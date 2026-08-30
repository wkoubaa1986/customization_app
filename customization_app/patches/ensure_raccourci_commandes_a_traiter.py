"""Raccourci « Commandes à traiter » dans l'espace de travail Ventes.

La page n'était atteignable qu'en tapant son adresse ou par la barre de
recherche — autant dire invisible pour qui ne la connaît pas déjà.

⚠️ Pourquoi un patch et pas une fixture : `Workspace` ne figure PAS dans les
fixtures de l'app (seuls Custom Field, Property Setter, Client/Server Script et
Responsable Relance y sont). Le fichier `fixtures/workspace.json` est un
vestige qui n'est ni exporté ni importé au migrate : y ajouter le raccourci
n'aurait rien produit en production.
"""

import frappe

ESPACE = "Selling"
PAGE = "commandes-a-traiter"
LIBELLE = "Commandes à traiter"


def execute():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    if any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        return
    espace.append("shortcuts", {
        "type": "Page",
        "label": LIBELLE,
        "link_to": PAGE,
        "color": "Orange",
    })
    espace.flags.ignore_permissions = True
    espace.save()
    frappe.db.commit()
