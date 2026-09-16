"""Tests des modes « Virement » et « Traite bancaire » des dépenses de caisse
(demande utilisateur 16/09/2026).

Convention : `unittest.TestCase` pur, aucune base. Ce qui se teste : la règle de
saisie par mode, le COMPTE crédité (la traite attend sur le découvert, jamais la
banque), la mention de remarque de la traite et sa relecture (rapport de caisse,
bank_retenue_sync), et la lecture du mode par le rapport.
"""
from __future__ import annotations

import os
import unittest

from customization_app import caisse_depenses as CD
from customization_app import rapport_caisse_journaliere as RCJ

DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")


class TestReglesDeSaisie(unittest.TestCase):
    def test_les_modes(self):
        self.assertEqual(CD.MODES, ("Espèces", "Chèque", "Carte de crédit", "Virement",
                                    "Traite bancaire"))

    def test_le_cheque_garde_ses_regles(self):
        self.assertIn("7 chiffres", CD.motif_refus_reglement("Chèque", "12", "BIAT", "p"))
        self.assertIn("banque", CD.motif_refus_reglement("Chèque", "1234567", "", "p"))
        self.assertIn("photo", CD.motif_refus_reglement("Chèque", "1234567", "BIAT", None))
        self.assertEqual(CD.motif_refus_reglement("Chèque", "1234567", "BIAT", "p"), "")

    def test_le_virement_accepte_une_reference_vide(self):
        self.assertEqual(CD.motif_refus_reglement("Virement", "", "", None), "")
        self.assertEqual(CD.motif_refus_reglement("Virement", "FT26260ABCDE", "", None), "")
        self.assertIn("3 à 30", CD.motif_refus_reglement("Virement", "a b", "", None))

    def test_la_traite_exige_numero_et_echeance(self):
        self.assertIn("4 à 20", CD.motif_refus_reglement("Traite bancaire", "12", "", None,
                                                          "2026-10-30"))
        self.assertIn("échéance", CD.motif_refus_reglement("Traite bancaire", "4521", "", None))
        self.assertEqual(CD.motif_refus_reglement("Traite bancaire", "4521", "", None,
                                                  "2026-10-30"), "")

    def test_especes_et_carte_sans_contrainte(self):
        self.assertEqual(CD.motif_refus_reglement("Espèces", "", "", None), "")
        self.assertEqual(CD.motif_refus_reglement("Carte de crédit", "", "", None), "")


class TestComptes(unittest.TestCase):
    def test_la_traite_attend_sur_le_decouvert(self):
        self.assertEqual(CD.compte_credit("Traite bancaire"), CD.COMPTE_DECOUVERT)

    def test_les_autres_modes_bancaires_vont_a_zitouna(self):
        for mode in ("Chèque", "Carte de crédit", "Virement"):
            self.assertEqual(CD.compte_credit(mode), CD.COMPTE_BANQUE, mode)

    def test_les_especes_sortent_de_la_caisse(self):
        self.assertEqual(CD.compte_credit("Espèces"), CD.COMPTE_ESPECES)

    def test_lignes_credit(self):
        regs = [{"mode": "Espèces", "montant": 10}, {"mode": "Traite bancaire", "montant": 90},
                {"mode": "Pas payé", "montant": 5}]
        self.assertEqual([(l["account"], l["credit_in_account_currency"])
                          for l in CD._lignes_credit(regs)],
                         [(CD.COMPTE_ESPECES, 10), (CD.COMPTE_DECOUVERT, 90)])


class TestMentionTraite(unittest.TestCase):
    REG = {"mode": "Traite bancaire", "montant": 1250.5, "n_cheque": "4521",
           "banque": "Amen Bank", "echeance": "2026-11-15"}

    def setUp(self):
        # `_()` a besoin d'un site : seule la LOGIQUE se teste ici.
        self._vrai = CD._
        CD._ = lambda message: message

    def tearDown(self):
        CD._ = self._vrai

    def test_libelle_et_relecture(self):
        texte = CD.libelle_traite(self.REG)
        self.assertEqual(texte, "Traite N° 4521 - Bq Amen Bank - 1250.500 DT - échéance 2026-11-15")
        lu = CD.parser_traites("Fournisseur : X\n" + texte + "\nSaisie caisse par a@b")
        self.assertEqual(lu, [{"numero": "4521", "banque": "Amen Bank", "montant": 1250.5,
                               "echeance": "2026-11-15"}])

    def test_sans_banque(self):
        reg = dict(self.REG, banque="")
        lu = CD.parser_traites(CD.libelle_traite(reg))
        self.assertEqual(lu[0]["banque"], "")
        self.assertEqual(lu[0]["numero"], "4521")

    def test_deux_traites_dans_une_remarque(self):
        texte = CD.libelle_traite(self.REG) + "\n" + CD.libelle_traite(
            dict(self.REG, n_cheque="4522", montant=300, echeance="2026-12-15"))
        self.assertEqual([t["numero"] for t in CD.parser_traites(texte)], ["4521", "4522"])

    def test_les_remarques_de_paiement(self):
        remarques = CD._remarques_paiements([], [
            {"mode": "Virement", "montant": 10, "n_cheque": "FT1"},
            {"mode": "Virement", "montant": 10, "n_cheque": ""},
            self.REG])
        self.assertEqual(remarques[0], "Réglé par virement | Réf de paiement : FT1")
        self.assertEqual(remarques[1], "Réglé par virement")
        self.assertTrue(remarques[2].startswith("Traite N° 4521"))


