"""Échange d'articles par Aramex (19/11/2026) — règles pures, sans la base."""
import unittest

from customization_app import echange_aramex as E


class TestLignes(unittest.TestCase):
    def test_lignes_vides_et_quantites_nulles_ecartees(self):
        self.assertEqual(E.lignes_propres([{"item_code": "Acc020-A", "qty": 3}, {"item_code": "", "qty": 2},
                                           {"item_code": "X", "qty": 0}, None]),
                         [{"item_code": "Acc020-A", "qty": 3.0}])

    def test_un_article_repete_s_additionne(self):
        self.assertEqual(E.lignes_propres([{"item_code": "Acc020-A", "qty": 1}, {"item_code": "Acc020-A", "qty": 2}]),
                         [{"item_code": "Acc020-A", "qty": 3.0}])

    def test_accepte_le_json_de_l_ecran(self):
        self.assertEqual(E.lignes_propres('[{"item_code": "Acc020-B", "qty": "3"}]'), [{"item_code": "Acc020-B", "qty": 3.0}])
        self.assertEqual(E.lignes_propres(""), [])

    def test_texte(self):
        self.assertEqual(E.texte_lignes([{"item_code": "Acc020-A", "qty": 3.0}, {"item_code": "T33", "qty": 1.5}]),
                         "3 × Acc020-A, 1.5 × T33")


class TestRetard(unittest.TestCase):
    """La pochette devrait être revenue 7 jours après la livraison au client."""

    def test_pas_encore_livre_pas_de_retard(self):
        self.assertEqual(E.jours_de_retard(None, "2026-11-30"), 0)

    def test_dans_les_sept_jours_pas_de_retard(self):
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-18"), 0)
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-23"), 0)

    def test_au_dela_on_compte_les_jours(self):
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-26"), 3)
        self.assertEqual(E.jours_de_retard("2026-11-16 15:08:00", "2026-11-24"), 1)


if __name__ == "__main__":
    unittest.main()
