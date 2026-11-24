"""Fiches de paie : le calcul reproduit les bulletins d'octobre 2026 du comptable (PUR, sans base).

Lancer : `../env/bin/python -m unittest customization_app.tests.test_paie_calcul` depuis frappe-bench/sites
(jamais `bench run-tests` sur le site de dev)."""
import unittest
from datetime import date

from customization_app import paie_calcul as P

# (profil, base, brut, CNSS, imposable, IRPP, CSS, net) — « FICHE DE PAIE STE AQUA 10 2026.pdf »
OCTOBRE_2026 = {
    "gérant": (P.GERANT, 2004.950, 2043.142, 0.0, 2043.142, 333.783, 9.383, 1699.976),
    "technicien 1": (P.CDI, 1836.860, 1875.052, 181.505, 1693.547, 235.896, 7.634, 1450.017),
    "technicien 2": (P.CDI, 1404.790, 1442.982, 139.681, 1303.301, 147.417, 5.865, 1150.019),
    "technicien 3": (P.CDI, 1275.495, 1313.687, 127.165, 1186.522, 121.146, 5.339, 1060.037),
    "civp": (P.CIVP, 700.000, 700.000, 0.0, 700.000, 0.0, 0.0, 700.000),
    "administratif": (P.CDI, 782.000, 820.192, 79.395, 740.797, 37.512, 3.334, 699.951),
}


class TestBulletinsOctobre(unittest.TestCase):
    def test_reproduit_le_comptable(self):
        for nom, (profil, base, brut, cnss, imposable, irpp, css, net) in OCTOBRE_2026.items():
            with self.subTest(nom):
                b = P.bulletin(profil, base)
                self.assertEqual((b["brut"], b["cnss"], b["imposable"], b["irpp"]), (brut, cnss, imposable, irpp))
                if nom == "gérant":
                    # Seul écart connu : 1 millime sur la CSS du gérant (9,382 au lieu de 9,383).
                    self.assertAlmostEqual(b["css"], css, delta=0.0011)
                    self.assertAlmostEqual(b["net"], net, delta=0.0011)
                else:
                    self.assertEqual((b["css"], b["net"]), (css, net))
                self.assertEqual(round(b["net"]), round(net))   # le net à payer (au dinar) est toujours le même

    def test_civp_sans_prime_ni_retenue(self):
        b = P.bulletin(P.CIVP, 700)
        self.assertEqual((b["prime_presence"], b["prime_transport"], b["cnss"], b["irpp"], b["css"]), (0, 0, 0, 0, 0))

    def test_prorata_sur_26_jours(self):
        b = P.bulletin(P.CDI, 1300, jours=13)
        self.assertEqual((b["base"], b["prime_presence"], b["prime_transport"]), (650.0, 1.04, 18.056))

    def test_prime_exceptionnelle_soumise_et_avance_apres_impot(self):
        sans, avec = P.bulletin(P.CDI, 1000), P.bulletin(P.CDI, 1000, autres_gains=100, autres_retenues=50)
        self.assertEqual(avec["brut"], P.m3(sans["brut"] + 100))
        self.assertGreater(avec["cnss"], sans["cnss"])
        self.assertEqual(avec["net"], P.m3(avec["imposable"] - avec["irpp"] - avec["css"] - 50))

    def test_bareme(self):
        self.assertEqual(P.irpp_annuel(5000), 0)
        self.assertAlmostEqual(P.irpp_annuel(10000), 750)
        self.assertAlmostEqual(P.irpp_annuel(20000), 3250)
        self.assertAlmostEqual(P.irpp_annuel(60000), 750 + 2500 + 3000 + 3300 + 3600 + 4000)


class TestJoursEtCalendrier(unittest.TestCase):
    def test_mois_complet_26(self):
        self.assertEqual(P.jours_payes(date(2026, 10, 1), date(2026, 10, 31)), 26)

    def test_sortie_en_cours_de_mois(self):
        # Départ le 10/11/2026 : du lundi 2 au mardi 10 (le 1er et le 8 sont des dimanches) = 8 jours.
        self.assertEqual(P.jours_payes(date(2026, 11, 1), date(2026, 11, 30), sortie=date(2026, 11, 10)), 8)

    def test_debut_de_paie_et_absences(self):
        self.assertEqual(P.jours_payes(date(2026, 12, 1), date(2026, 12, 31), debut_paie=date(2026, 12, 28)), 4)
        self.assertEqual(P.jours_payes(date(2026, 10, 1), date(2026, 10, 31), absences=2), 24)
        self.assertEqual(P.jours_payes(date(2026, 10, 1), date(2026, 10, 31), debut_paie=date(2026, 11, 1)), 0)

    def test_mois_a_traiter(self):
        m = P.mois_a_traiter(date(2025, 3, 1), date(2026, 3, 15))
        self.assertEqual(m, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])   # jamais avant janvier 2026
        self.assertEqual(P.mois_a_traiter(date(2026, 1, 1), date(2026, 12, 5), sortie=date(2026, 2, 10))[-1], date(2026, 2, 1))
        self.assertEqual(P.mois_a_traiter(date(2026, 12, 15), date(2026, 11, 24)), [])

    def test_validation_4_jours_avant_la_fin(self):
        self.assertFalse(P.a_valider(date(2026, 11, 1), date(2026, 11, 25)))
        self.assertTrue(P.a_valider(date(2026, 11, 1), date(2026, 11, 26)))     # 30 novembre − 4
        self.assertFalse(P.a_valider(date(2026, 12, 1), date(2026, 12, 26)))
        self.assertTrue(P.a_valider(date(2026, 12, 1), date(2026, 12, 27)))     # 31 décembre − 4
        self.assertTrue(P.a_valider(date(2026, 1, 1), date(2026, 11, 24)))      # mois passé : tout de suite
