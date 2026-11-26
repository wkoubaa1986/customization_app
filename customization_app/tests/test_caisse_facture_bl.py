"""Une FACTURE D'ACHAT qui couvre des BL, saisie à la caisse (26/11/2026) — les règles PURES de caisse_facture_bl,
et la règle « payé à la livraison » de caisse_bl selon l'usage (dépense / achat). Sans base."""
import unittest

from customization_app import caisse_bl as B
from customization_app import caisse_facture_bl as FB


class TestLignes(unittest.TestCase):
    def test_lignes_normalisees(self):
        l = FB.lignes_a_recevoir([{"item_code": " BRS-BQ-15-12 ", "qty": "30", "rate": "2.545", "designation": " Bague ",
                                   "bl_cle": "BL2615063", "code": "B15", "suggestion_item": "X"}, None])
        self.assertEqual(l[0], {"item_code": "BRS-BQ-15-12", "qty": 30.0, "rate": 2.545, "designation": "Bague",
                                "bl_cle": "BL2615063", "code": "B15"})
        self.assertEqual(l[1]["item_code"], "")

    def test_toute_ligne_est_de_la_marchandise(self):
        ok = {"item_code": "A", "qty": 1, "rate": 2, "designation": "Réduction", "code": ""}
        self.assertEqual(FB.controler_lignes([ok]), "")
        # Une ligne sans article n'est pas écartée en silence : en facture d'achat, elle doit être reçue.
        self.assertIn("n'a pas d'article", FB.controler_lignes([ok, dict(ok, item_code="")]))
        self.assertIn("« Dépense avec facture »", FB.controler_lignes([dict(ok, item_code="")]))
        self.assertIn("quantité", FB.controler_lignes([dict(ok, qty=0)]))
        self.assertIn("négatif", FB.controler_lignes([dict(ok, rate=-1)]))
        # Un article offert (prix 0) se reçoit.
        self.assertEqual(FB.controler_lignes([dict(ok, rate=0)]), "")
        self.assertIn("ligne 2", FB.controler_lignes([ok, {"item_code": "", "qty": 1, "rate": 1}]))


class TestGrouper(unittest.TestCase):
    CITES = [{"numero": "BL2615138", "date": "2026-08-25"}, {"numero": "BL 2615063", "date": "2026-08-13"}]

    def test_par_bl_dans_l_ordre(self):
        lignes = [{"item_code": "A", "bl_cle": "BL2615063"}, {"item_code": "B", "bl_cle": ""},
                  {"item_code": "C", "bl_cle": "bl-2615063"}]
        g = FB.grouper(lignes, self.CITES)
        self.assertEqual([(x["cle"], x["numero"], x["date"], len(x["lignes"])) for x in g],
                         [(B.cle_numero_bl("BL2615063"), "BL 2615063", "2026-08-13", 2), ("", "", None, 1)])
        self.assertTrue(all(x["fiche"] is None for x in g))

    def test_un_bl_coche_sans_reception_garde_sa_fiche(self):
        cle = B.cle_numero_bl("BL2615063")
        g = FB.grouper([{"item_code": "A", "bl_cle": "BL2615063"}], self.CITES, {cle: "FAS-9999"})
        self.assertEqual(g[0]["fiche"], "FAS-9999")

    def test_un_bl_que_la_facture_ne_cite_pas(self):
        g = FB.grouper([{"item_code": "A", "bl_cle": "BL777"}], self.CITES)
        self.assertEqual((g[0]["numero"], g[0]["date"]), ("BL777", None))

    def test_rien(self):
        self.assertEqual(FB.grouper([], self.CITES), [])


class TestBLEnDouble(unittest.TestCase):
    """Le BL reconnu décoché : ses lignes ne se reçoivent pas une 2e fois (vu à l'essai à l'écran du 26/11/2026)."""
    G = [{"cle": B.cle_numero_bl("BL2615138"), "numero": "BL2615138", "lignes": [{}]}, {"cle": "", "numero": "", "lignes": [{}]}]
    F = {"name": "FAS-0317", "numero_bl": "BL2615138", "numero_facture": "BL2615138", "statut": "À saisir",
         "purchase_receipt": "MAT-PRE-2026-00008", "pr_docstatus": 1}

    def test_recu_et_pas_coche(self):
        d = FB.bls_en_double(self.G, [self.F], [])
        self.assertEqual([(x["numero"], x["fiche"]) for x in d], [("BL2615138", "FAS-0317")])
        self.assertIn("déjà reçu en stock", d[0]["motif"])

    def test_coche_ou_autre_bl(self):
        self.assertEqual(FB.bls_en_double(self.G, [self.F], ["FAS-0317"]), [])
        self.assertEqual(FB.bls_en_double(self.G, [dict(self.F, numero_bl="BL999", numero_facture="")], []), [])
        self.assertEqual(FB.bls_en_double([self.G[1]], [self.F], []), [])   # des lignes sans rubrique de BL

    def test_deja_couvert_ou_pas_encore_recu(self):
        couvert = dict(self.F, statut="Saisie", depense_a_payer="DAP-1892", purchase_receipt=None, pr_docstatus=None)
        self.assertIn("déjà couvert (DAP-1892)", FB.bls_en_double(self.G, [couvert], [])[0]["motif"])
        sans = dict(self.F, purchase_receipt=None, pr_docstatus=None)
        self.assertIn("pas encore reçu", FB.bls_en_double(self.G, [sans], [])[0]["motif"])
        annulee = dict(self.F, pr_docstatus=2)   # réception annulée : la fiche reste, on ne double pas
        self.assertIn("pas encore reçu", FB.bls_en_double(self.G, [annulee], [])[0]["motif"])


