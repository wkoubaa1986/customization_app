"""Clients B2B (04/11/2026) : les règles pures — rythme d'achat, tendance, paliers de ristourne, alertes.

`unittest` pur, sans site. Lancer depuis apps/customization_app :
`python -m unittest customization_app.tests.test_clients_b2b`.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from customization_app import clients_b2b as B

AUJ = date(2026, 11, 4)


def _ligne(**kw):
    base = {"nb_bl": 5, "derniere_bl": date(2026, 10, 20), "a_deja_achete": True, "rythme": 20.0,
            "derniere_connexion": None, "acces_fiche": True, "tendance": {"variation": None},
            "nb_bl_12m": 5, "impaye": 0.0, "commercial": "Jamel Aloui"}
    base.update(kw)
    return base


class TestRythme(unittest.TestCase):
    def test_mediane_des_ecarts_sur_jours_distincts(self):
        # Deux BL le même jour = un seul achat ; écarts 10, 10, 30 → médiane 10.
        dates = [date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 11), date(2026, 9, 21), date(2026, 10, 21)]
        self.assertEqual(B.rythme(dates), 10.0)

    def test_trop_peu_d_achats(self):
        self.assertIsNone(B.rythme([date(2026, 9, 1), date(2026, 10, 1)]))
        self.assertIsNone(B.rythme([]))

    def test_seuls_les_derniers_achats_comptent(self):
        # 20 achats mensuels puis 10 hebdomadaires : le rythme actuel est hebdomadaire.
        anciens = [date(2024, m, 1) for m in range(1, 13)] + [date(2025, m, 1) for m in range(1, 9)]
        recents = [date(2026, 9, 1 + 7 * i) for i in range(4)] + [date(2026, 10, 1 + 7 * i) for i in range(4)]
        self.assertEqual(B.rythme(anciens + recents), 7.0)


class TestTendance(unittest.TestCase):
    def test_mois_glissants(self):
        self.assertEqual(B.mois_glissants(AUJ, 3), ["2026-09", "2026-10", "2026-11"])
        self.assertEqual(B.mois_glissants(date(2026, 2, 10), 3), ["2025-12", "2026-01", "2026-02"])

    def test_baisse_et_hausse(self):
        par_mois = {m: 100.0 for m in B.mois_glissants(AUJ, 12)[:6]}
        par_mois.update({m: 50.0 for m in B.mois_glissants(AUJ, 12)[6:]})
        t = B.tendance(par_mois, AUJ)
        self.assertEqual((t["avant"], t["recent"], t["variation"]), (600.0, 300.0, -0.5))

    def test_rien_avant_n_est_pas_une_hausse_infinie(self):
        t = B.tendance({"2026-10": 500.0}, AUJ)
        self.assertEqual(t["avant"], 0.0)
        self.assertIsNone(t["variation"])


class TestPalier(unittest.TestCase):
    PALIERS = [{"minimum": 0, "maximum": 2000, "taux": 0}, {"minimum": 2000, "maximum": 5000, "taux": 2},
               {"minimum": 5000, "maximum": None, "taux": 4}]

    def test_palier_atteint_et_prochain(self):
        p = B.palier(3200.0, self.PALIERS)
        self.assertEqual((p["taux"], p["seuil"]), (2.0, 2000.0))
        self.assertEqual(p["prochain"], {"taux": 4.0, "seuil": 5000.0, "manque": 1800.0})

    def test_dernier_palier(self):
        self.assertNotIn("prochain", B.palier(9000.0, self.PALIERS))

    def test_sans_paliers(self):
        self.assertIsNone(B.palier(100.0, []))


class TestAlertes(unittest.TestCase):
    def test_client_sain(self):
        self.assertEqual(B.alertes(_ligne(derniere_connexion=datetime(2026, 10, 15, 9, 0)), AUJ, 25.0), [])

    def test_retard_selon_son_rythme(self):
        # Rythme 20 j, silence 34 j : 34 > 1,5 × 20 → retard.
        self.assertIn("retard", B.alertes(_ligne(derniere_bl=date(2026, 10, 1)), AUJ, 40.0))
        # Même silence, rythme 25 j : 34 < 37,5 → rien.
        self.assertNotIn("retard", B.alertes(_ligne(derniere_bl=date(2026, 10, 1), rythme=25.0), AUJ, 40.0))
        # Exactement 1,5 × (30 j) : pas encore un retard.
        self.assertNotIn("retard", B.alertes(_ligne(derniere_bl=date(2026, 10, 5)), AUJ, 40.0))

    def test_rythme_du_groupe_a_defaut(self):
        l = _ligne(rythme=None, derniere_bl=date(2026, 9, 1))
        self.assertIn("retard", B.alertes(l, AUJ, 30.0))     # 64 j > 45
        self.assertNotIn("retard", B.alertes(l, AUJ, None))  # aucun rythme connu : on ne juge pas

    def test_perdu_prime_sur_retard(self):
        a = B.alertes(_ligne(nb_bl=0, derniere_bl=date(2025, 6, 1)), AUJ, 20.0)
        self.assertIn("perdu", a)
        self.assertNotIn("retard", a)

    def test_jamais_achete_n_est_pas_perdu(self):
        self.assertNotIn("perdu", B.alertes(_ligne(nb_bl=0, derniere_bl=None, a_deja_achete=False), AUJ, 20.0))

    def test_intention(self):
        # Connecté il y a 5 jours, pas de livraison depuis.
        l = _ligne(derniere_bl=date(2026, 10, 20), derniere_connexion=datetime(2026, 10, 30, 18, 0))
        self.assertIn("intention", B.alertes(l, AUJ, 25.0))
        # Livré après la connexion : l'intention est servie.
        l = _ligne(derniere_bl=date(2026, 11, 2), derniere_connexion=datetime(2026, 10, 30, 18, 0))
        self.assertNotIn("intention", B.alertes(l, AUJ, 25.0))
        # Connecté hier : on lui laisse le temps.
        l = _ligne(derniere_connexion=datetime(2026, 11, 3, 18, 0))
        self.assertNotIn("intention", B.alertes(l, AUJ, 25.0))
        # Connecté il y a deux mois : ce n'est plus une intention.
        l = _ligne(derniere_bl=date(2026, 8, 1), derniere_connexion=datetime(2026, 9, 1, 18, 0))
        self.assertNotIn("intention", B.alertes(l, AUJ, 200.0))

    def test_jamais_connecte(self):
        self.assertIn("jamais_connecte", B.alertes(_ligne(), AUJ, 25.0))
        self.assertNotIn("jamais_connecte", B.alertes(_ligne(acces_fiche=False), AUJ, 25.0))

    def test_baisse_impaye_commercial(self):
        a = B.alertes(_ligne(tendance={"variation": -0.35}, impaye=120.0, commercial=None), AUJ, 25.0)
        self.assertEqual(sorted(a), ["baisse", "impaye", "jamais_connecte", "sans_commercial"])
        # Trop peu d'achats pour parler de baisse.
        self.assertNotIn("baisse", B.alertes(_ligne(tendance={"variation": -0.35}, nb_bl_12m=2), AUJ, 25.0))

    def test_relance_depassee(self):
        def l(**kw):
            base = _ligne(derniere_connexion=datetime(2026, 10, 15), derniere_bl=date(2026, 9, 1), rythme=None)
            base.update(kw)
            return base
        visite = {"derniere": {"date": "2026-10-01", "date_relance": "2026-10-20"}, "prochaine": None}
        # Relance au 20/10, rien depuis : dépassée.
        self.assertIn("relance", B.alertes(l(visite=visite), AUJ, None))
        # Une livraison après la date de relance : la relance a servi.
        self.assertNotIn("relance", B.alertes(l(visite=visite, derniere_bl=date(2026, 10, 25)), AUJ, None))
        # Une visite déjà planifiée : pas la peine d'alerter.
        self.assertNotIn("relance", B.alertes(l(visite={**visite, "prochaine": {"date": "2026-11-10"}}), AUJ, None))
        # Relance future : rien.
        self.assertNotIn("relance", B.alertes(l(visite={"derniere": {"date_relance": "2026-11-20"}, "prochaine": None}), AUJ, None))
        # Pas de visite du tout : rien.
        self.assertNotIn("relance", B.alertes(l(), AUJ, None))

    def test_score(self):
        self.assertEqual(B.score(["retard", "impaye"]), 5)
        self.assertEqual(B.score([]), 0)


if __name__ == "__main__":
    unittest.main()
