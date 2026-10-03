"""Hook « a été appelé » (porté du Server Script « update donnee appelle », 03/10/2026)."""
from __future__ import annotations

import datetime
import json
import unittest

import frappe

from customization_app import liste_appels as LA


class TestPur(unittest.TestCase):
    def test_articles_appeles(self):
        self.assertEqual(LA.articles_appeles('{"A": "2026-04-03", "_autres": {"x": 1}}'), {"A": datetime.date(2026, 4, 3)})
        self.assertEqual(LA.articles_appeles("pas du json"), {})
        self.assertEqual(LA.articles_appeles(None), {})


class TestHook(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("liste_appels")
        row = frappe.db.sql("""select d.name, d.item_code, d.scheduled_date, ms.customer from `tabMaintenance Schedule Detail` d
                               join `tabMaintenance Schedule` ms on ms.name = d.parent
                               where ms.docstatus = 1 and d.custom_appelle is null and d.custom_1er_appel is null limit 1""", as_dict=True)
        if not row:
            self.skipTest("aucune visite non appelée en base")
        self.row = row[0]

    def tearDown(self):
        frappe.db.rollback(save_point="liste_appels")

    def test_marque_la_visite_et_les_cases_du_client(self):
        r = self.row
        frappe.db.set_value("Customer", r.customer, {"custom_intéressé_par_le_service_entretien": "Oui", "custom_envoi_sms": "Oui"})
        ligne = frappe._dict(client=r.customer, a_été_appelé=1, resume_appel=LA.NE_REPOND_PAS,
                             intéressé_par_le_service_dentretien="Non", intéressé_par_le_service_de_relance="Non",
                             detail_articles=json.dumps({r.item_code: str(r.scheduled_date)}))
        LA._synchroniser_ligne(ligne)
        v = frappe.db.get_value("Maintenance Schedule Detail", r.name, ["custom_appelle", "custom_1er_appel"], as_dict=True)
        self.assertEqual((str(v.custom_appelle), str(v.custom_1er_appel)), (frappe.utils.nowdate(), frappe.utils.nowdate()))
        # LES DEUX cases du client suivent (le script d'origine n'en changeait qu'une)
        self.assertEqual(frappe.db.get_value("Customer", r.customer, ["custom_intéressé_par_le_service_entretien", "custom_envoi_sms"]), ("Non", "Non"))
        # une visite déjà marquée garde sa date
        frappe.db.set_value("Maintenance Schedule Detail", r.name, "custom_appelle", "2026-01-15", update_modified=False)
        LA._synchroniser_ligne(ligne)
        self.assertEqual(str(frappe.db.get_value("Maintenance Schedule Detail", r.name, "custom_appelle")), "2026-01-15")
