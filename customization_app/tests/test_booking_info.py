"""Tests des bandeaux « autres rendez-vous du client » de la Tache de travail.

Le Server Script d'origine composait le libellé d'un rendez-vous en collant ses
champs bout à bout : un rendez-vous sans employé, sans type ou sans date levait
« can only concatenate str (not "NoneType") » à CHAQUE ouverture de la fiche.
Les tests portent donc d'abord sur les champs manquants.

Convention : `unittest.TestCase` pur, aucune base.
"""
from __future__ import annotations

import unittest
from datetime import datetime

from customization_app.api import _creneaux_se_chevauchent, _libelle_rdv


def _rdv(**kw):
    base = {
        "name": "TACHE-1",
        "custom_type_dintervention": "Entretien",
        "custom_employé": "Jamel Bouzid",
        "starts_on": datetime(2026, 9, 3, 9, 30),
        "ends_on": datetime(2026, 9, 3, 10, 0),
    }
    base.update(kw)
    return base


class TestLibelleDuRendezVous(unittest.TestCase):
    def test_un_rendez_vous_complet(self):
        self.assertEqual(
            _libelle_rdv(_rdv()),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_une_date_en_texte_est_acceptee(self):
        """Frappe rend `starts_on` tantôt en datetime, tantôt en chaîne."""
        self.assertEqual(
            _libelle_rdv(_rdv(starts_on="2026-09-03 09:30:00",
                              ends_on="2026-09-03 10:00:00")),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_employe_le_reste_est_annonce(self):
        self.assertEqual(
            _libelle_rdv(_rdv(custom_employé=None)),
            "Entretien le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_type_le_rendez_vous_reste_nomme(self):
        self.assertEqual(
            _libelle_rdv(_rdv(custom_type_dintervention=None)),
            "Rendez-vous avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_heure_de_fin(self):
        self.assertEqual(
            _libelle_rdv(_rdv(ends_on=None)),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30",
        )

    def test_sans_date_du_tout(self):
        self.assertEqual(
            _libelle_rdv(_rdv(starts_on=None, ends_on=None)),
            "Entretien avec Jamel Bouzid",
        )

    def test_tous_les_champs_vides_ne_levent_pas(self):
        """L'erreur exacte du ticket : ici on veut un libellé, pas une exception."""
        self.assertEqual(
            _libelle_rdv({"custom_type_dintervention": None, "custom_employé": None,
                          "starts_on": None, "ends_on": None}),
            "Rendez-vous",
        )

    def test_un_dictionnaire_vide_ne_leve_pas(self):
        self.assertEqual(_libelle_rdv({}), "Rendez-vous")

    def test_une_date_illisible_est_ignoree(self):
        self.assertEqual(_libelle_rdv(_rdv(starts_on="pas une date", ends_on=None)),
                         "Entretien avec Jamel Bouzid")


H9 = datetime(2026, 9, 3, 9, 0)
H10 = datetime(2026, 9, 3, 10, 0)
H11 = datetime(2026, 9, 3, 11, 0)
H12 = datetime(2026, 9, 3, 12, 0)


class TestChevauchementDeCreneaux(unittest.TestCase):
    """Le bandeau rouge n'a de sens que s'il est rare : un faux conflit par jour
    et plus personne ne le lit."""

    def test_deux_creneaux_disjoints(self):
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H11, H12))

    def test_deux_creneaux_adjacents_ne_se_chevauchent_pas(self):
        """9h-10h puis 10h-11h : c'est une journée normale, pas un conflit."""
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H10, H11))

    def test_deux_creneaux_qui_se_recouvrent(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H11, H10, H12))

    def test_un_creneau_inclus_dans_l_autre(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H12, H10, H11))

    def test_deux_creneaux_identiques(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H10, H9, H10))

    def test_l_ordre_des_arguments_est_indifferent(self):
        self.assertTrue(_creneaux_se_chevauchent(H10, H12, H9, H11))

    def test_sans_debut_pas_de_conflit(self):
        """On ne crie pas au conflit sur une donnée qu'on n'a pas."""
        self.assertFalse(_creneaux_se_chevauchent(None, H10, H9, H11))
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, None, H11))

    def test_sans_fin_le_creneau_se_reduit_a_son_debut(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H12, H10, None))
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H11, None))


if __name__ == "__main__":
    unittest.main()
