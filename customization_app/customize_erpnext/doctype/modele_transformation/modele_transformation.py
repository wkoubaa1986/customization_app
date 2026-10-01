"""Modele Transformation — une transformation « unitaire » réutilisable (page Transformation d’articles).

Les quantités sont celles d'UNE application ; la page les multiplie par le nombre
d'applications choisi avant de créer l'écriture Reconditionnement.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ModeleTransformation(Document):
    def validate(self):
        if not self.consommes or not self.obtenus:
            frappe.throw(_("Un modèle a au moins un article consommé et un article obtenu."))
        for l in list(self.consommes) + list(self.obtenus):
            if flt(l.qty) <= 0:
                frappe.throw(_("Quantité invalide pour {0}.").format(l.item_code))
        memes = {l.item_code for l in self.consommes} & {l.item_code for l in self.obtenus}
        if memes:
            frappe.throw(_("Même article des deux côtés : {0}").format(", ".join(sorted(memes))))
