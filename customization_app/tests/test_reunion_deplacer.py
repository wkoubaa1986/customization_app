"""Réunion du Suivi d'activité glissée sur un autre jour depuis le calendrier (02/11/2026)."""
import unittest

from frappe.utils import add_days, getdate, nowdate

from customization_app import reunion_activite as R
from customization_app import suivi_activite as SA


class TestDeplacerReunion(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("reunion_deplacer")
        employes = [e["name"] for e in SA.employes_parametres()][:2]
        if not employes:
            self.skipTest("aucun employé paramétré dans le Suivi d'activité")
        self.date = add_days(nowdate(), 40)
        self.doc = R.creer_reunion(self.date, "10:00:00", employes=employes, titre="Essai déplacement")

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="reunion_deplacer")

    def test_prevue_bouge_tenue_non(self):
        import frappe
        r = R.deplacer(self.doc.name, add_days(self.date, 3), "14:30:00")
        self.assertEqual((r["date"], r["heure"][:5]), (str(getdate(add_days(self.date, 3))), "14:30"))
        self.assertEqual(str(frappe.db.get_value(R.DOCTYPE, self.doc.name, "date")), str(getdate(add_days(self.date, 3))))
        R.deplacer(self.doc.name, add_days(self.date, 5))                   # sans heure : l'heure reste 14:30
        self.assertEqual(str(frappe.db.get_value(R.DOCTYPE, self.doc.name, "heure"))[:5], "14:30")
        frappe.db.set_value(R.DOCTYPE, self.doc.name, "statut", "Tenue")
        with self.assertRaises(frappe.ValidationError):
            R.deplacer(self.doc.name, add_days(self.date, 6))
