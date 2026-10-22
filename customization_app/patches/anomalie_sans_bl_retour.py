"""
Recalcule l'anomalie des commandes qui ont un BL de retour validé (22/10/2026).

« Tâche terminée, commande non soldée » ne compte plus les BL de retour (un retour vaut livraison) :
WEB1-007852 (Sahbi Chouchane) restait signalée à 230 − 140 = 90. Le recalcul de nuit le ferait ; le
patch le fait au déploiement. Sans effet de bord (pas de clôture de tâche). Idempotent.
"""

import frappe

from customization_app.commande_alertes import recalculer


def execute():
    noms = frappe.db.sql_list("""
        SELECT DISTINCT dni.against_sales_order FROM `tabDelivery Note Item` dni
        JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dn.docstatus = 1 AND dn.is_return = 1 AND IFNULL(dni.against_sales_order, '') <> ''""")
    n = recalculer(noms) if noms else 0
    print("[anomalie_sans_bl_retour] %d commande(s) avec retour, %d motif(s) modifié(s)" % (len(noms), n or 0))
