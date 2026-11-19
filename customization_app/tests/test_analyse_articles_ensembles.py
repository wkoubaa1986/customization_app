"""Analyse Articles : prix d'un ensemble = somme des composants MOINS ce que la règle exclut (19/11/2026). Sans la base."""
import unittest

from customization_app import analyse_articles as A

REGLE = {"familles": {"Coudes", "Clips & colliers"}, "debuts": ["Acc0"], "contient": ["relais"]}
# AP-C-DF-6-UA-AM tel qu'en base le 19/11/2026 (liste « Vente standard »)
COMPOSANTS = [{"item_code": "AP-C", "qty": 1}, {"item_code": "Acc001-B", "qty": 2}, {"item_code": "Acc005-D", "qty": 1},
              {"item_code": "Acc007-B", "qty": 1}, {"item_code": "Acc009-A", "qty": 2}, {"item_code": "Acc021-C", "qty": 1},
              {"item_code": "Acc022-A", "qty": 2}, {"item_code": "Rel-24V", "qty": 1}, {"item_code": "F-UV-6w", "qty": 1},
              {"item_code": "M-75-DF", "qty": 1}]
PRIX = {"AP-C": 455, "Acc001-B": 3.5, "Acc005-D": 7, "Acc007-B": 3, "Acc009-A": 6, "Acc021-C": 15, "Acc022-A": 4,
        "Rel-24V": 25, "F-UV-6w": 120, "M-75-DF": 60}


class TestExclusion(unittest.TestCase):
    def test_par_famille(self):
        self.assertTrue(A.est_exclu("X1", "Coude 1/4", "Coudes", REGLE))
        self.assertFalse(A.est_exclu("X1", "Membrane 75", "Membranes", REGLE))

    def test_par_debut_de_code_sans_tenir_compte_de_la_casse(self):
        self.assertTrue(A.est_exclu("Acc021-C", "Vanne", "Vannes & régulation", REGLE))
        self.assertTrue(A.est_exclu("ACC009-A", "Connecteur", "Connecteurs T", REGLE))
        self.assertFalse(A.est_exclu("M-Acc0", "Membrane", "Membranes", REGLE))        # « commence par », pas « contient »

    def test_par_mot_contenu_dans_le_code_ou_le_nom_sans_accent(self):
        self.assertTrue(A.est_exclu("Rel-24V", "Relais 24 V", "Accessoires électriques", REGLE))
        self.assertTrue(A.est_exclu("X-RELAIS", "Autre", "Divers", REGLE))
        regle = {"contient": ["électro"]}
        self.assertTrue(A.est_exclu("EV-1", "Electrovanne 24V", "Vannes", regle))

    def test_sans_regle_rien_n_est_exclu(self):
        self.assertFalse(A.est_exclu("Acc001-B", "Coude", "Coudes", {}))
        self.assertFalse(A.est_exclu("Acc001-B", "Coude", "Coudes", None))
        self.assertFalse(A.est_exclu("Acc001-B", "Coude", "Coudes", {"debuts": [""], "contient": [""]}))

    def test_lignes_du_reglage(self):
        self.assertEqual(A._lignes("Coudes\n Clips & colliers ,Acc0;\n\n"), ["Coudes", "Clips & colliers", "Acc0"])
        self.assertEqual(A._lignes(None), [])


class TestPrixEnsemble(unittest.TestCase):
    def test_sans_exclusion_c_est_la_somme_entiere(self):
        self.assertEqual(A.prix_ensemble(COMPOSANTS, PRIX), (712.0, False))

    def test_les_accessoires_ne_sont_pas_comptes(self):
        exclus = {c["item_code"] for c in COMPOSANTS if c["item_code"].startswith("Acc0")}
        self.assertEqual(A.prix_ensemble(COMPOSANTS, PRIX, exclus), (660.0, False))      # 712 − 52 d'accessoires

    def test_un_composant_compte_sans_prix_rend_l_ensemble_incomplet(self):
        prix = dict(PRIX, **{"F-UV-6w": None})
        self.assertEqual(A.prix_ensemble(COMPOSANTS, prix), (None, True))

    def test_un_composant_exclu_n_a_pas_besoin_de_prix(self):
        prix = dict(PRIX); del prix["Acc005-D"]
        self.assertEqual(A.prix_ensemble(COMPOSANTS, prix, {"Acc005-D"}), (705.0, False))

    def test_un_ensemble_fait_seulement_d_accessoires_n_est_pas_mis_a_zero(self):
        """Acc027-A (un kit d'accessoires) passait de 15 DT à 0 : la règle ne s'applique pas à lui."""
        kit = [{"item_code": "Acc001-B", "qty": 2}, {"item_code": "Acc005-D", "qty": 1}]
        self.assertEqual(A.prix_ensemble(kit, PRIX, {"Acc001-B", "Acc005-D"}), (None, True))
        self.assertEqual(A.prix_ensemble([], PRIX), (None, True))

    def test_les_quantites_comptent(self):
        self.assertEqual(A.prix_ensemble([{"item_code": "A", "qty": 2.5}], {"A": 4}), (10.0, False))


class TestValorisationJamaisAmputee(unittest.TestCase):
    """« Sur la valorisation des ensembles il ne faut jamais exclure la valeur des articles » (19/11/2026) :
    la règle n'enlève des composants que du PRIX DE VENTE. Le coût de revient, lui, les compte tous — la marge
    affichée « selon la règle » est donc la vraie : prix sans les accessoires, coût avec."""

    def ligne(self, exclus):
        listes = [{"name": "Vente standard"}]
        ctx = {"price_lists": listes,
               "valuation": {"E": 0, "AP": 200.0, "ACC": 10.0},
               "taxes": {"E": {"tva": 0}, "AP": {"tva": 0}, "ACC": {"tva": 0}},
               "prices": {("E", "Vente standard"): 500.0, ("AP", "Vente standard"): 450.0, ("ACC", "Vente standard"): 25.0},
               "bundles": {"E": [{"item_code": "AP", "qty": 1}, {"item_code": "ACC", "qty": 2}]},
               "item_info": {"AP": {"item_name": "Appareil"}, "ACC": {"item_name": "Accessoire"}},
               "exclus": exclus}
        return A.build_item_row({"name": "E", "item_name": "Ensemble", "item_group": "Kits"}, ctx)["bundle"]

    def test_le_cout_compte_tous_les_composants_meme_exclus_du_prix(self):
        sans, avec = self.ligne(set()), self.ligne({"ACC"})
        self.assertEqual(sans["cout_ht"], 220.0)                       # 200 + 2 × 10
        self.assertEqual(avec["cout_ht"], 220.0)                       # identique : l'accessoire reste dans le coût
        self.assertEqual(avec["cout_ttc"], sans["cout_ttc"])
        self.assertEqual([c["val_ht"] for c in avec["components"]], [200.0, 10.0])

    def test_seul_le_prix_de_vente_selon_la_regle_change(self):
        sans, avec = self.ligne(set())["prices"]["Vente standard"], self.ligne({"ACC"})["prices"]["Vente standard"]
        self.assertEqual((sans["calc_ttc"], sans["regle_ttc"]), (500.0, 500.0))
        self.assertEqual((avec["calc_ttc"], avec["regle_ttc"]), (500.0, 450.0))     # somme entière / sans l'accessoire
        self.assertEqual(avec["regle_marge"], round((450.0 - 220.0) / 450.0 * 100, 2))  # marge sur le coût COMPLET


if __name__ == "__main__":
    unittest.main()
