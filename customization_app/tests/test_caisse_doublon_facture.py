"""Une facture fournisseur ne se saisit (et ne se paie) qu'une fois (24/11/2026).

Le 16/11/2026, CITERNES PLAST n° 2174 (87,537 DT) a été saisie par Jamel (FAS-1619) et par Sadok (FAS-1620) à
24 secondes d'écart, payée en espèces dans les deux caisses. Lancer dans le site (jamais `bench run-tests`) :
`unittest customization_app.tests.test_caisse_doublon_facture` depuis frappe-bench/sites, site initialisé."""
import unittest

from customization_app import caisse_depenses as C


class TestCleFacture(unittest.TestCase):
    def test_numero_normalise(self):
        for ecrit in ("2174", "N° 2174", "n°2174", "02174", " 2174 "):
            self.assertEqual(C.cle_numero_facture(ecrit), "2174", ecrit)
        self.assertEqual(C.cle_numero_facture("FA-2174"), C.cle_numero_facture("FA 2174"))
        self.assertNotEqual(C.cle_numero_facture("FA-2174"), C.cle_numero_facture("2174"))
        self.assertEqual(C.cle_numero_facture(""), "")

    def test_cle(self):
        self.assertEqual(C.cle_facture("Citernes Plast", "N° 2174"), "Citernes Plast|2174")
        self.assertIsNone(C.cle_facture(None, "2174"))
        self.assertIsNone(C.cle_facture("Citernes Plast", ""))


class TestDoublonDansLeSite(unittest.TestCase):
    """Dans le site : une fiche existante bloque la seconde saisie ; la clé unique bloque deux insertions."""

    def setUp(self):
        import frappe
        self.frappe = frappe
        self.supplier = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
        if not self.supplier:
            self.skipTest("aucun fournisseur")
        frappe.db.savepoint("doublon_facture")

    def tearDown(self):
        self.frappe.db.rollback(save_point="doublon_facture")

    def test_seconde_saisie_reconnue_et_cle_unique(self):
        frappe = self.frappe
        numero = "ESSAI-DOUBLON-7731"
        self.assertIsNone(C.facture_deja_saisie(numero, supplier=self.supplier))
        fiche = frappe.get_doc({"doctype": "Facture Achat a Saisir", "supplier": self.supplier, "fournisseur": self.supplier,
                                "numero_facture": numero, "montant": 10, "mode_paiement": "Espèces",
                                "cle_facture": C.cle_facture(self.supplier, numero)}).insert(ignore_permissions=True)
        deja = C.facture_deja_saisie("N° " + numero.lower(), supplier=self.supplier)
        self.assertEqual(deja["name"], fiche.name)
        self.assertIsNone(C.facture_deja_saisie(numero, supplier=self.supplier, sauf_fiche=fiche.name))   # correction
        with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
            frappe.get_doc({"doctype": "Facture Achat a Saisir", "supplier": self.supplier, "numero_facture": numero,
                            "montant": 10, "cle_facture": C.cle_facture(self.supplier, numero)}).insert(ignore_permissions=True)
