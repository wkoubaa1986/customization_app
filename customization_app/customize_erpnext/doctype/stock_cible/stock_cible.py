"""Stock cible d'un entrepôt d'employé : ce que le véhicule doit contenir, article par article.
Le réassort = cible − stock actuel, transféré depuis le Magasin (page Stock par entrepôt)."""

import frappe
from frappe import _
from frappe.model.document import Document


class StockCible(Document):
    def validate(self):
        vus, lignes = set(), []
        for r in self.get("lignes") or []:
            if not r.item_code or r.item_code in vus:
                continue
            if (r.qte_cible or 0) < 0:
                frappe.throw(_("Quantité cible négative pour {0}.").format(r.item_code))
            vus.add(r.item_code)
            lignes.append(r)
        self.set("lignes", lignes)
        if frappe.db.get_value("Warehouse", self.entrepot, "is_group"):
            frappe.throw(_("{0} est un groupe d’entrepôts.").format(self.entrepot))
