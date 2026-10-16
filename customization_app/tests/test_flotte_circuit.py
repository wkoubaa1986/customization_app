"""Sorties de circuit : arrêts inexpliqués, hors horaires, détours — calcul pur sur une journée fabriquée."""

import unittest
from datetime import datetime, timedelta

from customization_app import flotte_circuit as fc

J = datetime(2026, 10, 7)                       # un mercredi
MAGASIN = (36.86684, 10.25487)
DOMICILE = (36.90000, 10.18000)
CHEZ_A = (36.76408, 10.28494)
CHEZ_B = (36.79570, 10.14557)
LOIN = (36.70000, 10.45000)                     # à ~17 km de A, ~30 km de B
HORAIRES = (8 * 60, 17 * 60)


def _h(h):
    return J + timedelta(hours=int(h), minutes=round((h % 1) * 60))


def _arret(h, minutes, point):
    return {"arr": _h(h), "dep": _h(h) + timedelta(minutes=minutes), "lat": point[0], "lng": point[1], "minutes": minutes}


def _trajet(h, minutes, km):
    return {"debut": _h(h), "fin": _h(h) + timedelta(minutes=minutes), "minutes": minutes, "km": km, "vmax": 80}


def _tache(nom, point, h, approx=None, client="Client " + "X"):
    return {"name": nom, "employe": "E1", "lat": point[0] if point else None, "lng": point[1] if point else None,
            "approx": approx, "debut": _h(h), "statut": "Open", "client": client, "type": "Entretien"}


class TestEcarts(unittest.TestCase):
    def setUp(self):
        self.taches = [_tache("A", CHEZ_A, 9, client="Client A"), _tache("B", CHEZ_B, 14, client="Client B")]
        self.journal = {"erreur": None,
                        "arrets": [_arret(8.0, 20, DOMICILE), _arret(9.1, 35, CHEZ_A), _arret(11.0, 8, LOIN), _arret(12.0, 50, LOIN),
                                   _arret(14.0, 30, CHEZ_B), _arret(18.5, 300, DOMICILE)],
                        "trajets": [_trajet(8.3, 40, 20), _trajet(9.7, 60, 30), _trajet(12.9, 60, 30), _trajet(14.5, 40, 20)],
                        "km": 100}

    def _ecarts(self, **kw):
        args = dict(jour=J.date(), taches=self.taches, journal=self.journal, lieux=[MAGASIN, DOMICILE], horaires=HORAIRES,
                    depart=DOMICILE, depot=MAGASIN)
        args.update(kw)
        return fc.ecarts_pure(**args)

    def test_arret_loin_de_tout_client_signale(self):
        e = [x for x in self._ecarts() if x["type"] == "Arrêt inexpliqué"]
        # 12:00, 50 min, loin : signalé ; 11:00 (8 min) : trop court ; chez A, chez B, domicile : non
        self.assertEqual([(x["debut"].strftime("%H:%M"), x["minutes"]) for x in e], [("12:00", 50)])
        self.assertEqual(e[0]["client_proche"], "Client A")
        self.assertGreater(e[0]["km_client"], 10)
        self.assertIn("50 min", e[0]["detail"])

    def test_tolerance_plus_large_pour_une_position_approchee(self):
        # Le client B n'a qu'une position approchée à 5 km de l'arrêt : 5 km ≤ 6 km → pas un écart
        pres = (LOIN[0] + 0.045, LOIN[1])                                 # ~5 km au nord
        self.journal["arrets"] = [a for a in self.journal["arrets"] if (a["lat"], a["lng"]) != CHEZ_B]     # B n'est plus là
        self.taches[1] = _tache("B", None, 14, approx=[pres[0], pres[1]], client="Client B")
        self.assertEqual([x for x in self._ecarts() if x["type"] == "Arrêt inexpliqué"], [])
        self.taches[1] = _tache("B", pres, 14, client="Client B")             # la même position dite exacte : 5 km > 3 km → écart
        self.assertEqual(len([x for x in self._ecarts() if x["type"] == "Arrêt inexpliqué"]), 1)

    def test_journee_sans_tache_tout_arret_long_est_inexplique(self):
        e = [x for x in self._ecarts(taches=[]) if x["type"] == "Arrêt inexpliqué"]
        self.assertEqual([x["debut"].strftime("%H:%M") for x in e], ["09:06", "12:00", "14:00"])
        self.assertIn("aucune tâche planifiée", e[0]["detail"])
        self.assertEqual([x["type"] for x in self._ecarts(taches=[]) if x["type"] == "Détour"], [])    # pas de circuit : pas de détour

    def test_hors_horaires(self):
        self.journal["trajets"] += [_trajet(5.5, 30, 15), _trajet(21.0, 20, 1.5), _trajet(21.5, 25, 12)]
        e = [x for x in self._ecarts() if x["type"] == "Hors horaires"]
        # 05:30 (avant 06:00) et 21:30 (après 20:00, 12 km) ; 21:00 : 1,5 km, ignoré
        self.assertEqual([(x["debut"].strftime("%H:%M"), x["km"]) for x in e], [("05:30", 15), ("21:30", 12)])
        self.assertIn("avant 06:00", e[0]["detail"])
        self.assertIn("après 20:00", e[1]["detail"])

    def test_dimanche_tout_trajet_est_hors_horaires(self):
        dim = datetime(2026, 10, 11)
        j = {"erreur": None, "arrets": [], "trajets": [{"debut": dim.replace(hour=10), "fin": dim.replace(hour=11), "minutes": 60, "km": 25}]}
        e = self._ecarts(jour=dim.date(), taches=[], journal=j)
        self.assertEqual([(x["type"], "dimanche" in x["detail"]) for x in e], [("Hors horaires", True)])

    def test_detour(self):
        # Circuit domicile → A → B → Magasin ≈ 1,3 × ~40 km ≈ 52 km ; 100 km réels : pas 1,5 × 52 = 78 ? si, 100 ≥ 78 et 100 ≥ 72
        e = [x for x in self._ecarts() if x["type"] == "Détour"]
        self.assertEqual(len(e), 1)
        self.assertIn("2 clients", e[0]["detail"])
        self.assertGreater(e[0]["km"], 20)
        # 60 km réels : sous le seuil
        self.journal["trajets"] = [_trajet(8.3, 40, 15), _trajet(9.7, 60, 15), _trajet(12.9, 60, 15), _trajet(14.5, 40, 15)]
        self.assertEqual([x for x in self._ecarts() if x["type"] == "Détour"], [])

    def test_la_nuit_n_est_pas_un_arret_inexplique_et_le_retour_chez_soi_n_est_pas_un_detour(self):
        """La voiture dort le soir à un endroit inconnu (arrêt qui atteint 23:59) : pas un « arrêt inexpliqué ». Et le circuit
        finit là où elle dort : les km du retour ne sont pas un détour."""
        loin = (36.70, 10.00)
        self.journal["arrets"] = self.journal["arrets"] + [{"arr": _h(18.0), "dep": _h(23.999), "minutes": 360, "lat": loin[0], "lng": loin[1]}]
        e = [x for x in self._ecarts() if x["type"] == "Arrêt inexpliqué" and x["debut"] == _h(18.0)]
        self.assertEqual(e, [])
        # 60 km réels : sous le seuil au départ du domicile ; le retour vers `loin` (~30 km) s'ajoute au circuit prévu, pas au détour
        self.journal["trajets"] = [_trajet(8.3, 40, 15), _trajet(9.7, 60, 15), _trajet(12.9, 60, 15), _trajet(14.5, 40, 15), _trajet(17.0, 60, 30)]
        self.assertEqual([x for x in self._ecarts() if x["type"] == "Détour"], [])

    def test_journal_absent_ou_en_erreur(self):
        self.assertEqual(self._ecarts(journal=None), [])
        self.assertEqual(self._ecarts(journal={"erreur": "x"}), [])

    def test_km_circuit(self):
        pts = fc.circuit(self.taches)
        self.assertEqual([(p[2], p[3]) for p in pts], [(False, "Client A"), (False, "Client B")])
        self.assertEqual(fc.km_circuit([], DOMICILE, MAGASIN), 0.0)
        self.assertGreater(fc.km_circuit(pts, DOMICILE, MAGASIN), 30)


