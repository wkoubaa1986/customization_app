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
        self.assertIsNone(l["prix_promo"])
        self.assertEqual(A.ligne_apercu(fiche, {**ligne, "prix_ttc_promo": "8.5"})["prix_promo"], 8.5)
        self.assertIsNone(A.ligne_apercu(fiche, {**ligne, "prix_ttc_promo": ""})["prix_promo"])

    def test_articles_pour_promotions(self):
        # la forme que lit le Server Script « gener_list_prix » : le prix est celui de la liste, TTC
        self.assertEqual(A.articles_pour_promotions([{"item_code": "A", "item_group": "G", "prix_ttc": 10.0, "image": "x"}]),
                         [{"item_code": "A", "item_group": "G", "price": 10.0}])

    def test_modele_et_rang_des_variantes(self):
        fiche = {"item_code": "V2", "item_name": "Art", "item_group": "G", "prix_ttc": 10.0, "modele": "M", "rang": 3}
        ligne = {"custom_quantite": 1, "custom_remise_quantite": "", "custom_prochain_palier": "", "custom_total_ttc": "10"}
        l = A.ligne_apercu(fiche, ligne)
        self.assertEqual((l["modele"], l["rang"]), ("M", 3))
        l = A.ligne_apercu({**fiche, "modele": None, "rang": None}, ligne)
        self.assertEqual((l["modele"], l["rang"]), (None, 0))

    def test_rangs_variantes_ordre_des_attributs(self):
        variantes = {"M-75-B": "M", "M-50-R": "M", "M-50-B": "M", "N-2": "N", "N-10": "N"}
        attributs = [
            # modèle M : 1er attribut Débit (liste), 2e Couleur (liste)
            {"parent": "M-75-B", "attribute": "Débit", "attribute_value": "75 GPD", "idx": 1},
            {"parent": "M-75-B", "attribute": "Couleur", "attribute_value": "Bleu", "idx": 2},
            {"parent": "M-50-R", "attribute": "Débit", "attribute_value": "50 GPD", "idx": 1},
            {"parent": "M-50-R", "attribute": "Couleur", "attribute_value": "Rouge", "idx": 2},
            {"parent": "M-50-B", "attribute": "Couleur", "attribute_value": "Bleu", "idx": 2},
            {"parent": "M-50-B", "attribute": "Débit", "attribute_value": "50 GPD", "idx": 1},
            # modèle N : attribut numérique, absent des listes de valeurs -> ordre numérique (2 avant 10)
            {"parent": "N-2", "attribute": "Longueur", "attribute_value": "2", "idx": 1},
            {"parent": "N-10", "attribute": "Longueur", "attribute_value": "10", "idx": 1},
        ]
        ordre = {("Débit", "50 GPD"): 1, ("Débit", "75 GPD"): 2, ("Couleur", "Rouge"): 1, ("Couleur", "Bleu"): 2}
        self.assertEqual(A.rangs_variantes(variantes, attributs, ordre),
                         {"M-50-R": 0, "M-50-B": 1, "M-75-B": 2, "N-2": 0, "N-10": 1})

    def test_attributs_variantes(self):
        attributs = [
            {"parent": "V", "attribute": "Couleur", "attribute_value": "Bleu", "idx": 2},
            {"parent": "V", "attribute": "Débit", "attribute_value": "75 GPD", "idx": 1},
            {"parent": "W", "attribute": "Longueur", "attribute_value": "2,5", "idx": 1},
            {"parent": "W", "attribute": "Finition", "attribute_value": "Mat", "idx": 2},
        ]
        a = A.attributs_variantes(attributs, {("Débit", "75 GPD"): 2, ("Couleur", "Bleu"): 1})
        self.assertEqual(a["V"], [{"nom": "Débit", "valeur": "75 GPD", "pos": 2}, {"nom": "Couleur", "valeur": "Bleu", "pos": 1}])
        # attribut numérique : sa valeur ; texte hors liste : None (alphabétique à l'écran)
        self.assertEqual(a["W"], [{"nom": "Longueur", "valeur": "2,5", "pos": 2.5}, {"nom": "Finition", "valeur": "Mat", "pos": None}])

    def test_ancetre_commun(self):
        self.assertEqual(A.ancetre_commun([["Racine", "Pompes", "Surface", "Inox"]]), "Inox")
        self.assertEqual(A.ancetre_commun([["Racine", "Pompes", "Surface", "Inox"], ["Racine", "Pompes", "Doseuses"]]), "Pompes")
        self.assertEqual(A.ancetre_commun([["Racine", "Pompes", "Surface"], ["Racine", "Pompes"]]), "Pompes")
        self.assertEqual(A.ancetre_commun([["Racine", "Pompes"], ["Racine", "Chimie"]]), "Racine")
        self.assertIsNone(A.ancetre_commun([]))

    def test_url_pdf(self):
        u = A.url_pdf("Liste prix -11-11-2026-00001", "Liste prix client")
        self.assertIn("doctype=Liste+prix+documents", u)
        self.assertIn("format=Liste+prix+client", u)


if __name__ == "__main__":
    unittest.main()
