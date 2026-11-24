"""Congés : sa règle du 24/11/2026 — l'approbateur des congés est Nejib Koubaa, le gérant (et lui-même valide aussi,
depuis « Congés & récupérations »). Salma Ben Saïd et Hedi Ibidhii n'avaient AUCUN approbateur sur leur fiche : HRMS
refusait leurs demandes (approbateur obligatoire) et, pour Nejib, toute absence posée ou approuvée à leur nom
(« Pas permis » : HRMS ne laisse lire leur solde qu'à l'approbateur de la fiche). Les fiches actives sans approbateur
le reçoivent ; une fiche qui en a déjà un n'est pas touchée."""
import frappe

from customization_app.conges_recuperations import APPROBATEUR_PAR_DEFAUT


def execute():
    if not frappe.db.exists("User", APPROBATEUR_PAR_DEFAUT):
        return
    for nom in frappe.get_all("Employee", filters={"status": "Active", "user_id": ["is", "set"],
                                                   "leave_approver": ["in", ["", None]]}, pluck="name"):
        frappe.db.set_value("Employee", nom, "leave_approver", APPROBATEUR_PAR_DEFAUT, update_modified=False)
