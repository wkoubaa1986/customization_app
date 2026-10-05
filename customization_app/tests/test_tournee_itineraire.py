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
