"""Nouveau technicien — essai SUR LE SITE (copie de prod), sans trace : commit neutralisé, tout annulé.
Le 26/11/2026, le téléphone de Radhouan Sifi saisi pour un nouveau technicien faisait échouer la création sous un message
trompeur (« n'a aucun rôle activé », puis « Mobile No doit être unique »)."""
import unittest
from unittest import mock

import frappe

from customization_app import arrivee_employe as A

EMAIL = "essai.technicien.site@example.com"


class TestNouveauTechnicienSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("tech")
        self.commit = mock.patch.object(frappe.db, "commit", lambda *a, **k: None)
        self.commit.start()

    def tearDown(self):
        self.commit.stop()
        frappe.db.rollback(save_point="tech")

    def test_un_telephone_deja_pris_est_refuse_avec_le_nom(self):
        pris = frappe.db.get_value("User", {"mobile_no": ["is", "set"], "enabled": 1}, ["mobile_no", "full_name"], as_dict=True)
        if not pris:
            self.skipTest("aucun utilisateur avec un mobile sur cette copie")
        with self.assertRaises(frappe.ValidationError) as e:
            A.creer_technicien(nom="Essai Technicien Site", email=EMAIL, mot_de_passe="Aqua-Essai-2026!x", telephone=pris.mobile_no)
        self.assertIn(pris.full_name, str(e.exception))
        self.assertFalse(frappe.db.exists("User", EMAIL))

    def test_les_roles_sont_donnes_a_la_creation(self):
        r = A.creer_technicien(nom="Essai Technicien Site", email=EMAIL, mot_de_passe="Aqua-Essai-2026!x", telephone="20999111",
                               date_entree=frappe.utils.nowdate())
        self.assertTrue(set(A.ROLES_TECHNICIEN) <= set(frappe.get_roles(EMAIL)))
        self.assertEqual(frappe.db.get_value("User", EMAIL, "user_type"), "System User")
        self.assertEqual(frappe.db.get_value("Employee", r["employe"], "user_id"), EMAIL)
        # Le message trompeur de Frappe n'est plus émis.
        self.assertFalse(any("aucun rôle" in str(m) or "no Role" in str(m) for m in (frappe.local.message_log or [])))


if __name__ == "__main__":
    unittest.main()
