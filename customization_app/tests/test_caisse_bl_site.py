"""BL déjà reçus qu'une facture couvre (25/11/2026) — essai SUR LE SITE, sans trace.

Rejoue la facture MH2C F26000463 sous un n° fictif contre la vraie fiche FAS-0319 (BL B26001723, réception
MAT-PRE-2026-00007 de 9,750 HT) : `creer` committe, on neutralise donc frappe.db.commit et tout est annulé à la fin.
À lancer seul, dans le contexte du site (jamais `bench run-tests`). Sauté si FAS-0319 n'est plus ouverte."""
import base64
import os
import unittest
from unittest import mock

import frappe

from customization_app import caisse_bl as B
from customization_app import caisse_depenses as C
from customization_app import caisse_stock_partiel as S

MH2C = "MH2C (Maison Hammami de Chauffage & Climatisation)"
FICHE, RECU, COMMANDE = "FAS-0319", "MAT-PRE-2026-00007", "PUR-ORD-2026-00004"
NUMERO = "TEST-BL-0001"
CITES = [{"numero": "B26001620", "date": "2026-08-07", "total_ht": 270.181, "total_ttc": 321.515},
         {"numero": "B26001723", "date": "2026-08-20", "total_ht": 8.75, "total_ttc": 10.413}]
# Une image d'un pixel : la pièce jointe n'est pas ce qu'on contrôle ici.
PHOTO = "data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def _creer(**kw):
    args = dict(type_depense="Dépense avec facture", montant=332.928, tva=52.997, taux_tva=19, mode="Pas payé",
                compte="Charges Diverses - A&S", description="Essai BL couverts", fournisseur="MH2C - MAISON HAMMAMI",
                supplier=MH2C, numero_facture=NUMERO, date_facture="2026-10-29", photo_facture=PHOTO, timbre=1,
                bls_couverts='["%s"]' % FICHE, bls_cites=frappe.as_json(CITES))
    args.update(kw)
    return C.creer(**args)


class TestBLCouvertsSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        f = frappe.db.get_value("Facture Achat a Saisir", FICHE, ["statut", "purchase_receipt"], as_dict=True)
        if not f or f.statut != "À saisir" or f.purchase_receipt != RECU:
            self.skipTest("FAS-0319 n'est plus le BL ouvert de MH2C sur ce site")
        frappe.db.savepoint("essai_bl")
        self.commit = mock.patch.object(frappe.db, "commit", lambda *a, **k: None)
        self.commit.start()

    def tearDown(self):
        self.commit.stop()
        frappe.db.rollback(save_point="essai_bl")
        frappe.set_user("Administrator")

    def test_rapprochement(self):
        r = B.bls_ouverts(MH2C, frappe.as_json(CITES))
        carte = next(c for c in r["bls"] if c["name"] == FICHE)
        self.assertEqual((carte["niveau"], carte["coche"], carte["recu"], carte["controle"]), ("trouve", True, 9.75, "conforme"))
        self.assertEqual([c["numero"] for c in r["inconnus"]], ["B26001620"])
        # Le nom seul ne donne que des candidats : le BL cité tranche.
        candidats = C._rapprocher_fournisseur("MAISON HAMMAMI DE CHAUFFAGE & CLIMATISATION")["candidats"]
        if candidats:
            self.assertEqual(B.fournisseur_par_bl(candidats, CITES), MH2C)

    def test_la_facture_solde_le_bl_sans_racheter(self):
        avant_sle = frappe.db.count("Stock Ledger Entry", {"item_code": "BRS-SP-15-12", "is_cancelled": 0})
        r = _creer()
        self.assertEqual([b["fiche"] for b in r["bls_soldes"]], [FICHE])
        solde = r["bls_soldes"][0]["journal_entry"]
        je = frappe.get_doc("Journal Entry", solde)
        self.assertTrue(je.cheque_no.startswith(B.PREFIXE_SOLDE))
        self.assertEqual(sorted((a.account, a.debit, a.credit) for a in je.accounts),
                         [("Charges Diverses - A&S", 0.0, 9.75), (S.compte_stock_recu_non_facture(), 9.75, 0.0)])
        # La réception est soldée : son crédit sur « Stock reçus mais non facturés » est repris par l'écriture.
        self.assertEqual(B.valeur_recu(RECU), 9.75)
        net = frappe.db.sql("""select sum(credit - debit) from `tabGL Entry` where account = %s and is_cancelled = 0
                               and voucher_no in (%s, %s)""", (S.compte_stock_recu_non_facture(), RECU, solde))[0][0]
        self.assertAlmostEqual(float(net), 0.0, places=3)
        self.assertEqual(frappe.db.get_value("Purchase Order", COMMANDE, "status"), "Closed")
        self.assertEqual(frappe.db.get_value("Purchase Receipt", RECU, "status"), "Closed")
        f = frappe.db.get_value("Facture Achat a Saisir", FICHE, ["statut", "numero_facture", "depense_a_payer", "journal_entry"], as_dict=True)
        self.assertEqual((f.statut, f.numero_facture, f.depense_a_payer, f.journal_entry), ("Saisie", NUMERO, r["fiche"], solde))
        # Rien n'est racheté : ni commande, ni réception, ni mouvement de stock nouveaux.
        self.assertEqual(frappe.db.count("Stock Ledger Entry", {"item_code": "BRS-SP-15-12", "is_cancelled": 0}), avant_sle)
        self.assertFalse(r.get("purchase_order"))
        # La dépense reste entière, et sa remarque cite le BL sans se faire passer pour un BL.
        dep = frappe.get_doc("Journal Entry", r["name"])
        self.assertIn("Couvre des BL déjà reçus : B26001723 (FAS-0319", dep.user_remark)
        self.assertEqual(C.lire_saisie(dep.cheque_no, dep.user_remark, [])["est_bl"], 0)
        self.assertEqual(round(sum(a.debit for a in dep.accounts), 3), 332.928)
        # La même facture ne se saisit plus ; le BL ne se facture plus ; la dépense ne se corrige pas en l'état.
        self.assertTrue(C.facture_deja_saisie(NUMERO, supplier=MH2C))
        self.assertRaises(frappe.ValidationError, _creer)
        self.assertRaises(frappe.ValidationError, C.creer_facture_depuis_commandes, '["%s"]' % FICHE)
        self.assertIn("BL", C.details_depense(r["name"])["motif_refus"])
        # Le dossier comptable : l'écriture de solde est un « solde », pas un financement.
        from bank_retenue_sync.facturation import charges
        financements, soldes = charges._financements_stock([solde, r["name"]], avec_soldes=True)
        self.assertEqual((financements, soldes), ({}, {solde}))
        # Défaire : réservé au comptable, tout revient comme avant.
        B.rouvrir_bls(r["fiche"])
        self.assertEqual(frappe.db.get_value("Purchase Receipt", RECU, "status"), "To Bill")
        self.assertNotEqual(frappe.db.get_value("Purchase Order", COMMANDE, "status"), "Closed")
        f = frappe.db.get_value("Facture Achat a Saisir", FICHE, ["statut", "numero_facture", "depense_a_payer"], as_dict=True)
        self.assertEqual((f.statut, f.numero_facture, f.depense_a_payer), ("À saisir", "B26001723", None))
        self.assertEqual(frappe.db.get_value("Journal Entry", solde, "docstatus"), 2)

    def test_refus(self):
        # Un article déjà reçu avec le BL coché ne repart pas en stock (par sa rubrique, ou par son article).
        for ligne in ({"item_code": "BRS-SP-15-12", "qty": 5, "rate": 1.75, "bl_cle": "B26001723"},
                      {"item_code": "BRS-SP-15-12", "qty": 5, "rate": 1.75}):
            with self.assertRaises(frappe.ValidationError):
                _creer(articles_stock=frappe.as_json([ligne]))
        # Une facture trop petite pour le BL coché.
        with self.assertRaises(frappe.ValidationError):
            _creer(montant=5.0, tva=0.5)
        # Un autre fournisseur.
        autre = frappe.db.get_value("Supplier", {"name": ["!=", MH2C], "disabled": 0}, "name")
        with self.assertRaises(frappe.ValidationError):
            _creer(supplier=autre, fournisseur=autre)
        # Écart de montant non confirmé.
        with self.assertRaises(frappe.ValidationError):
            _creer(bls_cites=frappe.as_json([{"numero": "B26001723", "total_ttc": 50.0}]))

    def test_rattacher_apres_coup(self):
        # Saisie sans cocher le BL, puis le comptable le rattache depuis la fiche de dépense.
        r = _creer(bls_couverts=None)
        self.assertEqual(frappe.db.get_value("Facture Achat a Saisir", FICHE, "statut"), "À saisir")
        m = B.bls_de_la_depense(r["fiche"]) if r.get("fiche") else None
        if not r.get("fiche"):
            self.skipTest("la dépense sans BL n'a pas de fiche (payée sans reste)")
        self.assertIn(FICHE, [b["name"] for b in m["ouverts"]])
        x = B.couvrir_bls_apres(r["fiche"], '["%s"]' % FICHE)
        self.assertEqual([f["fiche"] for f in x["soldes"]], [FICHE])
        self.assertEqual(frappe.db.get_value("Facture Achat a Saisir", FICHE, ["statut", "depense_a_payer"]), ("Saisie", r["fiche"]))
        self.assertEqual(frappe.db.get_value("Purchase Receipt", RECU, "status"), "Closed")
        self.assertRaises(frappe.ValidationError, B.couvrir_bls_apres, r["fiche"], '["%s"]' % FICHE)   # pas deux fois

    def test_un_bl_ne_couvre_pas_un_bl(self):
        with self.assertRaises(frappe.ValidationError):
            _creer(est_bl=1, numero_bl="TEST-BL-8888", numero_facture="")

    def test_rattacher_apres_coup_refuse_un_article_deja_remis_en_stock(self):
        # La facture saisie en « En partie achat » avec la réduction (rachetée), sans cocher le BL : le rattacher ensuite
        # est refusé — sinon le stock compterait la réduction deux fois (sceptique du 25/11/2026).
        r = _creer(bls_couverts=None, bls_cites=None,
                   articles_stock=frappe.as_json([{"item_code": "BRS-SP-15-12", "qty": 5, "rate": 1.75}]))
        with self.assertRaises(frappe.ValidationError):
            B.couvrir_bls_apres(r["fiche"], '["%s"]' % FICHE)
        self.assertEqual(frappe.db.get_value("Facture Achat a Saisir", FICHE, "statut"), "À saisir")
        # Le comptable ne se voit pas proposer « Rouvrir » sur la fiche de part stock de la dépense.
        self.assertEqual(B.bls_de_la_depense(r["fiche"])["couverts"], [])

    def test_part_stock_d_un_bl_hors_rapport(self):
        # La part stock d'une dépense sur BL : l'avance n'a aucune ligne de caisse — pas « Dépense caisse — ».
        item = frappe.db.get_value("Item", {"is_stock_item": 1, "disabled": 0, "has_variants": 0,
                                            "name": ["!=", "BRS-SP-15-12"]}, "name")
        r = _creer(bls_couverts=None, bls_cites=None, numero_facture="", est_bl=1, numero_bl="TEST-BL-9999",
                   articles_stock=frappe.as_json([{"item_code": item, "qty": 1, "rate": 10}]))
        self.assertTrue(frappe.db.get_value("Journal Entry", r["journal_entry"], "cheque_no").startswith(S.PREFIXE_FINANCEMENT))

    def test_part_stock_d_une_facture(self):
        # La part stock d'une facture passe par le même geste : réception sans TVA, soldée et fermée, hors rapport.
        item = frappe.db.get_value("Item", {"is_stock_item": 1, "disabled": 0, "has_variants": 0,
                                            "name": ["!=", "BRS-SP-15-12"]}, "name")
        r = _creer(bls_couverts=None, bls_cites=None, numero_facture="TEST-STOCK-0001",
                   articles_stock=frappe.as_json([{"item_code": item, "qty": 1, "rate": 10}]))
        pr = frappe.get_doc("Purchase Receipt", r["purchase_receipt"])
        self.assertEqual((pr.status, round(pr.grand_total, 3), round(pr.net_total, 3)), ("Closed", 10.0, 10.0))
        self.assertTrue(frappe.db.get_value("Journal Entry", r["journal_entry"], "cheque_no").startswith(B.PREFIXE_SOLDE))
        self.assertEqual(frappe.db.get_value("Facture Achat a Saisir", r["fiche_achat"], "statut"), "Saisie")


if __name__ == "__main__":
    unittest.main()
