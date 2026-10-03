import frappe
from frappe import _
from frappe.model.document import Document

from customization_app.modeles_sms import DEFAUTS, analyser


class ConfigModelesSMS(Document):
    def validate(self):
        # On prévient, on ne bloque pas : un texte en unicode reste envoyable, il coûte seulement plus cher.
        avis = []
        for champ in DEFAUTS:
            a = analyser(self.get(champ) or "")
            if a["unicode"]:
                avis.append(_("{0} : caractères hors alphabet SMS {1} → message en unicode (67 caractères par segment au lieu de 153)")
                            .format(self.meta.get_label(champ), " ".join(repr(c) for c in a["hors_gsm"])))
        if avis:
            frappe.msgprint("<br>".join(avis), title=_("Coût des SMS"), indicator="orange")
