"""La mémoire « article du fournisseur → notre article » (25/11/2026) : clés, décision, appariement. Pur.
Cas réel : la ligne « 2030042 REDUCTION LAITON SPECIAL 1/2"F*3/8"M » de MH2C est notre BRS-SP-15-12."""
import unittest

from customization_app import correspondance_articles as CA

MH2C = "MH2C (Maison Hammami de Chauffage & Climatisation)"


class TestCles(unittest.TestCase):
    def test_code_et_designation(self):
        self.assertEqual(CA.cle_code("2030042"), "2030042")
        self.assertEqual(CA.cle_code(" 02030 042 "), "2030042")
        self.assertEqual(CA.cle_code(None), "")
        self.assertEqual(CA.cle_designation('REDUCTION LAITON SPECIAL 1/2"F*3/8"M'),
                         CA.cle_designation('Réduction laiton spécial 1/2" F * 3/8" M'))
        self.assertEqual(CA.cle_designation('REDUCTION LAITON SPECIAL 1/2"F*3/8"M'), "REDUCTION LAITON SPECIAL 1 2 F 3 8 M")

    def test_le_code_d_abord(self):
        self.assertEqual(CA.cles(MH2C, "2030042", "REDUCTION LAITON"),
                         [MH2C + "|C|2030042", MH2C + "|D|REDUCTION LAITON"])
        self.assertEqual(CA.cles(MH2C, "", "REDUCTION LAITON"), [MH2C + "|D|REDUCTION LAITON"])
        self.assertEqual(CA.cles(None, "2030042", "X"), [])
        self.assertEqual(CA.cles(MH2C, None, None), [])
        self.assertLessEqual(len(CA.cles(MH2C, None, "X" * 400)[0]), CA.LONGUEUR_CLE)

    def test_decision(self):
        self.assertEqual(CA.decision(None, "BRS-SP-15-12"), "creer")
        self.assertEqual(CA.decision({"item_code": "BRS-SP-15-12"}, "BRS-SP-15-12"), "confirmer")
        self.assertEqual(CA.decision({"item_code": "tuy-02"}, "BRS-SP-15-12"), "corriger")


class TestApparier(unittest.TestCase):
    def test_une_ligne_de_chaque_cote(self):
        f = [{"code": "2030042", "designation": "REDUCTION LAITON", "qty": 5}]
        self.assertEqual(CA.apparier(f, [{"item_code": "BRS-SP-15-12", "qty": 5}]), [(f[0], "BRS-SP-15-12")])
        # une quantité différente (réception partielle, ligne mal rattachée par l'IA) n'apprend rien
        self.assertEqual(CA.apparier(f, [{"item_code": "BRS-SP-15-12", "qty": 4}]), [])
        # la pince d'un autre BL rattachée par erreur à cette rubrique : 1 contre 5, rien
        self.assertEqual(CA.apparier([{"code": "9910019", "qty": 1}], [{"item_code": "BRS-SP-15-12", "qty": 5}]), [])

    def test_par_quantite_unique(self):
        f = [{"code": "A", "qty": 1}, {"code": "B", "qty": 3}, {"code": "C", "qty": 1}]
        r = [{"item_code": "X", "qty": 3}, {"item_code": "Y", "qty": 1}, {"item_code": "Z", "qty": 1}]
        self.assertEqual([(l["code"], i) for l, i in CA.apparier(f, r)], [("B", "X")])   # qté 1 ambiguë : rien
        # une ligne de plus d'un côté (ligne manquée, réception partielle) : on n'apparie rien
        self.assertEqual(CA.apparier(f[:2], r), [])

    def test_libelles_compatibles(self):
        self.assertTrue(CA.libelles_compatibles('REDUCTION LAITON SPECIAL 1/2"F*3/8"M', "Réduction laiton 1/2 3/8"))
        self.assertFalse(CA.libelles_compatibles("PINCE COUPANTE 160MM TOTAL", 'REDUCTION LAITON SPECIAL'))
        self.assertTrue(CA.libelles_compatibles("", "PINCE"))           # un libellé manque : on ne refuse pas

    def test_rien_de_sur(self):
        self.assertEqual(CA.apparier([], [{"item_code": "X", "qty": 1}]), [])
        self.assertEqual(CA.apparier([{"designation": "", "code": "", "qty": 1}], [{"item_code": "X", "qty": 1}]), [])

    def test_historique(self):
        self.assertIn("tuy-02 (confirmé 2 fois) remplacé par BRS-SP-15-12", CA.ligne_historique("2026-11-25 20:00:00", "tuy-02", 2, "BRS-SP-15-12", "x"))


if __name__ == "__main__":
    unittest.main()
