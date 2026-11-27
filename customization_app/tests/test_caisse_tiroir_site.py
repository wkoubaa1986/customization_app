"""Le tiroir unique du collecteur principal — essai SUR LE SITE, sans trace (commit neutralisé, tout annulé à la fin).
Le circuit : la direction vérifie le tiroir, le collecteur verse au coffre, puis ouvre son comptage."""
import unittest
from unittest import mock

import frappe

from customization_app import argent_liquide as A
from customization_app import caisse_cloture as CL
from customization_app import caisse_regularisations as R
from customization_app import caisse_tiroir as CT


class TestTiroirSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not CT.en_service():
            self.skipTest("migration pas passée")
        self.titulaire, self.caisse = CT.principal()
        if not self.titulaire or not self.caisse:
            self.skipTest("aucun collecteur principal dans Config Caisse")
        frappe.db.savepoint("tiroir")
        self.commit = mock.patch.object(frappe.db, "commit", lambda *a, **k: None)
        self.commit.start()

    def tearDown(self):
        frappe.set_user("Administrator")
        self.commit.stop()
        frappe.db.rollback(save_point="tiroir")

    def _vers_le_coffre(self, montant):
        je = frappe.get_doc({"doctype": "Journal Entry", "posting_date": frappe.utils.nowdate(),
                             "company": frappe.defaults.get_global_default("company"), "user_remark": "Recette du jour",
                             "accounts": [{"account": A.COMPTE_SABAH, "debit_in_account_currency": montant},
                                          {"account": A.COMPTE_ESPECES, "credit_in_account_currency": montant}]})
        je.flags.ignore_permissions = True
        je.insert()
        je.submit()
        return je.name

    def test_la_caisse_du_collecteur_est_son_tiroir(self):
        self.assertTrue(CT.est_tiroir(self.caisse))
        self.assertFalse(CT.est_tiroir(CL.CAISSE_GLOBALE))
        self.assertFalse(CT.est_tiroir("Une caisse qui n'existe pas"))

    def test_verification_puis_versement_puis_comptage(self):
        avant = CT.etat_tiroir()
        v = R.inventorier("1500", "Compté par la direction avec lui, billets et pièces.")
        self.assertEqual(v["theorique"], avant["attendu"])
        t = CT.etat_tiroir()
        self.assertEqual((t["attendu"], t["mode"], t["base"]["name"]), (1500.0, "chaine", v["name"]))
        self.assertEqual(t["verification"]["name"], v["name"])            # comptée par un autre que lui : la preuve
        self.assertEqual(t["depuis"], {"collecte": 0.0, "sorties": 0.0, "autres": 0.0, "delta": 0.0})
        # Il verse 400 au coffre : son tiroir doit en contenir 1 100.
        frappe.set_user(self.titulaire)
        self._vers_le_coffre(400)
        frappe.set_user("Administrator")
        t = CT.etat_tiroir()
        self.assertEqual((t["attendu"], t["depuis"]["sorties"], t["depuis"]["autres"]), (1100.0, 400.0, 0.0))
        self.assertEqual(t["ecart_compte"], round(1100.0 - t["selon_compte"], 3))
        # La page dit la même chose, et sa ligne d'employé n'a plus de fond à part.
        s = A.situation()
        self.assertEqual(s["principal"]["especes"], 1100.0)
        self.assertEqual(next(e for e in s["employes"] if e["caisse"] == self.caisse)["total"], 0.0)
        self.assertEqual(next(x for x in s["collecteurs"] if x.get("principal"))["detient"], 1100.0)
        self.assertNotIn("_entrees_tiroir", next(x for x in s["collecteurs"] if x.get("principal")))
        # Son comptage du jour attend ce montant (s'il n'a pas déjà compté aujourd'hui sur ce site).
        if frappe.db.exists("Cloture Caisse", {"caisse": self.caisse, "date_cloture": frappe.utils.nowdate(), "docstatus": 1}):
            return
        frappe.set_user(self.titulaire)
        e = CL.etat(self.caisse, frappe.utils.nowdate())
        self.assertEqual(e["solde_theorique"], 1100.0)
        self.assertEqual((e["tiroir"]["mode"], e["tiroir"]["base"]["compte"], e["tiroir"]["depuis"]["sorties"]),
                         ("chaine", 1500.0, 400.0))

    def test_la_caisse_d_un_employe_ne_change_pas(self):
        autre = next((e["caisse"] for e in A.situation()["employes"] if e["caisse"] != self.caisse), None)
        if not autre:
            self.skipTest("aucune autre caisse")
        self.assertIsNone(CL._tiroir(autre))
        self.assertEqual(CL._champs_tiroir(CT.etat_tiroir())["tiroir_unique"], 1)


if __name__ == "__main__":
    unittest.main()