class TestLectureParLeRapport(unittest.TestCase):
    def test_especes(self):
        self.assertEqual(RCJ._mode_depense("Espèces - A&S", "", 10), "Espèces")

    def test_traite_sur_le_decouvert_du_meme_montant(self):
        remarque = CD.libelle_traite({"mode": "Traite bancaire", "montant": 90, "n_cheque": "4521",
                                      "banque": "", "echeance": "2026-11-15"})
        self.assertEqual(RCJ._mode_depense(CD.COMPTE_DECOUVERT, remarque, 90), "Traite bancaire")

    def test_une_part_pas_payee_sur_le_decouvert_n_est_pas_un_reglement(self):
        self.assertEqual(RCJ._mode_depense(CD.COMPTE_DECOUVERT, "Reste à payer : 40", 40), "")

    def test_virement_cheque_carte(self):
        self.assertEqual(RCJ._mode_depense(CD.COMPTE_BANQUE, "Chq N° 1234567 - Bq BIAT", 5), "Chèque")
        self.assertEqual(RCJ._mode_depense(CD.COMPTE_BANQUE, "Réglé par virement", 5), "Virement")
        self.assertEqual(RCJ._mode_depense(CD.COMPTE_BANQUE, "Réglé par carte bancaire", 5),
                         "Carte de crédit")


