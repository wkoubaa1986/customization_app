"""Réglage de la marge minimale de vente (voir customization_app.marge_minimale)."""

import frappe
from frappe import _
from frappe.model.document import Document

from customization_app.marge_minimale import BASE_PRIX


class ConfigMargeMinimale(Document):
    def validate(self):
        if (self.taux or 0) < 0:
            frappe.throw(_("La marge minimale doit être positive."))
        if (self.base or BASE_PRIX) == BASE_PRIX and (self.taux or 0) >= 100:
            frappe.throw(_("Une marge sur le prix de vente ne peut pas atteindre 100 %."))
        vus, lignes = set(), []
        for r in self.get("groupes_exclus") or []:
            if r.item_group and r.item_group not in vus:
                vus.add(r.item_group)
                lignes.append(r)
        self.set("groupes_exclus", lignes)
