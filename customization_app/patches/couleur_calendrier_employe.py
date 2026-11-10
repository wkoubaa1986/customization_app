"""Couleur du calendrier pour tout employé (10/11/2026) : le champ `custom_couleur` n'est plus réservé aux
sous-traitants — choisi à l'arrivée d'un technicien, modifiable sur la fiche. Idempotent."""
from __future__ import annotations

import frappe


def execute():
    nom = "Employee-custom_couleur"
    if not frappe.db.exists("Custom Field", nom):
        return
    frappe.db.set_value("Custom Field", nom, {
        "depends_on": None,
        "description": "Couleur de ses tâches dans le calendrier. Vide : barème par défaut (sous-traitant : palette).",
    }, update_modified=False)
    frappe.clear_cache(doctype="Employee")
