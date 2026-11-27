"""Le tiroir unique du collecteur principal (27/11/2026) — fonctions pures de caisse_tiroir.

Le cas d'origine : Nejib compte tout son tiroir, mais la caisse le coupait en deux poches. Compté 1 800 DT pour
2 871,826 attendus (−1 071,826) puis 2 600 pour 1 912 (+688), alors que le compte « Espèces » disait 1 991,784 puis
2 747,974.
"""
import unittest

from customization_app import caisse_tiroir as CT


class TestReference(unittest.TestCase):
    def test_le_tiroir_contient_ce_qui_n_est_pas_ailleurs(self):
        r = CT.reference_de(compte=416.974, autres=469.0, delegues=0, ecarts_autres=211.831)
        self.assertEqual(r["absolu"], -52.026)
        self.assertEqual(r["reference"], 159.805)

    def test_un_delegue_garde_ce_qu_il_a_collecte(self):
        r = CT.reference_de(compte=1000, autres=100, delegues=300, ecarts_autres=0)
        self.assertEqual(r["absolu"], 600.0)

    def test_l_ecart_d_un_employe_ne_tombe_pas_dans_le_tiroir(self):
        """Salma doit avoir 300, en compte 290 : sa caisse tient 10 de moins, son écart vaut −10, la référence ne
        bouge pas — les 10 DT manquent chez elle, pas chez le collecteur."""
        avant = CT.reference_de(compte=277.974, autres=330.0, delegues=0, ecarts_autres=211.831)
        apres = CT.reference_de(compte=277.974, autres=320.0, delegues=0, ecarts_autres=201.831)
        self.assertEqual(avant["reference"], apres["reference"])

    def test_une_collecte_fait_entrer_l_argent_dans_le_tiroir(self):
        avant = CT.reference_de(compte=277.974, autres=320.0, delegues=0, ecarts_autres=201.831)
        apres = CT.reference_de(compte=277.974, autres=30.0, delegues=0, ecarts_autres=201.831)
        self.assertEqual(round(apres["reference"] - avant["reference"], 3), 290.0)

    def test_un_versement_au_coffre_le_fait_sortir(self):
        avant = CT.reference_de(compte=277.974, autres=30.0, delegues=0, ecarts_autres=0)
        apres = CT.reference_de(compte=77.974, autres=30.0, delegues=0, ecarts_autres=0)
        self.assertEqual(round(apres["reference"] - avant["reference"], 3), -200.0)


class TestAttendu(unittest.TestCase):
    def test_premier_comptage_compare_au_compte(self):
        ref = CT.reference_de(2747.974, 0, 0, 211.831)
        a = CT.attendu_de(None, ref)
        self.assertEqual((a["attendu"], a["mode"]), (2747.974, "compte"))

    def test_un_comptage_sans_reference_ne_sert_pas_de_depart(self):
        a = CT.attendu_de({"compte": 2600, "reference": None}, CT.reference_de(100, 0, 0, 0))
        self.assertEqual(a["mode"], "compte")

    def test_la_chaine_repart_du_dernier_comptage(self):
        """Compté 120 ; puis 290 collectés et 200 versés au coffre : il doit avoir 210, quel que soit l'écart qu'il
        avait avec le compte au moment de compter."""
        base = {"compte": 120.0, "reference": 159.805}
        a = CT.attendu_de(base, {"reference": 249.805, "absolu": 47.974})
        self.assertEqual((a["attendu"], a["mode"], a["delta"]), (210.0, "chaine", 90.0))

    def test_le_cas_du_27_11(self):
        """Compté 2 600 à 08:04, 2 800 versés au coffre 18 minutes plus tard : le tiroir ne peut pas tenir −200."""
        base = {"compte": 2600.0, "reference": 2747.974}
        a = CT.attendu_de(base, {"reference": -52.026, "absolu": -52.026})
        self.assertEqual(a["attendu"], -200.0)

    def test_decomposition_lisible(self):
        d = CT.decomposer(delta=90.0, collecte=290.0, sorties=200.0)
        self.assertEqual(d["autres"], 0.0)
        d = CT.decomposer(delta=756.19, collecte=694.2, sorties=0)
        self.assertEqual(d["autres"], 61.99)


