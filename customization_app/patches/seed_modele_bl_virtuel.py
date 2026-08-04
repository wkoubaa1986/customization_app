"""
Crée les modèles de BL virtuel « Entretien » et « Réparation » : le kit
d'entretien osmoseur domestique chargé par le technicien quand la tâche de
travail n'a pas de commande client.

Idempotent — un modèle déjà présent n'est jamais réécrit. Les quantités
ajustées depuis le Desk survivent donc aux migrations suivantes, ce qu'une
fixture (delete + insert à chaque migrate) ne permettrait pas.

Utilisé par customization_app/generer_bl.py (_creer_bl_virtuel).
"""

import frappe

TYPES = ("Entretien", "Réparation")

# (code article, quantité) — l'ordre fixe l'ordre d'impression sur le BL.
ARTICLES = (
    ("PF-10'-PP-UDF-CTO", 1),
    ("M-75-Pu", 1),
    ("F-T33-C", 1),
    ("F-T33-M", 1),
    ("F-T33-A", 1),
    ("PO-75", 1),
    ("Ad-24-1.3A", 1),
    ("M-E-OD", 1),
)

DESCRIPTION = "Kit d'entretien osmoseur domestique — chargement technicien."


def execute():
    if not frappe.db.exists("DocType", "Modele BL Virtuel"):
        return

    presents = [code for code, _qty in ARTICLES if frappe.db.exists("Item", code)]
    manquants = [code for code, _qty in ARTICLES if code not in presents]

    if manquants:
        # Catalogue différent selon le site : on saute l'article plutôt que
        # de faire échouer le migrate.
        print(f"[seed_modele_bl_virtuel] articles absents du catalogue, ignorés : {', '.join(manquants)}")

    if not presents:
        print("[seed_modele_bl_virtuel] aucun article du kit présent, rien à créer.")
        return

    crees = []
    for type_intervention in TYPES:
        if frappe.db.exists("Modele BL Virtuel", type_intervention):
            continue

        # Une liste neuve par document : frappe.get_doc() écrit parent/parentfield
        # dans les dicts fournis, les partager entre deux docs les corromprait.
        articles = [
            {"item_code": code, "qty": qty} for code, qty in ARTICLES if code in presents
        ]

        frappe.get_doc(
            {
                "doctype": "Modele BL Virtuel",
                "type_intervention": type_intervention,
                "actif": 1,
                "description_modele": DESCRIPTION,
                "articles": articles,
            }
        ).insert(ignore_permissions=True)
        crees.append(type_intervention)

    if crees:
        frappe.db.commit()
        print(f"[seed_modele_bl_virtuel] modèles créés : {', '.join(crees)}")
    else:
        print("[seed_modele_bl_virtuel] modèles déjà présents, rien à faire.")
