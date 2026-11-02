"""Analyse de famille : règles pures (mensualisation, bilan, suspicions)."""
import unittest

from customization_app.analyse_famille import bilan_article, mensualiser, nature, prix_typiques, suspicions


class TestMensuel(unittest.TestCase):
    def test_nature_et_mensualisation(self):
        self.assertEqual(nature("Delivery Note", -2), "vente")
        self.assertEqual(nature("Delivery Note", 1), "retour_vente")
        self.assertEqual(nature("Stock Entry", 3), "transfert_in")
        self.assertEqual(nature("Stock Reconciliation", -4), "rapprochement")
        m = mensualiser([{"item_code": "C-4", "date": "2026-09-14", "voucher_type": "Delivery Note", "qte": -3},
                         {"item_code": "C-4", "date": "2026-09-20", "voucher_type": "Purchase Invoice", "qte": 80},
                         {"item_code": "C-4", "date": "2026-10-02", "voucher_type": "Stock Reconciliation", "qte": -71}])
        self.assertEqual(m["C-4"]["2026-09"], {"vente": 3, "achat": 80})
        self.assertEqual(m["C-4"]["2026-10"], {"rapprochement": -71})
        b = bilan_article("C-4", 100, m["C-4"], 106)
        self.assertEqual((b["achat"], b["vente"], b["rapprochement"], b["ecart_calcul"]), (80, 3, -71, 0))


class TestSuspicions(unittest.TestCase):
    def test_stock_negatif_et_substitution(self):
        recs = [{"item_code": "C-3.2", "date": "2026-10-28", "piece": "MAT-RECO-1", "avant": -29, "apres": 19, "diff": 48},
                {"item_code": "C-4", "date": "2026-11-02", "piece": "MAT-RECO-2", "avant": 172, "apres": 101, "diff": -71}]
        neg = [{"item_code": "C-3.2", "date": "2026-09-14", "piece": "MAT-DN-1", "piece_type": "Delivery Note", "apres": -29}]
        s = suspicions(["C-3.2", "C-4"], recs, neg, [], [], {}, [{"rapprochement": 48}, {"rapprochement": -71}])
        types = [x["type"] for x in s]
        self.assertIn("stock_negatif", types)
        self.assertIn("substitution", types)
        self.assertIn("famille_compensee", types)
        self.assertEqual(s[0]["type"], "stock_negatif")       # gravité 3 d'abord
        self.assertEqual(sum(1 for t in types if t == "substitution"), 1)   # la paire n'est comptée qu'une fois

    def test_recurrent(self):
        recs = [{"item_code": "C-3.2", "date": f"2026-08-{d}", "piece": f"R{d}", "avant": 1, "apres": 2, "diff": 1} for d in (13, 14, 21)]
        s = suspicions(["C-3.2"], recs, [], [], [], {}, [{"rapprochement": 3}])
        self.assertEqual([x["type"] for x in s], ["recurrent"])
        self.assertIn("3 fois", s[0]["texte"])

    def test_achats(self):
        achats = [{"item_code": "C-4", "date": "2025-11-04", "piece": "PI-1", "piece_type": "Purchase Invoice", "qte": 90, "uom": "Unité", "conversion": 1, "rate": 33.27},
                  {"item_code": "C-4", "date": "2026-03-16", "piece": "PI-2", "piece_type": "Purchase Invoice", "qte": 80, "uom": "Pièce", "conversion": 1, "rate": 14.8},
                  {"item_code": "C-4", "date": "2026-06-01", "piece": "PR-3", "piece_type": "Purchase Receipt", "qte": 500, "uom": "Carton", "conversion": 12, "rate": 15}]
        s = suspicions(["C-4"], [], [], achats, [], {}, [{"rapprochement": 0}])
        types = sorted(x["type"] for x in s)
        self.assertEqual(types, ["achat_prix", "achat_quantite", "achat_unite"])

    def test_ventes(self):
        ventes = [{"item_code": "C-3.2", "date": "2026-09-14", "piece": "DN-1", "piece_type": "Delivery Note", "qte": 46, "rate": 52, "client": "CFP"}] + \
                 [{"item_code": "C-3.2", "date": "2026-09-%02d" % d, "piece": f"DN-{d}", "piece_type": "Delivery Note", "qte": 1, "rate": 71, "client": "X"} for d in range(2, 8)] + \
                 [{"item_code": "C-4", "date": "2026-09-20", "piece": "DN-9", "piece_type": "Delivery Note", "qte": 1, "rate": 71, "client": "Y"}]
        prix = prix_typiques(ventes)
        self.assertEqual(prix["C-3.2"], 71)
        s = suspicions(["C-3.2", "C-4"], [], [], [], ventes, {"C-3.2": 71, "C-4": 105}, [{"rapprochement": 0}, {"rapprochement": 0}])
        types = [x["type"] for x in s]
        self.assertIn("vente_quantite", types)
        autre = next(x for x in s if x["type"] == "prix_autre_article")
        self.assertEqual((autre["item_code"], autre["piece"]), ("C-4", "DN-9"))   # C-4 vendu au prix de C-3.2
