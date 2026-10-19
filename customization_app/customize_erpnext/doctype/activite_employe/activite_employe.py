"""Activité confiée à un ou plusieurs employés — onglet « Suivi d'activité ».

Une activité partagée est UNE seule fiche (mêmes étapes, notes, photos) : chaque employé
affecté la voit dans sa liste. Les règles d'accès vivent dans `customization_app.suivi_activite`.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, nowdate


class ActiviteEmploye(Document):
    def validate(self):
        from customization_app import suivi_activite as SA

        vus, lignes = set(), []
        for r in self.get("affectations") or []:
            if r.employe and r.employe not in vus:
                vus.add(r.employe)
                r.utilisateur = frappe.db.get_value("Employee", r.employe, "user_id") or None
                r.nom = frappe.db.get_value("Employee", r.employe, "employee_name") or r.employe
                lignes.append(r)
        self.set("affectations", lignes)
        if not lignes:
            frappe.throw(_("Affectez l'activité à au moins un employé."))
        self.noms_employes = ", ".join(r.nom for r in lignes)[:140]
        # Responsable d'étape : un des employés de l'activité, sinon la ligne perd son responsable.
        for e in self.get("etapes") or []:
            if e.responsable and e.responsable not in vus:
                e.responsable = None
            e.responsable_nom = (frappe.db.get_value("Employee", e.responsable, "employee_name") or e.responsable) if e.responsable else None

        if not SA.est_responsable():
            moi = SA.employe_de(frappe.session.user)
            if not moi:
                frappe.throw(_("Votre compte n'est rattaché à aucun employé."))
            avant = self.get_doc_before_save()
            if avant:
                if {r.employe for r in avant.affectations} != vus:
                    frappe.throw(_("Seul un responsable peut changer les employés d'une activité."))
            elif vus != {moi}:
                frappe.throw(_("Vous ne pouvez créer des activités que pour vous-même."))
        if self.date_debut and self.date_prevue and str(self.date_prevue) < str(self.date_debut):
            frappe.throw(_("La date prévisionnelle ne peut pas précéder la date de début."))
        self._avancement_et_dates()

    def _avancement_et_dates(self):
        etapes = self.get("etapes") or []
        if etapes:
            faites = sum(1 for e in etapes if cint(e.fait))
            self.avancement = round(100.0 * faites / len(etapes))
            if faites and self.statut == "À faire":
                self.statut = "En cours"
        # Une activité sans date prévue prend celle de sa dernière étape datée (les étapes portent le planning).
        dates = [str(e.date_prevue) for e in etapes if e.date_prevue]
        if dates and not self.date_prevue and (not self.date_debut or max(dates) >= str(self.date_debut)):
            self.date_prevue = max(dates)
        if self.statut == "Terminée":
            self.avancement = 100
            self.date_fin = self.date_fin or nowdate()
        else:
            self.date_fin = None
