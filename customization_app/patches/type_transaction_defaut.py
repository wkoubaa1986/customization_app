"""Commande : « Vente directe sans BL et Facture » devient le type de transaction par défaut (18/11/2026).

Le champ `custom_type_de_transaction` (obligatoire, créé en base) s'ouvrait vide : il fallait le choisir à
chaque commande, alors que 7 commandes sur 10 sont des ventes directes. Ce patch pose la valeur par défaut.
Tous les programmes qui créent une commande (clôture de tâche, sous-traitant, site web…) fixent déjà leur
type : la valeur par défaut ne concerne que le formulaire. Idempotent ; ne touche pas un défaut déjà posé.
"""
import frappe

CHAMP = "Sales Order-custom_type_de_transaction"
DEFAUT = "Vente directe sans BL et Facture"


def execute():
    if not frappe.db.exists("Custom Field", CHAMP):
        return "champ absent"
    options = (frappe.db.get_value("Custom Field", CHAMP, "options") or "").split("\n")
    if DEFAUT not in options:
        return "option absente"
    if frappe.db.get_value("Custom Field", CHAMP, "default"):
        return "déjà posé"
    frappe.db.set_value("Custom Field", CHAMP, "default", DEFAUT, update_modified=False)
    frappe.db.commit()
    frappe.clear_cache(doctype="Sales Order")
    return "posé"
