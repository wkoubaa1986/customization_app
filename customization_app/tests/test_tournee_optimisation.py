"""Optimisation des tournées : lecture des liens, solveur, proposition et application sur une journée fabriquée.
Sans réseau (drapeau `tournee_sans_reseau` : distances à vol d'oiseau, liens courts non résolus)."""
from __future__ import annotations

import unittest

from customization_app import tournee_optimisation as T


class TestLiens(unittest.TestCase):
    def test_formats(self):
        c = T.coordonnees_du_lien
        self.assertEqual(c("https://maps.google.com/?q=36.8821509,10.252777"), (36.8821509, 10.252777))
        self.assertEqual(c("https://www.google.com/maps/place/X/@36.85,10.15,17z/data=!3m1"), (36.85, 10.15))
        self.assertEqual(c("https://www.google.com/maps/place/Rue/data=!4m6!3m5!1s0x12f:0x65!7e2!8m2!3d36.8505196!4d10.1578101?x=1"), (36.8505196, 10.1578101))
        self.assertEqual(c("https://www.google.com/maps/search/36.877016,+10.257761?entry=tts&shorturl=1"), (36.877016, 10.257761))   # goo.gl/maps
        self.assertEqual(c("https://consent.google.com/ml?continue=https://www.google.com/maps/search/36.877016,%2B10.257761?shorturl%3D1"), (36.877016, 10.257761))
        self.assertIsNone(c("https://maps.app.goo.gl/RRiBiumdXDYQ2wXUA"))      # lien court : à résoudre
        self.assertIsNone(c("https://maps.google.com/?cid=7227648310640122990"))
        self.assertIsNone(c(None))

    def test_vol_d_oiseau(self):
        mn, km, source = T.matrice_haversine([(36.80, 10.18), (36.80, 10.18), (36.89, 10.25)])
        self.assertEqual((mn[0][1], km[0][1], source), (0, 0.0, "vol d’oiseau"))
        self.assertTrue(10 < km[0][2] < 20 and mn[0][2] == int(round(km[0][2] / 30 * 60)))


class TestDurees(unittest.TestCase):
    def test_standard_du_type_sauf_planifie_plus_long(self):
        d = T.duree_retenue
        self.assertEqual(d("Entretien", 30, "15 min"), 30)       # « 15 min » ne raccourcit pas un entretien
        self.assertEqual(d("Entretien", 15, "15 min"), 30)
        self.assertEqual(d("Entretien", 60, "30 min"), 60)       # planifié nettement plus long : retenu
        self.assertEqual(d("Installation", None, "1 heure, 30 min"), 90)
        self.assertEqual(d("Installation", None, "30 min"), 75)
        self.assertEqual(d("Réparation", 600, None), 75)         # créneau aberrant ignoré ; réparation 1 h 15 (02/10)
        self.assertEqual(d("Inconnu", None, None), 60)


class TestPointes(unittest.TestCase):
    def test_plage_annoncee_et_seuil(self):
        self.assertEqual(T.plage_annoncee(15 * 60 + 15, 60)["plage"], "14h45 et 15h45")
        self.assertEqual(T.plage_annoncee(15 * 60 + 15, 60)["demi"], "l'après-midi")
        self.assertEqual(T.plage_annoncee(9 * 60, 60), {"debut": "08:30", "fin": "09:30", "plage": "8h30 et 9h30", "demi": "le matin"})
        self.assertEqual(T.plage_annoncee(10 * 60 + 10, 120)["plage"], "9h et 11h")      # borne au quart d'heure inférieur
        self.assertTrue(T.a_prevenir({"deplace": True, "decale": False, "ecart_min": 0}, 15))
        self.assertTrue(T.a_prevenir({"deplace": False, "decale": True, "ecart_min": -20}, 15))
        self.assertFalse(T.a_prevenir({"deplace": False, "decale": True, "ecart_min": 10}, 15))
        self.assertFalse(T.a_prevenir({"deplace": True, "fixe": True}, 0))
        self.assertEqual(T._extras_notification({"debut": "13:30", "ancien_debut": "13:30"}, 60)["au_lieu_de"], "")
        self.assertEqual(T._extras_notification({"debut": "13:30", "ancien_debut": "14:30"}, 60)["au_lieu_de"], " (au lieu de 14h30)")
        self.assertFalse(T.a_prevenir({"deplace": True, "non_place": True}, 0))

    def test_facteur_et_matrice(self):
        pointes = [(7 * 60 + 30, 9 * 60, 30), (12 * 60, 13 * 60 + 30, 20)]
        self.assertEqual(T.facteur_pointe(8 * 60, pointes), 1.3)
        self.assertEqual(T.facteur_pointe(9 * 60, pointes), 1.0)             # borne de fin exclue
        self.assertEqual(T.facteur_pointe(12 * 60 + 30, pointes), 1.2)
        mn = [[0, 10], [10, 0]]
        self.assertEqual(T.matrice_majoree(mn, {0: 8 * 60}, pointes), [[0, 13], [10, 0]])   # seule la ligne « part à 8 h » est majorée
        self.assertEqual(T.matrice_majoree(mn, {0: 8 * 60}, []), mn)


