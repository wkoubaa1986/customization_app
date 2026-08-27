"""
« Livraison Aramex sans bordereau » : un FAIT à côté de l'anomalie.

D'abord conçu comme un motif du CASE, il aurait remplacé l'anomalie classique
(« Tâche terminée, commande non soldée »…) sur la même commande. Décision du
27/08/2026 : les DEUX doivent se voir — même modèle que « Retour colis » : un
champ Check indépendant, sa propre pastille orange, son propre filtre.

La règle (commande_alertes._SQL_ARAMEX_SB) : échéancier « Livraison Aramex »,
colis parti (BL validé OU tâche Livraison terminée), pas encore encaissé, et
AUCUN bordereau — ni sur la commande, ni dans un paiement du compte Aramex
(via la commande ou ses factures).

Ce patch remet aussi les options du Select Anomalie à leur liste de motifs
(le libellé Aramex, brièvement ajouté au Select en dev, n'en fait plus partie).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.commande_alertes import (
    CHAMP,
    CHAMP_ARAMEX_SB,
    MOTIFS,
    recalculer_tout,
)


def execute():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": CHAMP_ARAMEX_SB,
                    "fieldtype": "Check",
                    "label": "Livraison Aramex faite sans bordereau",
                    "default": "0",
                    "read_only": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "in_standard_filter": 1,
                    "insert_after": "custom_statut_aramex",
                    "module": "Customize erpnext",
                    "description": "Colis Aramex parti (BL validé ou tâche Livraison terminée), pas encore encaissé, sans aucun bordereau enregistré — coexiste avec l'anomalie classique.",
                }
            ]
        },
        ignore_validate=True,
    )

    if frappe.db.exists("Custom Field", f"Sales Order-{CHAMP}"):
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
    print(f"[update_anomalie_aramex_sans_bordereau] {modifiees} commande(s) requalifiée(s).")
