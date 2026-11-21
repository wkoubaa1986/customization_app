"""Véhicules de l'entreprise en tête de la liste « Véhicule » de Générer les BL (fonctions pures)."""
import unittest

from customization_app.generer_bl import trier_vehicules
from customization_app.patches.vehicules_entreprise import VEHICULES, a_cocher


class TestVehiculesEntreprise(unittest.TestCase):
    def test_les_voitures_de_l_entreprise_passent_en_tete_puis_par_immatriculation(self):
        lignes = [{"name": "102 TU 6718", "entreprise": 0}, {"name": "3554 TU 261", "entreprise": 1},
                  {"name": "116TU3166", "entreprise": 0}, {"name": "232 TU 5957", "entreprise": 1}]
        self.assertEqual([v["name"] for v in trier_vehicules(lignes)],
                         ["232 TU 5957", "3554 TU 261", "102 TU 6718", "116TU3166"])

    def test_sans_case_cochee_l_ordre_reste_celui_des_immatriculations(self):
        lignes = [{"name": "B", "entreprise": 0}, {"name": "A", "entreprise": 0}]
        self.assertEqual([v["name"] for v in trier_vehicules(lignes)], ["A", "B"])

    def test_le_patch_ne_coche_que_les_fiches_qui_existent(self):
        self.assertEqual(a_cocher(["232 TU 5957", "102 TU 6718"]), ["232 TU 5957"])
        self.assertEqual(a_cocher([]), [])
        self.assertEqual(set(VEHICULES), {"3554 TU 261", "232 TU 5957", "261 TU 3647"})


if __name__ == "__main__":
    unittest.main()
