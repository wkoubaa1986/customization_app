"""La couleur d'un employé change : quelles tâches ouvertes la prennent (fonction pure)."""
import unittest

from customization_app.couleur_employe import a_recolorer

PARTENAIRE = "partenaire@exemple.tn"


class TestARecolorer(unittest.TestCase):
    def test_les_taches_ouvertes_prennent_la_nouvelle_couleur(self):
        taches = [{"name": "T1", "owner": "salma@exemple.tn", "color": "#449CF0"}, {"name": "T2", "owner": "a@b", "color": None}]
        self.assertEqual(a_recolorer(taches, "#0F766E", PARTENAIRE), ["T1", "T2"])

    def test_une_tache_creee_par_le_partenaire_reste_cyan(self):
        taches = [{"name": "T1", "owner": PARTENAIRE, "color": "#29def2"}, {"name": "T2", "owner": "salma@exemple.tn", "color": "#449CF0"}]
        self.assertEqual(a_recolorer(taches, "#0F766E", PARTENAIRE), ["T2"])

    def test_une_tache_deja_a_la_bonne_couleur_n_est_pas_reecrite(self):
        self.assertEqual(a_recolorer([{"name": "T1", "owner": "a@b", "color": "#0f766e"}], "#0F766E", PARTENAIRE), [])


if __name__ == "__main__":
    unittest.main()
