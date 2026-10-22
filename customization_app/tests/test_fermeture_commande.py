"""Commande fermée = statut de livraison « Closed » (22/10/2026) : elle quitte « Livré en Partie »."""
from __future__ import annotations

import unittest

import frappe

from customization_app import fermeture_commande as FC


class TestStatutLivraison(unittest.TestCase):

    def test_fermee_sans_tout_livrer(self):
        self.assertEqual(FC.statut_livraison("Closed", 70.8, "Partly Delivered"), "Closed")
        self.assertEqual(FC.statut_livraison("Closed", 0, "Not Delivered"), "Closed")

    def test_fermee_entierement_livree_garde_son_statut(self):
        self.assertEqual(FC.statut_livraison("Closed", 100, "Fully Delivered"), "Fully Delivered")

    def test_regularisee_sur_le_livre_quitte_livre_en_partie(self):
        # SAL-ORD-2026-03111 / 02915 : 100 % après update_child_qty_rate, statut resté partiel
        self.assertEqual(FC.statut_livraison("To Bill", 100, "Partly Delivered"), "Fully Delivered")

    def test_rien_a_livrer_jamais_reecrit(self):
        self.assertEqual(FC.statut_livraison("To Bill", 0, "Not Applicable"), "Not Applicable")

    def test_reouverte_se_recalcule(self):
        self.assertEqual(FC.statut_livraison("To Deliver", 70.8, "Closed"), "Partly Delivered")
        self.assertEqual(FC.statut_livraison("To Deliver and Bill", 0, "Closed"), "Not Delivered")
        self.assertEqual(FC.statut_livraison("To Bill", 100, "Closed"), "Fully Delivered")

    def test_ouverte_inchangee(self):
        self.assertEqual(FC.statut_livraison("To Deliver", 50, "Partly Delivered"), "Partly Delivered")


class TestFermerRouvrir(unittest.TestCase):

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("fermeture_commande")
        self.so = frappe.db.get_value("Sales Order", {
            "docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled"]],
            "per_delivered": [">", 0], "delivery_status": "Partly Delivered"}, "name")
        if not self.so:
            self.skipTest("aucune commande livrée en partie dans cette base")

    def tearDown(self):
        frappe.db.rollback(save_point="fermeture_commande")

    def _etat(self):
        return frappe.db.get_value("Sales Order", self.so, ["status", "delivery_status"])

    def test_fermer_puis_rouvrir_depuis_la_fiche(self):
        FC.update_status("Closed", self.so)
        self.assertEqual(self._etat(), ("Closed", "Closed"))
        FC.update_status("Draft", self.so)
        statut, livraison = self._etat()
        self.assertNotEqual(statut, "Closed")
        self.assertEqual(livraison, "Partly Delivered")

    def test_action_groupee_de_la_liste(self):
        FC.close_or_unclose_sales_orders(frappe.as_json([self.so]), "Closed")
        self.assertEqual(self._etat(), ("Closed", "Closed"))


if __name__ == "__main__":
    unittest.main()