class TestPartage(unittest.TestCase):
    def test_facture_scps_le_timbre_est_dans_le_prix_du_bl_recu(self):
        # FT26080301 : 207,158 TTC, TVA 32,916, timbre 1,000 ; réceptions 87,291 (timbre dans le prix) + 86,962.
        p = FB.partage_hors_ht(207.158, 32.916, 174.253, timbre_lu=1.0, nb_lignes=3)
        self.assertEqual(p, {"timbre": 0.0, "arrondi": -0.011, "ecart": 0.0})

    def test_facture_classique_avec_timbre(self):
        p = FB.partage_hors_ht(120.0, 19.0, 100.0, timbre_lu=1.0, nb_lignes=2)
        self.assertEqual(p, {"timbre": 1.0, "arrondi": 0.0, "ecart": 0.0})

    def test_timbre_et_quelques_millimes(self):
        p = FB.partage_hors_ht(120.012, 19.0, 100.0, timbre_lu=1.0, nb_lignes=4)
        self.assertEqual(p, {"timbre": 1.0, "arrondi": 0.012, "ecart": 0.0})

    def test_sans_timbre_lu_un_dinar_est_un_ecart(self):
        p = FB.partage_hors_ht(120.0, 19.0, 100.0, timbre_lu=0, nb_lignes=2)
        self.assertEqual((p["timbre"], p["arrondi"], p["ecart"]), (0.0, 0.0, 1.0))

    def test_un_vrai_ecart_n_est_pas_un_arrondi(self):
        p = FB.partage_hors_ht(150.0, 19.0, 100.0, timbre_lu=1.0, nb_lignes=2)
        self.assertEqual((p["timbre"], p["arrondi"], p["ecart"]), (1.0, 0.0, 30.0))
        self.assertFalse(FB.ecart_admis(p["ecart"], 150.0))

    def test_tolerance(self):
        self.assertEqual(FB.tolerance_arrondi(0), 0.02)
        self.assertEqual(FB.tolerance_arrondi(10), 0.07)
        self.assertEqual(FB.tolerance_arrondi(-3), 0.02)

    def test_ecart_admis_comme_le_controle_des_totaux(self):
        # 1 DT, ou 1 % du TTC : ce que le contrôle de la facture d'achat laisserait passer.
        self.assertTrue(FB.ecart_admis(0.137, 207.158))
        self.assertTrue(FB.ecart_admis(-1.0, 50.0))
        self.assertFalse(FB.ecart_admis(1.5, 100.0))
        self.assertTrue(FB.ecart_admis(14.0, 1429.0))
        self.assertFalse(FB.ecart_admis(15.0, 1429.0))


class TestLibelles(unittest.TestCase):
    def test_mode_de_l_echeancier(self):
        self.assertEqual(FB.mode_echeancier("Chèque"), "Chèque")
        self.assertEqual(FB.mode_echeancier("Pas payé"), "Dette non payée")
        self.assertEqual(FB.mode_echeancier("Traite bancaire"), "Traite bancaire LC")
        self.assertEqual(FB.mode_echeancier("Mixte"), "Espèces")
        self.assertEqual(FB.mode_echeancier(None), "Espèces")

    def test_description_du_bl_recu_a_la_facture(self):
        d = FB.description_bl("Réductions SCPS", "BL2615063", "FT26080301")
        self.assertEqual(d, "Réductions SCPS — BL n°BL2615063 (reçu à la facture FT26080301)")
        self.assertEqual(FB.description_bl("", "", ""), " (reçu à la facture)")
        long = FB.description_bl("x" * 300, "BL1", "F1")
        self.assertEqual(len(long), 140)
        self.assertTrue(long.endswith("(reçu à la facture F1)"))


