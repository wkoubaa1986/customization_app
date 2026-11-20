"""Analyse Articles : la marge minimale garantie, promos comprises (20/11/2026). Sans la base.

« Dans les règles, assurer une marge minimale à la vente en tenant compte même des promos ; la marge est toujours
sur le prix de vente. » Le plancher d'un prix de liste est le plus petit prix qui garde cette marge même à la remise
la plus forte ; tout prix posé par la page y est relevé quand il passerait dessous."""
import unittest

from customization_app import analyse_articles as A

M5 = {"name": "R5", "titre": "quantité", "type": "pct", "valeur": 5.0, "liste": "Technicien", "min_qty": 20, "max_qty": 49,
      "articles": {"C-10"}, "familles": set()}
M15 = dict(M5, name="R15", valeur=15.0, min_qty=100, max_qty=0)
FIXE = {"name": "RF", "titre": "compteur", "type": "montant", "valeur": 16.0, "liste": "Technicien", "min_qty": 0,
        "max_qty": 0, "articles": {"COMPT"}, "familles": set()}


class TestPlancher(unittest.TestCase):
    def test_la_marge_est_sur_le_prix_de_vente(self):
        # coût 85 TTC, marge 15 % du prix de vente : 85 ÷ 0,85 = 100 (et non 85 × 1,15 = 97,75)
        self.assertEqual(A.prix_plancher(85.0, 15), 100.0)
        self.assertEqual(A._marge(100.0, 85.0), 15.0)

    def test_la_remise_la_plus_forte_compte(self):
        # il faut encore 100 APRÈS −15 % : 100 ÷ 0,85 = 117,648 (arrondi au millime supérieur)
        p = A.prix_plancher(85.0, 15, [M5, M15])
        self.assertEqual(p, 117.648)
        self.assertGreaterEqual(A._marge(A.prix_remise(p, M15), 85.0), 15.0)
        self.assertLess(A._marge(A.prix_remise(p - 0.01, M15), 85.0), 15.0)        # un centime dessous : trop bas

    def test_remise_en_montant(self):
        self.assertEqual(A.prix_plancher(85.0, 15, [FIXE]), 116.0)                  # 100 + 16
        self.assertEqual(A.prix_plancher(85.0, 15, [FIXE, M15]), 117.648)           # la plus exigeante des deux

    def test_sans_cout_ou_sans_taux_pas_de_plancher(self):
        self.assertIsNone(A.prix_plancher(0, 15))            # service, main-d'œuvre : rien à garantir
        self.assertIsNone(A.prix_plancher(85.0, 0))          # garantie non réglée
        self.assertIsNone(A.prix_plancher(85.0, 100))        # 100 % du prix de vente : impossible

    def test_une_remise_de_cent_pour_cent_n_est_pas_comptee(self):
        self.assertEqual(A.prix_plancher(85.0, 15, [dict(M5, valeur=100.0)]), 100.0)

    def test_arrondi_au_millime_superieur(self):
        self.assertEqual(A.prix_plancher(1.0, 15), 1.177)    # 1,17647… ne redescend jamais sous le seuil


class TestCellule(unittest.TestCase):
    """Ce que l'écran signale : le prix lui-même sous le minimum, ou seulement une de ses promos."""

    def cellule(self, pv, taux=15):
        ctx = {"prices": {}, "regles": [M5, M15], "marge_min": taux}
        return A._cellule_prix("C-10", None, "Cartouches", "Technicien", pv, 85.0, ctx)

    def test_le_prix_tient_mais_une_promo_passe_dessous(self):
        c = self.cellule(110.0)                              # 22,7 % ; −5 % → 104,5 (18,7 %) ; −15 % → 93,5 (9,1 %)
        self.assertEqual((c["sous_min"], c["sous_min_prix"]), (1, 0))
        self.assertEqual([r["sous_min"] for r in c["remises"]], [0, 1])
        self.assertEqual(c["plancher"], 117.648)

    def test_le_prix_lui_meme_est_sous_le_minimum(self):
        c = self.cellule(95.0)
        self.assertEqual((c["sous_min"], c["sous_min_prix"]), (1, 1))

    def test_au_plancher_tout_tient(self):
        c = self.cellule(117.648)
        self.assertEqual((c["sous_min"], c["sous_min_prix"], [r["sous_min"] for r in c["remises"]]), (0, 0, [0, 0]))

    def test_sans_garantie_rien_n_est_signale(self):
        c = self.cellule(50.0, taux=0)
        self.assertEqual((c["sous_min"], c["sous_min_prix"], c["plancher"]), (0, 0, None))

    def test_un_prix_a_zero_n_est_pas_un_prix_trop_bas(self):
        c = self.cellule(0.0)                                # offert, ou sans tarif sur cette liste
        self.assertEqual((c["sous_min"], c["sous_min_prix"]), (0, 0))

    def test_le_filtre_garde_les_articles_sous_le_minimum(self):
        lignes = [{"item_code": "A", "prices": {"Technicien": self.cellule(110.0)}},
                  {"item_code": "B", "prices": {"Technicien": self.cellule(130.0)}}]
        self.assertEqual([r["item_code"] for r in A._filtre_marge(lignes, "sous_min")], ["A"])


