"""BL déjà reçus qu'une facture couvre (25/11/2026) : clés des n° de BL, rapprochement, motifs, contrôles. Pur.

Le jeu reprend le cas réel : la facture à terme MH2C F26000463 (29/10/2026, 332,928 TTC) regroupe le BL B26001620
(outillage, jamais saisi) et le BL B26001723 (réduction laiton, fiche FAS-0319, reçue le 07/09 pour 9,750 HT)."""
import unittest

from customization_app import caisse_bl as B

CITES = [{"numero": "B26001620", "date": "2026-08-07", "total_ht": 270.181, "total_ttc": 321.515},
         {"numero": "TOTAL BL N° B26001723 DU 20/08/2026", "date": "2026-08-20", "total_ht": 8.75, "total_ttc": 10.413}]
FAS_0319 = {"name": "FAS-0319", "numero_bl": "B26001723", "numero_facture": "B26001723", "montant": 10.413,
            "date_facture": "2026-08-20", "recu": 9.75, "items": [{"item_code": "BRS-SP-15-12", "qty": 5}], "motif": ""}
FAS_0317 = {"name": "FAS-0317", "numero_bl": "BL2615138", "numero_facture": "BL2615138", "montant": 103.684,
            "date_facture": "2026-08-21", "recu": 87.13, "items": [{"item_code": "M-100-FR", "qty": 2}], "motif": ""}


class TestCle(unittest.TestCase):
    def test_meme_bl_ecrit_de_plusieurs_facons(self):
        for ecrit in ("TOTAL BL N° B26001723 DU 20/08/2026", "BL N° B26001723", "bl n°b26001723", "B26001723",
                      "  B 2600 1723 ", "Bon de livraison N° B26001723"):
            self.assertEqual(B.cle_numero_bl(ecrit), "B26001723", ecrit)
        for ecrit in ("BL2615138", "BL 2615138", "N° BL2615138", "B.L. 0002615138", "BL: 2615138"):
            self.assertEqual(B.cle_numero_bl(ecrit), "2615138", ecrit)

    def test_bl_differents_et_vides(self):
        self.assertNotEqual(B.cle_numero_bl("B26001620"), B.cle_numero_bl("B26001723"))
        self.assertEqual(B.cle_numero_bl(""), "")
        self.assertEqual(B.cle_numero_bl(None), "")
        self.assertEqual(B.cles_fiche("B26001723", "B26001723"), {"B26001723"})
        self.assertEqual(B.cles_fiche("", "F26000463"), {"F26000463"})


