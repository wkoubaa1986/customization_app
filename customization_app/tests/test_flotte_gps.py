"""Flotte GPS : appariement tâche ↔ arrêts (pur), lecture des heures de la plateforme (UTC → Tunis)."""

import unittest
from datetime import datetime, timedelta

from customization_app import flotte_gps as fg

J = datetime(2026, 10, 7)
MAGASIN = (36.86684, 10.25487)
CHEZ_A = (36.76408, 10.28494)       # Bahri Hamed
CHEZ_B = (36.79570, 10.14557)       # Leila Bibi


def _arret(h, minutes, point, dm=0.0):
    """Arrêt à `point` décalé de dm mètres vers le nord."""
    arr = J + timedelta(hours=int(h), minutes=round((h % 1) * 60))
    return {"arr": arr, "dep": arr + timedelta(minutes=minutes), "lat": point[0] + dm / 111_000, "lng": point[1], "minutes": minutes}


def _tache(nom, emp, point, h, duree=30, **kw):
    d = J + timedelta(hours=int(h), minutes=round((h % 1) * 60))
    t = {"name": nom, "employe": emp, "lat": point[0] if point else None, "lng": point[1] if point else None,
         "debut": d, "fin": d + timedelta(minutes=duree), "type": "Entretien", "statut": "Open", "dans_local": ""}
    t.update(kw)
    return t


class TestHeures(unittest.TestCase):
    def test_utc_vers_tunis(self):
        self.assertEqual(fg._local("2026-10-07T16:05:44.000000000Z"), datetime(2026, 10, 7, 17, 5, 44))
        self.assertEqual(fg._local("2026-10-07 07:25:09+00:00"), datetime(2026, 10, 7, 8, 25, 9))
        self.assertIsNone(fg._local(None))

    def test_bornes_du_jour_en_utc(self):
        self.assertEqual(fg._bornes_utc("2026-10-07"), ("2026-10-06T23:00:00.000Z", "2026-10-07T22:59:59.000Z"))


class TestGrappes(unittest.TestCase):
    def test_arrets_proches_qui_se_suivent_fusionnes(self):
        # Akram devant chez Leila Bibi : 6 min, puis bouge la voiture de 50 m, puis 72 min → un seul passage.
        arrets = [_arret(9.88, 6, CHEZ_B, 53), _arret(9.98, 72, CHEZ_B, 18)]
        g = fg.grappes(arrets, CHEZ_B, 150)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["minutes"], 78)
        self.assertEqual(g[0]["arr"], arrets[0]["arr"])
        self.assertEqual(g[0]["dep"], arrets[1]["dep"])
        self.assertLess(g[0]["dist"], 20)

    def test_retour_apres_un_detour_reste_un_second_passage(self):
        # Mazni : 40 min, détour de 10 min à 1,3 km, retour 11 min → deux grappes (un arrêt ailleurs entre les deux).
        ailleurs = (CHEZ_A[0] + 0.012, CHEZ_A[1])
        arrets = [_arret(15.0, 40, CHEZ_A), _arret(15.73, 3, ailleurs), _arret(15.85, 11, CHEZ_A)]
        g = fg.grappes(arrets, CHEZ_A, 150)
        self.assertEqual([x["minutes"] for x in g], [40, 11])

    def test_hors_rayon_ignore(self):
        self.assertEqual(fg.grappes([_arret(10, 30, CHEZ_A, 400)], CHEZ_A, 150), [])


