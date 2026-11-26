"""Machine rendue (26/11/2026) — règles PURES : quand l'échéancier est annulé, quand il est gardé, les textes, la fenêtre
du passage de nuit, le compte rendu du rattrapage. Dates fixes."""
import unittest

from customization_app import retours_machines as RM
from customization_app.patches import echeanciers_machines_rendues as P

MACHINES = {"AP-C+-EP-7-MA-SM", "P-F-10'-B-3/4", "F-UV-25w", "TD-1.5"}


def est_machine(code):
    return code in MACHINES


class TestDecision(unittest.TestCase):
    def test_tout_rendu_annule(self):
        self.assertEqual(RM.decision([("AP-C+-EP-7-MA-SM", "WEB1-008450")], {("WEB1-008450", "AP-C+-EP-7-MA-SM"): (1, 1)},
                                     est_machine), ("annuler", ""))

    def test_rendu_en_partie_garde(self):
        action, motif = RM.decision([("TD-1.5", "SO1")], {("SO1", "TD-1.5"): (2, 1)}, est_machine)
        self.assertEqual(action, "garder")
        self.assertIn("TD-1.5 : 1 rendue(s) sur 2 livrée(s)", motif)

    def test_autre_machine_encore_chez_le_client(self):
        lignes = [("P-F-10'-B-3/4", "SO1"), ("F-UV-25w", "SO1")]
        action, motif = RM.decision(lignes, {("SO1", "F-UV-25w"): (1, 1), ("SO1", "P-F-10'-B-3/4"): (1, 0)}, est_machine)
        self.assertEqual(action, "garder")
        self.assertIn("P-F-10'-B-3/4 : 0 rendue(s) sur 1", motif)
        self.assertNotIn("F-UV-25w", motif)
        # … les deux rendues : annulé
        self.assertEqual(RM.decision(lignes, {("SO1", "F-UV-25w"): (1, 1), ("SO1", "P-F-10'-B-3/4"): (1, 1)},
                                     est_machine)[0], "annuler")

    def test_machine_sans_commande_supposee_chez_le_client(self):
        action, motif = RM.decision([("F-UV-25w", "SO1"), ("TD-1.5", None)], {("SO1", "F-UV-25w"): (1, 1)}, est_machine)
        self.assertEqual(action, "garder")
        self.assertIn("TD-1.5 (sans commande)", motif)

    def test_lignes_non_machines_ignorees(self):
        self.assertEqual(RM.decision([("F-UV-25w", "SO1"), ("Sac-sel", "SO1")], {("SO1", "F-UV-25w"): (1, 1)},
                                     est_machine)[0], "annuler")
        self.assertEqual(RM.decision([("Sac-sel", "SO1")], {}, est_machine)[0], "garder")

    def test_rien_de_livre_ne_vaut_pas_rendu(self):
        self.assertFalse(RM.toute_rendue(0, 1))
        self.assertFalse(RM.toute_rendue(2, 1.5))
        self.assertTrue(RM.toute_rendue(2, 3))
        self.assertEqual(RM.decision([("F-UV-25w", "SO1")], {}, est_machine)[0], "garder")


class TestRetablir(unittest.TestCase):
    """Relecture du 26/11/2026 : une machine revenue (remplacement sur la même commande, retour annulé) restait sans
    suivi pour toujours — l'échéancier annulé couvre sa commande pour la nuit."""

    def test_rien_de_livre_n_est_pas_chez_le_client(self):
        self.assertFalse(RM.encore_chez_le_client(0, 0))
        self.assertFalse(RM.encore_chez_le_client(1, 1))
        self.assertTrue(RM.encore_chez_le_client(2, 1))
        self.assertTrue(RM.encore_chez_le_client(1, 0))

    def test_remplacement_livre_retablit(self):
        # SAL-ORD-2026-04731 : 1 livré, 1 rendu, puis le remplacement livré sur la même commande → 2 livrés, 1 rendu
        self.assertEqual(RM.decision_retablir([("AP-C+-EP-7-MA-SM", "SO1")], {("SO1", "AP-C+-EP-7-MA-SM"): (2, 1)},
                                              est_machine, {}, {"SO1"}), ("retablir", ["AP-C+-EP-7-MA-SM"]))

    def test_retour_annule_retablit(self):
        self.assertEqual(RM.decision_retablir([("TD-1.5", "SO1"), ("Sac-sel", "SO1")], {("SO1", "TD-1.5"): (2, 0)},
                                              est_machine)[0], "retablir")

    def test_toujours_rendue_laisse(self):
        action, motif = RM.decision_retablir([("F-UV-25w", "SO1")], {("SO1", "F-UV-25w"): (1, 1)}, est_machine)
        self.assertEqual((action, motif), ("laisser", "toutes ses machines restent rendues"))
        self.assertEqual(RM.decision_retablir([("F-UV-25w", "SO1")], {}, est_machine)[0], "laisser")

    def test_deja_suivie_ailleurs_laisse(self):
        action, motif = RM.decision_retablir([("F-UV-25w", "SO1")], {("SO1", "F-UV-25w"): (2, 1)}, est_machine,
                                             {"F-UV-25w": ["MS-9"]}, {"SO1"})
        self.assertEqual(action, "laisser")
        self.assertIn("déjà suivie par MS-9", motif)

    def test_commande_annulee_ne_fait_rien_revenir(self):
        self.assertEqual(RM.decision_retablir([("F-UV-25w", "SO1")], {("SO1", "F-UV-25w"): (2, 1)}, est_machine,
                                              {}, set())[0], "laisser")

    def test_une_machine_sur_deux_revenue_suffit(self):
        lignes = [("F-UV-25w", "SO1"), ("P-F-10'-B-3/4", "SO1")]
        q = {("SO1", "F-UV-25w"): (2, 1), ("SO1", "P-F-10'-B-3/4"): (3, 3)}
        self.assertEqual(RM.decision_retablir(lignes, q, est_machine, {}, {"SO1"}), ("retablir", ["F-UV-25w"]))

    def test_paires_livrees(self):
        dn = {"items": [{"item_code": "A", "qty": 1, "against_sales_order": "SO1"},
                        {"item_code": "B", "qty": 1, "against_sales_order": None},
                        {"item_code": "C", "qty": -1, "against_sales_order": "SO1"}]}
        self.assertEqual(RM.paires_livrees(dn), {("SO1", "A")})
        self.assertEqual(RM.paires_livrees({}), set())

    def test_textes(self):
        t = RM.texte_retabli("BL MAT-DN-2 du 26/11/2026 (livraison sur la commande SO1)", "MS-1", "MS-1-1", ["A"])
        self.assertTrue(t.startswith(RM.TRACE_REVENUE))
        self.assertIn("L'échéancier MS-1, annulé à son retour, est rétabli : MS-1-1", t)
        self.assertNotIn("sans ce lien", t)
        self.assertIn("Commande(s) SO1 déjà portée(s)", RM.texte_retabli("r", "MS-1", "MS-1-1", ["A"], ["SO1"]))
        # le texte de traiter_retour ne doit pas être reconnu comme « rétabli », ni l'inverse
        self.assertNotIn(RM.TRACE + " —", t)
        self.assertNotIn("Échéancier annulé", RM.texte_retour_annule("B", "MS-1", "x"))


