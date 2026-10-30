"""Achats en magasin et marge sur la valorisation moyenne (décision utilisateur 30/10/2026).

  1. Tous les articles stockés de la société ont « Magasins - A&S » comme entrepôt par défaut
     (152 avaient « Hall ») ; le hook `achats_magasin` force de toute façon le magasin à la
     réception, mais les écrans de commande proposent ainsi le bon entrepôt d'emblée.
  2. La configuration de marge minimale prend pour coût la valorisation MOYENNE tous entrepôts :
     le plancher ne dépend plus du stock de sortie.
Idempotent.
"""
from __future__ import annotations

import frappe


def execute():
    from customization_app.achats_magasin import ENTREPOT_ACHATS
    from customization_app.marge_minimale import CONFIG, SOURCE_MOYENNE

    if frappe.db.exists("Warehouse", ENTREPOT_ACHATS):
        societe = frappe.db.get_value("Warehouse", ENTREPOT_ACHATS, "company")
        frappe.db.sql("""update `tabItem Default` d join `tabItem` i on i.name = d.parent
                         set d.default_warehouse = %s
                         where d.parenttype = 'Item' and d.company = %s and i.is_stock_item = 1
                           and ifnull(d.default_warehouse, '') != %s""",
                      (ENTREPOT_ACHATS, societe, ENTREPOT_ACHATS))
    if frappe.db.exists("DocType", CONFIG) and frappe.db.exists(CONFIG, CONFIG):
        frappe.db.set_single_value(CONFIG, "source_cout", SOURCE_MOYENNE)
    frappe.db.commit()
