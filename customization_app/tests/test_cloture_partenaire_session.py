"""`_en_systeme` rend la session de l'utilisateur réel INTACTE.

⚠️ LE BUG : `frappe.set_user` vide `local.session.data`, et frappe/app.py réécrit cet objet
dans le cache Redis en fin de requête. La session du partenaire était persistée sans `user` :
sa requête suivante tombait sur « User None is disabled » (417) jusqu'à expiration — vu en prod
le 05/09/2026 après une clôture de tâche.
"""
import unittest

import frappe

from customization_app.cloture_partenaire import _en_systeme


class TestEnSystemePreserveLaSession(unittest.TestCase):
    def setUp(self):
        self.session = frappe.local.session
        self.sauvegarde = (self.session.user, self.session.sid, self.session.data,
                           frappe.local.form_dict)

    def tearDown(self):
        (self.session.user, self.session.sid, self.session.data,
         frappe.local.form_dict) = self.sauvegarde
        frappe.set_user(self.session.user)
        self.session.sid, self.session.data, frappe.local.form_dict = self.sauvegarde[1:]

    def test_la_session_revient_a_l_identique(self):
        self.session.user = "partenaire@example.com"
        self.session.sid = "abc123sid"
        donnees = frappe._dict(user="partenaire@example.com", csrf_token="jeton",
                               session_ip="10.0.0.1")
        self.session.data = donnees
        frappe.local.form_dict = frappe._dict(tache="TACHE-1")

        with _en_systeme():
            self.assertEqual(frappe.session.user, "Administrator")

        self.assertEqual(frappe.session.user, "partenaire@example.com")
        self.assertIs(self.session.data, donnees, "le dict de session persisté en cache")
        self.assertEqual(self.session.data.user, "partenaire@example.com")
        self.assertEqual(self.session.data.csrf_token, "jeton")
        self.assertEqual(self.session.sid, "abc123sid")
        self.assertEqual(frappe.local.form_dict.tache, "TACHE-1")

    def test_meme_quand_le_bloc_leve(self):
        self.session.user = "partenaire@example.com"
        donnees = frappe._dict(user="partenaire@example.com")
        self.session.data = donnees
        with self.assertRaises(ValueError):
            with _en_systeme():
                raise ValueError("boum")
        self.assertEqual(frappe.session.user, "partenaire@example.com")
        self.assertIs(self.session.data, donnees)