class TestRapprochement(unittest.TestCase):
    def test_la_facture_mh2c(self):
        r = B.rapprocher_bls(CITES, [FAS_0319])
        self.assertEqual(r["coches"], ["FAS-0319"])
        c = r["bls"][0]
        self.assertEqual((c["niveau"], c["coche"], c["controle"], c["ecart_ht"], c["bloquant"]),
                         ("trouve", True, "conforme", 1.0, False))
        self.assertEqual(c["cles"], ["B26001723"])
        self.assertEqual(c["items"], ["BRS-SP-15-12"])
        # B26001620 n'a jamais été saisi : il reste en dépense
        self.assertEqual([x["numero"] for x in r["inconnus"]], ["B26001620"])
        self.assertEqual(r["deja_couverts"], [])

    def test_plusieurs_bl_meme_operation(self):
        cites = CITES + [{"numero": "BL 2615138", "total_ht": None, "total_ttc": 103.684}]
        r = B.rapprocher_bls(cites, [FAS_0317, FAS_0319])
        self.assertEqual(sorted(r["coches"]), ["FAS-0317", "FAS-0319"])
        self.assertEqual({c["name"]: c["niveau"] for c in r["bls"]}, {"FAS-0317": "trouve", "FAS-0319": "trouve"})

    def test_n_seulement_dans_le_numero_de_facture_de_la_fiche(self):
        f = dict(FAS_0319, numero_bl="")
        self.assertEqual(B.rapprocher_bls(CITES, [f])["coches"], ["FAS-0319"])

    def test_ecart_de_montant_coche_mais_a_verifier(self):
        cites = [{"numero": "B26001723", "total_ttc": 15.0}]
        c = B.rapprocher_bls(cites, [FAS_0319])["bls"][0]
        self.assertEqual((c["coche"], c["controle"]), (True, "a_verifier"))
        self.assertEqual(B.controle_ttc(11.4, 10.413), "timbre")       # 1 DT d'écart : le timbre

    def test_possible_probable_autre_jamais_coches(self):
        possible = B.rapprocher_bls([{"numero": "B99999999", "total_ttc": 10.413}], [FAS_0319])
        self.assertEqual((possible["bls"][0]["niveau"], possible["coches"], possible["inconnus"]), ("possible", [], []))
        probable = B.rapprocher_bls([], [FAS_0319], lignes=[{"item_code": "BRS-SP-15-12", "qty": 5}])
        self.assertEqual((probable["bls"][0]["niveau"], probable["coches"]), ("probable", []))
        quantite = B.rapprocher_bls([], [FAS_0319], lignes=[{"item_code": "BRS-SP-15-12", "qty": 4}])
        self.assertEqual(quantite["bls"][0]["niveau"], "autre")
        autre = B.rapprocher_bls([], [FAS_0319])
        self.assertEqual((autre["bls"][0]["niveau"], autre["coches"]), ("autre", []))

    def test_deja_couvert_et_bloquant(self):
        r = B.rapprocher_bls(CITES, [], clos=[{"name": "FAS-0319", "numero_bl": "B26001723", "numero_facture": "F26000463",
                                               "couvert_par": "DAP-1890"}])
        self.assertEqual([(x["numero"], x["couvert_par"]) for x in r["deja_couverts"]],
                         [("TOTAL BL N° B26001723 DU 20/08/2026", "DAP-1890")])
        bloque = B.rapprocher_bls(CITES, [dict(FAS_0319, motif="payé à la livraison")])
        self.assertEqual((bloque["bls"][0]["bloquant"], bloque["bls"][0]["coche"], bloque["coches"]), (True, False, []))


class TestMotifs(unittest.TestCase):
    BASE = {"est_bl": 1, "supplier": "MH2C", "statut": "À saisir", "mode_paiement": "Pas payé",
            "purchase_order": "PO-4", "po_docstatus": 1, "po_status": "To Bill", "po_advance": 0,
            "purchase_receipt": "PR-7", "pr_docstatus": 1, "pr_status": "To Bill", "pr_per_billed": 0}

    def test_traitable(self):
        self.assertEqual(B.motif_non_traitable(self.BASE, "MH2C"), "")
        self.assertEqual(B.motif_non_traitable({"est_bl": 1, "statut": "À saisir", "mode_paiement": "Pas payé"}), "")

    def test_bloques(self):
        cas = {"payé": {"mode_paiement": "Espèces"}, "écriture": {"journal_entry": "JV-1"},
               "paiement": {"payment_entry": "PE-1"}, "avance": {"po_advance": 5},
               "dépense": {"depense_a_payer": "DAP-1"}, "facture": {"purchase_invoice": "PINV-1"},
               "brouillon": {"po_docstatus": 0}, "sans réception": {"purchase_receipt": None},
               "facturée": {"pr_per_billed": 50}, "fermée": {"pr_status": "Closed"},
               "saisie": {"statut": "Saisie"}, "pas un BL": {"est_bl": 0}}
        for nom, changement in cas.items():
            self.assertTrue(B.motif_non_traitable(dict(self.BASE, **changement), "MH2C"), nom)
        self.assertEqual(B.motif_non_traitable(self.BASE, "AUTRE"), "autre fournisseur")


