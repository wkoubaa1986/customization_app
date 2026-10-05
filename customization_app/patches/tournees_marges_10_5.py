"""Optimisation des tournées : 10 min de stationnement à l'arrivée, 5 min de rangement après chaque tâche
(décision du 05/10/2026 — remplace les 15-20 min de « stationnement et sortie » d'avant), et correction
des temps OSRM à 1,3 (mesurée avec Google). Joué une fois."""

import frappe

CONFIG = "Config Optimisation Tournees"


def execute():
    if not frappe.db.exists("DocType", CONFIG):
        return
    frappe.db.set_single_value(CONFIG, {"marge_minutes": 10, "rangement_minutes": 5}, update_modified=False)
    # Rapport Google (sans trafic) ÷ OSRM mesuré le 05/10/2026 sur les tournées du 06/10 : ≈ 1,33. Point de départ ;
    # chaque calcul avec Google l'ajuste ensuite.
    if (frappe.db.get_single_value(CONFIG, "coef_osrm") or 1) <= 1:
        frappe.db.set_single_value(CONFIG, "coef_osrm", 1.3, update_modified=False)
    frappe.db.commit()
