"""« RDV avec l'app » depuis les listes d'appels : l'origine transmise au portail agent."""
import unittest


class TestOrigineValide(unittest.TestCase):
    def test_formes_acceptees(self):
        from customization_app.portail_rdv_agent import origine_valide as O
        self.assertEqual(O({"liste_appelle": "LAE-1", "ligne": "abc"}), {"liste_appelle": "LAE-1", "ligne": "abc"})
        self.assertEqual(O('{"liste_appelle": "LAE-1", "ligne": "abc"}'), {"liste_appelle": "LAE-1", "ligne": "abc"})
        self.assertEqual(O({"tache_rattrapage": "Tache-1"}), {"tache_rattrapage": "Tache-1"})

    def test_formes_ignorees(self):
        from customization_app.portail_rdv_agent import origine_valide as O
        self.assertIsNone(O(None))
        self.assertIsNone(O("pas du json"))
        self.assertIsNone(O({"liste_appelle": "LAE-1"}))          # sans la ligne
        self.assertIsNone(O({"autre": "x"}))
        self.assertIsNone(O([1, 2]))