class TestApparier(unittest.TestCase):
    EMP = {"HR-EMP-00006": "3554", "HR-EMP-00010": "3647"}

    def _journaux(self, arrets_3554=(), arrets_3647=()):
        return {"3554": {"arrets": list(arrets_3554), "trajets": [{"debut": J, "fin": J, "minutes": 1, "km": 1, "vmax": 1}], "km": 1, "erreur": None},
                "3647": {"arrets": list(arrets_3647), "trajets": [], "km": 0, "erreur": None}}

    def test_passage_confirme_avec_ecart_et_duree(self):
        taches = [_tache("T1", "HR-EMP-00006", CHEZ_A, 9.92)]            # annoncé 09:55
        r = fg.apparier_pure(taches, self._journaux([_arret(9.63, 63, CHEZ_A, 4)]), self.EMP, 150)   # arrivé 09:38
        self.assertEqual(r["T1"]["statut"], fg.CONFIRME)
        self.assertEqual(r["T1"]["duree"], 63)
        self.assertEqual(r["T1"]["ecart"], -17)
        self.assertEqual(r["T1"]["distance"], 4)
        self.assertEqual(r["T1"]["depart"], r["T1"]["arrivee"] + timedelta(minutes=63))

    def test_deux_taches_chez_le_meme_client_separees(self):
        # Jalila Nasra : deux tâches (10:45 et 11:35), deux arrêts à 200 m l'un de l'autre, chacun dans le rayon des deux.
        p1, p2 = CHEZ_A, (CHEZ_A[0] + 0.0018, CHEZ_A[1])       # ~200 m
        taches = [_tache("A", "HR-EMP-00006", p1, 10.75), _tache("B", "HR-EMP-00006", p2, 11.58)]
        arrets = [_arret(10.72, 53, p1), _arret(11.63, 17, p2)]
        r = fg.apparier_pure(taches, self._journaux(arrets), self.EMP, 250)
        self.assertEqual((r["A"]["duree"], r["B"]["duree"]), (53, 17))
        self.assertEqual((r["A"]["ecart"], r["B"]["ecart"]), (-2, 3))

    def test_un_arret_ne_sert_qu_une_fois(self):
        taches = [_tache("A", "HR-EMP-00006", CHEZ_A, 10), _tache("B", "HR-EMP-00006", CHEZ_A, 14)]
        r = fg.apparier_pure(taches, self._journaux([_arret(10.1, 30, CHEZ_A)]), self.EMP, 150)
        self.assertEqual(r["A"]["statut"], fg.CONFIRME)
        self.assertEqual(r["B"]["statut"], fg.AUCUN)

    def test_statuts_sans_vehicule_sans_position_sans_donnees(self):
        taches = [_tache("V", "HR-EMP-00001", CHEZ_A, 10),               # pas de véhicule
                  _tache("P", "HR-EMP-00006", None, 10),                 # pas de position
                  _tache("G", "HR-EMP-00010", CHEZ_B, 10),               # véhicule sans journal
                  _tache("C", "HR-EMP-00006", CHEZ_A, 10, statut="Cancelled"),
                  _tache("L", "HR-EMP-00006", CHEZ_A, 10, dans_local="Oui", type="Réparation"),
                  _tache("R", "HR-EMP-00006", CHEZ_A, 10, type="Jour de récupération")]
        r = fg.apparier_pure(taches, self._journaux([_arret(10, 30, CHEZ_A)]), self.EMP, 150)
        self.assertEqual(r["V"]["statut"], fg.SANS_VEH)
        self.assertEqual(r["P"]["statut"], fg.SANS_POS)
        self.assertEqual(r["G"]["statut"], fg.SANS_GPS)
        for nom in ("C", "L", "R"):
            self.assertNotIn(nom, r)

    def test_journal_en_erreur(self):
        taches = [_tache("T", "HR-EMP-00006", CHEZ_A, 10)]
        r = fg.apparier_pure(taches, {"3554": {"arrets": [], "trajets": [], "km": 0, "erreur": "RESPONSE_TIMEOUT"}}, self.EMP, 150)
        self.assertEqual(r["T"]["statut"], fg.SANS_GPS)

    def test_le_bon_arret_quand_le_client_est_proche_du_magasin(self):
        # Tâche annoncée 17:00 à 120 m du Magasin : l'arrêt du soir au Magasin (17:03, 9 min) est dans le rayon,
        # mais l'arrêt chez le client à 17:15 colle mieux (plus près ET à l'heure).
        client = (MAGASIN[0] + 0.0011, MAGASIN[1])
        taches = [_tache("T", "HR-EMP-00006", client, 17.0)]
        arrets = [_arret(17.05, 9, MAGASIN), _arret(17.25, 25, client, 5)]
        r = fg.apparier_pure(taches, self._journaux(arrets), self.EMP, 150)
        self.assertEqual(r["T"]["duree"], 25)
        self.assertEqual(r["T"]["ecart"], 15)