class TestGarantir(unittest.TestCase):
    """Les prix que la page s'apprête à poser."""
    PLANCHERS = {("A", "Vente standard"): 100.0, ("E", "Vente standard"): 672.0}

    def test_un_prix_trop_bas_est_releve_et_garde_la_trace(self):
        c = A.garantir([{"item_code": "A", "price_list": "Vente standard", "nouveau": 90.0}], self.PLANCHERS)[0]
        self.assertEqual((c["nouveau"], c["calcule"], c["plancher"]), (100.0, 90.0, 100.0))

    def test_un_prix_au_dessus_ne_bouge_pas(self):
        c = A.garantir([{"item_code": "A", "price_list": "Vente standard", "nouveau": 120.0}], self.PLANCHERS)[0]
        self.assertEqual(c["nouveau"], 120.0)
        self.assertNotIn("calcule", c)

    def test_garantie_decochee_le_prix_reste_mais_il_est_signale(self):
        c = A.garantir([{"item_code": "A", "price_list": "Vente standard", "nouveau": 90.0}], self.PLANCHERS, actif=False)[0]
        self.assertEqual((c["nouveau"], c["sous_min"]), (90.0, 1))

    def test_sans_plancher_connu_ou_ensemble_incomplet_rien_ne_change(self):
        lignes = [{"item_code": "SERVICE", "price_list": "Vente standard", "nouveau": 1.0},
                  {"bundle": "E", "price_list": "Vente standard", "nouveau": None}]
        self.assertEqual(A.garantir(lignes[:1], self.PLANCHERS)[0]["nouveau"], 1.0)
        self.assertIsNone(A.garantir(lignes[1:], self.PLANCHERS, cle="bundle")[0]["nouveau"])

    def test_un_prix_pose_a_zero_reste_a_zero(self):
        c = A.garantir([{"item_code": "A", "price_list": "Vente standard", "nouveau": 0.0}], self.PLANCHERS)[0]
        self.assertEqual(c["nouveau"], 0.0)

    def test_les_ensembles_de_la_cascade_aussi(self):
        b = A.garantir([{"bundle": "E", "price_list": "Vente standard", "nouveau": 660.0}], self.PLANCHERS, cle="bundle")[0]
        self.assertEqual((b["nouveau"], b["calcule"]), (672.0, 660.0))


class TestRegleDesEnsembles(unittest.TestCase):
    """La règle des ensembles (somme sans les accessoires) ne descend jamais sous la marge minimale : le coût, lui,
    compte tous les composants."""

    def prix(self, taux):
        listes = [{"name": "Vente standard"}]
        ctx = {"price_lists": listes, "marge_min": taux, "regles": [],
               "valuation": {"E": 0, "AP": 200.0, "ACC": 100.0},
               "taxes": {"E": {"tva": 0}, "AP": {"tva": 0}, "ACC": {"tva": 0}},
               "prices": {("E", "Vente standard"): 500.0, ("AP", "Vente standard"): 330.0, ("ACC", "Vente standard"): 150.0},
               "bundles": {"E": [{"item_code": "AP", "qty": 1}, {"item_code": "ACC", "qty": 1}]},
               "item_info": {"AP": {"item_name": "Appareil"}, "ACC": {"item_name": "Accessoire"}},
               "exclus": {"ACC"}}
        return A.build_item_row({"name": "E", "item_name": "Ensemble", "item_group": "Kits"}, ctx)["bundle"]["prices"]["Vente standard"]

    def test_sans_garantie_la_regle_donne_la_somme_sans_l_accessoire(self):
        p = self.prix(0)
        self.assertEqual(p["regle_ttc"], 330.0)              # 9,1 % de marge sur un coût de 300
        self.assertNotIn("regle_calcule", p)

    def test_avec_garantie_le_prix_de_la_regle_est_releve(self):
        p = self.prix(15)
        self.assertEqual((p["regle_ttc"], p["regle_calcule"]), (352.942, 330.0))   # 300 ÷ 0,85
        self.assertGreaterEqual(p["regle_marge"], 15.0)


if __name__ == "__main__":
    unittest.main()