class TestSolveur(unittest.TestCase):
    def test_depart_particulier(self):
        # véhicule 1 part de l'ouest (nœud 1) : il prend naturellement l'arrêt ouest, l'autre l'arrêt est.
        pts = [(36.87, 10.19), (36.87, 10.06), (36.87, 10.30), (36.87, 10.08)]
        mn, km, _s = T.matrice_haversine(pts)
        sol = T.resoudre(mn, [{"service": 30, "fenetre": None, "vehicule": None}, {"service": 30, "fenetre": None, "vehicule": None}],
                         2, 8 * 60, 17 * 60, limite_s=1, depots=[0, 1])
        places = {n: v for v, r in enumerate(sol["routes"]) for n, _t in r}
        self.assertEqual((places[2], places[3]), (0, 1))


    def test_deux_vehicules_et_une_fixe(self):
        # dépôt + 4 arrêts : A/B proches à l'est, C/D proches à l'ouest ; D fixe à 10:00 sur le véhicule 1.
        pts = [(36.87, 10.19), (36.87, 10.30), (36.875, 10.31), (36.87, 10.08), (36.865, 10.07)]
        mn, km, _s = T.matrice_haversine(pts)
        arrets = [{"service": 30, "fenetre": None, "vehicule": None}, {"service": 30, "fenetre": None, "vehicule": None},
                  {"service": 30, "fenetre": None, "vehicule": None}, {"service": 30, "fenetre": (600, 600), "vehicule": 1}]
        sol = T.resoudre(mn, arrets, 2, 8 * 60, 17 * 60, limite_s=2)
        self.assertEqual(sol["non_places"], [])
        places = {n: (v, t) for v, r in enumerate(sol["routes"]) for n, t in r}
        self.assertEqual(places[4][0], 1)                       # D sur le véhicule imposé
        self.assertEqual(places[4][1], 600)                     # à 10:00 exactement
        self.assertEqual(places[3][0], places[4][0])            # C (voisine de D) avec D
        self.assertEqual(places[1][0], places[2][0])            # A et B (voisines) ensemble
        # aucun zigzag : au total, au plus un aller-retour vers chaque côté (≈ 2 × 25 km)
        total = 0.0
        for v, r in enumerate(sol["routes"]):
            ordre = [0] + [n for n, _t in r] + [0]
            total += sum(km[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1))
        self.assertLessEqual(total, 60)
        for v, r in enumerate(sol["routes"]):
            self.assertEqual([t for _n, t in r], sorted(t for _n, t in r))   # heures croissantes

    def test_marge_et_pause(self):
        # un arrêt à 10 min du dépôt, service 30, marge 10 : départ 08:00, arrivée possible 08:20 mais pas de visite avant
        # « première » 09:00 → 09:00 ; avec la pause 09:00–10:00 l'intervention ne peut ni commencer ni finir dedans → 10:00.
        pts = [(36.87, 10.19), (36.87, 10.25)]
        mn, km, _s = T.matrice_haversine(pts)
        mn = [[0, 10], [10, 0]]
        sol = T.resoudre(mn, [{"service": 30, "fenetre": None, "vehicule": None}], 1, 8 * 60, 17 * 60, limite_s=1, premiere=9 * 60, marge=10)
        self.assertEqual(sol["routes"][0][0][1], 9 * 60)
        sol = T.resoudre(mn, [{"service": 30, "fenetre": None, "vehicule": None}], 1, 8 * 60, 17 * 60, limite_s=1, premiere=8 * 60, marge=10)
        self.assertEqual(sol["routes"][0][0][1], 8 * 60 + 20)                   # 10 de route + 10 de marge
        sol = T.resoudre(mn, [{"service": 30, "fenetre": None, "vehicule": None}], 1, 8 * 60, 17 * 60, limite_s=1, premiere=9 * 60, marge=10,
                         pause=(9 * 60, 10 * 60))
        self.assertEqual(sol["routes"][0][0][1], 10 * 60)

    def test_vehicule_occupe_hors_tournee(self):
        # le véhicule est pris 09:00–11:00 (tâche gardée telle quelle) : l'arrêt libre (service 30) se cale avant ou après,
        # jamais dessus.
        mn = [[0, 10], [10, 0]]
        for k in range(3):
            sol = T.resoudre(mn, [{"service": 30, "fenetre": (9 * 60 + 15, 12 * 60), "vehicule": None}], 1, 8 * 60, 17 * 60,
                             limite_s=1, premiere=8 * 60, occupations={0: [(9 * 60, 11 * 60)]})
            t = sol["routes"][0][0][1]
            self.assertTrue(t >= 11 * 60, t)

    def test_fusionner_intervalles(self):
        self.assertEqual(T.fusionner_intervalles([(661, 781), (680, 800)]), [(661, 800)])          # chevauchants
        self.assertEqual(T.fusionner_intervalles([(781, 800), (661, 781)]), [(661, 800)])          # contigus, désordonnés
        self.assertEqual(T.fusionner_intervalles([(540, 615), (661, 781)]), [(540, 615), (661, 781)])
        self.assertEqual(T.fusionner_intervalles([(700, 700), (600, 650)]), [(600, 650)])          # vide ignoré

    def test_occupations_chevauchantes_ne_retardent_pas(self):
        # Deux réparations qui se chevauchent (11:01–13:01 et 11:20–13:20, Akram le 03/10/2026) déroutaient OR-Tools :
        # la visite suivante partait à 14:39 au lieu de 13:30. Fusionnées, elles valent un seul créneau 11:01–13:20.
        pts = [(36.87, 10.19), (36.88, 10.22), (36.86, 10.25), (36.85, 10.20)]
        mn, km, _s = T.matrice_haversine(pts)
        arrets = [{"service": 75, "fenetre": (540, 540), "vehicule": 0}, {"service": 30, "fenetre": None, "vehicule": None},
                  {"service": 30, "fenetre": None, "vehicule": None}]
        heures = []
        for occ in ([(661, 781), (680, 800)], [(661, 800)]):
            sol = T.resoudre(mn, arrets, 1, 510, 1080, limite_s=1, premiere=540, marge=15, pause=(750, 810), occupations={0: occ})
            self.assertEqual(sol["non_places"], [])
            heures.append(sorted(t for _n, t in sol["routes"][0]))
        self.assertEqual(heures[0], heures[1])
        self.assertEqual(heures[0][1], 810)                                             # 13:30, juste après le créneau

    def test_equilibre_rapproche_les_fins_de_journee(self):
        # Six interventions d'une heure, voisines ; deux employés, le second pris 11:01–13:20 par des tâches hors
        # tournée (deux créneaux qui se chevauchent). Sans équilibrage le premier travaille jusqu'à 17:05 et le second
        # finit à 10:00 ; avec, le second reçoit l'après-midi et les deux finissent à moins d'1 h 30 l'un de l'autre.
        pts = [(36.87, 10.19)] + [(36.87 + 0.004 * i, 10.19 + 0.004 * (i % 3)) for i in range(1, 7)]
        mn, km, _s = T.matrice_haversine(pts)
        arrets = [{"service": 60, "fenetre": None, "vehicule": None} for _ in range(6)]
        fins = {}
        for eq in (0, 1):
            sol = T.resoudre(mn, arrets, 2, 510, 1080, equilibre=eq, limite_s=2, premiere=540, marge=15, pause=(750, 810),
                             occupations={1: [(661, 781), (680, 800)]})
            self.assertEqual(sol["non_places"], [])
            fins[eq] = [max((t + 60 for _n, t in r), default=510) for r in sol["routes"]]
            if eq:
                self.assertGreaterEqual(len([t for _n, t in sol["routes"][1] if t >= 800]), 2, sol["routes"])
        self.assertGreater(abs(fins[0][0] - fins[0][1]), 180, fins)                   # sans : très déséquilibré
        self.assertLessEqual(abs(fins[1][0] - fins[1][1]), 90, fins)                  # avec : fins voisines

    def test_horaires_par_vehicule(self):
        # véhicule 1 commence à 10:00 : son arrêt (le seul proche de lui) ne peut pas être avant 10:10 + marge.
        pts = [(36.87, 10.19), (36.87, 10.06), (36.87, 10.30), (36.87, 10.08)]
        mn, km, _s = T.matrice_haversine(pts)
        sol = T.resoudre(mn, [{"service": 30, "fenetre": None, "vehicule": None}, {"service": 30, "fenetre": None, "vehicule": 1}],
                         2, 8 * 60, 17 * 60, limite_s=1, premiere=8 * 60, depots=[0, 1], debuts=[8 * 60, 10 * 60], fins=[17 * 60, 12 * 60])
        places = {n: (v, t) for v, r in enumerate(sol["routes"]) for n, t in r}
        self.assertEqual(places[3][0], 1)
        self.assertGreaterEqual(places[3][1], 10 * 60 + mn[1][3])
        self.assertEqual(sol["non_places"], [])

    def test_journee_trop_courte_laisse_de_cote(self):
        pts = [(36.87, 10.19), (36.87, 10.30), (36.875, 10.31)]
        mn, km, _s = T.matrice_haversine(pts)
        # 5 h de journée, retour au dépôt compris : une tâche de 200 min tient (≈ 230 min), pas deux.
        sol = T.resoudre(mn, [{"service": 200, "fenetre": None, "vehicule": None}, {"service": 200, "fenetre": None, "vehicule": None}],
                         1, 8 * 60, 13 * 60, limite_s=1)
        self.assertEqual(len(sol["non_places"]), 1)