class TestContratDuDialogue(unittest.TestCase):
    def test_les_dialogues_offrent_les_modes(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        self.assertGreaterEqual(js.count('Espèces\\nChèque\\nCarte de crédit\\nVirement\\nTraite bancaire'), 3)
        self.assertIn('fieldname: "echeance"', js)
        self.assertIn("rcj-pay-echeance", js)


class TestDispenseDePhoto(unittest.TestCase):
    def test_le_cheque_passe_sans_photo_avec_dispense(self):
        self.assertEqual(CD.motif_refus_reglement("Chèque", "1234567", "BIAT", None, dispense=True), "")
        self.assertIn("photo", CD.motif_refus_reglement("Chèque", "1234567", "BIAT", None))

    def test_la_dispense_ne_leve_ni_numero_ni_banque(self):
        self.assertIn("7 chiffres", CD.motif_refus_reglement("Chèque", "1", "BIAT", None, dispense=True))
        self.assertIn("banque", CD.motif_refus_reglement("Chèque", "1234567", "", None, dispense=True))

    def test_tous_les_dialogues_portent_le_code(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        # dettes, Aramex, dépense, payer une dépense, règlement fournisseur
        self.assertGreaterEqual(js.count("rcj_champ_code_sans_photo("), 5 + 1)
        self.assertGreaterEqual(js.count("rcj_afficher_avertissements("), 4 + 1)
        self.assertIn("function rcj_collecter_paiements(etat, montant, dispense)", js)


class TestAvertissementsPieces(unittest.TestCase):
    """`caisse_pieces.avertissements` ne regarde que chèque / traite AVEC photo, et ne lève jamais."""

    def test_sans_piece_papier_rien(self):
        from customization_app import caisse_pieces as CP
        self.assertEqual(CP.avertissements([{"mode": "Virement", "numero": "FT1", "montant": 5,
                                             "photo": "data:image/png;base64,x"},
                                            {"mode": "Chèque", "numero": "1234567", "montant": 5,
                                             "photo": None}]), [])

    def test_une_panne_de_lecture_devient_un_avertissement(self):
        from customization_app import caisse_pieces as CP
        from customization_app import caisse_encaissement_dettes as CED
        vrais = (CED._verifier_photo, CP._)
        CED._verifier_photo = lambda p: (_ for _ in ()).throw(RuntimeError("panne"))
        CP._ = lambda m: m
        try:
            avert = CP.avertissements([{"mode": "Chèque", "numero": "1234567", "montant": 5,
                                        "photo": "data:image/png;base64,x"}])
        finally:
            CED._verifier_photo, CP._ = vrais
        self.assertEqual(len(avert), 1)


class TestNormalisationBanque(unittest.TestCase):
    B = ["ABC", "Amen Bank", "ATB", "Attijari Bank", "Banque Zitouna", "BH", "BIAT", "BNA",
         "BT", "BTE", "BTK", "STB", "UBCI", "UIB"]

    def test_sigle_en_mot_entier(self):
        from customization_app.caisse_pieces import normaliser_banque as N
        self.assertEqual(N("BIAT - Agence Soukra", self.B), "BIAT")
        self.assertEqual(N("BT agence Ariana", self.B), "BT")
        self.assertEqual(N("BTE", self.B), "BTE")

    def test_alias_et_accents(self):
        from customization_app.caisse_pieces import normaliser_banque as N
        self.assertEqual(N("Banque ZITOUNA", self.B), "Banque Zitouna")
        self.assertEqual(N("Attijari bank Tunisie", self.B), "Attijari Bank")
        self.assertEqual(N("Banque Internationale Arabe de Tunisie", self.B), "BIAT")
        self.assertEqual(N("Société Tunisienne de Banque", self.B), "STB")

    def test_inconnu_ou_vide(self):
        from customization_app.caisse_pieces import normaliser_banque as N
        self.assertEqual(N("", self.B), "")
        self.assertEqual(N("Banque de Mars", self.B), "")


class TestLectureDePieceDansLesDialogues(unittest.TestCase):
    """Chaque point de capture d'une photo de chèque / traite ouvre l'aperçu et lit la
    pièce : dettes, règlement fournisseur, Aramex, dépense, lignes fractionnées, payer une
    dépense."""

    def test_six_points_de_capture(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function rcj_photo_piece_ajoutee", js)
        self.assertGreaterEqual(js.count("rcj_photo_piece_ajoutee("), 6 + 1)
        self.assertIn('method: "customization_app.caisse_pieces.lire_piece"', js)


class TestZerosDeTeteDuNumero(unittest.TestCase):
    """Un numéro « 0012345 » doit ressortir tel quel — chèque ou traite (retour utilisateur
    16/09/2026 : le pré-remplissage perdait les zéros de tête)."""

    def test_chaine_conservee(self):
        from customization_app.caisse_pieces import normaliser_numero as N
        self.assertEqual(N("0012345", "Chèque"), "0012345")
        self.assertEqual(N("000451", "Traite bancaire"), "000451")
        self.assertEqual(N("N° 0012345", "Chèque"), "0012345")

    def test_nombre_json_complete_pour_un_cheque(self):
        from customization_app.caisse_pieces import normaliser_numero as N
        self.assertEqual(N(12345, "Chèque"), "0012345")
        self.assertEqual(N(12345.0, "Chèque"), "0012345")
        self.assertEqual(N(4001012, "Chèque"), "4001012")

    def test_traite_sans_longueur_fixe(self):
        from customization_app.caisse_pieces import normaliser_numero as N
        self.assertEqual(N(451, "Traite bancaire"), "451")
        self.assertEqual(N(None, "Chèque"), "")


class TestBanqueEnArabe(unittest.TestCase):
    """Sur une traite, la domiciliation est souvent imprimée en arabe."""

    B = ["Al Baraka Bank", "Amen Bank", "Attijari Bank", "Banque Zitouna", "BIAT", "BNA", "STB"]

    def test_noms_arabes(self):
        from customization_app.caisse_pieces import normaliser_banque as N
        self.assertEqual(N("بنك الزيتونة", self.B), "Banque Zitouna")
        self.assertEqual(N("بنك البركة تونس", self.B), "Al Baraka Bank")
        self.assertEqual(N("البنك التجاري", self.B), "Attijari Bank")
        self.assertEqual(N("البنك الوطني الفلاحي", self.B), "BNA")

    def test_le_latin_garde_la_priorite_quand_les_deux_sont_la(self):
        from customization_app.caisse_pieces import normaliser_banque as N
        self.assertEqual(N("BIAT بنك", self.B), "BIAT")


class TestLectureQuelQueSoitLeMode(unittest.TestCase):
    """Une traite photographiée sur une ligne restée « Virement » doit être lue quand même.

    Constat en prod le 16/09/2026 : la lecture ne se déclenchait que si le mode était déjà
    Chèque ou Traite. Rien ne se passait, sans un mot, et la lecture passait pour cassée.
    """

    def test_le_garde_par_mode_a_disparu(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        bloc = js[js.index("function rcj_photo_piece_ajoutee"):][:900]
        self.assertNotIn('if (!["Chèque", "Traite bancaire"].includes(mode)) return;', bloc)
        self.assertIn("lu.type_lu", bloc)
        self.assertIn("mode_propose", bloc)

    def test_chaque_point_de_capture_suit_le_mode_lu(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        # 6 points de capture + la pose dans le helper = 7 mentions minimum.
        self.assertGreaterEqual(js.count("mode_propose"), 7)
        # Jamais écrasé quand l'employé a déjà saisi un numéro.
        self.assertIn('if (lu.mode_propose && !(p.n_piece || "").trim()) p.mode = lu.mode_propose;', js)

    def test_le_lecteur_rend_le_type_de_la_piece(self):
        import inspect

        from customization_app import caisse_pieces as CP
        src = inspect.getsource(CP.lire_piece)
        self.assertIn('"type_lu"', src)
        self.assertIn("traite", src)
