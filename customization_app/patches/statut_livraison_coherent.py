"""
Remet le statut de livraison en accord avec le % livré (22/10/2026).

« Régulariser sur le livré » passe la commande à 100 % par update_child_qty_rate, qui ne recalcule
pas delivery_status : SAL-ORD-2026-02915 (OKIKOOL) et SAL-ORD-2026-03111 (Amri Sadok) restaient dans
le filtre « Livré en Partie » à 100 %. Mesuré sur les 11 350 commandes de la prod du 22/10 : ce sont
les deux seules. Idempotent.
"""

import frappe

from customization_app.fermeture_commande import aligner, statut_livraison


def execute():
    a_corriger = [
        r.name for r in frappe.get_all(
            "Sales Order", filters={"docstatus": 1, "status": ["!=", "Cancelled"]},
            fields=["name", "status", "per_delivered", "delivery_status"], limit_page_length=0)
        if statut_livraison(r.status, r.per_delivered, r.delivery_status) != r.delivery_status
    ]
    corriges = aligner(a_corriger)
    print("[statut_livraison_coherent] %d commande(s) : %s" % (len(corriges), ", ".join(corriges[:20])))
