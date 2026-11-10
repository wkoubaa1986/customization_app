"""Dépense de caisse avec une part en stock (10/11/2026) : ventilation et partage du paiement. Sans site."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import caisse_stock_partiel as S


class TestVentilation(unittest.TestCase):

    def test_lignes_valides(self):
        lignes = S.lignes_valides('[{"item_code": " A ", "qty": 2, "rate": "10.5", "designation": "x"}, {"item_code": "", "qty": 1, "rate": 1}, {"item_code": "B", "qty": 0, "rate": 1}]')
        self.assertEqual(lignes, [{"item_code": "A", "qty": 2.0, "rate": 10.5, "designation": "x"}])
        self.assertEqual(S.lignes_valides(None), [])

    def test_ventiler_la_depense_reste_entiere_le_stock_est_ht(self):
        v = S.ventiler_stock(1191.0, 190.0, [{"qty": 2, "rate": 100}, {"qty": 1, "rate": 50}])
        self.assertEqual((v["stock_ht"], v["ht_total"]), (250.0, 1001.0))
        # ce que la facture d'achat finale ne portera pas : le HT hors stock et TOUTE la TVA
        self.assertEqual((v["depense_ht"], v["depense_tva"], v["depense_ttc"]), (751.0, 190.0, 941.0))
        self.assertEqual(v["ht_total"] - v["depense_ht"], v["stock_ht"])

    def test_remarque(self):
        v = S.ventiler_stock(119.0, 19.0, [{"qty": 1, "rate": 50}])
        r = S.remarque(v, 1)
        self.assertIn("Part mise en stock : 50 HT (1 article(s))", r)
        self.assertIn("tout le HT (100.0) et toute la TVA (19.0)", r)


class TestCablage(unittest.TestCase):

    def setUp(self):
        self.base = os.path.dirname(inspect.getfile(customization_app))

    def _lire(self, *chemin):
        with open(os.path.join(self.base, *chemin), encoding="utf-8") as fh:
            return fh.read()

    def test_creer_porte_la_part_stock(self):
        from customization_app import caisse_depenses as C
        src = inspect.getsource(C.creer)
        self.assertIn("articles_stock=None", src)
        self.assertIn("S.preparer(articles_stock, montant, tva)", src)
        self.assertIn("S.creer_part_stock(stock", src)
        self.assertIn('"purchase_order": part["purchase_order"]', src)
        self.assertIn("or stock:", src)
        # la dépense reste entière : ni montant, ni TVA, ni paiement ne sont réduits par la part stock
        self.assertNotIn("regs_depense", src)
        self.assertNotIn("stock_ttc", src)

    def test_la_commande_est_sans_tva_et_l_avance_est_une_ecriture_sur_la_commande(self):
        src = inspect.getsource(S.creer_part_stock)
        self.assertNotIn('po.append("taxes"', src)
        self.assertEqual(src.count("_sans_taxes(po)"), 2)
        self.assertEqual(inspect.getsource(S._facture_immediate).count("_sans_taxes(pi)"), 2)
        self.assertIn('"is_advance": "Yes"', src)
        self.assertIn('"reference_type": "Purchase Order"', src)
        self.assertIn('{"account": ctx["compte"], "credit_in_account_currency": stock["stock_ht"]', src)
        self.assertIn("if not est_bl:", src)
        from customization_app import caisse_depenses as C
        self.assertIn("S.avance_en_journal(nom)", inspect.getsource(C.po_convertir_avances))
        self.assertIn("S.avance_en_journal(nom)", inspect.getsource(C.pi_marquer_fiche_saisie))

    def test_fiches(self):
        import json
        dap = json.load(open(os.path.join(self.base, "customize_erpnext", "doctype", "depense_a_payer", "depense_a_payer.json"), encoding="utf-8"))
        fas = json.load(open(os.path.join(self.base, "customize_erpnext", "doctype", "facture_achat_a_saisir", "facture_achat_a_saisir.json"), encoding="utf-8"))
        self.assertTrue({"purchase_order", "fiche_achat", "part_stock_ht", "articles_stock"} <= {f["fieldname"] for f in dap["fields"]})
        self.assertTrue({"depense_a_payer", "part_depense_ht", "part_depense_tva", "part_depense_ttc"} <= {f["fieldname"] for f in fas["fields"]})

    def test_dialogue(self):
        js = self._lire("customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js")
        for m in ("zone_articles", 'caisse_stock_partiel") + ".lire_articles"', "articles_stock:"):
            self.assertIn(m, js, m)
