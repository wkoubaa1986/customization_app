"""Dépense (pièce comptable) omise du calcul de la Situation mensuelle — en tout ou en partie.

Une ligne par (rubrique, pièce). Lue par customization_app.situation_mensuelle ; posée depuis la
page « Situation mensuelle » (boutons Omettre / Rétablir), avec motif obligatoire.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ExclusionSituation(Document):
    def validate(self):
        if self.mode == "Partielle" and flt(self.montant_exclu) <= 0:
            frappe.throw(_("Indiquez le montant à omettre."))
        doublon = frappe.db.get_value("Exclusion Situation", {"rubrique": self.rubrique, "voucher_type": self.voucher_type,
                                                              "voucher_no": self.voucher_no, "name": ["!=", self.name]})
        if doublon:
            frappe.throw(_("Cette pièce est déjà omise de la rubrique {0} ({1}).").format(self.rubrique, doublon))