class TestVehiculesDuJour(unittest.TestCase):
    def _j(self, arrets):
        return {"arrets": list(arrets), "trajets": [], "km": 0, "erreur": None}

    def test_le_vehicule_qui_sarrete_chez_les_clients_gagne(self):
        # Akram (habituellement sur 3647) a fait ses deux clients avec la 3554 ; la 3647 n'a bougé que jusqu'au Magasin.
        taches = [_tache("A", "HR-EMP-00010", CHEZ_A, 10), _tache("B", "HR-EMP-00010", CHEZ_B, 12)]
        journaux = {"3554": self._j([_arret(10, 30, CHEZ_A), _arret(12, 30, CHEZ_B)]), "3647": self._j([_arret(9, 600, MAGASIN)])}
        self.assertEqual(fg.vehicules_du_jour(taches, journaux, {"HR-EMP-00010": "3647"}, 150), {"HR-EMP-00010": "3554"})

    def test_un_seul_client_suffit_des_le_matin(self):
        # Live, 10 h : Akram (habituellement sur 3647) n'a encore qu'un arrêt… chez son premier client, avec la 3554.
        taches = [_tache("A", "HR-EMP-00010", CHEZ_A, 10), _tache("B", "HR-EMP-00010", CHEZ_B, 12)]
        journaux = {"3554": self._j([_arret(10, 30, CHEZ_A)]), "3647": self._j([_arret(9, 600, MAGASIN)])}
        self.assertEqual(fg.vehicules_du_jour(taches, journaux, {"HR-EMP-00010": "3647"}, 150), {"HR-EMP-00010": "3554"})

    def test_un_arret_au_magasin_n_identifie_personne(self):
        # Tâche de Hedi au Magasin + tâche d'Akram au Magasin : la 3647 s'y arrête — aucun indice, chacun garde son habituel.
        taches = [_tache("H", "HR-EMP-00006", MAGASIN, 9), _tache("K", "HR-EMP-00010", MAGASIN, 9)]
        journaux = {"3554": self._j([]), "3647": self._j([_arret(9, 30, MAGASIN)])}
        r = fg.vehicules_du_jour(taches, journaux, {"HR-EMP-00010": "3647", "HR-EMP-00006": "3554"}, 150, [MAGASIN])
        self.assertEqual(r, {"HR-EMP-00010": "3647", "HR-EMP-00006": "3554"})

    def test_un_vehicule_par_employe_et_defaut_pour_les_autres(self):
        taches = [_tache("A", "HR-EMP-00010", CHEZ_A, 10), _tache("B", "HR-EMP-00010", CHEZ_B, 12),
                  _tache("C", "HR-EMP-00006", CHEZ_A, 15), _tache("D", "HR-EMP-00006", CHEZ_B, 16)]
        journaux = {"3554": self._j([_arret(10, 30, CHEZ_A), _arret(12, 30, CHEZ_B)]), "3647": self._j([]), "5957": self._j([_arret(9, 60, MAGASIN)])}
        # Les arrêts de la 3554 (10 h, 12 h) tombent sur les adresses de Hedi, mais 5 h avant ses tâches : ce n'est pas lui.
        # Son véhicule habituel (3554) étant pris par Akram, Hedi reste sans véhicule — plutôt qu'un faux.
        r = fg.vehicules_du_jour(taches, journaux, {"HR-EMP-00010": "3647", "HR-EMP-00006": "3554"}, 150)
        self.assertEqual(r, {"HR-EMP-00010": "3554"})


if __name__ == "__main__":
    unittest.main()
