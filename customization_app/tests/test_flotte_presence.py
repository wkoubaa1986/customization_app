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


class TestTrajets(unittest.TestCase):
    def test_arrets_intermediaires_du_matin_et_du_soir(self):
        j = journee_akram()
        # Le soir, Akram dépose Hedi 12 min à mi-chemin ; le matin, un arrêt de 2 min (feu) ne compte pas.
        j["arrets"].insert(4, _arret(_h(18.0), _h(18.2), (36.85, 10.22)))
        j["arrets"].insert(1, _arret(_h(8.45), _h(8.48), (36.84, 10.20)))
        p = fp.presence_pure(J.date(), j, MAGASIN)
        self.assertEqual(p["arrets_matin"], 0)
        self.assertEqual(p["arrets_soir"], 1)
        self.assertEqual((p["soir_arret_lat"], p["soir_arret_lng"], p["soir_arret_minutes"]), (36.85, 10.22, 12))

    def _ligne(self, jour, **kw):
        base = {"employe": "A", "employe_nom": "Akram", "date": jour, "nuit_avant_lieu": "Domicile", "nuit_lieu": "Domicile",
                "depart_matin": "%s 08:20:00" % jour, "arrivee_aquaworld": "%s 08:45:00" % jour,
                "depart_aquaworld": "%s 17:40:00" % jour, "retour_soir": "%s 18:30:00" % jour, "arrets_matin": 0, "arrets_soir": 0}
        base.update(kw)
        return base

    def test_distributions_et_medianes(self):
        lignes = [self._ligne("2026-10-13"),
                  self._ligne("2026-10-14", arrivee_aquaworld="2026-10-14 09:10:00", depart_aquaworld="2026-10-14 18:05:00",
                              arrets_soir=1, soir_arret_lat=36.85, soir_arret_lng=10.22, soir_arret_minutes=10),
                  # Dernier passage au Magasin à 11 h, retour 18 h 30 : par le terrain, hors distribution du soir
                  self._ligne("2026-10-15", depart_aquaworld="2026-10-15 11:00:00"),
                  # Rentré sans passer par Aquaworld
                  self._ligne("2026-10-16", arrivee_aquaworld=None, depart_aquaworld=None),
                  # Le véhicule dort au Magasin : aucun trajet domicile
                  self._ligne("2026-10-17", nuit_avant_lieu="Magasin", nuit_lieu="Magasin")]
        t = fp.trajets_pure(lignes)
        self.assertEqual(len(t), 1)
        m, s = t[0]["matin"], t[0]["soir"]
        self.assertEqual((m["n"], m["directs"], m["sans_passage"], m["par_terrain"]), (3, 3, 1, 0))
        self.assertEqual(m["mediane"], "08:45")
        self.assertEqual(m["duree_mediane"], 25)
        self.assertEqual(dict((k, v) for k, v in m["tranches"] if v), {"08:30": 2, "09:00": 1})
        self.assertEqual((s["n"], s["directs"], s["sans_passage"], s["par_terrain"]), (2, 1, 1, 1))
        self.assertEqual(s["mediane"], "17:52")
        self.assertEqual(dict((k, v) for k, v in s["tranches"] if v), {"17:30": 1, "18:00": 1})
        self.assertEqual(s["lieux"][0]["n"], 1)
        self.assertEqual(s["lieux"][0]["minutes"], 10)
        self.assertEqual(s["lieux"][0]["passagers"], 0)
        t2 = fp.trajets_pure([self._ligne("2026-10-14", passager=True, depart_aquaworld="2026-10-14 18:05:00",
                                          arrets_soir=1, soir_arret_lat=36.85, soir_arret_lng=10.22, soir_arret_minutes=10)])
        self.assertEqual(t2[0]["soir"]["lieux"][0]["passagers"], 1)

    def test_tranches_bornes_ouvertes(self):
        t = fp.tranches([6 * 60, 7 * 60, 7 * 60 + 29, 7 * 60 + 30, 11 * 60, 12 * 60, None], (7 * 60, 11 * 60))
        self.assertEqual(t[0], ["< 07:00", 1])
        self.assertEqual(t[1], ["07:00", 2])
        self.assertEqual(t[2], ["07:30", 1])
        self.assertEqual(t[-1], ["≥ 11:00", 2])
        self.assertEqual(len(t), 10)


