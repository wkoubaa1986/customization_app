"""Regle Recuperation — un jour de récupération toutes les N semaines pour UN employé (pas tous).

Lue par customization_app.conges_recuperations.planifier_recuperations (page
« Congés & récupérations » + tâche quotidienne) : chaque jour planifié devient une
demande de congé approuvée de type « Récupération (quinzaine) » + une Tâche de
travail « Jour de récupération » sur toute la journée dans le calendrier.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RegleRecuperation(Document):
    def validate(self):
        if cint(self.periodicite_semaines) < 1:
            frappe.throw(_("La périodicité est d’au moins 1 semaine."))
        if cint(self.horizon_semaines) < 1:
            frappe.throw(_("L’horizon est d’au moins 1 semaine."))