class TestStatistiques(unittest.TestCase):
    def test_par_employe(self):
        lignes = [{"employe": "E1", "employe_nom": "Akram", "type": "Arrêt inexpliqué", "minutes": 50, "km": None, "justification": ""},
                  {"employe": "E1", "employe_nom": "Akram", "type": "Détour", "minutes": 0, "km": 35.0, "justification": "Fournisseur"},
                  {"employe": "E1", "employe_nom": "Akram", "type": "Hors horaires", "minutes": 20, "km": 12.0, "justification": "Anomalie confirmée"},
                  {"employe": "E2", "employe_nom": "Sadok", "type": "Arrêt inexpliqué", "minutes": 90, "km": None, "justification": ""}]
        journees = [{"employe": "E1", "employe_nom": "Akram", "km": 100}, {"employe": "E1", "employe_nom": "Akram", "km": 80},
                    {"employe": "E3", "employe_nom": "Hedi", "km": 120}]
        s = fc.statistiques_pure(lignes, journees)
        akram = next(x for x in s if x["employe"] == "E1")
        self.assertEqual((akram["jours"], akram["km"], akram["km_par_jour"]), (2, 180.0, 90.0))
        self.assertEqual((akram["arrets"], akram["minutes_arrets"], akram["detours"], akram["km_detour"], akram["hors_horaires"]), (1, 50, 1, 35.0, 1))
        self.assertEqual((akram["a_voir"], akram["justifies"], akram["anomalies"]), (1, 1, 1))
        hedi = next(x for x in s if x["employe"] == "E3")
        self.assertEqual((hedi["jours"], hedi["a_voir"]), (1, 0))
        self.assertEqual([x["employe"] for x in s][:2], ["E1", "E2"])          # ceux qui ont des écarts à regarder d'abord


class TestLieuxRecurrents(unittest.TestCase):
    def test_meme_case_de_200_m(self):
        l = lambda n, lat, lng, emp, just="": {"name": n, "type": "Arrêt inexpliqué", "lat": lat, "lng": lng, "minutes": 30, "employe_nom": emp, "justification": just}
        lignes = [l("a", 36.9630, 10.2170, "Sadok"), l("b", 36.9634, 10.2168, "Sadok"), l("c", 36.9631, 10.2171, "Akram", "Pause"),
                  l("d", 36.8370, 10.1920, "Akram"), {"name": "e", "type": "Détour", "lat": None, "lng": None, "minutes": 0}]
        r = fc.lieux_recurrents(lignes)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["n"], r[0]["minutes"], r[0]["a_voir"], r[0]["employes"], r[0]["justifications"]), (3, 90, 2, [("Sadok", 2), ("Akram", 1)], {"Pause": 1}))
        self.assertEqual(sorted(r[0]["noms"]), ["a", "b", "c"])
