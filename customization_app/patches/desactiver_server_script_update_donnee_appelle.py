"""Le Server Script « update donnee appelle » (After Save sur Liste Appelle Entretien, en base seulement) est remplacé par
le hook customization_app.liste_appels.synchroniser_appels (03/10/2026). Éteint ici, jamais supprimé."""
import frappe


def execute():
    if frappe.db.exists("Server Script", "update donnee appelle"):
        frappe.db.set_value("Server Script", "update donnee appelle", "disabled", 1)
        frappe.clear_cache()