class TestJournee(unittest.TestCase):
    """Une journée fabriquée : 2 employés, 5 tâches dont une épinglée et une « Autre » au Magasin."""
    JOUR = "2027-03-10"

    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("tournee")
        frappe.flags.tournee_sans_reseau = True
        # Un réglage FIXE, quel que soit celui enregistré sur le site (heures, exclus, départs).
        self._config = T.config
        T.config = lambda: {"depot": T.DEPOT_DEFAUT, "departs": {}, "debut": 8 * 60, "premiere": 9 * 60, "fin": 17 * 60,
                            "types": list(T.TYPES_MOBILES_DEFAUT), "osrm": T.OSRM_DEFAUT, "equilibre": 1, "exclus": set(),
                            "pause": None, "marge": 0, "fenetre": 0, "horaires": {}, "pointes": [],
                            "prevenir": {"sms": 1, "email": 0, "seuil": 15, "plage": 60, "sujet": T.SUJET_EMAIL_DEFAUT,
                                         "sms_texte": T.MODELE_SMS_DEFAUT, "email_texte": ""}}
        if not frappe.db.has_column("Tache de travail", "custom_tournee_fixe"):
            self.skipTest("patch ensure_tournee_fields non joué")
        emps = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", order_by="name", limit=2)
        if len(emps) < 2:
            self.skipTest("pas deux employés")
        self.e1, self.e2 = emps
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        # est (A, B) et ouest (C, D) ; tout est d'abord mal réparti : e1 a A et C, e2 a B et D.
        self.taches = {}
        for code, emp, heure, typ, lien, fixe in (
                ("A", self.e1, "09:00", "Entretien", "https://maps.google.com/?q=36.870,10.300", 0),
                ("C", self.e1, "11:00", "Entretien", "https://maps.google.com/?q=36.870,10.080", 0),
                ("B", self.e2, "09:00", "Installation", "https://maps.google.com/?q=36.875,10.310", 0),
                # D épinglée à 10:00 : un seul véhicule ne peut pas faire l'est avant elle → deux tournées
                ("D", self.e2, "10:00", "Entretien", "https://maps.google.com/?q=36.865,10.070", 1),
                # « Autre » = 2 h par défaut (hook de création) : à 07:00 pour laisser D à 10:00 atteignable
                ("V", self.e2, "07:00", "Autre", "", 0)):
            doc = frappe.get_doc({"doctype": "Tache de travail", "custom_type_dintervention": typ, "custom_choix_du_staff": emp,
                                  "custom_client": client if typ != "Autre" else None, "nom_client": "Client %s" % code,
                                  "starts_on": "%s %s:00" % (self.JOUR, heure), "temps": "15 min", "status": "Open",
                                  "google_map": lien, "custom_tournee_fixe": fixe, "subject": "essai tournée %s" % code})
            doc.flags.ignore_permissions = True
            doc.insert()
            self.taches[code] = doc.name

    def tearDown(self):
        import frappe
        frappe.flags.tournee_sans_reseau = False
        T.config = self._config
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="tournee")

    def test_proposition_puis_application(self):
        import frappe
        p = T.proposer(self.JOUR)
        self.assertEqual([e["employe"] for e in p["employes"]], sorted([self.e1, self.e2]))
        self.assertEqual(p["source"], "vol d’oiseau")
        self.assertEqual(p["non_places"], [])
        arrets = {a["tache"]: a for e in p["employes"] for a in e["apres"]["arrets"]}
        self.assertEqual(set(arrets), set(self.taches.values()))
        d, v = arrets[self.taches["D"]], arrets[self.taches["V"]]
        self.assertTrue(d["fixe"] and d["employe"] == self.e2 and d["debut"] == "10:00")      # épinglée : rien ne bouge
        self.assertTrue(v["fixe"] and v["employe"] == self.e2 and v["debut"] == "07:00")      # « Autre » : fixe
        self.assertEqual(arrets[self.taches["C"]]["employe"], self.e2)                          # C rejoint D à l'ouest
        self.assertEqual(arrets[self.taches["B"]]["employe"], self.e1)                          # B rejoint A à l'est
        self.assertLess(p["total"]["apres_km"], p["total"]["avant_km"])
        for e in p["employes"]:                                                                # pas de visite avant 09:00
            self.assertTrue(all(a["debut"] >= "09:00" for a in e["apres"]["arrets"] if not a["fixe"]), e["apres"]["arrets"])
        self.assertEqual(p["deplacees"], 2)
        # aperçu des messages : les deux tâches qui changent d'employé, texte rendu avec la NOUVELLE heure et sa plage
        notifs = {n["tache"]: n for n in p["notifications"]}
        self.assertTrue({self.taches["B"], self.taches["C"]} <= set(notifs))                 # A peut bouger d'heure aussi
        self.assertNotIn(self.taches["D"], notifs)                                             # épinglée : jamais prévenue
        nc = notifs[self.taches["C"]]
        self.assertEqual(nc["heure"], arrets[self.taches["C"]]["debut"])
        self.assertIn("entre %s" % nc["plage"], nc["sms"])
        self.assertIn("(au lieu de 11h)", nc["sms"])
        self.assertIn("/rdv", nc["sms"])

        # capture de l'envoi en arrière-plan : rien ne part, on vérifie ce qui aurait été mis en file
        envois = []
        enqueue = frappe.enqueue
        frappe.enqueue = lambda *a, **k: envois.append(k)
        try:
            plan = [{"tache": a["tache"], "employe": a["employe"], "starts_on": a["starts_on"], "ends_on": a["ends_on"],
                     "prevenir": 1 if a["tache"] in notifs else 0}
                    for e in p["employes"] for a in e["apres"]["arrets"] if a["deplace"] or a["decale"]]
            r = T.appliquer(self.JOUR, plan, prevenir=1)
        finally:
            frappe.enqueue = enqueue
        self.assertEqual(r["prevenus"], len(notifs))
        envois = [k for k in envois if str(k.get("job_name", "")).startswith("tournee_prevenir_")]   # les hooks de la tâche enfilent aussi
        self.assertEqual(len(envois), 1)
        self.assertEqual(set(envois[0]["taches"]), set(notifs))
        self.assertEqual(envois[0]["extras"][self.taches["C"]]["plage"], nc["plage"])
        self.assertEqual(envois[0]["sms"], 1)
        self.assertIn(self.taches["C"], r["modifiees"])
        t = frappe.get_doc("Tache de travail", self.taches["C"])
        self.assertEqual(t.custom_choix_du_staff, self.e2)
        self.assertEqual(str(t.starts_on)[11:16], arrets[self.taches["C"]]["debut"])
        self.assertEqual((t.ends_on - t.starts_on).total_seconds(), 30 * 60)      # standard Entretien, pas le « 15 min »
        self.assertTrue(frappe.db.exists("Comment", {"reference_name": t.name, "comment_type": "Comment", "content": ["like", "%Optimisation%"]}))
        # une seconde proposition sur la journée appliquée ne déplace plus rien
        p2 = T.proposer(self.JOUR)
        self.assertEqual(p2["deplacees"], 0)

    def test_journee_surchargee_sans_chevauchement(self):
        """Journée voulue trop courte : des tâches restent de côté, mais RIEN ne se pose sur leur créneau
        (bug du 03/10/2026 : une tâche laissée de côté au dernier tour n'était pas réservée)."""
        p = T.proposer(self.JOUR, fenetre=0, employes=[{"employe": self.e1, "depart": "magasin", "debut": "08:30", "fin": "10:00"},
                                                        {"employe": self.e2, "depart": "magasin", "debut": "08:30", "fin": "10:40"}])
        self.assertTrue(p["non_places"], "la journée raccourcie devrait laisser des tâches de côté")

        def paires(cle):
            arrets = [{"employe": e["employe"], "tache": a["tache"], "debut": T._minutes(a["debut"], 0),
                       "service": T._minutes(a["fin"], 0) - T._minutes(a["debut"], 0)} for e in p["employes"] for a in e[cle]["arrets"]]
            return {frozenset((a["tache"], b["tache"])) for a, b in T.chevauchements(arrets)}
        # B (installation 75 min) déborde déjà sur D épinglée à 10:00 dans les données : ce chevauchement-là est
        # d'origine et peut rester ; aucun NOUVEAU ne doit apparaître.
        self.assertTrue(paires("avant"))
        self.assertLessEqual(paires("apres"), paires("avant"), p["employes"])
        self.assertTrue(any("se chevauchent déjà" in w for w in p["avertissements"]))
        self.assertTrue(all(a["fixe"] for e in p["employes"] for a in e["apres"]["arrets"] if a["non_place"]))   # laissée de côté = affichée fixe

    def test_chevauchements(self):
        a = {"employe": "x", "debut": 600, "service": 75, "client": "A"}
        b = {"employe": "x", "debut": 630, "service": 30, "client": "B"}
        c = {"employe": "x", "debut": 670, "service": 30, "client": "C"}
        d = {"employe": "y", "debut": 630, "service": 30, "client": "D"}
        self.assertEqual([(x["client"], y["client"]) for x, y in T.chevauchements([c, b, a, d])], [("A", "B"), ("A", "C")])

    def test_reserve_aux_superviseurs(self):
        import frappe
        autre = frappe.db.get_value("User", {"enabled": 1, "name": ["not in", ["Administrator", "Guest"]]}, "name")
        if not autre or set(T.ROLES) & set(frappe.get_roles(autre)):
            self.skipTest("pas d'utilisateur simple")
        frappe.set_user(autre)
        with self.assertRaises(frappe.PermissionError):
            T.proposer(self.JOUR)
