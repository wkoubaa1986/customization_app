"""Commandes Aramex : le stock sort du Magasin et y revient, quelle que soit la sortie principale.
Vraies commandes de la base, BL créés dans un savepoint : tout est annulé."""
from __future__ import annotations

import unittest

import frappe

from customization_app import aramex_stock as AS

HALL = "Hall - A&S"


def _commande(aramex: bool) -> str | None:
    """Une commande validée, rien de livré, avec au moins un article stocké."""
    cond = ("so.payment_terms_template = %s" if aramex else
            "ifnull(so.payment_terms_template, '') != %s and ifnull(so.custom_bordereau_aramex, '') = ''")
    r = frappe.db.sql(f"""select so.name from `tabSales Order` so
                          where so.docstatus = 1 and so.per_delivered = 0
                            and so.status in ('To Deliver', 'To Deliver and Bill')
                            and {cond}
                            and exists (select 1 from `tabSales Order Item` soi join tabItem i on i.name = soi.item_code
                                        where soi.parent = so.name and i.is_stock_item = 1)
                          order by so.creation desc limit 1""", AS.TERMES_ARAMEX)
    return r[0][0] if r else None


def _bl_depuis(so: str, entrepot: str):
    """BL de la commande, chaque ligne et chaque composant forcés sur `entrepot` (sortie principale « Hall »)."""
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    dn = make_delivery_note(so)
    dn.set_warehouse = entrepot
    for l in dn.items:
        l.warehouse = entrepot
    for p in dn.get("packed_items") or []:
        p.warehouse = entrepot
    return dn


class TestAramexStock(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("aramex_stock")
        if frappe.db.get_value("Warehouse", HALL, "disabled") != 0 or not AS._entrepot_utilisable():
            self.skipTest("Hall ou Magasin désactivé")

    def tearDown(self):
        frappe.db.rollback(save_point="aramex_stock")

    def _stockes(self, doc):
        stockes = set(frappe.get_all("Item", filters={"is_stock_item": 1,
                                                      "name": ["in", [l.item_code for l in doc.items]]}, pluck="name"))
        return [l for l in doc.items if l.item_code in stockes]

    def test_hooks_branches(self):
        events = frappe.get_hooks("doc_events")
        self.assertIn("customization_app.aramex_stock.delivery_note_before_validate",
                      events["Delivery Note"]["before_validate"])
        self.assertIn("customization_app.aramex_stock.sales_order_before_validate",
                      events["Sales Order"]["before_validate"])

    def test_commande_reconnue(self):
        self.assertTrue(AS.est_aramex(AS.TERMES_ARAMEX, None))
        self.assertTrue(AS.est_aramex(None, " 51330112551 "))
        self.assertFalse(AS.est_aramex("Paiement comptant", "  "))

    def test_bl_aramex_sort_du_magasin(self):
        so = _commande(aramex=True)
        if not so:
            self.skipTest("aucune commande Aramex à livrer")
        dn = _bl_depuis(so, HALL)
        dn.insert(ignore_permissions=True)
        lignes = self._stockes(dn)
        self.assertTrue(lignes)
        self.assertEqual({l.warehouse for l in lignes}, {AS.ENTREPOT_ARAMEX})
        self.assertEqual({p.warehouse for p in dn.get("packed_items") or []} - {AS.ENTREPOT_ARAMEX}, set())

    def test_bl_ordinaire_garde_sa_sortie(self):
        so = _commande(aramex=False)
        if not so:
            self.skipTest("aucune commande ordinaire à livrer")
        dn = _bl_depuis(so, HALL)
        dn.insert(ignore_permissions=True)
        self.assertEqual({l.warehouse for l in self._stockes(dn)}, {HALL})

    def test_bl_de_retour_rentre_au_magasin(self):
        from erpnext.controllers.sales_and_purchase_return import make_return_doc
        dn = frappe.db.sql("""select dn.name from `tabDelivery Note` dn
                              join `tabDelivery Note Item` dni on dni.parent = dn.name
                              join `tabSales Order` so on so.name = dni.against_sales_order
                              join tabItem i on i.name = dni.item_code and i.is_stock_item = 1
                              where dn.docstatus = 1 and dn.is_return = 0 and so.payment_terms_template = %s
                                and not exists (select 1 from `tabDelivery Note` r where r.return_against = dn.name and r.docstatus < 2)
                              order by dn.posting_date desc limit 1""", AS.TERMES_ARAMEX)
        if not dn:
            self.skipTest("aucun BL Aramex sans retour")
        rd = make_return_doc("Delivery Note", dn[0][0])
        for l in rd.items:
            l.warehouse = HALL
        AS.delivery_note_before_validate(rd)
        self.assertEqual({l.warehouse for l in self._stockes(rd)}, {AS.ENTREPOT_ARAMEX})

    def test_commande_aramex_reserve_au_magasin(self):
        so = _commande(aramex=True)
        if not so:
            self.skipTest("aucune commande Aramex")
        doc = frappe.get_doc("Sales Order", so)
        for l in doc.items:
            l.warehouse = HALL
        AS.sales_order_before_validate(doc)
        self.assertEqual({l.warehouse for l in self._stockes(doc)}, {AS.ENTREPOT_ARAMEX})
        doc.payment_terms_template, doc.custom_bordereau_aramex = None, None
        for l in doc.items:
            l.warehouse = HALL
        AS.sales_order_before_validate(doc)                    # plus Aramex : la sortie principale reprend la main
        self.assertEqual({l.warehouse for l in self._stockes(doc)}, {HALL})

    def test_magasin_desactive_ne_bloque_rien(self):
        so = _commande(aramex=True)
        if not so:
            self.skipTest("aucune commande Aramex")
        frappe.db.set_value("Warehouse", AS.ENTREPOT_ARAMEX, "disabled", 1)
        doc = frappe.get_doc("Sales Order", so)
        for l in doc.items:
            l.warehouse = HALL
        AS.sales_order_before_validate(doc)
        self.assertEqual({l.warehouse for l in self._stockes(doc)}, {HALL})