class TestComptages(unittest.TestCase):
    LISTE = [
        {"name": "CLO-1", "quand": "2026-11-27 08:04:46.100000", "par": "nejib@x", "compte": 100},
        {"name": "REG-1", "quand": "2026-11-28 10:00:00.000000", "par": "wassim@x", "compte": 250},
        {"name": "CLO-2", "quand": "2026-11-29 08:00:00", "par": "nejib@x", "compte": 300},
    ]

    def test_le_plus_recent(self):
        self.assertEqual(CT.plus_recent(self.LISTE)["name"], "CLO-2")
        self.assertEqual(CT.plus_recent(self.LISTE, "2026-11-28 23:59:59")["name"], "REG-1")
        self.assertIsNone(CT.plus_recent(self.LISTE, "2026-11-26 23:59:59"))
        self.assertIsNone(CT.plus_recent([]))

    def test_seul_un_comptage_par_un_autre_est_une_verification(self):
        self.assertEqual(CT.derniere_verification(self.LISTE, "nejib@x")["name"], "REG-1")
        self.assertIsNone(CT.derniere_verification([c for c in self.LISTE if c["par"] == "nejib@x"], "nejib@x"))

    def test_l_instant_garde_sa_precision(self):
        """Un versement saisi dans la même seconde, AVANT le comptage, ne doit pas compter comme « depuis »."""
        self.assertLess("2026-11-27 08:04:46.050000", self.LISTE[0]["quand"])


class TestAnciennete(unittest.TestCase):
    def test_tiroir_vide(self):
        self.assertEqual(CT.depuis_quand([("2026-11-20", 500)], 0.2, "2026-11-27"), (None, 0))

    def test_l_argent_du_tiroir_est_celui_des_dernieres_entrees(self):
        entrees = [("2026-11-23", 1727.363), ("2026-11-25", 959.5), ("2026-11-26", 390.0), ("2026-11-27", 367.2)]
        self.assertEqual(CT.depuis_quand(entrees, 300, "2026-11-27"), ("2026-11-27", 0))
        self.assertEqual(CT.depuis_quand(entrees, 700, "2026-11-27"), ("2026-11-26", 1))
        self.assertEqual(CT.depuis_quand(entrees, 5000, "2026-11-27"), ("2026-11-23", 4))


