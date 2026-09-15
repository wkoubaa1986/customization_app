"""
Retire le bouton rouge « Créer un Échéancier d'Entretien » de la fiche commande.

Il venait du Client Script « generation bouton pour maintenace » (Sales Order), posé en base
et jamais versionné ; l'utilisateur ne s'en sert jamais (demande du 15/09/2026). Le script
est désactivé, pas supprimé : le mécanisme serveur (case custom_generate_maintenace_schedule
+ Server Script) reste intact si on veut le remettre.
"""

import frappe

NOM = "generation bouton pour maintenace"


def execute():
    if not frappe.db.exists("Client Script", NOM):
        return
    frappe.db.set_value("Client Script", NOM, "enabled", 0, update_modified=False)
    frappe.clear_cache(doctype="Sales Order")
    frappe.db.commit()
