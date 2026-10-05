"""Heures de la tournée recalculées trajet par trajet (Google, sinon OSRM corrigé) — Google simulé, aucun appel réel."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from customization_app import tournee_google as G

DEPOT = {"pos": (36.8, 10.18), "noeud": 0}


def etape(n, debut, service=30, mobile=True, fenetre=None, sans_lieu=False):
    return {"pos": (36.8 + n / 100, 10.2), "noeud": n, "debut": debut, "service": service, "mobile": mobile,
            "fenetre": fenetre, "sans_lieu": sans_lieu}


def vingt_minutes(o, d, depart):
    return {"minutes": 0 if o["pos"] == d["pos"] else 20, "km": 10.0, "source": "google"}


class TestRecaler(unittest.TestCase):
    def test_depart_marges_rangement_retour(self):
        # 1re tâche prévue à 09:30 : départ 09:30 − 10 (stationnement) − 20 (trajet) = 09:00.
        # Fin 10:00 → +5 rangement → 10:05 + 20 + 10 = 10:35 ; fixe à 10:30 : 5 min de retard.
        # Fin 11:00 → 11:05 + 20 + 10 = 11:35 (déplaçable : prend 11:35). Fin 12:05 → retour 12:10 + 20 = 12:30.
        r = G.recaler(DEPOT, [etape(1, 570), etape(2, 630, mobile=False), etape(3, 800)], vingt_minutes,
                      marge=10, rangement=5, debut_jour=480, fin_jour=1080, premiere=540)
        self.assertEqual((r["depart"], r["retour"], r["trajets"], r["km"]), (540, 750, 80, 40.0))
        self.assertEqual([(x["debut"], x["retard"]) for x in r["arrets"]], [(570, 0), (630, 5), (695, 0)])
        self.assertEqual(r["retards"], 1)

    def test_tache_au_magasin_ni_stationnement_ni_rangement(self):
        # Vérification de stock au Magasin à 09:00, puis un client : départ 10:30 sans rangement, arrivée 11:00.
        r = G.recaler(DEPOT, [dict(etape(0, 540, service=90, mobile=False, sans_lieu=True), pos=DEPOT["pos"]), etape(2, 700)],
                      vingt_minutes, marge=10, rangement=5, debut_jour=540, fin_jour=1080, premiere=540)
        self.assertEqual([(x["debut"], x["retard"], x["trajet"]) for x in r["arrets"]], [(540, 0, 0), (660, 0, 20)])
        self.assertEqual(r["depart"], 540)

    def test_pause_et_fenetre(self):
        # Arrivée 12:40 pour 45 min : à cheval sur la pause 13:00–14:00 → 14:00 ; sa fenêtre finit à 13:30 → signalée.
        r = G.recaler(DEPOT, [etape(1, 720, service=45), etape(2, 760, service=45, fenetre=(700, 810))], vingt_minutes,
                      marge=10, rangement=5, debut_jour=480, fin_jour=1080, premiere=540, pause=(780, 840))
        self.assertEqual((r["arrets"][1]["debut"], r["arrets"][1]["hors_fenetre"]), (840, True))

    def test_journee_depassee(self):
        r = G.recaler(DEPOT, [etape(1, 1020, service=60)], vingt_minutes, marge=10, rangement=5, debut_jour=480, fin_jour=1080)
        self.assertTrue(r["depasse"])                     # 17:00–18:00 + 5 + 20 → retour 18:25 > 18:00


class TestGoogle(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("tournee_google")
        self.prefixe = patch.object(G, "PREFIXE", "tournee_google_test:")
        self.prefixe.start()
        frappe.cache().delete_keys("tournee_google_test:")
        # Compteur du jour et coefficient remis à zéro DANS le savepoint : les vraies valeurs reviennent au rollback.
        frappe.db.set_single_value(G.CONFIG, {"google_compte_jour": None, "google_compte": 0, "coef_osrm": 1}, update_modified=False)

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="tournee_google")
        frappe.db.value_cache.pop(G.CONFIG, None)
        frappe.cache().delete_keys("tournee_google_test:")
        self.prefixe.stop()

    @staticmethod
    def _reponse(secondes=1500, libre=1200, metres=12345):
        r = MagicMock(status_code=200)
        r.json.return_value = {"routes": [{"duration": f"{secondes}s", "staticDuration": f"{libre}s", "distanceMeters": metres}]}
        return r

    def test_heure_utc_tunis(self):
        self.assertEqual(G.heure_utc("2026-10-06", 8 * 60 + 30, "Africa/Tunis"), "2026-10-06T07:30:00Z")

    def test_mesure_cache_et_plafond(self):
        import frappe
        frappe.db.set_single_value(G.CONFIG, "google_routes_max_jour", 2)
        with patch.object(G.requests, "post", return_value=self._reponse()) as post:
            m = G.mesurer((36.8, 10.1), (36.9, 10.2), "2026-10-06T07:30:00Z", "TEST")
            self.assertEqual((m["minutes"], m["minutes_libre"], m["km"], m["cache"]), (25, 20, 12.3, False))
            corps = post.call_args.kwargs["json"]
            self.assertEqual((corps["routingPreference"], corps["departureTime"]), ("TRAFFIC_AWARE_OPTIMAL", "2026-10-06T07:30:00Z"))
            self.assertTrue(G.mesurer((36.8, 10.1), (36.9, 10.2), "2026-10-06T07:30:00Z", "TEST")["cache"])   # même trajet, même heure
            G.mesurer((36.8, 10.1), (36.9, 10.2), "2026-10-06T08:00:00Z", "TEST")
            with self.assertRaises(G.LimiteAtteinte):
                G.mesurer((36.8, 10.1), (36.9, 10.2), "2026-10-06T08:30:00Z", "TEST")
        self.assertEqual((post.call_count, G.appels_du_jour()), (2, 2))

    def test_refus_de_google(self):
        import frappe
        refus = MagicMock(status_code=403)
        refus.json.return_value = {"error": {"message": "API key not valid for this IP"}}
        with patch.object(G.requests, "post", return_value=refus), self.assertRaises(frappe.ValidationError) as cm:
            G.mesurer((36.8, 10.1), (36.9, 10.2), "2026-10-06T07:30:00Z", "TEST")
        self.assertIn("403", str(cm.exception))

    def test_apprendre_coef(self):
        self.assertEqual(G.apprendre_coef(60, 20), 1.0)                 # moins de 30 min d'OSRM : on n'apprend pas
        self.assertEqual(G.apprendre_coef(130, 100), 1.09)              # 0,7 × 1 + 0,3 × 1,3
        self.assertEqual(G.apprendre_coef(150, 100), 1.21)              # 0,7 × 1,09 + 0,3 × 1,5 (lissé)
        self.assertEqual(G.apprendre_coef(10000, 100), 2.0)             # 0,7 × 1,21 + 0,3 × 100 → borné à 2

    def _proposition(self):
        return [{"employe": "E1", "depart_point": [36.8, 10.18], "depot_noeud": 0, "journee_min": [480, 1080],
                 "apres": {"fin": "", "arrets": [
                     {"lat": 36.81, "lng": 10.2, "noeud": 1, "position": "adresse", "debut_min": 570, "original_min": 600,
                      "service": 30, "mobile": True, "fenetre": None},
                     {"lat": 36.82, "lng": 10.2, "noeud": 2, "position": "adresse", "debut_min": 630, "original_min": 630,
                      "service": 30, "mobile": False, "fenetre": None}]}}]

    def _cfg(self):
        return {"marge": 10, "rangement": 5, "premiere": 540, "pause": None, "pointes": []}

    def test_proposition_recalee_avec_google(self):
        import frappe
        mn = [[0, 15, 15], [15, 0, 15], [15, 15, 0]]
        km = [[0, 9, 9], [9, 0, 9], [9, 9, 0]]
        jour = frappe.utils.add_days(frappe.utils.nowdate(), 2)
        emps = self._proposition()
        with patch.object(G, "cle", return_value="TEST"), patch.object(G.requests, "post", return_value=self._reponse(secondes=1200, libre=1080)):
            r = G.recaler_proposition(emps, jour, self._cfg(), mn, mn, km)
        self.assertEqual((r["source"], r["trajets_google"], r["trajets_osrm"]), ("google", 3, 0))
        a = emps[0]["apres"]["arrets"]
        self.assertEqual((a[0]["debut"], a[0]["trajet_source"], a[0]["decale"], a[0]["ecart_min"]), ("09:30", "google", True, -30))
        self.assertEqual((a[1]["debut"], a[1]["retard"]), ("10:30", 5))          # 10:05 + 20 + 10 = 10:35 pour 10:30
        self.assertEqual((emps[0]["horaires"]["depart"], emps[0]["horaires"]["retour"]), ("09:00", "11:25"))
        self.assertEqual(r["coef"], 1.06)                                        # 0,7 × 1 + 0,3 × (54 min Google ÷ 45 min OSRM)

    def test_sans_cle_ou_jour_passe_osrm_corrige(self):
        import frappe
        mn_brut = [[0, 10, 10], [10, 0, 10], [10, 10, 0]]
        mn = [[0, 13, 13], [13, 0, 13], [13, 13, 0]]                          # × 1,3
        km = [[0, 9, 9], [9, 0, 9], [9, 9, 0]]
        with patch.object(G, "cle", return_value=None):
            emps = self._proposition()
            r = G.recaler_proposition(emps, frappe.utils.add_days(frappe.utils.nowdate(), 2), self._cfg(), mn_brut, mn, km)
        self.assertEqual((r["source"], r["raison"]), ("osrm", "clé Google non réglée"))
        self.assertEqual(emps[0]["apres"]["arrets"][0]["trajet"], 13)
        with patch.object(G, "cle", return_value="TEST"), patch.object(G.requests, "post") as post:
            r = G.recaler_proposition(self._proposition(), frappe.utils.add_days(frappe.utils.nowdate(), -1), self._cfg(), mn_brut, mn, km)
        self.assertEqual((post.call_count, r["source"]), (0, "osrm"))
        self.assertIn("jour passé", r["raison"])
