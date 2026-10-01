"""Réglage de la page « Stock par entrepôt » : quel entrepôt est le Magasin (il porte les écarts), et
quels autres entrepôts ne sont jamais proposés à la remise à zéro."""

import frappe
from frappe import _
from frappe.model.document import Document


class ConfigStockEntrepot(Document):
    def validate(self):
        vus, lignes = set(), []
        for r in self.get("entrepots_exclus") or []:
            if r.entrepot and r.entrepot not in vus and r.entrepot != self.entrepot_magasin:
                vus.add(r.entrepot)
                lignes.append(r)
        self.set("entrepots_exclus", lignes)
        if self.entrepot_magasin and frappe.db.get_value("Warehouse", self.entrepot_magasin, "is_group"):
            frappe.throw(_("Le Magasin doit être un entrepôt, pas un groupe d’entrepôts."))
