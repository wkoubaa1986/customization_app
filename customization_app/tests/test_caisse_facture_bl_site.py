"""Une FACTURE D'ACHAT qui couvre des BL, saisie à la caisse (26/11/2026) — essai SUR LE SITE, sans trace.

Le cas de la facture SCPS FT26080301, rejoué sous des n° fictifs : un BL déjà reçu (27 réductions, 87,291 — le timbre
dans le prix) et un BL jamais saisi (30 bagues + 4 réductions, 86,962). `creer` committe : frappe.db.commit est
neutralisé, tout est annulé à la fin. À lancer seul, dans le contexte du site (jamais `bench run-tests`).
La validation de la facture préparée est jouée comme le comptable, sans la lecture du scan (pas d'appel à l'IA)."""
import base64
import unittest
from unittest import mock

import frappe
from frappe.utils import flt

from customization_app import caisse_bl as B
from customization_app import caisse_depenses as C
from customization_app import caisse_facture_bl as FB
from customization_app import caisse_stock_partiel as S

SCPS, SP, BQ = "SCPS", "BRS-SP-15-12", "BRS-BQ-15-12"
BL_A, BL_B, NUMERO = "TESTBL-A001", "TESTBL-B002", "TEST-FA-0001"
FICHE = "Facture Achat a Saisir"
CITES = [{"numero": BL_A, "date": "2026-08-25"}, {"numero": BL_B, "date": "2026-08-13"}]
LIGNES_B = [{"item_code": BQ, "qty": 30, "rate": 2.545, "designation": "Bague 15/12", "bl_cle": BL_B, "code": ""},
            {"item_code": SP, "qty": 4, "rate": 2.653, "designation": "Réduction 15/12", "bl_cle": BL_B, "code": ""}]
LUES = [{"code": "", "designation": "Réduction 15/12", "bl_cle": BL_A, "qty": 27, "item_code": None}] + [
    {"code": l["code"], "designation": l["designation"], "bl_cle": l["bl_cle"], "qty": l["qty"], "item_code": l["item_code"]}
    for l in LIGNES_B]
# Une image d'un pixel : la pièce jointe n'est pas ce qu'on contrôle ici.
PHOTO = "data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def _stock(article):
    return flt(frappe.db.sql("select sum(actual_qty) from tabBin where item_code = %s", article)[0][0])


