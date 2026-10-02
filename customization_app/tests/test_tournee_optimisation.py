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
        self.assertIsNone(c("https://maps.app.goo.gl/RRiBiumdXDYQ2wXUA"))      # lien court : à résoudre
        self.assertIsNone(c("https://maps.google.com/?cid=7227648310640122990"))
        self.assertIsNone(c(None))

    def test_vol_d_oiseau(self):
        mn, km, source = T.matrice_haversine([(36.80, 10.18), (36.80, 10.18), (36.89, 10.25)])
        self.assertEqual((mn[0][1], km[0][1], source), (0, 0.0, "vol d’oiseau"))
        self.assertTrue(10 < km[0][2] < 20 and mn[0][2] == int(round(km[0][2] / 30 * 60)))


class TestSolveur(unittest.TestCase):
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
                ("V", self.e2, "08:00", "Autre", "", 0)):
            doc = frappe.get_doc({"doctype": "Tache de travail", "custom_type_dintervention": typ, "custom_choix_du_staff": emp,
                                  "custom_client": client if typ != "Autre" else None, "nom_client": "Client %s" % code,
                                  "starts_on": "%s %s:00" % (self.JOUR, heure), "temps": "30 min", "status": "Open",
                                  "google_map": lien, "custom_tournee_fixe": fixe, "subject": "essai tournée %s" % code})
            doc.flags.ignore_permissions = True
            doc.insert()
            self.taches[code] = doc.name

    def tearDown(self):
        import frappe
        frappe.flags.tournee_sans_reseau = False
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
        self.assertTrue(v["fixe"] and v["employe"] == self.e2 and v["debut"] == "08:00")      # « Autre » : fixe
        self.assertEqual(arrets[self.taches["C"]]["employe"], self.e2)                          # C rejoint D à l'ouest
        self.assertEqual(arrets[self.taches["B"]]["employe"], self.e1)                          # B rejoint A à l'est
        self.assertLess(p["total"]["apres_km"], p["total"]["avant_km"])
        self.assertEqual(p["deplacees"], 2)

        plan = [{"tache": a["tache"], "employe": a["employe"], "starts_on": a["starts_on"], "ends_on": a["ends_on"]}
                for e in p["employes"] for a in e["apres"]["arrets"] if a["deplace"] or a["decale"]]
        r = T.appliquer(self.JOUR, plan)
        self.assertIn(self.taches["C"], r["modifiees"])
        t = frappe.get_doc("Tache de travail", self.taches["C"])
        self.assertEqual(t.custom_choix_du_staff, self.e2)
        self.assertEqual(str(t.starts_on)[11:16], arrets[self.taches["C"]]["debut"])
        self.assertEqual((t.ends_on - t.starts_on).total_seconds(), 30 * 60)
        self.assertTrue(frappe.db.exists("Comment", {"reference_name": t.name, "comment_type": "Comment", "content": ["like", "%Optimisation%"]}))
        # une seconde proposition sur la journée appliquée ne déplace plus rien
        p2 = T.proposer(self.JOUR)
        self.assertEqual(p2["deplacees"], 0)

    def test_reserve_aux_superviseurs(self):
        import frappe
        autre = frappe.db.get_value("User", {"enabled": 1, "name": ["not in", ["Administrator", "Guest"]]}, "name")
        if not autre or set(T.ROLES) & set(frappe.get_roles(autre)):
            self.skipTest("pas d'utilisateur simple")
        frappe.set_user(autre)
        with self.assertRaises(frappe.PermissionError):
            T.proposer(self.JOUR)
