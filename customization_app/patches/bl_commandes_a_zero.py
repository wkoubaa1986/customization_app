"""
BL des commandes à 0 DT restées sans BL (22/10/2026).

Le BL automatique (fin du Server Script « Generation payement ») ne couvre les commandes à 0 DT que
depuis le 13/10/2026 (v5.158.1) ; avant, il était sous la condition des paiements. Les commandes à 0
validées avant cette date n'ont jamais eu de BL : leur article n'est pas sorti du stock et elles
restent « Non livré ». Cas réel : SAL-ORD-2026-04148 (Slim Ghribi, 07/10, porte-filtre P-F-10'-O).

Même logique que le script : BL tiré de la commande, daté de la livraison prévue, validé
directement sauf si un article exige la validation du magasin (il attend alors « En attente
validation magasin »). Idempotent : une commande qui a déjà un BL (brouillon ou validé) est ignorée.
"""

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note


def execute():
    commandes = frappe.db.sql_list("""
        SELECT so.name FROM `tabSales Order` so
        WHERE so.docstatus = 1
          AND so.status NOT IN ('Draft', 'Cancelled', 'Closed', 'On Hold', 'Completed')
          AND so.grand_total <= 0 AND IFNULL(so.per_delivered, 0) < 100
          AND NOT EXISTS (
            SELECT 1 FROM `tabDelivery Note Item` dni
            JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dni.against_sales_order = so.name AND dn.docstatus < 2)""")

    crees = []
    for nom in commandes:
        try:
            so = frappe.get_doc("Sales Order", nom)
            bl = make_delivery_note(nom)
            bl.insert(ignore_permissions=True)
            bl.set_posting_time = 1
            bl.posting_date = so.delivery_date
            if bl.get("custom_requires_store_approval"):
                bl.workflow_state = "En attente validation magasin"
                bl.save(ignore_permissions=True)
            else:
                bl.submit()
            bl.add_comment("Comment", "BL créé par le patch bl_commandes_a_zero (22/10/2026) : commande "
                                      "à 0 DT validée avant le BL automatique du 13/10/2026.")
            crees.append("%s → %s" % (nom, bl.name))
        except Exception:
            frappe.log_error(title="bl_commandes_a_zero — %s" % nom, message=frappe.get_traceback())

    print("[bl_commandes_a_zero] %d BL créé(s) : %s" % (len(crees), ", ".join(crees)))
