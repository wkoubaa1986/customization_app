"""Ensembles de produits — règles pures et circuit en savepoint (01/10/2026)."""
from __future__ import annotations

import unittest

from customization_app import ensembles_produits as EP


class TestAssemblables(unittest.TestCase):
    def test_min_sur_les_composants_suivis(self):
        comps = [{"qty": 2, "stock": 10, "is_stock_item": 1},       # 5
                 {"qty": 1, "stock": 3, "is_stock_item": 1},        # 3  ← borne
                 {"qty": 1, "stock": 0, "is_stock_item": 0}]        # non suivi : ignoré
        self.assertEqual(EP.assemblables(comps), 3)

    def test_stock_negatif_vaut_zero(self):
        self.assertEqual(EP.assemblables([{"qty": 1, "stock": -4, "is_stock_item": 1}]), 0)

    def test_aucun_composant_suivi(self):
        self.assertIsNone(EP.assemblables([{"qty": 1, "stock": 0, "is_stock_item": 0}]))
        self.assertIsNone(EP.assemblables([]))

    def test_quantite_fractionnaire(self):
        self.assertEqual(EP.assemblables([{"qty": 0.5, "stock": 2.4, "is_stock_item": 1}]), 4)


class TestCircuit(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("ens")
        self.comps = frappe.db.sql_list("""select name from tabItem where disabled = 0 and is_stock_item = 1 and has_variants = 0
                                           and name not in (select new_item_code from `tabProduct Bundle`) order by name limit 2""")

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="ens")

    def test_creer_modifier_desactiver_supprimer(self):
        import frappe
        c1, c2 = self.comps
        nom = EP.creer_ensemble([{"item_code": c1, "qty": 2}, {"item_code": c1, "qty": 1}, {"item_code": c2, "qty": 1}],
                                nouveau={"item_code": "TEST-ENS-X", "item_name": "Ensemble test", "item_group": frappe.db.get_value("Item", c1, "item_group"), "prix": 99.5})
        doc = frappe.get_doc(EP.PB, nom)
        self.assertEqual([(l.item_code, l.qty) for l in doc.items], [(c1, 3.0), (c2, 1.0)])     # même article cumulé
        self.assertEqual(frappe.db.get_value("Item", "TEST-ENS-X", "is_stock_item"), 0)
        self.assertEqual(EP._prix(["TEST-ENS-X"]).get("TEST-ENS-X"), 99.5)
        res = EP.get_ensembles(recherche="TEST-ENS-X", etat="tous")
        self.assertEqual(res["total"], 1)
        e = res["ensembles"][0]
        self.assertEqual((e["prix"], len(e["composants"])), (99.5, 2))
        EP.enregistrer_ensemble(nom, [{"item_code": c2, "qty": 4}], description="juste c2")
        doc = frappe.get_doc(EP.PB, nom)
        self.assertEqual([(l.item_code, l.qty) for l in doc.items], [(c2, 4.0)])
        with self.assertRaises(frappe.ValidationError):
            EP.enregistrer_ensemble(nom, [{"item_code": "TEST-ENS-X", "qty": 1}])        # un ensemble n'est pas un composant
        with self.assertRaises(frappe.ValidationError):
            EP.creer_ensemble([{"item_code": c1, "qty": 1}], parent=c1)                   # parent suivi en stock : refusé
        EP.activer_ensemble(nom, 0)
        self.assertEqual(frappe.db.get_value(EP.PB, nom, "disabled"), 1)
        self.assertEqual(EP.get_ensembles(recherche="TEST-ENS-X", etat="actifs")["total"], 0)
        EP.supprimer_ensemble(nom)
        self.assertFalse(frappe.db.exists(EP.PB, nom))
