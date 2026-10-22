"""
Champ « Pastille livraison » des commandes, filtrable dans la liste (demande 22/10/2026).

Les pastilles « BL à valider » / « Pas totalement livrée » étaient calculées à l'affichage : on ne
pouvait pas filtrer dessus. Elles sont désormais stockées (commande_alertes.CHAMP_PASTILLE_LIVRAISON),
recalculées avec l'anomalie. Le patch crée le champ — les fixtures ne sont synchronisées qu'APRÈS les
patches au migrate — puis le remplit pour toutes les commandes. Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import (
    CHAMP_PASTILLE_LIVRAISON, _calculer_pastille_livraison, _stocker_pastille_livraison)

CHAMP = {
    "fieldname": CHAMP_PASTILLE_LIVRAISON,
    "label": "Pastille livraison",
    "fieldtype": "Select",
    "options": "\nBL à valider\nPas totalement livrée",
    "insert_after": "custom_aramex_sans_bordereau",
    "in_standard_filter": 1,
    "read_only": 1,
    "allow_on_submit": 1,
    "no_copy": 1,
    "module": "Customize erpnext",
    "description": "Calculé automatiquement : un BL en brouillon attend sa validation, ou la commande "
                   "n'est pas entièrement livrée sans qu'aucun BL ne soit en attente.",
}


def execute():
    create_custom_fields({"Sales Order": [CHAMP]}, ignore_validate=True)
    frappe.clear_cache(doctype="Sales Order")
    n = _stocker_pastille_livraison(_calculer_pastille_livraison("so.docstatus < 2"))
    print("[pastille_livraison_commande] %d commande(s) marquée(s)" % n)
