"""
Champ « Anomalie » sur la commande client, et calcul initial sur l'historique.

Rend filtrable et triable dans la liste ce que la coloration ne faisait que
montrer. Voir customization_app/commande_alertes.py pour la règle.

Le calcul complet est set-based : une requête pour déterminer les motifs, puis
une mise à jour par valeur distincte. Les ~9 700 commandes sont traitées sans
charger un seul document.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import CHAMP, MOTIFS, recalculer_tout


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": CHAMP,
                    "label": "Anomalie",
                    "fieldtype": "Select",
                    # Première option vide : commande saine.
                    "options": "\n".join([""] + MOTIFS),
                    "insert_after": "status",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    # Fait apparaître le champ directement dans la barre de
                    # filtres de la liste, sans passer par « Filtrer ».
                    "in_standard_filter": 1,
                    "module": "Customize erpnext",
                    "description": (
                        "Calculé automatiquement. Signale une commande sans intervention "
                        "planifiée, ou dont la tâche est terminée sans que la commande soit soldée."
                    ),
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()

    modifiees = recalculer_tout()
    print(f"[ensure_commande_anomalie_field] {modifiees} commande(s) qualifiée(s).")
