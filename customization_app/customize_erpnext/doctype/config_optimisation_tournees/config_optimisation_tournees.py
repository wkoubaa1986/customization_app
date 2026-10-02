"""Réglage de l'optimisation des tournées : point de départ, journée, types déplaçables, serveur de distances."""

import frappe
from frappe import _
from frappe.model.document import Document


class ConfigOptimisationTournees(Document):
    def validate(self):
        from customization_app.tournee_optimisation import resoudre_lien

        if self.depot_lien and not (self.depot_latitude and self.depot_longitude):
            c = resoudre_lien(self.depot_lien)
            if not c:
                frappe.throw(_("Le lien du Magasin ne donne pas de coordonnées : collez un lien Google Maps ouvert sur le lieu."))
            self.depot_latitude, self.depot_longitude = c
        for r in self.get("departs") or []:
            if r.lien and not (r.latitude and r.longitude):
                c = resoudre_lien(r.lien)
                if not c:
                    frappe.throw(_("Point de départ de {0} : le lien ne donne pas de coordonnées.").format(r.employe))
                r.latitude, r.longitude = c
        if self.heure_debut and self.heure_fin and str(self.heure_debut) >= str(self.heure_fin):
            frappe.throw(_("La fin de journée doit suivre son début."))
