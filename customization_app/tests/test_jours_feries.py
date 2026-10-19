"""Jours fériés : calendrier, avertissement tâche, portail RDV fermé sauf réglage. Base de dev (liste « Vacance »)."""
from __future__ import annotations

import unittest


class TestJoursFeries(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("feries")
        from customization_app import jours_feries as JF
        self.JF = JF
        if not JF.liste_par_defaut():
            self.skipTest("pas de Holiday List par défaut")
        # Un férié sûr pour le test : on l'ajoute à la liste de la société (annulé au rollback).
        liste = frappe.get_doc("Holiday List", JF.liste_par_defaut())
        if not any(str(h.holiday_date) == "2026-12-24" for h in liste.holidays):
            liste.append("holidays", {"holiday_date": "2026-12-24", "description": "Férié de test", "weekly_off": 0})
            liste.save(ignore_permissions=True)

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="feries")

    def test_liste_et_est_ferie(self):
        f = self.JF.feries("2026-12-20", "2026-12-31")
        self.assertEqual(f.get("2026-12-24"), "Férié de test")
        self.assertEqual(self.JF.est_ferie("2026-12-24"), "Férié de test")
        self.assertIsNone(self.JF.est_ferie("2026-12-23"))
        self.assertNotIn("2026-12-27", f)                          # dimanche = repos hebdo, pas un férié

    def test_calendrier(self):
        from customization_app.api import get_custom_tache_events
        ev = [e for e in get_custom_tache_events("2026-12-20", "2026-12-31") if e.get("ferie")]
        self.assertEqual([(e["name"], e["titre"], e["all_day"]) for e in ev], [("FERIE-2026-12-24", "🎌 Férié de test", 1)])

    def test_avertissement_tache(self):
        import frappe
        doc = frappe.new_doc("Tache de travail")
        doc.update({"starts_on": "2026-12-24 09:00:00", "ends_on": "2026-12-24 10:00:00"})
        self.assertIn("Férié de test", self.JF.message_ferie(doc) or "")
        doc.starts_on = "2026-12-23 09:00:00"
        self.assertIsNone(self.JF.message_ferie(doc))

    def test_portail_ferme_sauf_reglage(self):
        from frappe.utils import getdate
        from customization_app import portail_rdv_planning as P
        config = {"autoriser_dimanche": 0, "nb_employes": 2, "accepter_feries": 0}
        liste = ["HR-EMP-00001", "HR-EMP-00002"]
        self.assertEqual(P._pool_du_jour(config, liste, {}, getdate("2026-12-24")), [])
        self.assertTrue(P._pool_du_jour(config, liste, {}, getdate("2026-12-23")))
        config["accepter_feries"] = 1
        self.assertTrue(P._pool_du_jour(config, liste, {}, getdate("2026-12-24")))
        # Zone partenaire : fermée aussi un férié.
        contexte = {"employes": ["HR-EMP-00001"], "dimanche": True}
        config["accepter_feries"] = 0
        self.assertEqual(P._pool_du_jour(config, liste, {}, getdate("2026-12-24"), contexte=contexte), [])
