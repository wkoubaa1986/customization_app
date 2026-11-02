"""Plafond du taux de valorisation : prix de vente le plus bas hors TVA, moins la marge minimale (02/11/2026)."""
import unittest

from customization_app.inventaire_magasin import plafond_taux


class TestPlafondTaux(unittest.TestCase):
    def test_marge_sur_prix_de_vente(self):
        # 20 TTC à 19 % = 16,807 HT ; marge 15 % sur le prix → coût max 14,286
        self.assertEqual(plafond_taux(20, 19, 15), 14.286)

    def test_marge_sur_le_cout(self):
        self.assertEqual(plafond_taux(20, 19, 15, "Coût d'achat"), 14.615)

    def test_sans_prix_ou_sans_marge(self):
        self.assertIsNone(plafond_taux(0, 19, 15))
        self.assertEqual(plafond_taux(23.8, 19, 0), 20.0)      # marge 0 : le plafond est le prix HT