class TestVerdict(unittest.TestCase):
    def _v(self, **k):
        base = dict(jour="2026-11-27", nom="Nejib", tiroir={"attendu": 500.0},
                    comptage={"quand": "2026-11-27 08:04:46", "compte": 500, "attendu": 500, "ecart": 0, "par_nom": "Nejib"},
                    verification={"quand": "2026-11-20 10:00:00"},
                    coffre={"solde": 63800.0, "inventaire": {"date": "2026-11-20", "ecart": 0}},
                    pieces={"anciennes": 0, "anciennes_total": 0, "ecart_comptable": 0},
                    employes=[{"caisse": "Salma", "jours_non_comptes": 1}], a_regulariser=0, alertes=())
        base.update(k)
        return CT.verdict(**base)

    def test_tout_est_en_ordre(self):
        v = self._v()
        self.assertEqual((v["etat"], v["points"]), ("ok", []))

    def test_tiroir_negatif(self):
        v = self._v(tiroir={"attendu": -200.0})
        self.assertEqual(v["etat"], "alerte")
        self.assertIn("200,000 DT de plus", v["points"][0]["texte"])

    def test_petit_negatif_tolere(self):
        self.assertEqual(self._v(tiroir={"attendu": -5.0})["etat"], "ok")

    def test_ecart_au_dernier_comptage(self):
        v = self._v(comptage={"quand": "2026-11-27 08:04:46", "compte": 1800, "attendu": 1991.784, "ecart": -191.784, "par_nom": "Nejib"})
        self.assertEqual(v["etat"], "alerte")
        self.assertIn("il manquait 191,784 DT", v["points"][0]["texte"])
        v = self._v(comptage={"quand": "2026-11-27 08:04:46", "compte": 510, "attendu": 500, "ecart": 10, "par_nom": "Nejib"})
        self.assertEqual(v["etat"], "ok")

    def test_jamais_verifie_par_un_autre(self):
        v = self._v(verification=None)
        self.assertEqual(v["etat"], "a_faire")
        self.assertIn("Personne d'autre que Nejib", v["points"][0]["texte"])

    def test_verification_trop_ancienne(self):
        v = self._v(verification={"quand": "2026-10-01 10:00:00"})
        self.assertEqual(v["etat"], "a_faire")
        self.assertIn("01/10/2026", v["points"][0]["texte"])

    def test_tiroir_pas_compte(self):
        self.assertIn("pas encore été compté", self._v(comptage=None)["points"][0]["texte"])
        v = self._v(comptage={"quand": "2026-11-20 08:00:00", "compte": 1, "attendu": 1, "ecart": 0, "par_nom": "Nejib"})
        self.assertIn("pas été compté depuis le 20/11/2026", v["points"][0]["texte"])

    def test_coffre_jamais_compte(self):
        v = self._v(coffre={"solde": 63800.0, "inventaire": None})
        self.assertIn("63 800,000 DT", v["points"][0]["texte"])
        self.assertEqual(self._v(coffre={"solde": 0.0, "inventaire": None})["etat"], "ok")

    def test_pieces_anciennes_et_alertes_d_abord(self):
        v = self._v(pieces={"anciennes": 5, "anciennes_total": 7188.914, "ecart_comptable": 0}, verification=None,
                    a_regulariser=2, employes=[{"caisse": "Sadok", "jours_non_comptes": 3}])
        self.assertEqual(v["etat"], "alerte")
        self.assertEqual([p["niveau"] for p in v["points"]], ["alerte", "a_faire", "a_faire", "a_faire"])
        self.assertIn("7 188,914 DT", v["points"][0]["texte"])
        self.assertIn("Sadok", v["points"][2]["texte"])
        self.assertEqual(v["titre"], "4 point(s) à voir")

    def test_rien_d_anormal_mais_des_choses_a_faire(self):
        self.assertEqual(self._v(a_regulariser=1)["titre"], "Rien d'anormal, 1 chose(s) à faire")


class TestCaissesValidees(unittest.TestCase):
    """« Une valeur de ce que Nejib doit avoir en remontant les caisses validées », à partir d'un montant figé."""

    def test_le_cas_du_27_11(self):
        """Départ figé au soir du 24/11 : 1 009,724 ; caisses des 25 et 26/11 ; 2 800 versés au coffre ; 100 donnés
        ce matin, pas encore comptés."""
        total = CT.total_caisses(base=1009.724, collecte=1076.7, encaissements=847.38, depenses=0, ecritures=-2800, non_compte=-100)
        self.assertEqual(total, 33.804)
        c = CT.comparer_caisses(total, selon_compte=-52.026, ecarts_employes=135.83, sans_caisse=50.0)
        self.assertEqual((c["difference"], c["reste"]), (85.83, 0.0))

    def test_tout_tombe_juste(self):
        c = CT.comparer_caisses(500.0, 500.0, 0.0)
        self.assertEqual((c["difference"], c["reste"], c["sans_caisse_total"]), (0.0, 0.0, 0.0))

    def test_ce_qui_n_est_pas_explique_reste_visible(self):
        self.assertEqual(CT.comparer_caisses(100.0, 80.0, 5.0, 0.0)["reste"], 15.0)

    def test_le_verdict_le_dit(self):
        base = dict(jour="2026-11-27", nom="Nejib", tiroir={"attendu": 500.0},
                    comptage={"quand": "2026-11-27 08:04:46", "compte": 500, "attendu": 500, "ecart": 0, "par_nom": "Nejib"},
                    verification={"quand": "2026-11-20 10:00:00"}, coffre={"solde": 0.0, "inventaire": None},
                    pieces={"anciennes": 0}, employes=[], a_regulariser=0)
        v = CT.verdict(**base, caisses={"depuis": "2026-11-25", "difference": 85.83, "ecarts_employes": 135.83,
                                        "sans_caisse_total": 50.0, "reste": 0.0})
        self.assertEqual(v["etat"], "alerte")
        texte = v["points"][0]["texte"]
        self.assertIn("85,830 DT de plus", texte)
        self.assertIn("135,830 DT d'écarts de comptage des employés", texte)
        self.assertIn("50,000 DT de paiements que personne n'a comptés", texte)
        self.assertNotIn("sans explication", texte)
        calme = CT.verdict(**base, caisses={"depuis": "2026-11-25", "difference": 4.0, "ecarts_employes": 4.0, "sans_caisse_total": 0, "reste": 0})
        self.assertEqual(calme["etat"], "ok")
        self.assertEqual(CT.verdict(**base, caisses=None)["etat"], "ok")


