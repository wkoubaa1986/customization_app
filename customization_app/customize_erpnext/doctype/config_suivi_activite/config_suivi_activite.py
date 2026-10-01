"""Qui a accès à l'onglet « Suivi d'activité » — et qui y est responsable.

L'enregistrement synchronise les rôles des comptes utilisateurs : « Suivi Activité » pour
chaque employé listé, « Responsable Activité » en plus pour ceux cochés. Un employé retiré de
la liste perd les deux rôles (et donc l'onglet). Seuls ces deux rôles sont touchés.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ConfigSuiviActivite(Document):
    def validate(self):
        vus = set()
        for r in self.get("employes") or []:
            if r.employee in vus:
                frappe.throw(_("{0} figure deux fois dans la liste.").format(r.nom or r.employee))
            vus.add(r.employee)
            r.utilisateur = frappe.db.get_value("Employee", r.employee, "user_id") or None

    def on_update(self):
        from customization_app import suivi_activite as SA

        self._sans_compte = SA.synchroniser_roles(self)
        if self._sans_compte:
            frappe.msgprint(_("Sans compte utilisateur sur leur fiche Employé, ces employés ne verront pas l'onglet : {0}")
                            .format(", ".join(self._sans_compte)), indicator="orange")
