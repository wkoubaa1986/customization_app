"""Tests du garde-fou sur « Détail Adresse » de la Tache de travail.

Le champ est un Data de 140 caractères. Un rendez-vous pris depuis la commande
échouait à l'enregistrement — « Valeur trop grande » — parce que l'adresse y
était composée sur plusieurs lignes. Le rendez-vous était alors simplement
perdu : c'est ce que ces tests protègent.

Convention : `unittest.TestCase` pur, aucune base.
"""
from __future__ import annotations

import unittest

from customization_app.api import DETAILS_ADRESSE_MAX, compacter_details_adresse as C


class TestUneSeuleLigne(unittest.TestCase):
    def test_les_sauts_de_ligne_deviennent_des_virgules(self):
        self.assertEqual(C("Rue de Carthage\nLa Soukra\nAriana"),
                         "Rue de Carthage, La Soukra, Ariana")

    def test_les_fins_de_ligne_windows_aussi(self):
        self.assertEqual(C("Rue de Carthage\r\nLa Soukra"), "Rue de Carthage, La Soukra")

    def test_les_lignes_vides_ne_laissent_pas_de_virgule_orpheline(self):
        self.assertEqual(C("Rue de Carthage\n\n  \nAriana"), "Rue de Carthage, Ariana")

    def test_les_espaces_de_bord_sont_retires(self):
        self.assertEqual(C("  Rue de Carthage  \n  Ariana "), "Rue de Carthage, Ariana")


class TestLaLimiteDuChamp(unittest.TestCase):
    """140 caractères, pas un de plus : au-delà, MariaDB refuse la ligne."""

    def test_une_adresse_bavarde_est_coupee(self):
        texte = "A" * 200
        self.assertEqual(len(C(texte)), DETAILS_ADRESSE_MAX)

    def test_le_debut_est_conserve(self):
        """On coupe la fin — le numéro et la rue, en tête, sont ce qui permet
        d'arriver sur place."""
        texte = "Rue de Carthage " + "x" * 300
        self.assertTrue(C(texte).startswith("Rue de Carthage "))

    def test_une_coupe_ne_laisse_ni_virgule_ni_espace_en_fin(self):
        texte = ("B" * 138) + "\n" + ("C" * 50)
        compact = C(texte)
        self.assertLessEqual(len(compact), DETAILS_ADRESSE_MAX)
        self.assertFalse(compact.endswith((",", " ")))

    def test_une_adresse_multiligne_longue_tient_dans_le_champ(self):
        texte = "\n".join(["Avenue Habib Bourguiba, immeuble El Manar, 3e étage"] * 4)
        self.assertLessEqual(len(C(texte)), DETAILS_ADRESSE_MAX)


class TestCeQuOnNeTouchePas(unittest.TestCase):
    """Le garde-fou ne doit rien réécrire quand il n'y a rien à corriger."""

    def test_une_adresse_courte_reste_entiere(self):
        self.assertEqual(C("Rue de Carthage, Ariana"), "Rue de Carthage, Ariana")

    def test_une_adresse_pile_a_la_limite_reste_entiere(self):
        texte = "D" * DETAILS_ADRESSE_MAX
        self.assertEqual(C(texte), texte)

    def test_le_vide_reste_vide(self):
        self.assertEqual(C(""), "")

    def test_none_reste_none(self):
        """Le champ non renseigné ne doit pas devenir une chaîne vide : la tâche
        passe ensuite par `details_adresse or select_address` un peu partout."""
        self.assertIsNone(C(None))


if __name__ == "__main__":
    unittest.main()
