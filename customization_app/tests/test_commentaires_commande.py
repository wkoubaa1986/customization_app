"""Dernier commentaire d'une commande (03/10/2026) : texte propre, traces automatiques ignorées, hook Comment."""
from __future__ import annotations

import unittest

import frappe

from customization_app import commentaires_commande as CC


class TestPur(unittest.TestCase):
    def test_texte_propre_et_automatiques(self):
        self.assertEqual(CC.texte_propre('<div class="ql-editor read-mode"><p>le client ne répond pas </p></div>'), "le client ne répond pas")
        self.assertEqual(len(CC.texte_propre("x" * 500)), 140)
        self.assertTrue(CC.est_automatique("SMS d'annulation envoyé à 1 numéro(s) : 21144306."))
        self.assertTrue(CC.est_automatique("📨 Envoi groupé SMS …"))
        self.assertFalse(CC.est_automatique("rappeler le client demain"))


class TestHook(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("commentaires_commande")
        if not CC.champs_presents():
            self.skipTest("champs absents (patch non joué)")
        self.so = frappe.db.get_value("Sales Order", {"docstatus": 1}, "name", order_by="creation desc")

    def tearDown(self):
        frappe.db.rollback(save_point="commentaires_commande")

    def _commenter(self, texte):
        return frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Sales Order",
                               "reference_name": self.so, "content": texte}).insert(ignore_permissions=True)

    def test_le_dernier_commentaire_humain_est_recopie(self):
        c1 = self._commenter("<p>le client ne répond pas</p>")
        v = frappe.db.get_value("Sales Order", self.so, ["custom_dernier_commentaire", "custom_avec_commentaire", "custom_commentaire_par"], as_dict=True)
        self.assertEqual((v.custom_dernier_commentaire, v.custom_avec_commentaire), ("le client ne répond pas", 1))
        self.assertTrue(v.custom_commentaire_par)
        self._commenter("SMS d'annulation envoyé à 1 numéro(s) : 21144306.")          # trace automatique : ignorée
        self.assertEqual(frappe.db.get_value("Sales Order", self.so, "custom_dernier_commentaire"), "le client ne répond pas")
        c2 = self._commenter("rappeler demain matin")
        self.assertEqual(frappe.db.get_value("Sales Order", self.so, "custom_dernier_commentaire"), "rappeler demain matin")
        c2.delete(ignore_permissions=True)                                              # suppression → le précédent revient
        self.assertEqual(frappe.db.get_value("Sales Order", self.so, "custom_dernier_commentaire"), "le client ne répond pas")
        c1.delete(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Sales Order", self.so, ["custom_dernier_commentaire", "custom_avec_commentaire"]), (None, 0))