class TestRattrapage(unittest.TestCase):
    def test_jours_manquants_jusqu_a_hier(self):
        calcules = {"2026-10-10", "2026-10-12"}
        self.assertEqual(fp.jours_manquants_pure("2026-10-09", "2026-10-16", calcules, "2026-10-16"),
                         ["2026-10-09", "2026-10-11", "2026-10-13", "2026-10-14", "2026-10-15"])
        self.assertEqual(fp.jours_manquants_pure("2026-10-16", "2026-10-20", set(), "2026-10-16"), [])   # aujourd'hui pas fini

    def test_le_lieu_de_depose_dit_qui_etait_passager(self):
        pts = [{"lat": 36.95, "lng": 10.23, "minutes": 10, "passager": True, "date": "2026-10-13"},
               {"lat": 36.9501, "lng": 10.2301, "minutes": 20, "passager": True, "date": "2026-10-14"},
               {"lat": 36.9502, "lng": 10.2302, "minutes": 6, "passager": False, "date": "2026-10-15"},
               {"lat": 36.80, "lng": 10.10, "minutes": 5, "date": "2026-10-15"}]
        g = fp._grouper(pts)
        self.assertEqual([(c["n"], c["passagers"]) for c in g], [(3, 2), (1, 0)])
        self.assertEqual(g[0]["minutes"], 36)
        self.assertEqual(g[0]["derniere"], "2026-10-15")


class TestEquipages(unittest.TestCase):
    EMP = [("HR-EMP-00001", "Sadok Bouziri"), ("HR-EMP-00002", "Jamel Aloui"), ("HR-EMP-00006", "Mohamed Hedi Chouchane"),
           ("HR-EMP-00010", "Akram"), ("HR-EMP-00012", "Hedi ibidhii")]

    def test_lecture_du_reglage(self):
        g = fp.equipages_pure("Sadok Bouziri + Jamel Aloui\nakram + MOHAMED HEDI CHOUCHANE\nInconnu + Akram\nSadok Bouziri", self.EMP)
        self.assertEqual(g, [["HR-EMP-00001", "HR-EMP-00002"], ["HR-EMP-00010", "HR-EMP-00006"]])
        self.assertEqual(fp.equipages_pure("HR-EMP-00001, HR-EMP-00002", self.EMP), [["HR-EMP-00001", "HR-EMP-00002"]])
        self.assertEqual(fp.equipages_pure("", self.EMP), [])

    def test_le_coequipier_herite_de_la_journee(self):
        noms = dict(self.EMP)
        lignes = [{"name": "GPS-1", "date": "2026-10-14", "employe": "HR-EMP-00001", "employe_nom": "Sadok Bouziri", "depart_aquaworld": "x", "attribue_par_taches": "Jamel Aloui"},
                  {"name": "GPS-2", "date": "2026-10-15", "employe": "HR-EMP-00001", "employe_nom": "Sadok Bouziri"},
                  {"name": "GPS-3", "date": "2026-10-15", "employe": "HR-EMP-00002", "employe_nom": "Jamel Aloui"}]
        out = fp.heriter_pure(lignes, [["HR-EMP-00001", "HR-EMP-00002"]], noms)
        self.assertEqual(len(out), 4)                               # le 14, Jamel hérite ; le 15, il a sa propre journée
        v = out[-1]
        self.assertEqual((v["date"], v["employe"], v["employe_nom"], v["avec"], v["passager"]), ("2026-10-14", "HR-EMP-00002", "Jamel Aloui", "Sadok Bouziri", True))
        self.assertEqual(v["depart_aquaworld"], "x")
        self.assertEqual(v["name"], "GPS-1|HR-EMP-00002")
        self.assertNotIn("attribue_par_taches", v)                 # la note « par la nuit » reste au conducteur
        self.assertFalse(any(l.get("passager") for l in out[:3]))

    def test_les_nuits_du_passager_ne_comptent_pas(self):
        s = fp.statistiques_pure([{"employe": "J", "employe_nom": "Jamel", "nuit_avant_lieu": "Domicile", "nuit_lieu": "Domicile",
                                   "passager": True, "depart_aquaworld": "2026-10-14 17:30:00"}])
        self.assertEqual(s[0]["nuits"], {"Domicile": 0, "Magasin": 0, "Autre": 0})
        self.assertEqual(s[0]["passager"], 1)
        self.assertEqual(s[0]["depart"], "17:30")


