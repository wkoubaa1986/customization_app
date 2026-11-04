"""Réunion d'équipe du Suivi d'activité — point d'avancement périodique (toutes les N semaines).

L'ordre du jour n'est pas stocké : il est assemblé à l'ouverture depuis les activités (en retard,
en cours, terminées depuis la réunion précédente, décisions de la précédente). Les règles vivent
dans `customization_app.reunion_activite`.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ReunionActivite(Document):
    def validate(self):
        from customization_app import suivi_activite as SA
        avant = self.get_doc_before_save()
        anciens = {r.employe for r in avant.participants} if avant else set()
        vus, lignes = set(), []
        for r in self.get("participants") or []:
            if r.employe and r.employe not in vus:
                vus.add(r.employe)
                r.nom = frappe.db.get_value("Employee", r.employe, "employee_name") or r.employe
                r.utilisateur = frappe.db.get_value("Employee", r.employe, "user_id") or None
                lignes.append(r)
        self.set("participants", lignes)
        SA.verifier_nouvelles_affectations(vus, anciens)
        # Une ancienne attribution ne maintient pas un compte désactivé dans une réunion future.
        # Les présences et participants du passé restent inchangés.
        from customization_app import reunion_activite as RA
        if self.date and RA.etat_horaire(self)["statut_affiche"] == "Prévue":
            actifs = SA.employes_actifs(vus)
            self.set("participants", [r for r in lignes if r.employe in actifs])
        if not self.titre:
            self.titre = _("Réunion d'équipe")
        if self.statut == "Tenue" and not self.tenue_le:
            self.tenue_le = frappe.utils.now_datetime()
        if self.statut != "Tenue":
            self.tenue_le = None