class TestCausesDeLaDifference(unittest.TestCase):
    ECARTS = [{"name": "CLO-1874", "caisse": "Sadok Bouziri", "date": "2026-11-25", "ecart": 135.82, "attendu": -62.82,
               "compte": 73.0, "depenses": 135.82, "note": ""},
              {"name": "CLO-1905", "caisse": "Jamel Aloui", "date": "2026-11-26", "ecart": 0.01, "attendu": 154.39,
               "compte": 154.4, "depenses": 11.01, "note": "arrondi"}]
    SANS = [{"name": "ACC-PAY-2026-08643", "date": "2026-11-26", "montant": 50.0, "client": "Ben Aissa Ahmed", "saisi_par": "Administrator"}]

    def test_la_somme_des_causes_est_la_difference(self):
        causes = CT.causes_difference(self.ECARTS, self.SANS, 0.0)
        self.assertEqual([c["nature"] for c in causes], ["Écart de comptage", "Écart de comptage", "Paiement dans aucune caisse"])
        self.assertEqual(round(sum(c["effet"] for c in causes), 3), 85.83)
        self.assertEqual((causes[0]["document_type"], causes[0]["document"]), ("Cloture Caisse", "CLO-1874"))
        self.assertEqual((causes[2]["document"], causes[2]["effet"]), ("ACC-PAY-2026-08643", -50.0))

    def test_la_depense_payee_avec_un_autre_argent_est_dite(self):
        sadok, jamel = CT.causes_difference(self.ECARTS, [], 0.0)
        self.assertIn("135,820 DT de dépenses sont saisies sur sa caisse", sadok["explication"])
        self.assertIn("a compté 73,000 DT alors que sa caisse en attendait −62,820", sadok["explication"])
        self.assertNotIn("dépenses sont saisies", jamel["explication"])     # 0,010 : pas une dépense payée ailleurs
        self.assertIn("Sa note : « arrondi »", jamel["explication"])
        self.assertIn("avec quel argent la dépense a été payée", sadok["a_faire"])
        self.assertEqual(jamel["a_faire"], "Rien : un arrondi.")

    def test_un_manque_et_un_reste(self):
        causes = CT.causes_difference([{"name": "CLO-9", "caisse": "Salma", "date": "2026-11-27", "ecart": -10.0,
                                        "attendu": 300.0, "compte": 290.0, "depenses": 0, "note": ""}], [], 3.5)
        self.assertIn("n'est jamais arrivé chez le collecteur", causes[0]["explication"])
        self.assertIn("où est passé l'argent", causes[0]["a_faire"])
        trop = CT.causes_difference([{"name": "CLO-8", "caisse": "Badis", "date": "2026-11-27", "ecart": 40.0,
                                      "attendu": 100.0, "compte": 140.0, "depenses": 0, "note": ""}], [], 0)[0]
        self.assertIn("d'où vient l'argent en trop", trop["a_faire"])
        self.assertEqual((causes[1]["nature"], causes[1]["effet"]), ("Sans explication", 3.5))
        self.assertEqual(CT.causes_difference([], [], 0.0), [])


class TestFormat(unittest.TestCase):
    def test_montants(self):
        self.assertEqual(CT._dt(7188.914), "7 188,914")
        self.assertEqual(CT._dt(-52.026), "−52,026")
        self.assertEqual(CT._jour("2026-11-27 08:04:46"), "27/11/2026")
        self.assertEqual(CT.jours_entre("2026-11-20 10:00:00", "2026-11-27"), 7)


if __name__ == "__main__":
    unittest.main()
