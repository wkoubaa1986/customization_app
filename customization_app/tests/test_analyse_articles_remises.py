"""Analyse Articles : ordre des listes de prix, écart avec la vente, réductions et leur marge (19/11/2026). Sans la base."""
import unittest

from customization_app import analyse_articles as A

# Les vraies règles du 19/11/2026, simplifiées
SOPAL = {"name": "PRLE-0222", "titre": "remise compteur Sopal", "type": "montant", "valeur": 16.0, "liste": "Technicien",
         "min_qty": 0, "max_qty": 0, "articles": {"COMPT-SOP-15"}, "familles": set()}
QTE_10 = {"name": "PRLE-0201", "titre": "Réduction quantité", "type": "pct", "valeur": 10.0, "liste": "Technicien",
          "min_qty": 8, "max_qty": 14, "articles": set(), "familles": {"Osmoseurs Domestiques", "RO domestique avec pompe"}}
QTE_15 = dict(QTE_10, name="PRLE-0202", valeur=15.0, min_qty=15, max_qty=0)
PARTOUT = {"name": "PRLE-X", "titre": "promo", "type": "pct", "valeur": 5.0, "liste": None, "min_qty": 0, "max_qty": 0,
           "articles": {"AP-M"}, "familles": set()}
REGLES = [QTE_15, SOPAL, QTE_10, PARTOUT]


class TestOrdreDesListes(unittest.TestCase):
    def test_la_vente_d_abord_puis_les_listes_professionnelles(self):
        self.assertEqual(A.ordonner_listes(["Compte Pro", "Pro Grand Rayon", "Quincaillerie", "Technicien", "Vente standard"]),
                         ["Vente standard", "Technicien", "Quincaillerie", "Compte Pro", "Pro Grand Rayon"])

    def test_une_liste_inconnue_se_range_apres(self):
        self.assertEqual(A.ordonner_listes(["Zeta", "Technicien", "Alpha", "Vente standard"]),
                         ["Vente standard", "Technicien", "Alpha", "Zeta"])


class TestReductions(unittest.TestCase):
    def test_remise_en_pourcentage_et_en_montant(self):
        self.assertEqual(A.prix_remise(100, QTE_10), 90.0)
        self.assertEqual(A.prix_remise(60, SOPAL), 44.0)
        self.assertEqual(A.prix_remise(10, SOPAL), 0.0)             # jamais négatif
        self.assertIsNone(A.prix_remise(None, SOPAL))

    def test_regle_sur_l_article_et_seulement_sur_sa_liste(self):
        self.assertEqual([r["name"] for r in A.remises_applicables("COMPT-SOP-15", None, "Compteurs", "Technicien", REGLES)], ["PRLE-0222"])
        self.assertEqual(A.remises_applicables("COMPT-SOP-15", None, "Compteurs", "Vente standard", REGLES), [])

    def test_regle_sur_la_famille_avec_ses_paliers_dans_l_ordre(self):
        r = A.remises_applicables("AP-C-1", None, "RO domestique avec pompe", "Technicien", REGLES)
        self.assertEqual([x["name"] for x in r], ["PRLE-0201", "PRLE-0202"])
        self.assertEqual([A.libelle_palier(x) for x in r], ["de 8 à 14", "dès 15"])

    def test_regle_sans_liste_vaut_pour_toutes_et_le_modele_couvre_ses_variantes(self):
        self.assertEqual([r["name"] for r in A.remises_applicables("AP-M-3", "AP-M", "Autre", "Quincaillerie", REGLES)], ["PRLE-X"])
        self.assertEqual(A.remises_applicables("AUTRE", None, "Autre", "Quincaillerie", REGLES), [])

    def test_libelle_sans_palier(self):
        self.assertEqual(A.libelle_palier(SOPAL), "")
        self.assertEqual(A.libelle_palier({"min_qty": 1, "max_qty": 5}), "jusqu'à 5")


class TestCellule(unittest.TestCase):
    """La cellule d'un prix : marge sur le prix de vente, écart avec la Vente standard, réductions avec LEUR marge."""

    def cellule(self, liste, pv):
        ctx = {"prices": {("AP-C-1", "Vente standard"): 500.0}, "regles": REGLES}
        return A._cellule_prix("AP-C-1", None, "RO domestique avec pompe", liste, pv, 300.0, ctx)

    def test_marge_sur_le_prix_de_vente(self):
        self.assertEqual(self.cellule("Vente standard", 500.0)["marge"], 40.0)       # (500 − 300) / 500

    def test_ecart_avec_la_vente(self):
        self.assertIsNone(self.cellule("Vente standard", 500.0)["ecart_ref"])        # la référence elle-même
        self.assertEqual(self.cellule("Technicien", 440.0)["ecart_ref"], -12.0)      # 440 est 12 % sous 500

    def test_chaque_reduction_a_son_prix_et_sa_marge(self):
        r = self.cellule("Technicien", 440.0)["remises"]
        self.assertEqual([(x["libelle"], x["palier"], x["prix"]) for x in r],
                         [("−10 %", "de 8 à 14", 396.0), ("−15 %", "dès 15", 374.0)])
        self.assertEqual(r[0]["marge"], round((396.0 - 300.0) / 396.0 * 100, 2))
        self.assertEqual(self.cellule("Vente standard", 500.0)["remises"], [])

    def test_sans_prix_pas_de_reduction_affichee(self):
        c = self.cellule("Technicien", None)
        self.assertEqual((c["marge"], c["ecart_ref"], c["remises"]), (None, None, []))


if __name__ == "__main__":
    unittest.main()