class TestControles(unittest.TestCase):
    def test_total(self):
        self.assertEqual(B.controle_total(9.75, 0, 279.931, 1), "")              # F26000463
        self.assertEqual(B.controle_total(9.75, 0, 8.75, 1), "")                 # facture du seul BL : timbre toléré
        self.assertTrue(B.controle_total(12, 0, 9.75, 1))
        self.assertTrue(B.controle_total(9.75, 270.181, 279.931, 1) == "")       # outillage en stock + BL : juste
        self.assertTrue(B.controle_total(9.75, 290, 279.931, 1))

    def test_tolerance_compte_les_bl_deja_soldes(self):
        # Deux BL facturés 8,750 et reçus 9,750 : ensemble à la saisie, ou l'un puis l'autre après coup — même verdict.
        self.assertEqual(B.controle_total(19.5, 0, 17.5, 2), "")
        self.assertEqual(B.controle_total(9.75 + 9.75, 0, 17.5, 1 + 1), "")
        self.assertTrue(B.controle_total(19.5, 0, 17.5, 1))

    def test_ligne_deja_recue(self):
        infos = [{"numero_bl": "B26001723", "cles": ["B26001723"], "items": ["BRS-SP-15-12"]}]
        self.assertEqual(B.ligne_deja_recue({"item_code": "X", "bl_cle": "B26001723"}, infos), "B26001723")
        self.assertEqual(B.ligne_deja_recue({"item_code": "BRS-SP-15-12"}, infos), "B26001723")
        self.assertIsNone(B.ligne_deja_recue({"item_code": "BRS-SP-15-12", "bl_cle": "B26001620"}, infos))
        self.assertIsNone(B.ligne_deja_recue({"item_code": "AUTRE"}, infos))
        # Un BL SANS réception n'a rien fait entrer en stock : ses lignes restent libres (relecture du 25/11/2026).
        sans = [dict(infos[0], recu=False)]
        self.assertIsNone(B.ligne_deja_recue({"item_code": "X", "bl_cle": "B26001723"}, sans))
        self.assertIsNone(B.ligne_deja_recue({"item_code": "BRS-SP-15-12"}, sans))

    def test_ecriture_de_solde(self):
        l = B.lignes_solde(9.75, "Charges Diverses - A&S", "Stock Reçus Mais Non Facturés - A&S", "Principal - A&S")
        self.assertEqual(sum(x.get("debit_in_account_currency", 0) for x in l), sum(x.get("credit_in_account_currency", 0) for x in l))
        self.assertEqual(l[0]["account"], "Stock Reçus Mais Non Facturés - A&S")
        self.assertEqual(B.libelle_solde("B26001723", "MH2C", "F26000463"), "BL B26001723 — MH2C — Fact. F26000463")

    def test_remarque_et_prefixe_ne_trompent_pas_la_caisse(self):
        from customization_app import caisse_depenses as C
        r = B.remarque_bls([{"numero_bl": "B26001723", "fiche": "FAS-0319", "recu_doc": "MAT-PRE-2026-00007", "recu": 9.75}])
        self.assertIn("B26001723 (FAS-0319, réception MAT-PRE-2026-00007 soldée 9,750 HT)", r)
        self.assertIsNone(C._RX_SAISIE_BL.search(r))          # « Corriger » ne la relirait pas comme un BL
        self.assertEqual(C.lire_saisie("Dépense caisse — x", r, [])["est_bl"], 0)
        self.assertFalse(B.PREFIXE_SOLDE.startswith((C.PREFIXE_CAISSE, C.PREFIXE_A_PAYER)))
        self.assertFalse((B.PREFIXE_SOLDE + "x").startswith("Dépense caisse"))

    def test_motif_de_correction(self):
        from customization_app import caisse_depenses as C
        infos = {"type_depense": "Dépense avec facture", "name": "JV-1"}
        self.assertTrue(C.motif_non_corrigeable(infos, depense={"purchase_order": "PO-8"}))
        self.assertTrue(C.motif_non_corrigeable(infos, depense={"name": "DAP-1"}, bls_soldes=1))
        self.assertEqual(C.motif_non_corrigeable(infos, depense={"name": "DAP-1"}), "")


