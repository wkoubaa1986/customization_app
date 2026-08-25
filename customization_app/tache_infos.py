"""Détails des tâches de travail liées à une commande client.

Affichés en bandeau sur la fiche Sales Order (public/js/sales_order_tache_details.js) :
type d'intervention, employé affecté, statut, durée prévue et date planifiée —
sans avoir à ouvrir la tâche.
"""
from __future__ import annotations

import frappe


@frappe.whitelist()
def taches_de_commande(commande: str) -> list:
    """Les tâches de travail dont commande_client = la commande, plus récentes
    d'abord. L'employé affiché est le nom RH (employee_name), pas le matricule."""
    frappe.has_permission("Tache de travail", "read", throw=True)

    taches = frappe.get_all(
        "Tache de travail",
        filters={"commande_client": commande},
        fields=["name", "custom_type_dintervention", "custom_choix_du_staff",
                "status", "temps", "starts_on"],
        order_by="starts_on desc, creation desc",
    )
    matricules = {t.custom_choix_du_staff for t in taches if t.custom_choix_du_staff}
    noms = {}
    if matricules:
        noms = dict(frappe.get_all(
            "Employee", filters={"name": ["in", list(matricules)]},
            fields=["name", "employee_name"], as_list=True))
    for t in taches:
        t["employe"] = noms.get(t.custom_choix_du_staff) or t.custom_choix_du_staff or ""
    return taches
