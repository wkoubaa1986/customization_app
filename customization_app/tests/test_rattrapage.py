"""Liste Appels Rattrapage — client injoignable (05/10/2026) : le 1er appel sans réponse le laisse dans la
liste, le 2e l'en sort. Le rapport lui-même (son SQL en base) est exécuté avant / après chaque geste."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from customization_app import api

CLIENT = "ESSAI RATTRAPAGE"
AUTRE = "ESSAI RATTRAPAGE 2"


class TestAppelSansReponse(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("rattrapage")
        hier = frappe.utils.add_days(frappe.utils.nowdate(), -1)
        avant = frappe.utils.add_days(frappe.utils.nowdate(), -8)
        self.t1 = self._tache("ESSAI-RATT-1", CLIENT, f"{avant} 10:00:00")
        self.t2 = self._tache("ESSAI-RATT-2", CLIENT, f"{hier} 10:00:00")     # 2e rendez-vous manqué du même client
        self.t3 = self._tache("ESSAI-RATT-3", AUTRE, f"{hier} 11:00:00")

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="rattrapage")

    def _rattrapee(self, tache, raison):
        """marquer_tache_rattrapee COMMITE (appel direct du navigateur) : neutralisé, sinon le savepoint du test saute."""
        with patch("frappe.db.commit"):
            api.marquer_tache_rattrapee(tache, raison)

    def _tache(self, name, client, starts_on):
        import frappe
        frappe.get_doc({"doctype": "Tache de travail", "name": name, "custom_client": client, "nom_client": client,
                        "subject": "Entretien osmoseur", "custom_type_dintervention": "Entretien",
                        "status": "Cancelled", "raison_annulation": "Client non joignable", "starts_on": starts_on}).db_insert()
        return name

    def _liste(self) -> dict:
        import frappe
        _colonnes, lignes = frappe.get_doc("Report", "Liste Appels Rattrapage").execute_script_report({})[:2]
        return {r["name"]: r for r in lignes if r["name"].startswith("ESSAI-RATT-")}

    def test_premier_appel_reste_deuxieme_sort(self):
        import frappe
        self.assertEqual(set(self._liste()), {self.t1, self.t2, self.t3})
        r = api.appel_sans_reponse([self.t1, self.t2])                       # les deux cartes du client
        self.assertEqual((r["recontacte"], r["sort"]), (api.RECONTACTE_1ER_APPEL, False))
        liste = self._liste()
        self.assertEqual(set(liste), {self.t1, self.t2, self.t3})              # toujours dans la liste
        self.assertEqual(liste[self.t1]["recontacte"], api.RECONTACTE_1ER_APPEL)
        self.assertTrue(liste[self.t1]["recontacte_le"])
        self.assertEqual(liste[self.t1]["recontacte_par"], frappe.utils.get_fullname("Administrator"))
        self.assertTrue(frappe.db.exists("Comment", {"reference_name": self.t1, "content": ["like", "%1er appel sans réponse%"]}))

        r = api.appel_sans_reponse([self.t1, self.t2])
        self.assertEqual((r["recontacte"], r["sort"]), (api.RECONTACTE_2E_APPEL, True))
        self.assertEqual(set(self._liste()), {self.t3})                        # le client sort, l'autre reste
        self.assertEqual(frappe.db.get_value("Tache de travail", self.t2, "recontacte"), api.RECONTACTE_2E_APPEL)

    def test_rendez_vous_repris_apres_un_premier_appel(self):
        import frappe
        api.appel_sans_reponse([self.t3])
        self._rattrapee(self.t3, "planifié")                       # au 2e appel, il a répondu
        self.assertNotIn(self.t3, self._liste())
        self.assertEqual(frappe.db.get_value("Tache de travail", self.t3, ["recontacte", "recontacte_par"]),
                         ("planifié", "Administrator"))

    def test_refus(self):
        import frappe
        with self.assertRaises(frappe.ValidationError):
            api.appel_sans_reponse([self.t1, self.t3])                         # deux clients à la fois
        self._rattrapee(self.t3, "Investigué")
        with self.assertRaises(frappe.ValidationError):
            api.appel_sans_reponse([self.t3])                                  # déjà sortie de la liste
        with self.assertRaises(frappe.ValidationError):
            api.appel_sans_reponse([])
        self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(api.appel_sans_reponse), ["POST"])

    def test_sans_droit_d_ecriture(self):
        import frappe
        sans_droit = next((u for u in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User",
                                                                      "name": ["not in", ["Administrator", "Guest"]]}, pluck="name")
                           if not frappe.has_permission("Tache de travail", "write", user=u)), None)
        if not sans_droit:
            self.skipTest("tous les utilisateurs peuvent modifier les tâches")
        frappe.set_user(sans_droit)
        with self.assertRaises(frappe.PermissionError):
            api.appel_sans_reponse([self.t1])
