"""Noms lisibles des véhicules de la flotte GPS (information de l'utilisateur, 16/10/2026) : la 261 TU 3554 est la Cenntro
Logistar 100 (utilitaire électrique, 16 kWh — elle dort au Magasin pour charger), la 5957 TU 232 la Fiat Fiorino, la
261 TU 3647 le Changan New Star. Le libellé venait de la plateforme (LVEH) : on le remplace dans le réglage ET dans
l'historique (journées véhicule, écarts de circuit, tâches appariées) pour que les regroupements restent cohérents.
Idempotent : un libellé déjà renommé n'est plus touché."""

import frappe

NOMS = {
    "261 tu 3554": "261 TU 3554 Cenntro Logistar 100 (électrique)",
    "5957 TU 232": "5957 TU 232 Fiat Fiorino",
    "261 tu 3647 Changan New STAR": "261 TU 3647 Changan New Star",
}
TABLES = (("Config Flotte GPS Vehicule", "vehicule"), ("Journee Flotte GPS", "vehicule"), ("Ecart Circuit GPS", "vehicule"),
          ("Tache de travail", "custom_gps_vehicule"))


def execute():
    for doctype, champ in TABLES:
        if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, champ):
            continue
        for ancien, nouveau in NOMS.items():
            frappe.db.sql("update `tab%s` set `%s` = %%s where `%s` = %%s" % (doctype, champ, champ), (nouveau, ancien))
    frappe.db.commit()
