"""
Deux évolutions des anomalies de commande :

1. Nouveau motif « Tâche ouverte en retard » : une intervention dont la date
   est passée sans avoir été clôturée.

2. « Tâche annulée » est restreint aux commandes dont le paiement reste dû —
   échéancier de la commande ou de sa facture au mode « Dette non payée ».
   Une commande annulée et déjà réglée n'appelle aucune action. Le motif est
   renommé en conséquence, l'ancien libellé doit donc être purgé.

Voir customization_app/commande_alertes.py pour les règles.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import CHAMP, MOTIFS, recalculer_tout

ANCIEN_LIBELLE = "Tâche annulée, commande active"


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

    # Le libellé a changé : sans ce vidage, les commandes porteraient une valeur
    # absente des options du Select. recalculer_tout leur repose le bon motif.
    frappe.db.sql(
        f"UPDATE `tabSales Order` SET `{CHAMP}` = '' WHERE `{CHAMP}` = %(ancien)s",
        {"ancien": ANCIEN_LIBELLE},
    )
    frappe.db.commit()

    modifiees = recalculer_tout()
    print(f"[update_anomalie_retard_et_dette] {modifiees} commande(s) requalifiée(s).")
