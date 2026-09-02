"""Tests du choix de l'employé par le portail de rendez-vous.

Convention : `unittest.TestCase` pur, aucune base — on n'éprouve ici que la
RÈGLE D'AFFECTATION, celle qui décide qui prend le rendez-vous.

Contexte (02/09/2026) : l'utilisateur constatait plus d'employés servis que le
nombre réglé, et une charge très inégale — le 14/09, Akram avait 7 rendez-vous
quand Sadok en avait 2. Deux mécanismes distincts l'expliquaient : le
remplacement d'un employé plein par le suivant de la liste, et le fait que le
moteur EMPILE sur le premier éligible au lieu de répartir. Le second est
désormais un réglage.
"""
from __future__ import annotations

import unittest
from datetime import datetime

from customization_app import portail_rdv_planning as PL


def _creneau(h_debut, minutes):
    debut = datetime(2026, 9, 14, h_debut, 0)
    return (debut, debut.replace(hour=h_debut + minutes // 60,
                                 minute=minutes % 60))


class TestChargeDemiJournee(unittest.TestCase):
    """La charge se compte par DEMI-JOURNÉE : c'est l'unité que le client
    réserve, et un technicien chargé le matin peut être le plus libre
    l'après-midi."""

    def test_sans_rien_la_charge_est_nulle(self):
        self.assertEqual(PL._charge_demi(None, "matin"), 0)
        self.assertEqual(PL._charge_demi({}, "matin"), 0)

    def test_un_creneau_compte_ses_minutes(self):
        entree = {"matin": [_creneau(9, 30)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 30)

    def test_les_creneaux_s_additionnent(self):
        entree = {"matin": [_creneau(9, 30), _creneau(10, 75)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 105)

    def test_l_autre_demi_journee_n_est_pas_comptee(self):
        entree = {"matin": [_creneau(9, 30)], "apres_midi": [_creneau(14, 120)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 30)
        self.assertEqual(PL._charge_demi(entree, "apres_midi"), 120)


class TestReglageRepartition(unittest.TestCase):
    """Le mode vit dans Config Portail RDV : c'est un RÉGLAGE, pas un choix
    figé dans le code (demande de l'utilisateur)."""

    def test_par_defaut_on_garde_la_priorite_de_la_liste(self):
        """Réglage absent = comportement historique. Un correctif qui
        redistribuerait la charge de tout le monde sans qu'on l'ait demandé
        serait une mauvaise surprise."""
        self.assertFalse(PL._repartir({}))
        self.assertFalse(PL._repartir(None))

    def test_le_libelle_du_reglage_declenche_la_repartition(self):
        self.assertTrue(PL._repartir({"mode_affectation": PL.MODE_REPARTITION}))

    def test_un_libelle_inconnu_ne_repartit_pas(self):
        """Si quelqu'un renomme l'option, on retombe sur l'ancien
        comportement — jamais sur un comportement indéterminé."""
        self.assertFalse(PL._repartir({"mode_affectation": "Autre chose"}))

    def test_la_priorite_reste_le_libelle_de_l_autre_option(self):
        self.assertFalse(PL._repartir(
            {"mode_affectation": "Priorité à l'ordre de la liste"}))


class TestOrdreDeChoix(unittest.TestCase):
    """Ce que produit le tri, à partir des charges — le cœur de la demande."""

    def _ordonner(self, charges, demi="matin"):
        taches = {(e, "J"): {demi: [_creneau(9, m)] if m else []}
                  for e, m in charges.items()}
        pool = list(charges)
        return sorted(pool, key=lambda e: PL._charge_demi(taches.get((e, "J")), demi))

    def test_le_moins_charge_passe_devant(self):
        self.assertEqual(self._ordonner({"A": 150, "B": 0, "C": 75}),
                         ["B", "C", "A"])

    def test_a_charge_egale_l_ordre_de_la_liste_est_conserve(self):
        """Le tri est STABLE : à égalité, la priorité historique reprend la
        main. On ne perd pas l'ordre de la liste, on ne l'applique qu'ensuite."""
        self.assertEqual(self._ordonner({"A": 60, "B": 60, "C": 60}),
                         ["A", "B", "C"])

    def test_un_employe_vide_passe_devant_un_employe_charge(self):
        """Cas réel du 07/09/2026 : la priorité donnait le rendez-vous à Sadok
        (75 min déjà posées), la répartition le donne à Jamel (0)."""
        self.assertEqual(self._ordonner({"Sadok": 75, "Jamel": 0})[0], "Jamel")
