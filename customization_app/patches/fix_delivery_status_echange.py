"""
Rattrape le statut de livraison des commandes soldées au montant.

Deux corrections en une :

1. `delivery_status` restait « Partly Delivered » sur les 48 commandes passées
   à 100 % par fix_per_delivered_echange : ce champ est distinct de
   `per_delivered` et lui aussi calculé sur les quantités, si bien que la liste
   continuait d'afficher « Livrée en partie ».

2. La tolérance MARGE est passée à 1 DT : les commandes dont les BL couvrent le
   TTC à un dinar près doivent maintenant être considérées intégralement
   livrées.

Voir customization_app/per_delivered_montant.py pour la règle.
"""

import frappe

from customization_app.per_delivered_montant import MARGE, aligner_commande


def execute():
    candidats = frappe.db.sql(
        """
        SELECT so.name
        FROM `tabSales Order` so
        WHERE so.docstatus = 1
          AND so.status NOT IN ('Closed', 'Cancelled')
          AND so.grand_total > 0
          AND (so.per_delivered < 100 OR COALESCE(so.delivery_status, '') <> 'Fully Delivered')
          AND COALESCE((
                SELECT SUM(dn.grand_total) FROM `tabDelivery Note` dn
                WHERE dn.docstatus = 1 AND EXISTS (
                    SELECT 1 FROM `tabDelivery Note Item` dni
                    WHERE dni.parent = dn.name AND dni.against_sales_order = so.name)
              ), 0) >= so.grand_total - %(marge)s
        ORDER BY so.name
    """,
        {"marge": MARGE},
        pluck=True,
    )

    corriges = []
    for nom in candidats:
        try:
            if aligner_commande(nom):
                corriges.append(nom)
        except Exception:
            frappe.log_error(
                title=f"fix_delivery_status_echange — {nom}", message=frappe.get_traceback()
            )

    frappe.db.commit()
    print(
        f"[fix_delivery_status_echange] {len(corriges)} commande(s) alignée(s) "
        f"sur {len(candidats)} candidate(s)."
    )
