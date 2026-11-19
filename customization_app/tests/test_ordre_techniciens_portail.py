"""Ordre des techniciens du portail de rendez-vous (19/11/2026) — sans la base."""
import unittest

from customization_app.patches.ordre_techniciens_portail_rdv import ORDRE, ordonner


class TestOrdre(unittest.TestCase):
    def test_radhouan_puis_sadok_puis_jamel_malgre_les_rangs_en_double(self):
        lignes = [{"name": "a", "employe": "HR-EMP-00001", "idx": 2}, {"name": "b", "employe": "HR-EMP-00002", "idx": 3},
                  {"name": "c", "employe": "HR-EMP-00013", "idx": 3}]
        self.assertEqual([l["employe"] for l in ordonner(lignes)], list(ORDRE))

    def test_un_technicien_non_prevu_reste_apres_les_autres_dans_son_ordre(self):
        lignes = [{"name": "x", "employe": "HR-EMP-00099", "idx": 1}, {"name": "a", "employe": "HR-EMP-00001", "idx": 2},
                  {"name": "y", "employe": "HR-EMP-00098", "idx": 3}, {"name": "c", "employe": "HR-EMP-00013", "idx": 4}]
        self.assertEqual([l["employe"] for l in ordonner(lignes)],
                         ["HR-EMP-00013", "HR-EMP-00001", "HR-EMP-00099", "HR-EMP-00098"])


if __name__ == "__main__":
    unittest.main()
