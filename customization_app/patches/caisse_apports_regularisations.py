"""Apports du coffre dans la journée de caisse, et régularisations (25/11/2026, argent_liquide / caisse_regularisations).

Config Caisse « apports_depuis » = DEMAIN, s'il est vide : à partir de ce jour, un transfert du coffre (Sabah Espèces)
ou de la banque vers Espèces, saisi par le titulaire d'une caisse, est une entrée de SA journée de caisse (et plus de
l'argent « chez le collecteur »). Demain, pas aujourd'hui : les caisses d'aujourd'hui ont pu être comptées avant la mise
en route. L'historique n'est pas rebalayé."""
import frappe
from frappe.utils import add_days, nowdate


def execute():
    deja = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'apports_depuis'")
    if not deja or not deja[0][0]:
        frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'apports_depuis'")
        frappe.db.sql("INSERT INTO `tabSingles` (doctype, field, value) VALUES ('Config Caisse', 'apports_depuis', %s)",
                      (str(add_days(nowdate(), 1)),))
    frappe.clear_cache(doctype="Config Caisse")
    frappe.db.commit()
