"""Raccourci « Commandes à traiter » dans l'espace de travail Ventes.

La page n'était atteignable qu'en tapant son adresse ou par la barre de
recherche — autant dire invisible pour qui ne la connaît pas déjà.

⚠️ Pourquoi un patch et pas une fixture : `Workspace` ne figure PAS dans les
fixtures de l'app (seuls Custom Field, Property Setter, Client/Server Script et
Responsable Relance y sont). Le fichier `fixtures/workspace.json` est un
vestige qui n'est ni exporté ni importé au migrate : y ajouter le raccourci
n'aurait rien produit en production.
"""

import json

import frappe

ESPACE = "Selling"
PAGE = "commandes-a-traiter"
LIBELLE = "Commandes à traiter"


def execute():
    """⚠️ DEUX endroits à servir, pas un.

    La table `shortcuts` déclare le raccourci ; le champ `content` décrit la
    MISE EN PAGE. Frappe v15 n'affiche que les blocs listés dans `content` : un
    raccourci présent en table mais absent de la mise en page est renvoyé par
    l'API et pourtant invisible à l'écran — exactement ce qui est arrivé le
    30/08, et qu'aucun vidage de cache ne pouvait corriger.
    """
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", PAGE):
        return
    espace = frappe.get_doc("Workspace", ESPACE)
    change = False

    if not any(s.link_to == PAGE for s in (espace.shortcuts or [])):
        espace.append("shortcuts", {
            "type": "Page",
            "label": LIBELLE,
            "link_to": PAGE,
            "color": "Orange",
        })
        change = True

    blocs = json.loads(espace.content or "[]")
    if not any(b.get("type") == "shortcut"
               and (b.get("data") or {}).get("shortcut_name") == LIBELLE
               for b in blocs):
        bloc = {"id": "cdesATraiterRac", "type": "shortcut",
                "data": {"shortcut_name": LIBELLE, "col": 3}}
        # Juste après le DERNIER raccourci, pour rester dans « Accès rapide ».
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
