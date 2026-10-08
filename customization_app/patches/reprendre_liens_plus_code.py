"""Adresses restées « lien mort » / « texte ≈ (lien mort) » : leur lien Google Maps porte souvent un Plus Code, lisible
depuis le 08/10/2026. La relecture (≈ 0,2 s par lien, quelques centaines de liens) part en tâche de fond pour ne pas
allonger le déploiement. En dev le 08/10 : 135 adresses sur 332 reprises, dont 23 décalées de plus de 5 km."""

import frappe


def execute():
    frappe.enqueue("customization_app.tournee_optimisation.reprendre_liens_sans_coordonnees", queue="long", timeout=1800,
                   limite=2000, job_name="reprendre_liens_plus_code")
