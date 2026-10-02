"""Réglage de la page « Stock par entrepôt » : quel entrepôt est le Magasin (il porte les écarts), quels
autres entrepôts ne sont jamais remis à zéro, les seuils de réapprovisionnement et le planning des
vérifications hebdomadaires des stocks d'employés."""

import frappe
from frappe import _
from frappe.model.document import Document

from customization_app.stock_entrepots import articles_non_suivis


class ConfigStockEntrepot(Document):
    def validate(self):
        # Un stock cible ne parle que d'articles suivis en stock : un service ou un article désactivé
        # n'a pas de quantité à viser, et le réassort ne saurait pas le transférer.
        mauvais = articles_non_suivis([r.item_code for r in self.get("modele_cible") or [] if r.item_code])
        if mauvais:
            frappe.throw(_("Stock cible : articles non suivis en stock (ou désactivés) : {0}").format(", ".join(mauvais)))
        vus, lignes = set(), []
        for r in self.get("entrepots_exclus") or []:
            if r.entrepot and r.entrepot not in vus and r.entrepot != self.entrepot_magasin:
                vus.add(r.entrepot)
                lignes.append(r)
        self.set("entrepots_exclus", lignes)
        if self.entrepot_magasin and frappe.db.get_value("Warehouse", self.entrepot_magasin, "is_group"):
            frappe.throw(_("Le Magasin doit être un entrepôt, pas un groupe d’entrepôts."))
        vus = set()
        for r in self.get("seuils") or []:
            if r.entrepot in vus:
                frappe.throw(_("Seuil : l’entrepôt {0} figure deux fois.").format(r.entrepot))
            vus.add(r.entrepot)
            if (r.seuil or 0) < 0:
                frappe.throw(_("Seuil : la valeur de {0} doit être positive.").format(r.entrepot))
            if not r.cible or r.cible < r.seuil:
                r.cible = r.seuil
        vus = set()
        for r in self.get("verifications") or []:
            if r.entrepot in vus:
                frappe.throw(_("Vérification : le stock {0} figure deux fois.").format(r.entrepot))
            vus.add(r.entrepot)
            if not frappe.db.exists("Employee", {"custom_warehouse": r.entrepot, "status": "Active"}):
                frappe.throw(_("Vérification : aucun employé actif n’a {0} pour stock (fiche Employé, champ Warehouse).")
                             .format(r.entrepot))