class TestBLPayeALaLivraison(unittest.TestCase):
    """Un BL payé à la livraison ne se SOLDE pas contre une dépense (il serait payé deux fois), mais il se FACTURE :
    son paiement est une avance que la facture d'achat reprend."""
    FICHE = {"name": "FAS-1", "est_bl": 1, "statut": "À saisir", "supplier": "SCPS", "mode_paiement": "Espèces",
             "journal_entry": "ACC-JV-1", "purchase_order": "PUR-ORD-1", "purchase_receipt": "MAT-PRE-1",
             "pr_docstatus": 1, "po_docstatus": 1, "recu": 50.0}

    def test_refuse_en_depense_admis_en_achat(self):
        en_depense = B.motif_non_traitable(dict(self.FICHE), "SCPS")
        en_achat = B.motif_non_traitable(dict(self.FICHE), "SCPS", pour="achat")
        self.assertTrue(en_depense)
        self.assertEqual(en_achat, "")
        # … et l'écran le sait, pour prévenir de ne payer que le reste.
        self.assertTrue(B.paye_a_la_livraison(self.FICHE))
        self.assertFalse(B.paye_a_la_livraison(dict(self.FICHE, mode_paiement="Pas payé", journal_entry=None)))
        self.assertTrue(B.paye_a_la_livraison({"mode_paiement": "Pas payé", "po_advance": 12.5}))
        carte = B.rapprocher_bls([{"numero": "BL1"}], [dict(self.FICHE, numero_bl="BL1", montant=59.5, motif=en_achat)])["bls"][0]
        self.assertEqual((carte["paye_livraison"], carte["bloquant"], carte["coche"]), (True, False, True))

    def test_les_autres_refus_valent_pour_les_deux(self):
        for pour in ("depense", "achat"):
            self.assertTrue(B.motif_non_traitable(dict(self.FICHE, statut="Saisie"), "SCPS", pour=pour))
            self.assertTrue(B.motif_non_traitable(dict(self.FICHE, mode_paiement="Pas payé", journal_entry=None), "AUTRE", pour=pour))


class TestEcran(unittest.TestCase):
    """La fenêtre de dépense : ce que la saisie « Facture d'achat sur BL » doit garder branché."""

    @classmethod
    def setUpClass(cls):
        import os
        ici = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(ici, "customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js"), encoding="utf-8") as f:
            cls.js = f.read()
        with open(os.path.join(ici, "caisse_depenses.py"), encoding="utf-8") as f:
            cls.py = f.read()

    def test_le_bloc_des_bl_sert_les_deux_types(self):
        for morceau in ('t === "Dépense avec facture" || t === "Facture d\'achat"', "function rcj_achat_bl(", "function rcj_achat_bl_en_jeu(",
                        "!rcj_type_avec_bls(d)", 'const pour = rcj_achat_bl(d, etat) ? "achat" : "depense";',
                        "bls_cites: JSON.stringify(etat.bls_cites || []), pour,"):
            self.assertIn(morceau, self.js, morceau)

    def test_les_deux_zones_sont_visibles_en_facture_d_achat(self):
        # Vu à l'essai à l'écran : les zones étaient remplies mais masquées par leur règle d'affichage.
        regle = 'depends_on: "eval:doc.type_depense==\\"Dépense avec facture\\" || doc.type_depense==\\"Facture d\'achat\\"",'
        for champ in ('fieldname: "zone_bls",', 'fieldname: "zone_articles",'):
            bloc = self.js[self.js.index(champ):][:600]
            self.assertIn(regle, bloc.split("},")[0], champ)

    def test_l_enregistrement_envoie_les_bl_et_les_lignes_a_recevoir(self):
        for morceau in ("const achat_bl = !initial && v.type_depense === \"Facture d'achat\" && rcj_achat_bl_en_jeu(d, etat)",
                        '(v.type_depense === "Dépense avec facture" || achat_bl) ? (etat.bls_coches || []) : []',
                        '(v.type_depense === "Dépense avec facture" || achat_bl) && etat.choix_stock === "achat"',
                        "r.message.achat", "achat_sans_article", "achat_non_lu", "achat_double", "function rcj_achat_bls_en_double("):
            self.assertIn(morceau, self.js, morceau)

    def test_l_alerte_tout_est_marchandise_bascule_en_un_clic(self):
        self.assertNotIn("tout le HT part en stock : utilisez", self.js)   # l'ancienne alerte ignorait les BL déjà reçus
        for morceau in ("rcj-vers-achat", "d.rcj_vers_facture_achat = async () =>", 'await d.set_value("type_depense", "Facture d\'achat");',
                        'etat.analyse_type === "Dépense avec facture" && !initial', "etat.achat_avec_lignes = true"):
            self.assertIn(morceau, self.js, morceau)

    def test_le_serveur_branche_la_facture_sur_bl(self):
        for morceau in ("FB.traiter(fiche.name, supplier, achat_bls, achat_lignes", 'pour="achat" if type_depense == "Facture d\'achat" else "depense"',
                        'resultat["bls_a_rattacher"]'):
            self.assertIn(morceau, self.py, morceau)


if __name__ == "__main__":
    unittest.main()
