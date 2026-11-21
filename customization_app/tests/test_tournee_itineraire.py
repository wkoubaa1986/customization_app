"""Itinéraire d'une journée PLANIFIÉE (Ma journée, Générer les BL) — sans réseau ni Google (positions dans les liens)."""
from __future__ import annotations

import unittest

from customization_app import tournee_itineraire as I

AKRAM = "morchediakram0@gmail.com"          # employé simple, pas superviseur


class TestItineraire(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("itineraire")
        frappe.flags.tournee_sans_reseau = True      # distances à vol d'oiseau, aucun appel Google
        self.akram = frappe.db.get_value("Employee", {"user_id": AKRAM, "status": "Active"}, "name")
        if not self.akram:
            self.skipTest("employé d'essai absent")
        self.jour = frappe.utils.add_days(frappe.utils.nowdate(), 30)
        for i, (h, lat) in enumerate(((9, 36.85), (11, 36.90))):
            frappe.get_doc({"doctype": "Tache de travail", "name": f"ESSAI-ITI-{i}", "custom_choix_du_staff": self.akram,
                            "custom_client": f"Client {i}", "nom_client": f"Client {i}", "custom_type_dintervention": "Entretien",
                            "status": "Open", "starts_on": f"{self.jour} {h:02d}:00:00", "ends_on": f"{self.jour} {h:02d}:30:00",
                            "google_map": f"https://www.google.com/maps/place/x/data=!3d{lat}!4d10.25"}).db_insert()

    def tearDown(self):
        import frappe
        frappe.flags.tournee_sans_reseau = False
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="itineraire")

    def test_journee_planifiee(self):
        r = I.itineraire_employe(self.jour, self.akram)
        e = r["employes"][0]
        essais = [a for a in e["apres"]["arrets"] if a["tache"].startswith("ESSAI-ITI-")]
        self.assertEqual([(a["debut"], a["fin"], a["trajet_source"]) for a in essais], [("09:00", "09:30", "osrm"), ("11:00", "11:30", "osrm")])
        self.assertTrue(e["horaires"]["depart"] and e["horaires"]["retour"] and e["horaires"]["km"] > 0)
        self.assertEqual(r["recalage"]["raison"], "sans réseau")
        self.assertNotIn("noeud", essais[0])                                     # rien d'interne ne sort

    def test_un_employe_ne_voit_que_le_sien(self):
        import frappe
        autre = frappe.db.get_value("Employee", {"status": "Active", "name": ["!=", self.akram]}, "name")
        frappe.set_user(AKRAM)
        self.assertEqual(I.itineraire_employe(self.jour)["employes"][0]["employe"], self.akram)   # sans paramètre : le sien
        with self.assertRaises(frappe.PermissionError):
            I.itineraire_employe(self.jour, autre)
        self.assertEqual(I.itineraires_du_jour(self.jour)["employes"], [])                       # résumé BL : superviseurs


class TestLibelleSansClient(unittest.TestCase):
    """Le nom d'une étape sans client dans l'itinéraire : la première ligne utile de son titre."""

    def test_une_verification_de_stock_porte_son_nom(self):
        self.assertEqual(I.libelle_sans_client("🧾 Vérification stock\nStock Sadok Bouziri\nSadok Bouziri"), "🧾 Vérification stock")

    def test_le_mot_client_et_le_statut_ne_sont_pas_un_nom(self):
        self.assertEqual(I.libelle_sans_client("\n☕ Autre: Client: \nSalma Ben Saïd"), "☕ Autre")
        self.assertEqual(I.libelle_sans_client("terminé!!!\n☕ Autre: Client: \nHedi"), "☕ Autre")

    def test_sans_titre_rien(self):
        self.assertEqual(I.libelle_sans_client(None), "")
        self.assertEqual(I.libelle_sans_client("  \n "), "")


class TestPassageMagasin(unittest.TestCase):
    """Domicile → Magasin → clients → Magasin → domicile, pour qui garde la voiture chez lui (22/11/2026)."""

    def test_matin_et_soir_autour_de_la_tournee(self):
        # il quitte le Magasin à 09:06 pour le 1er client et y revient à 17:46 ; 25 min / 16,6 km de chez lui, 28 min / 17,6 km pour rentrer
        p = I.passage_magasin(9 * 60 + 6, 17 * 60 + 46, False, False, (25, 16.6), (28, 17.6), 15, 10)
        self.assertEqual((p["arrivee_magasin"], p["depart_domicile"]), (8 * 60 + 51, 8 * 60 + 26))     # 15 min sur place
        self.assertEqual((p["fin_magasin"], p["retour_domicile"]), (17 * 60 + 56, 18 * 60 + 24))       # 10 min sur place
        self.assertEqual((p["aller_km"], p["rentree_km"], p["sur_place_matin"], p["sur_place_soir"]), (16.6, 17.6, 15, 10))

    def test_la_premiere_tache_au_magasin_ne_rajoute_pas_de_temps_sur_place(self):
        # vérification de stock à 08:45 au Magasin : il y arrive pour 08:45, pas un quart d'heure avant
        p = I.passage_magasin(8 * 60 + 45, 17 * 60 + 4, True, False, (25, 16.6), (28, 17.6), 15, 10)
        self.assertEqual((p["arrivee_magasin"], p["depart_domicile"], p["sur_place_matin"]), (8 * 60 + 45, 8 * 60 + 20, 0))
        p = I.passage_magasin(9 * 60, 17 * 60, False, True, (20, 10), (20, 10), 15, 10)
        self.assertEqual((p["fin_magasin"], p["sur_place_soir"]), (17 * 60, 0))                        # la dernière aussi

    def test_la_majoration_des_heures_de_pointe_s_applique_au_depart_de_chaque_trajet(self):
        pointe = lambda minute: 1.3 if 7 * 60 + 30 <= minute < 9 * 60 + 30 else 1.0
        p = I.passage_magasin(9 * 60, 15 * 60, False, False, (20, 10), (20, 10), 15, 10, pointe)
        self.assertEqual((p["aller_min"], p["rentree_min"]), (26, 20))                                 # matin en pointe, soir non
        self.assertEqual(p["depart_domicile"], 9 * 60 - 15 - 26)
