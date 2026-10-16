"""Présence Aquaworld : nuits du véhicule, arrivée / départ au Magasin, domiciles — calcul pur sur des journées fabriquées."""

import unittest
from datetime import datetime, timedelta

from customization_app import flotte_presence as fp

J = datetime(2026, 10, 14)
MAGASIN = (36.86684, 10.25487)
DOM_AKRAM = (36.83703, 10.19232)
DOM_SADOK = (36.96324, 10.21724)
CLIENT = (36.76408, 10.28494)


def _h(h, jour=J):
    return jour + timedelta(hours=int(h), minutes=round((h % 1) * 60))


def _arret(debut, fin, point):
    return {"arr": debut, "dep": fin, "lat": point[0], "lng": point[1], "minutes": int((fin - debut).total_seconds() // 60)}


def journee_akram():
    """Comme la Changan le 14/10/2026 : nuit au domicile, Magasin 08:36-09:25, clients, retour 20:26."""
    return {"erreur": None, "trajets": [], "km": 135.8, "arrets": [
        _arret(_h(19.33, J - timedelta(days=1)), _h(8.4), DOM_AKRAM),
        _arret(_h(8.6), _h(9.42), MAGASIN),
        _arret(_h(10.5), _h(11.5), CLIENT),
        _arret(_h(17.0), _h(17.75), MAGASIN),
        _arret(_h(20.45), J + timedelta(days=1) - timedelta(seconds=1), DOM_AKRAM),
    ]}


def journee_hedi():
    """Comme la 3554 : le véhicule dort au Magasin (18:32 → 09:03), clients, retour au Magasin 18:18."""
    return {"erreur": None, "trajets": [], "km": 63.7, "arrets": [
        _arret(_h(18.53, J - timedelta(days=1)), _h(9.06), MAGASIN),
        _arret(_h(10.0), _h(11.0), CLIENT),
        _arret(_h(18.3), J + timedelta(days=1) - timedelta(seconds=1), MAGASIN),
    ]}


class TestPresence(unittest.TestCase):
    def test_nuits_avant_et_apres(self):
        avant, apres = fp.nuits_pure(J.date(), journee_akram()["arrets"])
        self.assertEqual((avant["lat"], avant["lng"]), DOM_AKRAM)
        self.assertEqual(avant["dep"].strftime("%H:%M"), "08:24")
        self.assertEqual(apres["arr"].strftime("%H:%M"), "20:27")

    def test_journee_complete_depuis_le_domicile(self):
        p = fp.presence_pure(J.date(), journee_akram(), MAGASIN)
        self.assertEqual(p["depart_matin"].strftime("%H:%M"), "08:24")
        self.assertEqual(p["arrivee_aquaworld"].strftime("%H:%M"), "08:36")
        self.assertEqual(p["depart_aquaworld"].strftime("%H:%M"), "17:45")      # le DERNIER passage au Magasin
        self.assertEqual(p["retour_soir"].strftime("%H:%M"), "20:27")
        self.assertEqual(p["minutes_aquaworld"], 49 + 45)
        self.assertEqual((p["nuit_avant_lat"], p["nuit_avant_lng"]), DOM_AKRAM)
        self.assertEqual((p["nuit_lat"], p["nuit_lng"]), DOM_AKRAM)
        self.assertFalse(p["immobile"] or p["en_cours"])

    def test_vehicule_qui_dort_au_magasin_n_a_pas_d_arrivee(self):
        # Hedi : la nuit est au Magasin → pas d'« arrivée » inventée ; le matin c'est un départ du Magasin, le soir un retour.
        p = fp.presence_pure(J.date(), journee_hedi(), MAGASIN)
        self.assertIsNone(p["arrivee_aquaworld"])
        self.assertIsNone(p["depart_aquaworld"])
        self.assertEqual(p["minutes_aquaworld"], 0)
        self.assertEqual(p["depart_matin"].strftime("%H:%M"), "09:04")
        self.assertEqual(p["retour_soir"].strftime("%H:%M"), "18:18")
        self.assertEqual(fp.lieu((p["nuit_lat"], p["nuit_lng"]), MAGASIN, None), "Magasin")

    def test_journee_en_cours_sans_nuit_d_apres(self):
        j = journee_akram()
        j["arrets"] = j["arrets"][:3]                              # il est midi, le véhicule est chez le client
        p = fp.presence_pure(J.date(), j, MAGASIN)
        self.assertTrue(p["en_cours"])
        self.assertIsNone(p["retour_soir"])
        self.assertEqual(p["nuit_lat"], 0)
        self.assertEqual(p["arrivee_aquaworld"].strftime("%H:%M"), "08:36")

    def test_vehicule_immobile_toute_la_journee(self):
        j = {"arrets": [_arret(_h(19.0, J - timedelta(days=1)), J + timedelta(days=1) - timedelta(seconds=1), DOM_SADOK)], "trajets": []}
        p = fp.presence_pure(J.date(), j, MAGASIN)
        self.assertTrue(p["immobile"])
        self.assertIsNone(p["depart_matin"])
        self.assertIsNone(p["retour_soir"])
        self.assertEqual((p["nuit_lat"], p["nuit_lng"]), DOM_SADOK)

    def test_journal_vide(self):
        p = fp.presence_pure(J.date(), {"arrets": [], "trajets": []}, MAGASIN)
        self.assertTrue(p["en_cours"])
        self.assertIsNone(p["arrivee_aquaworld"])
        self.assertEqual(p["nuit_avant_lat"], 0)

    def test_lieu(self):
        self.assertEqual(fp.lieu(MAGASIN, MAGASIN, DOM_AKRAM), "Magasin")
        self.assertEqual(fp.lieu((DOM_AKRAM[0] + 0.0005, DOM_AKRAM[1]), MAGASIN, DOM_AKRAM), "Domicile")   # ~55 m
        self.assertEqual(fp.lieu(CLIENT, MAGASIN, DOM_AKRAM), "Autre")
        self.assertEqual(fp.lieu(CLIENT, MAGASIN, None), "Autre")
        self.assertEqual(fp.lieu(None, MAGASIN, None), "")
        self.assertEqual(fp.lieu((0, 0), MAGASIN, None), "")


class TestDomiciles(unittest.TestCase):
    def _nuits(self, employe, point, n, nom=None, bruit=0.0):
        return [{"employe": employe, "employe_nom": nom or employe, "lat": point[0] + bruit * (i % 2), "lng": point[1],
                 "date": (J + timedelta(days=i)).date()} for i in range(n)]

    def test_domicile_a_regler_quand_rien_n_est_regle(self):
        nuits = self._nuits("AKR", DOM_AKRAM, 6, "Akram", bruit=0.0004) + self._nuits("AKR", MAGASIN, 1, "Akram")
        d = fp.domiciles_pure(nuits, MAGASIN, {})
        self.assertEqual(len(d), 1)
        e = d[0]
        self.assertEqual(e["verdict"], "a_regler")
        self.assertEqual(e["nuits"], 7)
        self.assertEqual(e["lieux"][0]["n"], 6)                     # les 6 nuits à ± 45 m forment UN endroit
        self.assertFalse(e["lieux"][0]["magasin"])
        self.assertTrue(e["lieux"][1]["magasin"])
        self.assertEqual(e["principal"], e["lieux"][0])
        self.assertEqual(e["lieux"][0]["part"], 86)

    def test_domicile_regle_au_bon_endroit(self):
        d = fp.domiciles_pure(self._nuits("SAD", DOM_SADOK, 4), MAGASIN, {"SAD": (DOM_SADOK[0] + 0.0003, DOM_SADOK[1])})
        self.assertEqual(d[0]["verdict"], "ok")
        self.assertTrue(d[0]["lieux"][0]["regle"])

    def test_domicile_regle_ailleurs(self):
        d = fp.domiciles_pure(self._nuits("SAD", DOM_SADOK, 4), MAGASIN, {"SAD": DOM_AKRAM})
        self.assertEqual(d[0]["verdict"], "different")
        self.assertGreater(d[0]["ecart_m"], 10000)

    def test_vehicule_qui_dort_au_magasin(self):
        nuits = self._nuits("HED", MAGASIN, 10, "Hedi") + self._nuits("HED", CLIENT, 1, "Hedi")
        d = fp.domiciles_pure(nuits, MAGASIN, {})
        self.assertEqual(d[0]["verdict"], "magasin")
        self.assertIsNotNone(d[0]["principal"])                      # l'arrêt isolé existe, mais trop rare pour un domicile

    def test_le_domicile_regle_d_un_collegue_n_est_jamais_propose(self):
        # Jamel conduit la voiture de Sadok et la rend chez Sadok le soir : ce n'est pas le domicile de Jamel.
        nuits = self._nuits("SAD", DOM_SADOK, 5, "Sadok") + self._nuits("JAM", DOM_SADOK, 4, "Jamel")
        d = {e["employe"]: e for e in fp.domiciles_pure(nuits, MAGASIN, {"SAD": DOM_SADOK})}
        self.assertEqual(d["SAD"]["verdict"], "ok")
        self.assertEqual(d["JAM"]["verdict"], "chez_autre")
        self.assertEqual(d["JAM"]["lieux"][0]["chez_nom"], "Sadok")
        self.assertTrue(d["JAM"]["lieux"][0]["exclu"])
        self.assertIsNone(d["JAM"]["principal"])
        # Sans réglage, l'endroit est proposé à Jamel aussi : rien ne permet encore de le distinguer
        d = {e["employe"]: e for e in fp.domiciles_pure(nuits, MAGASIN, {})}
        self.assertEqual(d["JAM"]["verdict"], "a_regler")

    def test_pas_assez_de_nuits(self):
        d = fp.domiciles_pure(self._nuits("X", DOM_AKRAM, 2), MAGASIN, {})
        self.assertEqual(d[0]["verdict"], "insuffisant")

    def test_sans_employe_ni_point_ignore(self):
        d = fp.domiciles_pure([{"employe": None, "lat": 1, "lng": 1}, {"employe": "A", "lat": 0, "lng": 0}], MAGASIN, {})
        self.assertEqual(d, [])


class TestStatistiques(unittest.TestCase):
    def test_medianes_et_nuits(self):
        lignes = [
            {"employe": "A", "employe_nom": "Akram", "depart_matin": "2026-10-13 08:20:00", "arrivee_aquaworld": "2026-10-13 08:35:00",
             "depart_aquaworld": "2026-10-13 17:40:00", "retour_soir": "2026-10-13 20:10:00", "minutes_aquaworld": 90,
             "nuit_avant_lieu": "Domicile", "nuit_lieu": "Domicile"},
            {"employe": "A", "employe_nom": "Akram", "depart_matin": "2026-10-14 08:30:00", "arrivee_aquaworld": None,
             "depart_aquaworld": None, "retour_soir": "2026-10-14 19:00:00", "minutes_aquaworld": 0,
             "nuit_avant_lieu": "Domicile", "nuit_lieu": "Autre"},
            {"employe": "A", "employe_nom": "Akram", "depart_matin": "2026-10-15 09:00:00", "arrivee_aquaworld": "2026-10-15 09:15:00",
             "depart_aquaworld": "2026-10-15 18:00:00", "retour_soir": None, "minutes_aquaworld": 60,
             "nuit_avant_lieu": "Autre", "nuit_lieu": "", "immobile": False},
        ]
        s = fp.statistiques_pure(lignes)
        self.assertEqual(len(s), 1)
        e = s[0]
        self.assertEqual(e["jours"], 3)
        self.assertEqual(e["jours_magasin"], 2)
        self.assertEqual(e["depart_matin"], "08:30")
        self.assertEqual(e["arrivee"], "08:55")                     # médiane de 08:35 et 09:15
        self.assertEqual(e["depart"], "17:50")
        self.assertEqual(e["retour"], "19:35")
        self.assertEqual(e["minutes_aquaworld"], 75)
        self.assertEqual(e["nuits"], {"Domicile": 3, "Magasin": 0, "Autre": 2})

    def test_sans_employe_groupe_par_vehicule(self):
        s = fp.statistiques_pure([{"employe": None, "vehicule": "5957 TU 232", "depart_matin": "2026-10-13 08:00:00"}])
        self.assertEqual(s[0]["employe_nom"], "5957 TU 232")
        self.assertEqual(s[0]["depart_matin"], "08:00")
        self.assertEqual(s[0]["arrivee"], "")


if __name__ == "__main__":
    unittest.main()
