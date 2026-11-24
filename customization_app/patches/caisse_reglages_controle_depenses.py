"""Config Caisse : les réglages du contrôle des dépenses et de l'argent liquide (24/11/2026) reçoivent leur valeur par
défaut s'ils n'ont jamais été réglés — accord préalable au-delà de 200 DT en espèces, alerte quand un collecteur garde
plus de 5 000 DT ou plus de 3 jours.

Sans ce patch, le premier enregistrement de Config Caisse (ajout d'un délégué…) écrirait 0 dans ces champs encore vides :
l'accord préalable serait désactivé sans que personne l'ait décidé. Une valeur déjà posée (même 0) n'est pas touchée.
Idempotent."""
import frappe

DEFAUTS = {"seuil_accord_depense": 200, "plafond_collecteur": 5000, "jours_plafond_collecteur": 3}


def execute():
    poses = []
    for champ, valeur in DEFAUTS.items():
        if not frappe.db.sql("SELECT 1 FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = %s", champ):
            frappe.db.set_single_value("Config Caisse", champ, valeur)
            poses.append(champ)
    frappe.db.commit()
    return "posés : %s" % (", ".join(poses) or "aucun (déjà réglés)")
