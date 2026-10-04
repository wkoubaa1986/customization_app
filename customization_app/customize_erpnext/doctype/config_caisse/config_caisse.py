# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ConfigCaisse(Document):
    # La logique de collecte vit dans customization_app/caisse_collecte.py.

    def validate(self):
        # « Date de départ par caisse » : le nom doit être une caisse du rapport, sinon la date
        # ne s'appliquerait à personne (faute de frappe silencieuse).
        from customization_app.rapport_caisse_journaliere import noms_caisses
        connus = set(noms_caisses())
        vus = set()
        for r in self.get("departs") or []:
            nom = (r.caisse or "").strip()
            r.caisse = nom
            if nom not in connus:
                frappe.throw(_("Ligne {0} : « {1} » n'est pas une caisse du rapport. Noms acceptés : {2}")
                             .format(r.idx, nom, ", ".join(sorted(connus))))
            if nom in vus:
                frappe.throw(_("Ligne {0} : la caisse « {1} » est en double.").format(r.idx, nom))
            vus.add(nom)
