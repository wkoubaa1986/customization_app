"""Assistant « Nouvelle liste de prix » (11/11/2026) : les fonctions pures. Sans site."""
from __future__ import annotations

import unittest

from customization_app import liste_prix_assistant as A


class TestQuantites(unittest.TestCase):

    def test_codes_quantites_et_cumul(self):
        q = A.quantites_demandees('[{"item_code": " A ", "qte": 3}, "B", {"item_code": "A", "qty": 2}, {"item_code": ""}, {"item_code": "C", "qte": 0}]')
        self.assertEqual(q, {"A": 5, "B": 1, "C": 1})
        self.assertEqual(list(q), ["A", "B", "C"])          # ordre d'ajout conservé
        self.assertEqual(A.quantites_demandees(None), {})


class TestFamilles(unittest.TestCase):

    def test_cumul_niveaux_et_familles_vides_retirees(self):
        groupes = [
            {"name": "Pompes", "parent": "Racine", "lft": 2, "rgt": 9},
            {"name": "Surface", "parent": "Pompes", "lft": 3, "rgt": 6},
            {"name": "Inox", "parent": "Surface", "lft": 4, "rgt": 5},
            {"name": "Doseuses", "parent": "Pompes", "lft": 7, "rgt": 8},
            {"name": "Vide", "parent": "Racine", "lft": 10, "rgt": 11},
            {"name": "Chimie", "parent": "Racine", "lft": 12, "rgt": 13},
        ]
        arbre = A.cumuler_familles(groupes, {"Pompes": 1, "Inox": 4, "Chimie": 2})
        self.assertEqual([(f["name"], f["niveau"], f["nb"], f["feuille"]) for f in arbre], [
            ("Pompes", 0, 5, False), ("Surface", 1, 4, False), ("Inox", 2, 4, True), ("Chimie", 0, 2, True)])


class TestLigneApercu(unittest.TestCase):

    def test_total_et_paliers(self):
        fiche = {"item_code": "A", "item_name": "Art", "item_group": None, "image": None, "brand": "M",
                 "prix_ttc": 10.0, "prix_standard_ttc": 12.5, "remise_pct": 20.0}
        ligne = {"custom_quantite": 3, "custom_remise_quantite": "-5 % dès 3", "custom_prochain_palier": "", "custom_total_ttc": "28.5"}
        l = A.ligne_apercu(fiche, ligne)
        self.assertEqual((l["qte"], l["total"], l["palier"], l["item_group"], l["remise_pct"]), (3, 28.5, "-5 % dès 3", "", 20.0))
        self.assertIsNone(A.ligne_apercu(fiche, {**ligne, "custom_total_ttc": ""})["total"])

    def test_url_pdf(self):
        u = A.url_pdf("Liste prix -11-11-2026-00001", "Liste prix client")
        self.assertIn("doctype=Liste+prix+documents", u)
        self.assertIn("format=Liste+prix+client", u)


if __name__ == "__main__":
    unittest.main()
