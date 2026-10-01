"""Prime Coefficient — part (en %) de la prime calculée accordée à un employé pour un trimestre.

Sans fiche : 100 %. Saisi depuis la page « Rapport Prime » (carte du trimestre).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PrimeCoefficient(Document):
    def validate(self):
        if flt(self.coefficient) < 0 or flt(self.coefficient) > 100:
            frappe.throw(_("Le coefficient doit être entre 0 et 100 %."))
        doublon = frappe.db.get_value("Prime Coefficient", {"employee": self.employee, "annee": self.annee,
                                                            "trimestre": self.trimestre, "name": ["!=", self.name]}, "name")
        if doublon:
            frappe.throw(_("Un coefficient existe déjà pour {0}, {1} {2} ({3}).")
                         .format(self.employee_name or self.employee, self.trimestre, self.annee, doublon))
