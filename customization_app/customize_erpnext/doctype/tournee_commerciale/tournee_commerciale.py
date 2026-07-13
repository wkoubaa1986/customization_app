# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TourneeCommerciale(Document):
    def validate(self):
        if not self.date_tournee and self.tache_de_travail:
            starts_on = frappe.db.get_value("Tache de travail", self.tache_de_travail, "starts_on")
            if starts_on:
                self.date_tournee = frappe.utils.getdate(starts_on)

    def visites(self):
        return frappe.get_all(
            "Visite Commerciale",
            filters={"tournee": self.name},
            fields=["name", "client", "statut", "photo_visite", "gps_lat", "gps_lng",
                    "resume_discussion", "resultat"],
        )

    def visites_incompletes(self):
        """Visites non annulées qui ne sont pas encore clôturées (Réalisée)."""
        return [v for v in self.visites() if v.statut not in ("Réalisée", "Annulée")]

    def est_complete(self):
        """Une tournée est complète si elle a au moins une visite et que toutes
        les visites non annulées sont clôturées (donc photo+GPS+CR validés)."""
        visites = self.visites()
        actives = [v for v in visites if v.statut != "Annulée"]
        return bool(actives) and not self.visites_incompletes()

    def refresh_compteur(self):
        realisees = frappe.db.count("Visite Commerciale",
                                    {"tournee": self.name, "statut": "Réalisée"})
        frappe.db.set_value("Tournee Commerciale", self.name,
                            "nb_visites_realisees", realisees, update_modified=False)
