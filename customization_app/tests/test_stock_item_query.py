"""Recherche d'article de l'Écriture de stock : les modèles suivis en stock sont proposés."""
import unittest

import frappe


class TestStockItemQuery(unittest.TestCase):
    def _noms(self, fn, txt, **filters):
        return [r[0] for r in fn("Item", txt, "name", 0, 50, filters or {"is_stock_item": 1})]

    def test_modele_suivi_en_stock_propose(self):
        from customization_app.query import stock_item_query
        from erpnext.controllers.queries import item_query

        modele = frappe.db.get_value(
            "Item", {"has_variants": 1, "is_stock_item": 1, "disabled": 0}, "name")
        if not modele:
            self.skipTest("aucun modèle suivi en stock sur ce site")
        self.assertIn(modele, self._noms(stock_item_query, modele))
        # Référence : la recherche d'ERPNext l'écarte, c'est bien ce que l'on corrige.
        self.assertNotIn(modele, self._noms(item_query, modele))

    def test_article_non_suivi_exclu(self):
        from customization_app.query import stock_item_query

        bundle = frappe.db.get_value(
            "Item", {"is_stock_item": 0, "disabled": 0, "variant_of": ["is", "set"]}, "name")
        if not bundle:
            self.skipTest("aucune variante non suivie en stock sur ce site")
        self.assertNotIn(bundle, self._noms(stock_item_query, bundle))

    def test_achat_inchange(self):
        from customization_app.query import buying_item_query

        achat = frappe.db.get_value(
            "Item", {"has_variants": 1, "is_purchase_item": 1, "disabled": 0}, "name")
        if not achat:
            self.skipTest("aucun modèle achetable sur ce site")
        self.assertIn(achat, self._noms(buying_item_query, achat, is_purchase_item=1))
