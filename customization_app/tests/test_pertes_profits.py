"""Pertes et Profits : comptes retenus, omissions propres à l'écran, sortie du rapport (règles pures, sans site)."""
import unittest

from frappe import _dict

from customization_app import pertes_profits as P
from customization_app import situation_mensuelle as S


class TestEcran(unittest.TestCase):
    def test_chaque_ecran_ses_omissions(self):
        self.assertTrue(S.compte_dans(None, S.VUE_SITUATION))              # omissions d'avant le champ
        self.assertFalse(S.compte_dans(None, S.VUE_PNL))
        self.assertTrue(S.compte_dans(S.PORTEE_SITUATION, S.VUE_SITUATION))
        self.assertFalse(S.compte_dans(S.PORTEE_SITUATION, S.VUE_PNL))
        self.assertFalse(S.compte_dans(S.PORTEE_PNL, S.VUE_SITUATION))
        self.assertTrue(S.compte_dans(S.PORTEE_PNL, S.VUE_PNL))

    def test_periode_des_filtres_du_graphique(self):
        self.assertEqual(P.periode_des_filtres({"Mensuel": 1, "mois": "novembre 2026", "annee": "Total"}),
                         {"mode": "mois", "mois": "novembre 2026"})
        self.assertEqual(P.periode_des_filtres({"Mensuel": 0, "mois": "mars 2024", "annee": "2024"}), {"mode": "annee", "annee": "2024"})
        self.assertEqual(P.periode_des_filtres({"Mensuel": 0, "annee": "Total"}), {"mode": "annee", "annee": "Total"})
        self.assertEqual(P.periode_des_filtres(None), {"mode": "mois"})


class TestCalcul(unittest.TestCase):
    def test_comptes_retenus(self):
        enfants = ["Charges Diverses - A&S", "Dépenses non déclarées - A&S", "Frais bancaire - A&S"]
        petits = {"Frais bancaire - A&S": ["Commissions - A&S", "Perte de non paiement - A&S"],
                  "Dépenses non déclarées - A&S": ["Sous-compte ignoré - A&S"]}
        self.assertEqual(P.comptes_retenus(enfants, petits),
                         ["Charges Diverses - A&S", "Frais bancaire - A&S", "Commissions - A&S"])

    def test_omis_total_et_partiel(self):
        pieces = {("Journal Entry", "JV-1"): 29902.4, ("Journal Entry", "JV-2"): 100.0, ("Payment Entry", "PE-1"): -50.0}
        exclusions = {("Journal Entry", "JV-1"): _dict(mode="Totale", montant_exclu=29902.4),
                      ("Journal Entry", "JV-2"): _dict(mode="Partielle", montant_exclu=30),
                      ("Journal Entry", "ABSENTE"): _dict(mode="Totale", montant_exclu=999)}
        self.assertAlmostEqual(P.omis(pieces, exclusions), 29932.4)

    def test_ventes_omissibles_en_solde_crediteur(self):
        pieces = {("Sales Invoice", "SI-1"): -1000.0, ("Sales Invoice", "SI-2"): -221.109}
        om = P.omis(pieces, {("Sales Invoice", "SI-1"): _dict(mode="Partielle", montant_exclu=400)})
        self.assertAlmostEqual(om, -400)
        r = P.resultat({"ventes": (sum(pieces.values()), om), "stock": (343.6, 0), "depenses": (100, 0)})
        self.assertEqual((r["ventes"], r["ventes_brut"], r["ventes_omis"]), (821.109, 1221.109, 400.0))
        self.assertEqual(r["benefice"], 377.509)

    def test_resultat_comme_le_rapport(self):
        r = P.resultat({"ventes": (-1221.109, 0), "stock": (343.6, 0), "depenses": (33153.761, 29902.4)})
        self.assertEqual((r["ventes"], r["stock"], r["depenses"], r["benefice"]), (1221.109, 343.6, 3251.361, -2373.852))
        self.assertEqual(r["depenses_omis"], 29902.4)

    def test_sortie_rapport(self):
        r = P.resultat({"ventes": (-1000, 0), "stock": (300, 0), "depenses": (200, 0)})
        s = P.sortie_rapport(r, "novembre 2026", 0)
        self.assertEqual([d["Type"] for d in s["data"]],
                         ["Total Vente H.T", "Total Stock Délivré H.T", "Total Dépenses Comptabilisées", "Bénéfice / Perte"])
        self.assertEqual(s["chart"]["title"], "novembre 2026")
        self.assertEqual(s["chart"]["data"]["datasets"][3], {"values": [500.0], "name": "Bénéfice"})
        perte = P.sortie_rapport(P.resultat({"ventes": (-100, 0), "stock": (300, 0), "depenses": (200, 50)}), "2026", 2)
        self.assertEqual(perte["chart"]["data"]["datasets"][3]["name"], "Perte")
        self.assertEqual(perte["chart"]["colors"][3], "#eb4034")
        self.assertEqual(perte["chart"]["title"], "2026 — 2 pièces omises")
        self.assertIn("dépenses 50.0", perte["data"][-1]["Type"])

    def test_script_du_rapport(self):
        self.assertIn('frappe.call("customization_app.pertes_profits.rapport"', P.SCRIPT_RAPPORT)
        compile(P.SCRIPT_RAPPORT, "rapport", "exec")


class TestPage(unittest.TestCase):
    def test_gabarit_sans_apostrophe_droite(self):
        """Frappe sert le .html de la page comme chaîne JS entre apostrophes, SANS les échapper : une apostrophe
        droite (« l'onglet ») coupe la chaîne et la page reste blanche (constaté le 05/11/2026)."""
        import pathlib
        html = pathlib.Path(__file__).parents[1] / "customize_erpnext" / "page" / "pertes_et_profits" / "pertes_et_profits.html"
        self.assertNotIn("'", html.read_text())
