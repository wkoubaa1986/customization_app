"""Éteint les deux Server Scripts de l'ancien mécanisme d'échange, que
`retour_echange` remplace.

« Validation Stock negative » (Before Insert, Delivery Note) remplaçait la
ligne « E-… » du BL par la pièce remise (au prorata du prix), créait une
réconciliation de stock pour la pièce reprise et basculait « Commande client
requise » à Non pour tout le site ; « Valider Le bon livraison avec valeur
negatif » (After Submit) validait cette réconciliation et remettait le réglage
à Oui. Désormais la ligne « E-… » reste sur le BL (rattachée à la commande) et
la pièce reprise rentre par un BL retour créé à la validation.

« cancel bon de livraison » (After Cancel) reste actif : il annule la
réconciliation des BL d'échange passés, qui ne changent pas.

Désactivés, pas supprimés : réversible d'une case à cocher.
"""
import frappe

SCRIPTS = (
    "Validation Stock negative",
    "Valider Le bon livraison avec valeur negatif",
)


def execute():
    eteints = []
    for nom in SCRIPTS:
        if not frappe.db.exists("Server Script", nom):
            continue
        if frappe.db.get_value("Server Script", nom, "disabled"):
            continue
        frappe.db.set_value("Server Script", nom, "disabled", 1)
        eteints.append(nom)
    # Le réglage est laissé sur Oui même si un ancien BL a été inséré sans être validé.
    if frappe.db.get_single_value("Selling Settings", "so_required") != "Yes":
        frappe.db.set_single_value("Selling Settings", "so_required", "Yes")
        eteints.append("Selling Settings.so_required -> Yes")
    if eteints:
        frappe.clear_cache()   # les Server Scripts sont en cache
        frappe.db.commit()
    print(f"[desactiver_server_scripts_echange] {eteints or 'rien à faire'}")
    return eteints
