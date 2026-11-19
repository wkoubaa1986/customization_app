"""Analyse Articles : tri par famille, variantes puis prix (19/11/2026) — sans la base."""
import unittest

from customization_app import analyse_articles as A

L = "Vente standard"


def art(code, famille, prix, variant_of=None, has_variants=0):
    return {"item_code": code, "item_group": famille, "variant_of": variant_of, "has_variants": has_variants,
            "prices": {L: {"pv_ttc": prix}}}


class TestTriFamillePrix(unittest.TestCase):
    def codes(self, rows, sens="asc"):
        return [r["item_code"] for r in A.trier_famille_prix(rows, L, sens)]

    def test_familles_dans_l_ordre_puis_prix_croissant(self):
        rows = [art("C", "Vannes", 30), art("A", "Filtres", 20), art("B", "Filtres", 5), art("D", "Vannes", 10)]
        self.assertEqual(self.codes(rows), ["B", "A", "D", "C"])

    def test_prix_decroissant(self):
        rows = [art("A", "Filtres", 20), art("B", "Filtres", 5), art("C", "Filtres", 50)]
        self.assertEqual(self.codes(rows, "desc"), ["C", "A", "B"])

    def test_les_variantes_d_un_modele_restent_ensemble_le_modele_en_tete(self):
        rows = [art("AP-M-3", "Osmoseurs", 900, "AP-M"), art("X", "Osmoseurs", 700), art("AP-M", "Osmoseurs", None, None, 1),
                art("AP-M-1", "Osmoseurs", 600, "AP-M"), art("AP-M-2", "Osmoseurs", 800, "AP-M"), art("Y", "Osmoseurs", 1000)]
        # le bloc AP-M se place à 600 (son prix le plus bas) : avant X (700) ; dedans : modèle, puis 600, 800, 900
        self.assertEqual(self.codes(rows), ["AP-M", "AP-M-1", "AP-M-2", "AP-M-3", "X", "Y"])
        # en décroissant le bloc se place à 900 (son prix le plus haut) : après Y (1000), avant X (700)
        self.assertEqual(self.codes(rows, "desc"), ["Y", "AP-M", "AP-M-3", "AP-M-2", "AP-M-1", "X"])

    def test_sans_prix_en_dernier_dans_les_deux_sens(self):
        rows = [art("A", "Filtres", None), art("B", "Filtres", 5), art("C", "Filtres", 9)]
        self.assertEqual(self.codes(rows), ["B", "C", "A"])
        self.assertEqual(self.codes(rows, "desc"), ["C", "B", "A"])

    def test_la_liste_de_reference_decide(self):
        rows = [{"item_code": "A", "item_group": "F", "prices": {L: {"pv_ttc": 10}, "Technicien": {"pv_ttc": 3}}},
                {"item_code": "B", "item_group": "F", "prices": {L: {"pv_ttc": 5}, "Technicien": {"pv_ttc": 8}}}]
        self.assertEqual([r["item_code"] for r in A.trier_famille_prix(rows, L)], ["B", "A"])
        self.assertEqual([r["item_code"] for r in A.trier_famille_prix(rows, "Technicien")], ["A", "B"])

    def test_rien_n_est_perdu(self):
        rows = [art(str(i), "F%d" % (i % 3), i * 1.5 if i % 4 else None, "M" if i % 5 == 0 else None) for i in range(40)]
        self.assertEqual(sorted(self.codes(rows)), sorted(r["item_code"] for r in rows))


if __name__ == "__main__":
    unittest.main()