class TestTextes(unittest.TestCase):
    def test_annule(self):
        t = RM.texte_annule("MAT-DN-2026-04499", "2026-09-11", "WEB1-008450", ["AP-C+-EP-7-MA-SM"])
        self.assertTrue(t.startswith(RM.TRACE))
        self.assertIn("MAT-DN-2026-04499 du 11/09/2026 (commande WEB1-008450) : AP-C+-EP-7-MA-SM.", t)
        self.assertIn("Échéancier annulé", t)
        self.assertNotIn("rattrapage", t)
        self.assertIn("(rattrapage du 26/11/2026)", RM.texte_annule("B", "2026-09-11", "S", ["A"], rattrapage=True))

    def test_garde_et_client(self):
        t = RM.texte_garde("B1", "2026-11-20", "S1", ["TD-1.5"], "encore chez le client — TD-1.5 : 1 rendue(s) sur 2")
        self.assertIn("Échéancier gardé : encore chez le client — TD-1.5 : 1 rendue(s) sur 2.", t)
        self.assertIn("l'échéancier MS-1 est annulé", RM.texte_client("B1", "MS-1", ["A", "B"]))
        self.assertIn("(A, B, BL de retour B1)", RM.texte_client("B1", "MS-1", ["A", "B"]))

    def test_retour_annule(self):
        t = RM.texte_retour_annule("MAT-DN-1", "MAT-MSH-1", "toutes ses machines restent rendues")
        self.assertTrue(t.startswith(RM.TRACE))
        self.assertIn("n'est pas rétabli : toutes ses machines restent rendues.", t)

    def test_quantites_lisibles(self):
        self.assertEqual(RM._q(1.0), "1")
        self.assertEqual(RM._q(1.5), "1.5")


class TestFenetre(unittest.TestCase):
    CFG = {"fenetre_mois_avant": 4, "fenetre_mois_apres": 1}

    def test_bornes(self):
        self.assertTrue(RM.dans_la_fenetre("2026-11-18", self.CFG, "2026-11-26"))
        self.assertTrue(RM.dans_la_fenetre("2026-07-26", self.CFG, "2026-11-26"))
        self.assertFalse(RM.dans_la_fenetre("2026-07-25", self.CFG, "2026-11-26"))
        self.assertTrue(RM.dans_la_fenetre("2026-12-26", self.CFG, "2026-11-26"))
        self.assertFalse(RM.dans_la_fenetre("2026-12-27", self.CFG, "2026-11-26"))
        self.assertFalse(RM.dans_la_fenetre(None, self.CFG, "2026-11-26"))


class TestCompteRendu(unittest.TestCase):
    def test_compte_rendu(self):
        t = P.compte_rendu({"bl": 6, "annules": ["MS-1", "MS-2"], "gardes": [("MS-3", "encore chez le client")],
                            "deja_annules": [], "sans_echeancier": [("P-F", "SO-9"), ("X", None)], "erreurs": 0})
        self.assertIn("6 BL de retour", t)
        self.assertIn("Échéanciers annulés : 2 (MS-1, MS-2)", t)
        self.assertIn("  - MS-3 : encore chez le client", t)
        self.assertIn("Machines rendues sans échéancier : 2 (P-F / SO-9, X / commande inconnue)", t)
        self.assertIn("Lignes de listes d'appels brouillon dégagées : 0", t)
        self.assertIn("Erreurs : 0", t)
        t = P.compte_rendu({"bl": 1, "annules": ["MS-1"], "gardes": [], "deja_annules": [], "sans_echeancier": [],
                            "listes": [("LIST-1", "retirer", "Karray", None), ("LIST-2", "repointer", "Adnane", "MS-2")],
                            "erreurs": 0})
        self.assertIn("dégagées : 2 (LIST-1 : retirer Karray, LIST-2 : repointer Adnane → MS-2)", t)


if __name__ == "__main__":
    unittest.main()