class TestArticlesDeLaRubrique(unittest.TestCase):
    """La ligne grisée « déjà reçue » montre l'article de la RÉCEPTION (demande du 25/11/2026 : elle montrait tuy-028)."""
    LIGNES = [{"code": "9910019", "designation": "SCIE CLOCHE BIMETAL 32", "qty": 2, "bl_cle": "B26001620",
               "item_code": None},
              {"code": "2030042", "designation": 'REDUCTION LAITON SPECIAL 1/2"F*3/8"M', "qty": 5, "bl_cle": "B26001723",
               "item_code": "tuy-028"}]
    RECUS = [{"item_code": "BRS-SP-15-12", "item_name": "Reduction Bronze Special 15/12", "qty": 5.0}]
    ATTENDU = [{"i": 1, "item_code": "BRS-SP-15-12", "item_name": "Reduction Bronze Special 15/12"}]

    def test_la_reduction_de_mh2c(self):
        self.assertEqual(B.articles_de_la_rubrique({"B26001723"}, self.LIGNES, self.RECUS), self.ATTENDU)
        # Le BL B26001620 (jamais saisi) n'a pas de réception : rien.
        self.assertEqual(B.articles_de_la_rubrique({"B26001620"}, self.LIGNES, []), [])

    def test_dans_la_carte_du_bl(self):
        f = dict(FAS_0319, items=[dict(self.RECUS[0])])
        c = B.rapprocher_bls(CITES, [f], lignes=self.LIGNES)["bls"][0]
        self.assertEqual(c["appariees"], self.ATTENDU)
        self.assertEqual(c["recus"], self.RECUS)
        self.assertEqual(c["items"], ["BRS-SP-15-12"])
        # Sans lignes (analyse, avant la lecture des articles) : rien d'apparié, le reste inchangé.
        sans = B.rapprocher_bls(CITES, [f])["bls"][0]
        self.assertEqual((sans["appariees"], sans["coche"], sans["items"]), ([], True, ["BRS-SP-15-12"]))

    def test_le_rang_est_celui_de_l_ecran(self):
        lignes = [{"designation": "", "code": "", "qty": 1}, "illisible"] + self.LIGNES
        self.assertEqual([x["i"] for x in B.articles_de_la_rubrique({"B26001723"}, lignes, self.RECUS)], [3])

    def test_meme_regle_que_l_apprentissage(self):
        from customization_app import correspondance_articles as CA
        rubrique = [self.LIGNES[1]]
        self.assertEqual([it for _, it in CA.apparier(rubrique, self.RECUS)], ["BRS-SP-15-12"])
        # Pas de certitude, pas d'article : quantité différente, deux lignes de même quantité, autre BL.
        self.assertEqual(B.articles_de_la_rubrique({"B26001723"}, [dict(self.LIGNES[1], qty=4)], self.RECUS), [])
        deux = [dict(self.LIGNES[1]), dict(self.LIGNES[1], code="2030043", designation="REDUCTION 3/4")]
        recus2 = [dict(self.RECUS[0]), {"item_code": "BRS-SP-20-15", "item_name": "", "qty": 5.0}]
        self.assertEqual(B.articles_de_la_rubrique({"B26001723"}, deux, recus2), [])
        self.assertEqual(B.articles_de_la_rubrique({"B99999999"}, self.LIGNES, self.RECUS), [])
        self.assertEqual(B.articles_de_la_rubrique(set(), self.LIGNES, self.RECUS), [])
        self.assertEqual(B.articles_de_la_rubrique({"B26001723"}, None, self.RECUS), [])

    def test_plusieurs_lignes_quantites_uniques(self):
        lignes = [dict(self.LIGNES[1], qty=5), dict(self.LIGNES[1], code="2030043", designation="REDUCTION 3/4", qty=2)]
        recus = [{"item_code": "BRS-SP-20-15", "item_name": "R 20/15", "qty": 2.0}, dict(self.RECUS[0])]
        self.assertEqual([(x["i"], x["item_code"]) for x in B.articles_de_la_rubrique({"B26001723"}, lignes, recus)],
                         [(0, "BRS-SP-15-12"), (1, "BRS-SP-20-15")])


if __name__ == "__main__":
    unittest.main()
