# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class VisiteCommerciale(Document):
    def validate(self):
        self.check_completude()
        self.set_lien_google_maps()

    def check_completude(self):
        """Règle métier : une visite ne peut être clôturée (Réalisée) sans
        preuve de présence (photo + GPS) ni compte rendu. Exception « Client
        absent » : la preuve suffit, pas de compte rendu exigé."""
        if self.statut != "Réalisée":
            return

        manque = []
        if not self.photo_visite:
            manque.append(_("la photo de la visite"))
        if not (self.gps_lat and self.gps_lng):
            manque.append(_("la position GPS"))
        if not self.lien_google_maps:
            manque.append(_("le lien Google Maps"))
        if self.resultat != "Client absent":
            if not self.resume_discussion:
                manque.append(_("le résumé de la discussion"))
            if not self.resultat:
                manque.append(_("le résultat de la visite"))
        if manque:
            frappe.throw(
                _("Impossible de clôturer la visite {0} : il manque {1}.").format(
                    frappe.bold(self.client), " , ".join(manque)
                ),
                title=_("Visite incomplète"),
            )

    def set_lien_google_maps(self):
        if self.gps_lat and self.gps_lng and not self.lien_google_maps:
            self.lien_google_maps = f"https://maps.google.com/?q={self.gps_lat},{self.gps_lng}"

    def on_update(self):
        from customization_app.tournee import on_visite_update
        on_visite_update(self)

    def on_trash(self):
        from customization_app.tournee import refresh_tournee_compteur
        refresh_tournee_compteur(self.tournee)
