"""
Nouveau motif d'anomalie « Livraison partielle, dette surévaluée ».

Une commande sortie en plusieurs bons de livraison porte une ligne « Dette non
payée » égale à commande − réglé : la marchandise jamais livrée y est comptée
comme une dette du client. Le motif la signale ; le bouton « Régulariser sur
le livré » de la fiche la corrige. Voir customization_app/livraison_partielle.py.

Le patch ajoute l'option au Select « Anomalie » et requalifie toute la base.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import CHAMP, MOTIFS, recalculer_tout


def execute():
    if not frappe.db.exists("Custom Field", f"Sales Order-{CHAMP}"):
        # ensure_commande_anomalie_field passera ensuite avec la bonne liste.
        return

    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": CHAMP,
                    "fieldtype": "Select",
                    "options": "\n".join([""] + MOTIFS),
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()

    modifiees = recalculer_tout()
    print(f"[update_anomalie_livraison_partielle] {modifiees} commande(s) requalifiée(s).")
