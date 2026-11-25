"""La mémoire des articles — essai SUR LE SITE, sans trace (commit neutralisé, tout annulé à la fin).
Rejoue la facture MH2C F26000463 contre la vraie fiche FAS-0319 (réception MAT-PRE-2026-00007 : BRS-SP-15-12 ×5)."""
import unittest
from unittest import mock

import frappe

from customization_app import correspondance_articles as CA
from customization_app.tests.test_caisse_bl_site import FICHE, MH2C, RECU, _creer

LIGNE_BL = {"code": "2030042", "designation": 'REDUCTION LAITON SPECIAL 1/2"F*3/8"M', "bl": "B26001723",
            "bl_cle": "B26001723", "qty": 5, "item_code": "tuy-02"}
OUTIL = {"code": "9910019", "designation": "PINCE COUPANTE 160MM TOTAL", "bl": "B26001620", "bl_cle": "B26001620", "qty": 1}


class TestMemoireSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if frappe.db.get_value("Facture Achat a Saisir", FICHE, "statut") != "À saisir":
            self.skipTest("FAS-0319 n'est plus le BL ouvert de MH2C sur ce site")
        frappe.db.savepoint("memoire")
        self.commit = mock.patch.object(frappe.db, "commit", lambda *a, **k: None)
        self.commit.start()
        self.autre = frappe.db.get_value("Item", {"is_stock_item": 1, "disabled": 0, "has_variants": 0,
                                                 "name": ["not in", ["BRS-SP-15-12"]]}, "name")

    def tearDown(self):
        self.commit.stop()
        frappe.db.rollback(save_point="memoire")

    def test_apprendre_confirmer_corriger(self):
        n = CA.apprendre(MH2C, "2030042", LIGNE_BL["designation"], "BRS-SP-15-12", CA.PAR_BL, "essai")
        self.assertEqual(CA.proposer(MH2C, "2030042", None)["item_code"], "BRS-SP-15-12")
        self.assertEqual(CA.apprendre(MH2C, " 2030042", "autre libellé", "BRS-SP-15-12"), n)        # même code
        self.assertEqual(frappe.db.get_value(CA.DOCTYPE, n, "fois"), 2)
        # Un simple choix (devinette acceptée, mémoire jamais montrée) n'écrase pas : le désaccord est noté.
        self.assertIsNone(CA.apprendre(MH2C, "2030042", None, self.autre, CA.CHOIX))
        c = frappe.db.get_value(CA.DOCTYPE, n, ["item_code", "fois", "historique"], as_dict=True)
        self.assertEqual((c.item_code, c.fois), ("BRS-SP-15-12", 2))
        self.assertIn("correspondance gardée, à vérifier", c.historique)
        CA.apprendre(MH2C, "2030042", None, self.autre, CA.CORRECTION, remplacer=True)              # vraie correction
        c = frappe.db.get_value(CA.DOCTYPE, n, ["item_code", "fois", "source", "historique"], as_dict=True)
        self.assertEqual((c.item_code, c.fois, c.source), (self.autre, 1, CA.CORRECTION))
        self.assertIn("BRS-SP-15-12 (confirmé 2 fois) remplacé par", c.historique)
        # La « Réf. fournisseur » d'ERPNext suit (et quitte l'ancien article).
        self.assertEqual(frappe.db.get_value("Item Supplier", {"parent": self.autre, "supplier": MH2C}, "supplier_part_no"), "2030042")
        self.assertFalse(frappe.db.get_value("Item Supplier", {"parent": "BRS-SP-15-12", "supplier": MH2C}, "supplier_part_no"))
        # Sans code : la désignation sert de clé, et on la retrouve écrite autrement.
        CA.apprendre(MH2C, None, "Raccord laiton 1/2 x 3/8", "BRS-SP-15-12")
        self.assertEqual(CA.proposer(MH2C, None, "RACCORD LAITON 1/2 X 3/8")["item_code"], "BRS-SP-15-12")
        self.assertIsNone(CA.proposer(MH2C, "999999", "inconnu"))
        self.assertIsNone(CA.apprendre(MH2C, "1", "x", "ARTICLE-QUI-N-EXISTE-PAS"))

    def test_lignes_stock_correction_seulement_si_vue(self):
        CA.apprendre(MH2C, "2030042", LIGNE_BL["designation"], "BRS-SP-15-12", CA.PAR_BL)
        base = {"code": "2030042", "designation": LIGNE_BL["designation"], "qty": 5, "rate": 1.75, "item_code": self.autre}
        # devinette acceptée : rien n'est écrasé
        CA.apprendre_lignes_stock(MH2C, [dict(base, suggestion_item=self.autre, suggestion_source=CA.DEVINE)])
        self.assertEqual(CA.proposer(MH2C, "2030042", None)["item_code"], "BRS-SP-15-12")
        # la mémoire proposait BRS-SP-15-12, l'employé l'a changée : correction
        CA.apprendre_lignes_stock(MH2C, [dict(base, suggestion_item="BRS-SP-15-12", suggestion_source=CA.APPRIS,
                                              suggestion_cle=MH2C + "|C|2030042")])
        self.assertEqual(CA.proposer(MH2C, "2030042", None)["item_code"], self.autre)
        # deux lignes de même libellé, deux articles différents dans la même saisie : rien n'est appris
        faits = CA.apprendre_lignes_stock(MH2C, [{"designation": "TUYAU", "item_code": self.autre, "qty": 1},
                                                 {"designation": "TUYAU", "item_code": "BRS-SP-15-12", "qty": 1}])
        self.assertEqual(faits, [])
        self.assertIsNone(CA.proposer(MH2C, None, "TUYAU"))

    def test_code_mal_lu_d_un_autre_article(self):
        CA.apprendre(MH2C, "2030042", 'REDUCTION LAITON SPECIAL 1/2"F*3/8"M', "BRS-SP-15-12", CA.PAR_BL)
        self.assertIsNone(CA.proposer(MH2C, "2030042", "PINCE COUPANTE 160MM TOTAL"))     # libellé sans rapport

    def test_saisie_manuelle_dans_la_liste(self):
        d = frappe.get_doc({"doctype": CA.DOCTYPE, "supplier": MH2C, "code_fournisseur": " 2030042 ",
                            "item_code": "BRS-SP-15-12"}).insert()
        self.assertEqual((d.cle, d.source, d.fois), (MH2C + "|C|2030042", "Saisie manuelle", 1))
        self.assertEqual(CA.proposer(MH2C, "2030042", None)["item_code"], "BRS-SP-15-12")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": CA.DOCTYPE, "supplier": MH2C, "code_fournisseur": "2030042",
                            "item_code": self.autre}).insert()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": CA.DOCTYPE, "supplier": MH2C, "item_code": self.autre}).insert()

    def test_proposer_pour_lignes(self):
        CA.apprendre(MH2C, "2030042", None, "BRS-SP-15-12", CA.PAR_BL)
        lignes = CA.proposer_pour_lignes(MH2C, [dict(LIGNE_BL, item_code=None), dict(OUTIL)])
        self.assertEqual((lignes[0]["item_code"], lignes[0]["suggestion_source"], lignes[0]["suggestion_item"]),
                         ("BRS-SP-15-12", CA.APPRIS, "BRS-SP-15-12"))
        self.assertIn(lignes[1]["suggestion_source"], (CA.DEVINE, ""))

    def test_la_facture_apprend_du_bl_couvert(self):
        # « En partie achat » : les lignes lues partent avec la dépense ; le BL couvert apprend sa réduction.
        r = _creer(lignes_lues=frappe.as_json([LIGNE_BL, OUTIL]))
        self.assertTrue(r.get("appris"))
        p = CA.proposer(MH2C, "2030042", None)
        self.assertEqual((p["item_code"], p["source"]), ("BRS-SP-15-12", CA.PAR_BL))
        self.assertIsNone(CA.proposer(MH2C, "9910019", None))            # B26001620 n'a pas de réception : rien de sûr

    def test_la_part_stock_apprend_le_choix(self):
        ligne = {"item_code": self.autre, "qty": 1, "rate": 10, "designation": "PINCE COUPANTE 160MM TOTAL",
                 "code": "9910019", "suggestion_item": None, "suggestion_source": ""}
        r = _creer(bls_couverts=None, bls_cites=None, numero_facture="TEST-MEM-0001", articles_stock=frappe.as_json([ligne]))
        self.assertTrue(r.get("appris"))
        self.assertEqual(CA.proposer(MH2C, "9910019", None)["item_code"], self.autre)

    def test_seulement_depense_apprend_en_fond(self):
        # Sans lignes lues, la dépense part en tâche de fond (relecture du scan) ; on vérifie qu'elle est demandée.
        with mock.patch.object(frappe, "enqueue") as file_d_attente:
            _creer()
        appels = [c for c in file_d_attente.call_args_list if c.args and c.args[0].endswith("apprendre_en_fond")]
        self.assertEqual(len(appels), 1)
        self.assertEqual(appels[0].kwargs["supplier"], MH2C)


if __name__ == "__main__":
    unittest.main()