class TestLieuxDeNuit(unittest.TestCase):
    """Toutes voitures confondues : les endroits où les voitures dorment, et qui on propose."""
    AKRAM, SADOK = (36.8370, 10.1924), (36.9634, 10.2172)

    def _nuits(self):
        out = []
        for i in range(5):                                             # le Changan dort chez Akram, attribué à Akram
            out.append({"lat": self.AKRAM[0] + 0.0003 * (i % 2), "lng": self.AKRAM[1], "date": "2026-10-%02d" % (1 + i), "vehicule": "Changan", "employe": "A", "employe_nom": "Akram"})
        for i in range(4):                                             # la 5957 dort chez Sadok, 3 fois attribuée à Jamel (tâches)
            out.append({"lat": self.SADOK[0], "lng": self.SADOK[1], "date": "2026-10-%02d" % (1 + i), "vehicule": "5957", "employe": "J" if i else "S", "employe_nom": "Jamel" if i else "Sadok"})
        out.append({"lat": MAGASIN[0], "lng": MAGASIN[1], "date": "2026-10-03", "vehicule": "3554", "employe": "H", "employe_nom": "Hedi"})
        out.append({"lat": 36.80, "lng": 10.10, "date": "2026-10-04", "vehicule": "Changan", "employe": "A", "employe_nom": "Akram"})
        return out

    def test_regroupement_toutes_voitures_et_statuts(self):
        lieux = fp.lieux_nuit_pure(self._nuits(), MAGASIN, {}, {"A": "Akram"})
        self.assertEqual(sorted((c["n"], c["statut"]) for c in lieux), [(1, "magasin"), (1, "rare"), (4, "a_regler"), (5, "a_regler")])
        self.assertEqual([c["n"] for c in lieux], [5, 4, 1, 1])                    # du plus fréquent au moins
        self.assertEqual(lieux[0]["suggestion"], "Akram")
        self.assertEqual(lieux[1]["employes"], {"Jamel": 3, "Sadok": 1})       # indice seulement : à l'utilisateur de dire
        self.assertEqual(lieux[1]["suggestion"], "Jamel")
        self.assertEqual(lieux[0]["vehicules"], {"Changan": 5})
        self.assertEqual((lieux[0]["premiere"], lieux[0]["derniere"]), ("2026-10-01", "2026-10-05"))

    def test_domicile_regle_nomme_et_visible_meme_sans_nuit(self):
        lieux = fp.lieux_nuit_pure(self._nuits(), MAGASIN, {"S": self.SADOK, "X": (36.70, 10.00)}, {"S": "Sadok", "X": "Nouveau"})
        chez_sadok = next(c for c in lieux if c["n"] == 4)
        self.assertEqual((chez_sadok["statut"], chez_sadok["employe_nom"], chez_sadok["suggestion"]), ("domicile", "Sadok", None))
        vide = next(c for c in lieux if c["employe"] == "X")
        self.assertEqual((vide["n"], vide["statut"]), (0, "domicile"))

    def test_la_nuit_prime_sur_les_taches_a_la_lecture(self):
        noms = {"A": "Akram", "S": "Sadok", "J": "Jamel"}
        lignes = [{"name": "1", "date": "2026-10-14", "employe": "A", "employe_nom": "Akram", "nuit_avant_lat": self.SADOK[0] + 0.004, "nuit_avant_lng": self.SADOK[1]},   # ~445 m de chez Sadok (≤ 500 m)
                  {"name": "2", "date": "2026-10-14", "employe": "S", "employe_nom": "Sadok", "nuit_avant_lat": MAGASIN[0], "nuit_avant_lng": MAGASIN[1]},
                  {"name": "3", "date": "2026-10-15", "employe": "J", "employe_nom": "Jamel", "nuit_avant_lat": self.SADOK[0], "nuit_avant_lng": self.SADOK[1]},
                  {"name": "4", "date": "2026-10-15", "employe": "S", "employe_nom": "Sadok", "nuit_avant_lat": self.SADOK[0] + 0.003, "nuit_avant_lng": self.SADOK[1]}]
        out = fp.reattribuer_pure(lignes, {"S": self.SADOK, "A": self.AKRAM, "N": (MAGASIN[0] - 0.0069, MAGASIN[1] - 0.0035)}, noms, depot=MAGASIN)
        self.assertEqual((out[0]["employe"], out[0]["attribue_par_taches"]), ("S", "Akram"))     # le Changan a dormi chez Sadok
        self.assertEqual(out[1]["employe"], "S")                                                 # a dormi au Magasin : jamais réattribué (pas à Néjib, à 830 m)
        self.assertNotIn("attribue_par_taches", out[1])
        self.assertEqual((out[2]["employe"], out[2]["attribue_par_taches"]), ("S", "Jamel"))     # la plus proche de chez Sadok devient la sienne
        self.assertEqual(out[3]["employe"], "S")                                                 # deux voitures chez Sadok : la 2e garde son employé
        self.assertNotIn("attribue_par_taches", out[3])

    def test_attribution_par_nuit_au_calcul(self):
        from customization_app import flotte_gps as fg
        nuits = {"CB1": (self.AKRAM[0] + 0.003, self.AKRAM[1]), "CB2": (self.SADOK[0], self.SADOK[1]), "CB3": MAGASIN, "CB4": None}
        self.assertEqual(fg.attribution_par_nuit(nuits, {"A": self.AKRAM, "S": self.SADOK}), {"A": "CB1", "S": "CB2"})
        self.assertEqual(fg.attribution_par_nuit(nuits, {"A": (36.0, 10.0)}), {})                 # trop loin
        self.assertEqual(fg.attribution_par_nuit({"CB1": (self.AKRAM[0] + 0.006, self.AKRAM[1])}, {"A": self.AKRAM}), {})   # 670 m : au-delà de 500 m
        # Néjib habite à 830 m du Magasin : une voiture qui dort au Magasin ne lui est pas attribuée
        nejib = (MAGASIN[0] - 0.0069, MAGASIN[1] - 0.0035)
        self.assertEqual(fg.attribution_par_nuit({"CB3": MAGASIN, "CB5": nejib}, {"N": nejib}, depot=MAGASIN), {"N": "CB5"})
        self.assertEqual(fg.attribution_par_nuit({"CB3": nejib}, {"N": nejib}), {"N": "CB3"})       # une voiture chez Néjib, elle, lui revient
        # vehicules_du_jour : la nuit prime, les tâches servent au reste
        journaux = {"CB1": {"arrets": [], "trajets": []}, "CB2": {"arrets": [], "trajets": []}}
        out = fg.vehicules_du_jour([], journaux, {"J": "CB2", "A": "CB1"}, 150, [], nuits={"CB2": self.SADOK}, domiciles={"S": self.SADOK})
        self.assertEqual(out, {"S": "CB2", "A": "CB1"})                                            # CB2 à Sadok (nuit) ; Jamel n'a plus son défaut, Akram garde le sien
        self.assertIsNone(fg.point_de_nuit(J.date(), {"arrets": [_arret(_h(8.0), _h(9.0), self.AKRAM)]}))
        self.assertEqual(fg.point_de_nuit(J.date(), journee_akram()), (journee_akram()["arrets"][0]["lat"], journee_akram()["arrets"][0]["lng"]))


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
