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

    def test_taux(self):
        self.assertEqual(S.taux_effectif(119, 19, 19), 19.0)
        self.assertEqual(S.taux_effectif(119, 19, 0), 19.0)      # déduit des totaux
        self.assertEqual(S.taux_effectif(100, 0, 19), 0.0)       # pièce sans TVA

    def test_ventiler(self):
        v = S.ventiler_stock(1191.0, 190.0, 19, [{"qty": 2, "rate": 100}, {"qty": 1, "rate": 50}])
        self.assertEqual((v["stock_ht"], v["stock_tva"], v["stock_ttc"]), (250.0, 47.5, 297.5))
        self.assertEqual((v["depense_ht"], v["depense_tva"], v["depense_ttc"]), (751.0, 142.5, 893.5))
        self.assertEqual(v["stock_ttc"] + v["depense_ttc"], 1191.0)

    def test_la_tva_stock_ne_depasse_pas_celle_de_la_piece(self):
        v = S.ventiler_stock(300.0, 10.0, 19, [{"qty": 1, "rate": 200}])
        self.assertEqual((v["stock_tva"], v["depense_tva"]), (10.0, 0.0))

    def test_partage_des_reglements(self):
        regs = [{"mode": "Chèque", "montant": 200.0, "n_cheque": "1234567"}, {"mode": "Espèces", "montant": 100.0}]
        avance, depense = S.partager_regs(regs, 250.0)
        self.assertEqual([(r["mode"], r["montant"]) for r in avance], [("Chèque", 200.0), ("Espèces", 50.0)])
        self.assertEqual([(r["mode"], r["montant"]) for r in depense], [("Espèces", 50.0)])
        avance, depense = S.partager_regs(regs, 120.0)
        self.assertEqual([r["montant"] for r in avance], [120.0])
        self.assertEqual([(r["mode"], r["montant"], r.get("n_cheque")) for r in depense], [("Chèque", 80.0, "1234567"), ("Espèces", 100.0, None)])
        self.assertEqual(S.partager_regs([], 10), ([], []))

    def test_remarque(self):
        v = S.ventiler_stock(119.0, 19.0, 19, [{"qty": 1, "rate": 50}])
        self.assertIn("Part mise en stock : 50 HT + 9.5 TVA = 59.5 TTC (1 article(s)", S.remarque(v, 1))


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
        self.assertIn("S.preparer(articles_stock", src)
        self.assertIn("S.creer_part_stock(stock", src)
        self.assertIn('"purchase_order": part["purchase_order"]', src)
        self.assertIn("or stock:", src)

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
