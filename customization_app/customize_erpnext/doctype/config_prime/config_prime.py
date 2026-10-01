"""Config Prime — taux (ventes, main-d'œuvre) et mode de calcul par employé.

Lue par customization_app.rapport_prime. Un employé absent de la table est en
mode « Ventes » ; « Exclu » le retire du rapport.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ConfigPrime(Document):
    def validate(self):
        for champ in ("taux_vente", "taux_main_oeuvre"):
            if flt(self.get(champ)) < 0 or flt(self.get(champ)) > 100:
                frappe.throw(_("Le taux {0} doit être entre 0 et 100 %.").format(_(self.meta.get_label(champ))))
        vus = set()
        for l in self.employes or []:
            if l.employee in vus:
                frappe.throw(_("L'employé {0} apparaît deux fois.").format(l.employee_name or l.employee))
            vus.add(l.employee)
