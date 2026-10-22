"""
Un retour vaut livraison (décision utilisateur, 22/10/2026) : passe à 100 % livré les commandes
ouvertes dont les retours expliquent tout ce qui n'a pas été livré (cf. per_delivered_montant).

Mesuré sur la prod du 22/10 : WEB1-007852 (électrovanne retournée, soldée par avoir) et
SAL-ORD-2026-03713 (cartouche retournée le jour même). Idempotent.
"""

import frappe

from customization_app.per_delivered_montant import aligner_commande


def execute():
    candidates = frappe.db.sql_list("""
        SELECT DISTINCT so.name FROM `tabSales Order` so
        JOIN `tabSales Order Item` i ON i.parent = so.name
        WHERE so.docstatus = 1 AND so.status NOT IN ('Closed', 'Completed', 'Cancelled')
          AND so.per_delivered < 100 AND i.returned_qty > 0""")
    alignees = [nom for nom in candidates if aligner_commande(nom)]
    if alignees:
        try:
            from customization_app.commande_alertes import recalculer
            recalculer(alignees)
        except Exception:
            frappe.log_error(title="livre_par_retour — anomalies", message=frappe.get_traceback())
    print("[livre_par_retour] %d commande(s) passée(s) à 100 %% : %s" % (len(alignees), ", ".join(alignees)))
