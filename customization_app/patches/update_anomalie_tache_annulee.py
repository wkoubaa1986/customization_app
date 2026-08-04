"""
Ajoute le motif « Tâche annulée, commande active » au champ Anomalie.

Le champ ayant déjà été créé par ensure_commande_anomalie_field, ses options
doivent être étendues et l'ensemble des commandes recalculé.

La règle : si la DERNIÈRE tâche d'une commande est annulée, plus rien n'est
prévu. Une tâche annulée puis replanifiée ne ressort pas, la dernière étant
alors active. Voir customization_app/commande_alertes.py.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import CHAMP, MOTIFS, recalculer_tout


def execute():
    if not frappe.db.exists("Custom Field", f"Sales Order-{CHAMP}"):
        # ensure_commande_anomalie_field passera après et posera la bonne liste.
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
    print(f"[update_anomalie_tache_annulee] {modifiees} commande(s) requalifiée(s).")
