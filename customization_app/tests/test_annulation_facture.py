"""Tests de la réaffectation des paiements à l'annulation d'une facture.

Convention : `unittest.TestCase` pur. Seul le CALCUL se teste ici — le prorata
et la répartition. L'annulation elle-même touche la comptabilité et se vérifie
en recette sur des cas réels, pas dans un test unitaire.

La capacité des commandes est injectée : ces tests n'ouvrent aucune base, comme
`test_livraison_aramex`.
"""
from __future__ import annotations

import unittest

import frappe

from customization_app import annulation_facture as AF


class _Facture:
    """Le strict nécessaire pour `_parts_par_commande` : des lignes."""

    def __init__(self, lignes):
        self._items = list(lignes)

    def get(self, champ):
        return self._items if champ == "items" else None


def _ligne(montant, commande=None):
    # `frappe._dict` et non un dict nu : le code lit `l.amount`, comme sur une
    # vraie ligne de facture.
    return frappe._dict(amount=montant, sales_order=commande)


class TestPartsParCommande(unittest.TestCase):
    def test_une_seule_commande_prend_tout(self):
        parts = AF._parts_par_commande(_Facture([_ligne(303.0, "WEB1-007819")]))
        self.assertEqual(parts, {"WEB1-007819": 1.0})

    def test_le_nom_de_la_serie_est_indifferent(self):
        """Le défaut d'origine : seules les commandes « SAL-ORD » étaient vues, et
        les commandes web « WEB1-… » repartaient sans rien."""
        for nom in ("WEB1-007819", "SAL-ORD-2026-03325", "AUTRE-2027-1"):
            self.assertEqual(AF._parts_par_commande(_Facture([_ligne(10.0, nom)])),
                             {nom: 1.0})

    def test_plusieurs_commandes_au_prorata_des_montants(self):
        parts = AF._parts_par_commande(_Facture([
            _ligne(300.0, "A"), _ligne(100.0, "B")]))
        self.assertAlmostEqual(parts["A"], 0.75)
        self.assertAlmostEqual(parts["B"], 0.25)

    def test_plusieurs_lignes_d_une_meme_commande_s_additionnent(self):
        parts = AF._parts_par_commande(_Facture([
            _ligne(100.0, "A"), _ligne(200.0, "A"), _ligne(100.0, "B")]))
        self.assertAlmostEqual(parts["A"], 0.75)

    def test_une_ligne_sans_commande_reduit_les_parts(self):
        """Elle ne peut être rendue à personne : sa part doit rester non allouée,
        et surtout ne pas être redistribuée aux autres commandes."""
        parts = AF._parts_par_commande(_Facture([
            _ligne(1710.0, "A"), _ligne(80.0, None)]))
        self.assertAlmostEqual(parts["A"], 1710.0 / 1790.0)
        self.assertNotIn(None, parts)
        self.assertLess(sum(parts.values()), 1.0)

    def test_une_facture_sans_aucune_commande_ne_rend_rien(self):
        self.assertEqual(AF._parts_par_commande(_Facture([_ligne(50.0, None)])), {})

    def test_une_facture_a_zero_ne_divise_pas_par_zero(self):
        self.assertEqual(AF._parts_par_commande(_Facture([_ligne(0.0, "A")])), {})


class TestRepartition(unittest.TestCase):
    """`_repartir` interroge la capacité des commandes : on la remplace ici."""

    def setUp(self):
        self._vrai = AF._capacite
        self.capacites = {}
        AF._capacite = lambda commande, deja=None: max(
            0.0, self.capacites.get(commande, 10 ** 6) - (deja or 0))

    def tearDown(self):
        AF._capacite = self._vrai

    def test_une_commande_recoit_tout(self):
        lignes, reste = AF._repartir(161.0, {"A": 1.0}, {})
        self.assertEqual(lignes, [{"commande": "A", "montant": 161.0}])
        self.assertEqual(reste, 0.0)

    def test_le_prorata_est_respecte(self):
        lignes, reste = AF._repartir(1000.0, {"A": 0.75, "B": 0.25}, {})
        montants = {l["commande"]: l["montant"] for l in lignes}
        self.assertEqual(montants, {"A": 750.0, "B": 250.0})
        self.assertEqual(reste, 0.0)

    def test_la_somme_ne_depasse_jamais_le_montant(self):
        """Sur huit commandes, les arrondis pourraient dépasser d'un millime :
        allouer plus que reçu ferait refuser l'écriture."""
        parts = {chr(65 + i): 1 / 8 for i in range(8)}
        lignes, reste = AF._repartir(100.0, parts, {})
        total = round(sum(l["montant"] for l in lignes) + reste, 3)
        self.assertEqual(total, 100.0)
        self.assertLessEqual(sum(l["montant"] for l in lignes), 100.0)

    def test_le_reste_d_arrondi_va_a_la_plus_grosse_part(self):
        lignes, reste = AF._repartir(100.0, {"A": 2 / 3, "B": 1 / 3}, {})
        self.assertEqual(reste, 0.0)
        self.assertEqual(round(sum(l["montant"] for l in lignes), 3), 100.0)
        self.assertEqual(lignes[0]["commande"], "A")

    def test_la_part_sans_commande_reste_non_allouee(self):
        """1790 payés, 80 de lignes sans commande : 4,47 % ne repartent pas."""
        lignes, reste = AF._repartir(1790.0, {"A": 1710.0 / 1790.0}, {})
        self.assertEqual(lignes, [{"commande": "A", "montant": 1710.0}])
        self.assertEqual(reste, 80.0)

    def test_une_commande_saturee_ne_recoit_que_sa_marge(self):
        """Le surplus n'est pas perdu : il reste non alloué sur le paiement."""
        self.capacites["A"] = 60.0
        lignes, reste = AF._repartir(100.0, {"A": 1.0}, {})
        self.assertEqual(lignes, [{"commande": "A", "montant": 60.0}])
        self.assertEqual(reste, 40.0)

    def test_une_commande_pleine_est_sautee_sans_bloquer_les_autres(self):
        self.capacites["A"] = 0.0
        lignes, reste = AF._repartir(100.0, {"A": 0.5, "B": 0.5}, {})
        self.assertEqual(lignes, [{"commande": "B", "montant": 50.0}])
        self.assertEqual(reste, 50.0)

    def test_deux_paiements_partagent_la_meme_capacite(self):
        """`deja` porte d'un paiement à l'autre : sans lui, deux paiements
        rempliraient chacun la commande jusqu'à son total."""
        self.capacites["A"] = 120.0
        deja = {}
        AF._repartir(100.0, {"A": 1.0}, deja)
        lignes, reste = AF._repartir(100.0, {"A": 1.0}, deja)
        self.assertEqual(lignes, [{"commande": "A", "montant": 20.0}])
        self.assertEqual(reste, 80.0)

    def test_un_residu_infime_n_est_pas_alloue(self):
        """ERPNext refuse une allocation sous le millime."""
        lignes, _ = AF._repartir(0.001, {"A": 0.4, "B": 0.6}, {})
        self.assertTrue(all(l["montant"] >= AF.MINIMUM for l in lignes))
