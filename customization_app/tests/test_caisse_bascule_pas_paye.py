"""Bascule d'une fiche de caisse « Espèces / Chèque / … » vers « Pas payé » (erreur de saisie).

L'intégration s'appuie sur une fiche réelle du site (avance ou paiement encore désigné) ;
tout est annulé par savepoint, rien n'est conservé.
"""
import unittest

import frappe


class TestResume(unittest.TestCase):
    def test_trace(self):
        from customization_app.caisse_depenses import resume_bascule
        t = resume_bascule("Espèces", ["ACC-JV-1"], [])
        self.assertIn("Espèces", t)
        self.assertIn("ACC-JV-1", t)
        self.assertNotIn("paiement", t)
        t = resume_bascule("Chèque", [], ["ACC-PAY-1", "ACC-PAY-2"])
        self.assertIn("ACC-PAY-1, ACC-PAY-2", t)
        self.assertIn("—", resume_bascule(None, [], []))

    def test_pieces_de_fiche(self):
        from customization_app.caisse_depenses import _pieces_de_fiche
        f = frappe._dict(journal_entry="J1", journal_entries='["J1", "J2"]',
                         payment_entry="", payment_entries="pas du json")
        self.assertEqual(_pieces_de_fiche(f), (["J1", "J2"], []))


class TestBascule(unittest.TestCase):
    def test_bascule_puis_refus(self):
        from customization_app.caisse_depenses import basculer_pas_paye, factures_a_payer

        ancien = frappe.session.user
        frappe.set_user("Administrator")
        frappe.db.savepoint("bascule")
        try:
            nom = frappe.db.get_value(
                "Facture Achat a Saisir",
                {"mode_paiement": ["not in", ["Pas payé", "Traite bancaire"]],
                 "payment_entry": ["!=", ""]}, "name") or frappe.db.get_value(
                "Facture Achat a Saisir",
                {"mode_paiement": ["!=", "Pas payé"], "journal_entry": ["!=", ""]}, "name")
            if not nom:
                self.skipTest("Une fiche de caisse payée (avance ou paiement) est nécessaire.")
            r = basculer_pas_paye(nom)
            self.assertEqual(frappe.db.get_value("Facture Achat a Saisir", nom, "mode_paiement"), "Pas payé")
            for je in r["ecritures"]:
                self.assertFalse(frappe.db.exists("Journal Entry", je))
            for pe in r["paiements"]:
                self.assertEqual(frappe.db.get_value("Payment Entry", pe, "docstatus"), 2)
            a_payer = factures_a_payer()
            if r["purchase_invoice"]:
                self.assertGreater(frappe.db.get_value("Purchase Invoice", r["purchase_invoice"], "outstanding_amount"), 0)
                self.assertIn(r["purchase_invoice"], [x.name for x in a_payer["factures"]])
            else:
                self.assertIn(nom, [x.name for x in a_payer["fiches"]])
            with self.assertRaises(frappe.ValidationError):
                basculer_pas_paye(nom)
        finally:
            frappe.db.rollback(save_point="bascule")
            frappe.set_user(ancien)
