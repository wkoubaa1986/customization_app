"""
Rattrape les commandes client bloquées sous 100 % livré alors que leurs bons
de livraison validés couvrent déjà la totalité du montant TTC.

Cause : lors d'un échange d'article, le BL porte l'article réellement remis,
non rattaché à la ligne de commande. ERPNext calculant `per_delivered` sur les
quantités jointes par `against_sales_order`, la commande reste à 50 % ou 66,7 %.

Mesuré avant correction : 49 commandes concernées, dont 48 en livraison
partielle. Voir customization_app/per_delivered_montant.py pour la règle,
appliquée en continu sur les nouveaux BL.
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
          AND so.per_delivered < 100
          AND so.grand_total > 0
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
                title=f"fix_per_delivered_echange — {nom}", message=frappe.get_traceback()
            )

    frappe.db.commit()
    print(
        f"[fix_per_delivered_echange] {len(corriges)} commande(s) passée(s) à 100 % livré "
        f"sur {len(candidats)} candidate(s)."
    )
