"""Échéancier d'une facture créée depuis une commande encaissée à la caisse (29/10/2026) : cas réel
SAL-ORD-2026-04399 (Tynischem, 331 + 50 reçus en espèces, facture de 381)."""
from __future__ import annotations

import unittest

from customization_app.echeancier_facture import aligner_echeancier


class TestAlignerEcheancier(unittest.TestCase):
    def test_commande_entierement_reglee_tout_a_zero(self):
        lignes = [{"payment_amount": 331, "invoice_portion": 0}, {"payment_amount": 50, "invoice_portion": 0}]
        self.assertTrue(aligner_echeancier(lignes, 381, 381))
        self.assertEqual([l["payment_amount"] for l in lignes], [0, 0])
        self.assertEqual([l["invoice_portion"] for l in lignes], [0, 0])

    def test_avance_partielle_au_prorata(self):
        lignes = [{"payment_amount": 300, "invoice_portion": 0}, {"payment_amount": 100, "invoice_portion": 0}]
        self.assertTrue(aligner_echeancier(lignes, 400, 100))                 # reste 300
        self.assertEqual([l["payment_amount"] for l in lignes], [225, 75])
        self.assertEqual(sum(l["payment_amount"] for l in lignes), 300)
        self.assertAlmostEqual(lignes[0]["invoice_portion"], 56.25)

    def test_sans_avance_rien_ne_change(self):
        lignes = [{"payment_amount": 381, "invoice_portion": 100}]
        self.assertFalse(aligner_echeancier(lignes, 381, 0))
        self.assertEqual(lignes[0]["payment_amount"], 381)

    def test_deja_au_reste_a_payer_rien_ne_change(self):
        lignes = [{"payment_amount": 0, "invoice_portion": 100}]            # ERPNext l'a déjà recalculé
        self.assertFalse(aligner_echeancier(lignes, 404, 404))

    def test_echeancier_qui_n_est_pas_une_copie_de_la_commande_n_est_pas_touche(self):
        lignes = [{"payment_amount": 100, "invoice_portion": 0}]            # ni le total, ni le reste : saisie à part
        self.assertFalse(aligner_echeancier(lignes, 381, 50))

    def test_arrondi_sur_la_derniere_ligne(self):
        lignes = [{"payment_amount": 1}, {"payment_amount": 1}, {"payment_amount": 1}]
        self.assertTrue(aligner_echeancier(lignes, 3, 1))                   # reste 2 → 0,667 + 0,667 + 0,666
        self.assertEqual(round(sum(l["payment_amount"] for l in lignes), 3), 2)
