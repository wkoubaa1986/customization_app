"""Analyse d'un écart d'inventaire : règles pures (bilan des mouvements, phrases d'explication)."""
import unittest

from customization_app.inventaire_analyse import bilan_mouvements, expliquer


class TestBilan(unittest.TestCase):
    def test_entrees_sorties_par_type(self):
        b = bilan_mouvements([{"libelle": "BL", "qte": -2}, {"libelle": "BL", "qte": -1},
                              {"libelle": "Réception d’achat", "qte": 5}, {"libelle": "Transfert", "qte": -1.5}])
        self.assertEqual((b["entrees"], b["sorties"], b["n"]), (5, 4.5, 4))
        self.assertEqual(b["par_type"]["BL"], {"entrees": 0, "sorties": 3, "n": 2})
        self.assertEqual(bilan_mouvements([])["n"], 0)


class TestExpliquer(unittest.TestCase):
    def test_sans_ecart(self):
        self.assertEqual(expliquer(0, bilan_mouvements([]), None, [], [], [], [], None)["verdict"], "Expliqué")

    def test_manque_couvert_par_un_bl_en_attente(self):
        r = expliquer(-2, bilan_mouvements([]), None, [{"name": "MAT-DN-1", "qte": 2}], [], [], [], "2026-10-01")
        self.assertEqual(r["verdict"], "Expliqué")
        self.assertIn("MAT-DN-1", r["phrases"][0])
        self.assertIn("ne pas rapprocher", r["phrases"][0])

    def test_manque_partiellement_couvert(self):
        r = expliquer(-3, bilan_mouvements([]), None, [{"name": "MAT-DN-1", "qte": 1}], [], [], [], "2026-10-01")
        self.assertEqual(r["verdict"], "Probable")
        self.assertIn("le reste (2)", r["phrases"][0])

    def test_manque_sans_document(self):
        r = expliquer(-1, bilan_mouvements([{"libelle": "BL", "qte": -4}]), None, [], [], [], [], "2026-10-01")
        self.assertEqual(r["verdict"], "Inexpliqué")
        self.assertTrue(any("−4 sorties" in p for p in r["phrases"]))
        self.assertTrue(any("sans aucun document" in p for p in r["phrases"]))

    def test_surplus_et_ailleurs_et_cases_vides(self):
        r = expliquer(2, bilan_mouvements([]), None, [], [], [{"entrepot": "Hall - A&S", "qte": 3}], ["L2"], "2026-10-01")
        self.assertEqual(r["verdict"], "Probable")          # case vide → comptage peut-être incomplet
        self.assertTrue(any("L2" in p for p in r["phrases"]))
        self.assertTrue(any("Hall - A&S : 3" in p for p in r["phrases"]))
        self.assertFalse(any("Il manque" in p for p in r["phrases"]))

    def test_ecart_recurrent(self):
        prec = {"name": "INV-1", "date": "2026-10-10 09:00", "ecart": -1, "qte_comptee": 4, "qte_systeme": 5}
        r = expliquer(-2, bilan_mouvements([]), prec, [], [], [], [], "2026-10-10")
        self.assertTrue(any("RÉCURRENT" in p for p in r["phrases"]))
        prec["ecart"] = 0
        r = expliquer(-2, bilan_mouvements([]), prec, [], [], [], [], "2026-10-10")
        self.assertTrue(any("sans écart" in p for p in r["phrases"]))

    def test_a_livrer_sans_bl(self):
        r = expliquer(-1, bilan_mouvements([]), None, [], [{"commande": "SAL-ORD-1", "client": "X", "reste": 1}], [], [], None)
        self.assertEqual(r["verdict"], "Probable")
        self.assertIn("SAL-ORD-1", r["phrases"][0])
