"""Le Server Script « renommer tournee » (créé à la main le 26/04/2026, jamais en fixture) tourne avant l'insertion
de chaque « Tournee Commerciale » et fait `doc.nom_employe + "-" + str(doc.date_debut)` : ces champs n'existent plus
sur le DocType (commercial, date_tournee). Résultat : TypeError à chaque création de tournée — aucune tournée n'a
jamais pu être créée (0 en base au 04/11/2026), d'où un outil « Ma Tournée » inutilisé.

Joué à chaque migrate (after_migrate), idempotent : désactive le script s'il existe encore et vide le cache des
scripts (ils sont mis en cache par événement). Le nom des tournées est TC-AAAA-NNNNN (autoname du DocType)."""
import frappe

SCRIPT = "renommer tournee"


def execute():
    if not frappe.db.exists("Server Script", SCRIPT):
        return
    if frappe.db.get_value("Server Script", SCRIPT, "disabled"):
        return
    frappe.db.set_value("Server Script", SCRIPT, "disabled", 1, update_modified=False)
    frappe.cache().delete_value("server_script_map")