class TestFactureAchatSurBL(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.exists("Supplier", SCPS) or not all(
                frappe.db.get_value("Item", i, "is_stock_item") for i in (SP, BQ)):
            self.skipTest("SCPS ou ses articles n'existent pas sur ce site")
        frappe.db.savepoint("essai_facture_bl")
        self.erreurs = []
        self.mocks = [mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
                      mock.patch.object(frappe, "enqueue", lambda *a, **k: None),
                      mock.patch.object(frappe, "log_error", lambda *a, **k: self.erreurs.append(
                          str(k.get("title") or (a[1] if len(a) > 1 else (a[0] if a else "")))[:120])),
                      mock.patch.dict(frappe.local.conf, {"disable_global_search": 1})]
        for m in self.mocks:
            m.start()
        # Le BL A, tel qu'un BL photographié à la livraison puis reçu : fiche BL, commande sans taxe, réception.
        self.a = FB.recevoir({"cle": B.cle_numero_bl(BL_A), "numero": BL_A, "date": "2026-08-25", "fiche": None,
                              "lignes": FB.lignes_a_recevoir([{"item_code": SP, "qty": 27, "rate": 3.233,
                                                               "designation": "Réduction 15/12"}])},
                             SCPS, {"fournisseur": "SCPS", "taux_tva": 19, "description": "Essai BL A"})

    def tearDown(self):
        for m in reversed(self.mocks):
            m.stop()
        frappe.db.rollback(save_point="essai_facture_bl")
        frappe.set_user("Administrator")

    # ── outils ──
    def _creer(self, **kw):
        args = dict(type_depense="Facture d'achat", montant=207.158, tva=32.916, taux_tva=19, mode="Pas payé",
                    description="Essai facture sur BL", fournisseur="SCPS", supplier=SCPS, numero_facture=NUMERO,
                    date_facture="2026-08-31", photo_facture=PHOTO, timbre=1,
                    bls_couverts=frappe.as_json([self.a["fiche"]]), bls_cites=frappe.as_json(CITES),
                    articles_stock=frappe.as_json(LIGNES_B), lignes_lues=frappe.as_json(LUES))
        args.update(kw)
        return C.creer(**args)

    def _refuse(self, motif, **kw):
        """La saisie est refusée avec ce motif, et ne laisse rien derrière elle."""
        frappe.db.savepoint("refus")
        try:
            with self.assertRaisesRegex(frappe.ValidationError, motif):
                self._creer(**kw)
        finally:
            frappe.db.rollback(save_point="refus")
            frappe.local.message_log = []

    def _valider(self, pi):
        """Le comptable enregistre puis valide ; la lecture du scan (IA) et la comparaison au scan sont neutralisées."""
        from bank_retenue_sync.achat import facture as F
        with mock.patch.object(F, "completer", lambda doc: {}), \
                mock.patch.object(F.regles, "bloquants", lambda *a, **k: []):
            pi.reload()
            pi.save()
            pi.submit()
        pi.reload()
        return pi

    # ── essais ──
    def test_le_bl_recu_est_propose_pour_une_facture_d_achat(self):
        r = B.bls_ouverts(SCPS, frappe.as_json(CITES), frappe.as_json(LUES), pour="achat")
        carte = next(c for c in r["bls"] if c["name"] == self.a["fiche"])
        self.assertEqual((carte["niveau"], carte["coche"], carte["bloquant"], carte["recu"]), ("trouve", True, False, 87.291))
        self.assertEqual(r["pour"], "achat")
        self.assertEqual([c["numero"] for c in r["inconnus"]], [BL_B])
        self.assertIn(self.a["fiche"], r["coches"])

    def test_facture_sur_un_bl_recu_et_un_bl_jamais_saisi(self):
        sp, bq = _stock(SP), _stock(BQ)
        r = self._creer()
        self.assertFalse(r["name"])   # « Pas payé » : aucune écriture de caisse, la dette naîtra avec la facture
        achat = r["achat"]
        pi = frappe.get_doc("Purchase Invoice", achat["purchase_invoice"])
        # La facture préparée : un brouillon, depuis les DEUX réceptions, aux prix des réceptions, sans mouvement de stock.
        self.assertEqual((pi.docstatus, pi.supplier, pi.bill_no, str(pi.bill_date), pi.update_stock), (0, SCPS, NUMERO, "2026-08-31", 0))
        self.assertEqual(sorted((i.item_code, flt(i.qty), flt(i.rate, 3)) for i in pi.items),
                         [(BQ, 30.0, 2.545), (SP, 4.0, 2.653), (SP, 27.0, 3.233)])
        nouveau = achat["bls_recus"][0]
        self.assertEqual({i.purchase_receipt for i in pi.items}, {self.a["purchase_receipt"], nouveau["purchase_receipt"]})
        # Le TTC de la facture fait foi : TVA lue, pas de timbre (il est dans le prix du BL reçu), 11 millimes d'arrondi.
        arrondi = frappe.db.get_value("Company", C.COMPANY, "round_off_account")
        self.assertEqual(sorted((t.account_head, flt(t.tax_amount, 3)) for t in pi.taxes),
                         sorted([(C.COMPTE_TVA_19, 32.916), (arrondi, -0.011)]))
        self.assertEqual((flt(pi.net_total, 3), flt(pi.grand_total, 3)), (174.253, 207.158))
        self.assertEqual((achat["timbre"], achat["arrondi"], achat["ecart"], achat["ttc"]), (0.0, -0.011, 0.0, 207.158))
        self.assertEqual([(s.mode_of_payment, flt(s.payment_amount, 3)) for s in pi.payment_schedule], [("Dette non payée", 207.158)])
        # Le stock n'entre qu'UNE fois : rien pour le BL déjà reçu, 30 + 4 pour le BL jamais saisi.
        self.assertEqual((_stock(SP) - sp, _stock(BQ) - bq), (4.0, 30.0))
        # Le BL jamais saisi a maintenant sa fiche, sa commande et sa réception — comme un BL photographié à la livraison.
        f_b = frappe.db.get_value(FICHE, nouveau["fiche"], ["est_bl", "numero_bl", "statut", "supplier", "purchase_order",
                                                             "purchase_receipt", "mode_paiement"], as_dict=True)
        self.assertEqual((f_b.est_bl, f_b.numero_bl, f_b.statut, f_b.supplier, f_b.mode_paiement), (1, BL_B, "À saisir", SCPS, "Pas payé"))
        self.assertEqual((f_b.purchase_order, f_b.purchase_receipt), (nouveau["purchase_order"], nouveau["purchase_receipt"]))
        self.assertEqual(flt(frappe.db.get_value("Purchase Order", nouveau["purchase_order"], "total_taxes_and_charges")), 0.0)
        # Les trois fiches (la facture, le BL reçu, le BL né ici) désignent la facture préparée.
        self.assertEqual([b["fiche"] for b in achat["bls_factures"]], [self.a["fiche"]])
        for nom in (r["fiche"], self.a["fiche"], nouveau["fiche"]):
            self.assertEqual(frappe.db.get_value(FICHE, nom, "purchase_invoice"), pi.name, nom)
        self.assertIn("Préparée par la caisse", " ".join(frappe.get_all(
            "Comment", filters={"reference_doctype": "Purchase Invoice", "reference_name": pi.name}, pluck="content")))
        # La même facture ne se saisit pas deux fois ; le BL ne se propose plus.
        self._refuse("déjà", numero_facture=NUMERO)
        self.assertNotIn(self.a["fiche"], [c["name"] for c in B.bls_ouverts(SCPS, frappe.as_json(CITES), pour="achat")["bls"]])

        # Le comptable valide : les réceptions sont facturées, les fiches passent « Saisie », rien n'est racheté.
        pi = self._valider(pi)
        self.assertEqual(pi.docstatus, 1)
        self.assertEqual(flt(pi.outstanding_amount, 3), 207.158)   # pas payée : la dette est chez le fournisseur
        for recu in (self.a["purchase_receipt"], nouveau["purchase_receipt"]):
            self.assertEqual(flt(frappe.db.get_value("Purchase Receipt", recu, "per_billed")), 100.0, recu)
        for nom in (r["fiche"], self.a["fiche"], nouveau["fiche"]):
            self.assertEqual(frappe.db.get_value(FICHE, nom, "statut"), "Saisie", nom)
        net = frappe.db.sql("""select sum(credit - debit) from `tabGL Entry` where account = %s and is_cancelled = 0
                               and voucher_no in %s""", (S.compte_stock_recu_non_facture(),
                                                         (pi.name, self.a["purchase_receipt"], nouveau["purchase_receipt"])))[0][0]
        self.assertAlmostEqual(flt(net), 0.0, places=3)   # « Stock reçus non facturés » soldé par la facture
        self.assertEqual((_stock(SP) - sp, _stock(BQ) - bq), (4.0, 30.0))
        self.assertEqual(self.erreurs, [])

    def test_facture_qui_ne_couvre_que_le_bl_recu(self):
        # Aucune ligne à recevoir : la facture ne reprend que la réception du BL coché (HT 87,291, timbre dans le prix).
        sp = _stock(SP)
        r = self._creer(montant=103.684, tva=16.395, articles_stock=None,
                        bls_cites=frappe.as_json(CITES[:1]), lignes_lues=frappe.as_json(LUES[:1]))
        achat = r["achat"]
        self.assertEqual((achat["ht"], achat["ttc"], achat["bls_recus"]), (87.291, 103.684, []))
        self.assertEqual(_stock(SP), sp)

    def test_grosse_facture_payee_par_cheque_avec_retenue(self):
        # 1 532,876 TTC (au-dessus du seuil de retenue), payée par chèque net de 1 % : l'avance de caisse devient le
        # paiement de la facture à la validation, la retenue est posée une seule fois.
        lignes = [dict(LIGNES_B[1], qty=400, rate=3.0)]
        r = self._creer(montant=1532.876, tva=244.585, mode="Chèque", n_cheque="9990001", banque="Banque Zitouna",
                        photo_cheque=PHOTO, photo_cheque_nom="cheque.png", articles_stock=frappe.as_json(lignes),
                        lignes_lues=frappe.as_json([LUES[0], dict(LUES[2], qty=400)]))
        je = frappe.get_doc("Journal Entry", r["name"])
        avance = [a for a in je.accounts if a.party == SCPS]
        self.assertEqual([flt(a.debit, 3) for a in avance], [1532.876])
        achat = r["achat"]
        self.assertEqual((achat["ht"], achat["timbre"], achat["arrondi"], achat["ecart"]), (1287.291, 1.0, 0.0, 0.0))
        pi = self._valider(frappe.get_doc("Purchase Invoice", achat["purchase_invoice"]))
        self.assertEqual(pi.docstatus, 1)
        # Tout est réglé : rien ne reste dû au fournisseur, et son compte est à zéro pour cette facture.
        self.assertAlmostEqual(flt(pi.outstanding_amount), 0.0, places=3)
        solde = frappe.db.sql("""select sum(debit - credit) from `tabGL Entry` where party_type = 'Supplier' and party = %s
                                 and is_cancelled = 0 and (voucher_no in %s or against_voucher = %s)""",
                              (SCPS, (pi.name, je.name), pi.name))[0][0]
        self.assertAlmostEqual(flt(solde), 0.0, places=3)
        for nom in (r["fiche"], self.a["fiche"], achat["bls_recus"][0]["fiche"]):
            self.assertEqual(frappe.db.get_value(FICHE, nom, "statut"), "Saisie", nom)
        # La retenue (1 % du TTC hors timbre) est sur la facture UNE fois, celle que la caisse a déjà retenue.
        self.assertEqual([flt(t.tax_amount, 3) for t in pi.taxes if t.account_head == C._compte_retenue()], [15.319])
        self.assertEqual(flt(sum(a.credit for a in je.accounts if a.account == C._compte_retenue()), 3), 15.319)
        # (La photo du chèque est un pixel : seule sa vérification, une aide, a le droit d'échouer ici.)
        self.assertEqual([e for e in self.erreurs if "photo" not in e], [])

    def test_refus(self):
        # Une ligne sous la rubrique du BL coché déjà reçu ne se reçoit pas une 2e fois.
        self._refuse("déjà reçu", articles_stock=frappe.as_json(LIGNES_B + [dict(LIGNES_B[1], bl_cle=BL_A, qty=27, rate=3.233)]))
        # Le BL reconnu DÉCOCHÉ : ses lignes ne se reçoivent pas une 2e fois.
        self._refuse("déjà saisi", bls_couverts=None,
                     articles_stock=frappe.as_json(LIGNES_B + [dict(LIGNES_B[1], bl_cle=BL_A, qty=27, rate=3.233)]))
        # Toute ligne doit avoir son article.
        self._refuse("pas d'article", articles_stock=frappe.as_json([dict(LIGNES_B[0], item_code="")] + LIGNES_B[1:]))
        # Les réceptions ne font pas la facture : rien n'est enregistré.
        self._refuse("ne tombe pas juste|écart", montant=260.0)
        # Le BL d'un autre fournisseur.
        autre = frappe.db.get_value("Supplier", {"name": ["!=", SCPS], "disabled": 0}, "name")
        self._refuse("fournisseur|BL", supplier=autre, fournisseur=autre)
        # Un BL ne couvre pas d'autres BL.
        self._refuse("BL", est_bl=1, numero_bl="TESTBL-Z9", numero_facture="")
        # Un article qui n'est pas de stock ne se reçoit pas par une facture d'achat.
        service = frappe.db.get_value("Item", {"is_stock_item": 0, "disabled": 0, "has_variants": 0}, "name")
        if service:
            self._refuse("stock", articles_stock=frappe.as_json([dict(LIGNES_B[0], item_code=service)]))
        # Après tous ces refus, rien n'a bougé : le BL A est toujours ouvert, aucune facture préparée.
        self.assertEqual(frappe.db.get_value(FICHE, self.a["fiche"], ["statut", "purchase_invoice"]), ("À saisir", None))
        self.assertFalse(frappe.db.exists("Purchase Invoice", {"bill_no": NUMERO, "supplier": SCPS}))

    def test_sans_les_bl_le_comptable_est_prevenu(self):
        # Lignes illisibles à la caisse, « Sans les BL » : la fiche part comme avant, avec un avertissement.
        r = self._creer(bls_couverts=None, articles_stock=None, lignes_lues=None)
        self.assertNotIn("achat", r)
        self.assertIn(BL_A, r["bls_a_rattacher"])
        commentaires = " ".join(frappe.get_all("Comment", filters={"reference_doctype": FICHE, "reference_name": r["fiche"]},
                                               pluck="content"))
        self.assertIn("Rattacher des BL", commentaires)
        self.assertEqual(frappe.db.get_value(FICHE, self.a["fiche"], "statut"), "À saisir")


if __name__ == "__main__":
    unittest.main()
