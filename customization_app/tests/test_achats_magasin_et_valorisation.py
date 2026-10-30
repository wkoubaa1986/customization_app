"""Achats en magasin, reconditionnement sous contrôle, marge sur la valorisation moyenne
(décisions utilisateur 30/10/2026). Fonctions pures, aucune base."""
from __future__ import annotations

import inspect
import os
import unittest

from customization_app import achats_magasin as A, marge_minimale as M, stock_reconditionnement as R


class TestAchatsEnMagasin(unittest.TestCase):
    def test_les_articles_stockes_partent_en_magasin(self):
        lignes = [{"item_code": "Acc001-B", "warehouse": "Hall - A&S"},
                  {"item_code": "SERVICE", "warehouse": "Hall - A&S"},
                  {"item_code": "PO-200", "warehouse": "Magasins - A&S"}]
        a_forcer = A.lignes_a_forcer(lignes, lambda c: c != "SERVICE")
        self.assertEqual([l["item_code"] for l in a_forcer], ["Acc001-B"])
        self.assertEqual(A.ENTREPOT_ACHATS, "Magasins - A&S")

    def test_branche_sur_les_trois_documents_d_achat(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "hooks.py"), encoding="utf-8") as f:
            hooks = f.read()
        self.assertEqual(hooks.count('"validate": "customization_app.achats_magasin.forcer_entrepot_magasin"'), 3)
        with open(os.path.join(base, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.entrepot_achats_magasin", f.read())
        self.assertIn('if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):',
                      inspect.getsource(A.forcer_entrepot_magasin))


class TestReconditionnement(unittest.TestCase):
    """MAT-STE-2026-00171 : trois produits finis, les coudes à 609,097."""
    SORTIES = [{"item_code": "AP-BF-TDS2-5-1500GPD", "qty": 1, "basic_rate": 0.0, "is_finished_item": 1},
               {"item_code": "Acc008-K", "qty": 1, "basic_rate": 6.279, "is_finished_item": 1},
               {"item_code": "Acc001-B", "qty": 2, "basic_rate": 609.097, "is_finished_item": 1}]
    VALO = {"AP-BF-TDS2-5-1500GPD": 0.0, "Acc008-K": 6.0, "Acc001-B": 0.097}

    def test_le_cas_reel_est_refuse_pour_ses_produits_finis(self):
        a = R.anomalies_repack(self.SORTIES, lambda c: self.VALO.get(c, 0.0))
        self.assertEqual(len(a), 1)                      # Acc001-B à 0,097 : sous le plancher de comparaison
        self.assertIn("3 lignes", a[0])

    def test_un_taux_aberrant_sur_une_valorisation_connue_est_refuse(self):
        sorties = [{"item_code": "AP-BF-TDS2-5-1500GPD", "qty": 1, "basic_rate": 1200.0, "is_finished_item": 1},
                   {"item_code": "Acc005-E", "qty": 2, "basic_rate": 40.0, "is_finished_item": 0}]
        a = R.anomalies_repack(sorties, lambda c: {"Acc005-E": 3.4}.get(c, 0.0))
        self.assertEqual(len(a), 1)
        self.assertIn("Acc005-E", a[0])

    def test_un_reconditionnement_sain_passe(self):
        sorties = [{"item_code": "AP-BF-TDS2-5-1500GPD", "qty": 1, "basic_rate": 1224.474, "is_finished_item": 1}]
        self.assertEqual(R.anomalies_repack(sorties, lambda c: 0.0), [])


class TestValorisationMoyenne(unittest.TestCase):
    def test_moyenne_ponderee_tous_entrepots(self):
        bins = [{"actual_qty": 203, "stock_value": 1099.454}, {"actual_qty": 202, "stock_value": 19.645},
                {"actual_qty": 4, "stock_value": 0.389}, {"actual_qty": 0, "stock_value": 0}]
        self.assertEqual(M.moyenne_ponderee(bins), round(1119.488 / 409, 6))
        self.assertEqual(M.moyenne_ponderee([]), 0.0)

    def test_la_source_est_offerte_dans_la_configuration(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "customize_erpnext", "doctype", "config_marge_minimale",
                               "config_marge_minimale.json"), encoding="utf-8") as f:
            self.assertIn(M.SOURCE_MOYENNE, f.read())
        self.assertIn("if source == SOURCE_MOYENNE:", inspect.getsource(M._cout_article))
