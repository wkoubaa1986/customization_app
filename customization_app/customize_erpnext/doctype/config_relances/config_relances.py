import frappe
from frappe.model.document import Document


class ConfigRelances(Document):
    def onload(self):
        """26/11/2026 : un réglage ENTIER (case à cocher ou nombre) ajouté au formulaire après son premier enregistrement
        n'a pas de ligne en base. relances_config.config() lui donne son défaut, mais le formulaire l'afficherait à 0 et
        l'enregistrerait à 0 — c'est-à-dire « fonction coupée » (plus de solde « Fait lui-même », plus de réveil…), sans
        aucun signal. Le patch config_relances_reglages_entiers pose ceux connus le jour de la migration ; cette garde
        couvre ceux ajoutés plus tard : à l'ouverture, un tel champ reçoit le défaut que config() applique déjà."""
        try:
            from customization_app import relances_config as RC

            presents = set(frappe.db.sql_list("select field from `tabSingles` where doctype = %s", (self.doctype,)))
            if not presents:            # jamais enregistré : Frappe applique déjà les défauts du JSON
                return
            for champ, defaut in RC.DEFAUTS.items():
                if isinstance(defaut, int) and champ not in presents and self.meta.has_field(champ):
                    self.set(champ, defaut)
        except Exception:
            frappe.log_error(title="Config Relances — défauts à l'ouverture", message=frappe.get_traceback())

    def validate(self):
        """26/11/2026 (relecture finale) : un réglage ENTIER vide arrive à None et Frappe l'écrirait 0 = « fonction coupée »
        (formulaire chargé avant l'ajout du champ, ou nombre vidé). Il reprend son défaut, selon la règle du réglage :
        « un champ vide = la valeur historique ». 0 saisi reste 0. Un formulaire périmé est de toute façon refusé : le
        patch config_relances_reglages_entiers a avancé la date de modification."""
        try:
            from customization_app import relances_config as RC

            for champ, defaut in RC.DEFAUTS.items():
                if isinstance(defaut, int) and self.meta.has_field(champ) and self.get(champ) in (None, ""):
                    self.set(champ, defaut)
        except Exception:
            frappe.log_error(title="Config Relances — réglages absents à l'enregistrement", message=frappe.get_traceback())
