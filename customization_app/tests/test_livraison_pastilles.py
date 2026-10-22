"""Retour = livraison, et faits de livraison de la liste des commandes (22/10/2026)."""
from __future__ import annotations

import unittest

from customization_app.commande_alertes import pastilles_livraison
from customization_app.per_delivered_montant import couverte_par_retours


def _l(qty, livre, retour=0):
    return {"qty": qty, "delivered_qty": livre, "returned_qty": retour}


class TestRetourVautLivraison(unittest.TestCase):

    def test_electrovanne_retournee(self):
        # WEB1-007852 : 3 lignes livrées, l'électrovanne revenue
        self.assertTrue(couverte_par_retours([_l(1, 1), _l(2, 2), _l(1, 0, 1)]))

    def test_reste_jamais_livre_sans_retour(self):
        self.assertFalse(couverte_par_retours([_l(10, 8)]))                 # sel de nader ghodbane

    def test_retour_partiel_et_reste_du(self):
        self.assertFalse(couverte_par_retours([_l(3, 1, 1)]))               # 1 revenu, 1 jamais parti

    def test_sans_aucun_retour(self):
        self.assertFalse(couverte_par_retours([_l(2, 2)]))
        self.assertFalse(couverte_par_retours([]))


class TestPastillesLivraison(unittest.TestCase):

    def test_bl_en_brouillon(self):
        self.assertEqual(pastilles_livraison("To Deliver", "Partly Delivered", 88.2, 746, {"MAT-DN-2026-05005"}),
                         {"bl_a_valider": ["MAT-DN-2026-05005"]})

    def test_brouillon_doublon_sur_commande_livree(self):
        # SAL-ORD-2026-02990 : BL validé de 354 + brouillon de 354 → rien « à valider »
        self.assertEqual(pastilles_livraison("To Bill", "Fully Delivered", 100, 354, {"MAT-DN-2026-03549"}), {})

    def test_brouillon_de_retour_toujours_a_valider(self):
        self.assertEqual(pastilles_livraison("To Bill", "Fully Delivered", 100, 354, None, {"MAT-DN-RET"}),
                         {"bl_a_valider": ["MAT-DN-RET"]})

    def test_partielle_sans_bl_en_attente(self):
        self.assertEqual(pastilles_livraison("To Deliver and Bill", "Partly Delivered", 84.6, 330, None),
                         {"partielle": 85})
        self.assertEqual(pastilles_livraison("To Deliver and Bill", "Not Delivered", 0, 120, set()),
                         {"partielle": 0})

    def test_rien_a_signaler(self):
        self.assertEqual(pastilles_livraison("To Bill", "Fully Delivered", 100, 500, None), {})
        self.assertEqual(pastilles_livraison("To Deliver", "Not Applicable", 0, 50, None), {})
        self.assertEqual(pastilles_livraison("To Deliver", "Not Delivered", 0, 0, None), {})

    def test_commande_hors_circuit(self):
        self.assertEqual(pastilles_livraison("Closed", "Closed", 70, 742, {"MAT-DN-X"}), {})
        self.assertEqual(pastilles_livraison("Completed", "Fully Delivered", 100, 10, None), {})


class TestPastilleStockee(unittest.TestCase):
    """Le champ filtrable dit la même chose que les pastilles affichées (base réelle, rollback)."""

    def setUp(self):
        import frappe
        self.frappe = frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("pastille_livraison")

    def tearDown(self):
        self.frappe.db.rollback(save_point="pastille_livraison")

    def test_sql_et_affichage_concordent(self):
        from customization_app import commande_alertes as CA
        stockees = CA._calculer_pastille_livraison("so.docstatus = 1")
        ouvertes = self.frappe.get_all("Sales Order", filters={"docstatus": 1},
                                       fields=["name", "status", "delivery_status", "per_delivered", "grand_total"],
                                       limit_page_length=0)
        brouillons, retours = CA._bl_brouillons([o.name for o in ouvertes]) if ouvertes else ({}, {})
        libelle = {"bl_a_valider": CA.PASTILLE_BL_A_VALIDER, "partielle": CA.PASTILLE_PARTIELLE}
        for o in ouvertes:
            p = pastilles_livraison(o.status, o.delivery_status, o.per_delivered, o.grand_total,
                                    brouillons.get(o.name), retours.get(o.name))
            attendu = libelle[next(iter(p))] if p else ""
            self.assertEqual(stockees.get(o.name, ""), attendu, o.name)

    def test_un_bl_brouillon_fait_basculer_la_pastille(self):
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        from customization_app.commande_alertes import CHAMP_PASTILLE_LIVRAISON as CHAMP
        so = self.frappe.db.get_value("Sales Order", {CHAMP: "Pas totalement livrée"}, "name")
        if not so:
            self.skipTest("aucune commande « Pas totalement livrée » dans cette base")
        bl = make_delivery_note(so)
        bl.insert(ignore_permissions=True)                            # after_insert
        self.assertEqual(self.frappe.db.get_value("Sales Order", so, CHAMP), "BL à valider")
        bl.delete(ignore_permissions=True)                            # after_delete
        self.assertEqual(self.frappe.db.get_value("Sales Order", so, CHAMP), "Pas totalement livrée")


if __name__ == "__main__":
    unittest.main()
