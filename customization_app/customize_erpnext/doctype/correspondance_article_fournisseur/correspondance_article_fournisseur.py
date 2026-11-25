import frappe
from frappe import _
from frappe.model.document import Document


class CorrespondanceArticleFournisseur(Document):
    """La mémoire « article du fournisseur → notre article » (25/11/2026, customization_app.correspondance_articles).

    Saisie ou retouchée À LA MAIN depuis la liste : la clé (fournisseur + code, sinon désignation normalisée) est
    recalculée comme l'apprentissage la calcule — sans elle, la correspondance ne serait jamais trouvée."""

    def validate(self):
        if self.flags.cle_calculee:          # posée par correspondance_articles.apprendre
            return
        from customization_app.correspondance_articles import cles

        toutes = cles(self.supplier, self.code_fournisseur, self.designation)
        if not toutes:
            frappe.throw(_("Indiquez le code article du fournisseur ou sa désignation."))
        self.cle = toutes[0]
        autre = frappe.db.get_value(self.doctype, {"cle": self.cle, "name": ["!=", self.name or ""]}, "name")
        if autre:
            frappe.throw(_("Cette correspondance existe déjà : {0}. Modifiez-la plutôt.")
                         .format(frappe.utils.get_link_to_form(self.doctype, autre)))
        if self.is_new() and not self.source:
            self.source = "Saisie manuelle"
        if not self.fois:
            self.fois = 1
