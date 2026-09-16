"""Tests de l'encaissement des colis Aramex livrés depuis la caisse (16/09/2026).

Convention : `unittest.TestCase` pur, aucune base — comme
`test_caisse_encaissement_dettes`. Ce qui se teste ici est ce qui décide de
l'écriture : la règle de saisie de la pièce par mode, le libellé du paiement
recréé (qui doit rester lisible par `reference_aramex` et porter la référence
bancaire), le choix des lignes d'échéancier à aligner, et l'ordre des refus
opposés à une sélection. La suppression/recréation des Payment Entry touche la
comptabilité et se vérifie en recette.
"""
from __future__ import annotations

import os
import unittest

from customization_app import caisse_encaissement_aramex as CEA
from customization_app.livraison_aramex import reference_aramex

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "customize_erpnext", "page", "caisse_journaliere")


class TestPiece(unittest.TestCase):
    def test_un_virement_passe_avec_sa_reference_sans_photo(self):
        self.assertEqual(CEA.motif_refus_piece("Virement", "FT26260ABCDE", "", None), "")

    def test_un_virement_sans_reference_est_refuse(self):
        self.assertIn("référence du virement", CEA.motif_refus_piece("Virement", "", "", None))

    def test_un_cheque_exige_sept_chiffres_banque_et_photo(self):
        self.assertIn("7 chiffres", CEA.motif_refus_piece("Chèque", "12345", "BIAT", "p"))
        self.assertIn("banque", CEA.motif_refus_piece("Chèque", "1234567", "", "p"))
        self.assertIn("photo", CEA.motif_refus_piece("Chèque", "1234567", "BIAT", None))
        self.assertEqual(CEA.motif_refus_piece("Chèque", "1234567", "BIAT", "p"), "")

    def test_une_traite_passe_sans_banque_mais_avec_photo(self):
        self.assertEqual(CEA.motif_refus_piece("Traite bancaire", "4521", "", "p"), "")
        self.assertIn("photo", CEA.motif_refus_piece("Traite bancaire", "4521", "", None))

    def test_un_mode_inconnu_est_refuse(self):
        self.assertIn("inconnu", CEA.motif_refus_piece("Espèces", "1", "", None))

    def test_les_comptes_par_mode(self):
        self.assertEqual(CEA.MODES["Virement"]["compte"], "STE430127B - Zitouna - A&S")
        self.assertEqual(CEA.MODES["Chèque"]["compte"], "Chèques - A&S")
        self.assertEqual(CEA.MODES["Traite bancaire"]["compte"], "Traite Bancaire - A&S")
        self.assertEqual(CEA.MODES["Traite bancaire"]["mode"], "Traite bancaire LC")


class TestLibelle(unittest.TestCase):
    """Le paiement recréé garde « Aramex N: … » en tête et cite la pièce."""

    def test_virement(self):
        lib = CEA.libelle_reference("Virement", "FT26260ABCDE", "", "50919841361")
        self.assertEqual(lib, "Aramex N: 50919841361 / Virement reçu N: FT26260ABCDE")
        self.assertEqual(reference_aramex(lib), "50919841361")

    def test_cheque_avec_banque(self):
        lib = CEA.libelle_reference("Chèque", "1234567", "BIAT", "50919841361")
        self.assertEqual(lib, "Aramex N: 50919841361 / Chèque N° 1234567 - BIAT")
        self.assertEqual(reference_aramex(lib), "50919841361")

    def test_traite_sans_banque(self):
        self.assertEqual(CEA.libelle_reference("Traite bancaire", "4521", "", "48812240761"),
                         "Aramex N: 48812240761 / Traite N° 4521")


class TestEcheancier(unittest.TestCase):
    L = [
        {"name": "esp", "mode_of_payment": "Espèces", "custom__n_chèque__transaction": "",
         "payment_amount": 20},
        {"name": "ar1", "mode_of_payment": "Dette non payée",
         "custom__n_chèque__transaction": "Aramex N: 50919841361", "payment_amount": 56},
        {"name": "ar2", "mode_of_payment": "Dette non payée",
         "custom__n_chèque__transaction": "0000", "payment_amount": 30},
    ]

    def test_la_ligne_qui_porte_le_bordereau_est_choisie(self):
        self.assertEqual(CEA.lignes_echeancier_a_aligner(self.L, "50919841361", 56), ["ar1"])

    def test_sans_numero_on_retombe_sur_le_montant_exact(self):
        self.assertEqual(CEA.lignes_echeancier_a_aligner(self.L, "99999999", 30), ["ar2"])

    def test_jamais_une_avance_d_un_autre_mode(self):
        self.assertEqual(CEA.lignes_echeancier_a_aligner(self.L, "99999999", 20), [])

    def test_sans_ligne_d_attente_rien(self):
        self.assertEqual(CEA.lignes_echeancier_a_aligner([], "1", 1), [])


class TestSelection(unittest.TestCase):
    D = [{"paiement": "PE-1", "montant": 56, "en_cours": False},
         {"paiement": "PE-2", "montant": 30, "en_cours": True}]

    def test_la_selection_est_rendue_dans_l_ordre(self):
        choisis, refus = CEA.trier_selection(self.D, ["PE-1"])
        self.assertIsNone(refus)
        self.assertEqual([c["paiement"] for c in choisis], ["PE-1"])

    def test_un_colis_disparu_refuse_tout(self):
        choisis, refus = CEA.trier_selection(self.D, ["PE-1", "PE-9"])
        self.assertEqual(choisis, [])
        self.assertEqual(refus, (CEA.REFUS_DISPARUS, ["PE-9"]))

    def test_un_colis_en_cours_de_rapprochement_est_nomme(self):
        choisis, refus = CEA.trier_selection(self.D, ["PE-2"])
        self.assertEqual(refus, (CEA.REFUS_EN_COURS, ["PE-2"]))

    def test_sans_selection_refus_generique(self):
        self.assertEqual(CEA.trier_selection(self.D, [])[1], (CEA.REFUS_AUCUN, []))


class TestContratDuDialogue(unittest.TestCase):
    def test_le_bouton_et_le_dialogue_existent(self):
        with open(os.path.join(PAGE, "caisse_journaliere.html"), encoding="utf-8") as f:
            self.assertIn('id="rcj-btn-aramex"', f.read())
        with open(os.path.join(PAGE, "caisse_journaliere.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function rcj_encaissement_aramex", js)
        bloc = js[js.index("function rcj_encaissement_aramex"):]
        self.assertIn('method: API + ".encaisser"', bloc)
        self.assertIn('method: API + ".colis"', bloc)
        self.assertIn('const MODES = ["Virement", "Chèque", "Traite bancaire"]', bloc)


class TestDispenseDePhoto(unittest.TestCase):
    """Le code de dispense (vérifié en amont) lève l'obligation de photo — et rien d'autre."""

    def test_sans_dispense_la_photo_est_exigee(self):
        self.assertIn("photo", CEA.motif_refus_piece("Chèque", "1234567", "BIAT", None))

    def test_avec_dispense_le_cheque_passe_sans_photo(self):
        self.assertEqual(CEA.motif_refus_piece("Chèque", "1234567", "BIAT", None, dispense=True), "")
        self.assertEqual(CEA.motif_refus_piece("Traite bancaire", "4521", "", None, dispense=True), "")

    def test_la_dispense_ne_leve_pas_les_autres_regles(self):
        self.assertIn("7 chiffres", CEA.motif_refus_piece("Chèque", "12", "BIAT", None, dispense=True))
        self.assertIn("banque", CEA.motif_refus_piece("Chèque", "1234567", "", None, dispense=True))

    def test_le_dialogue_porte_le_code_et_les_avertissements(self):
        with open(os.path.join(PAGE, "caisse_journaliere.js"), encoding="utf-8") as f:
            js = f.read()
        bloc = js[js.index("function rcj_encaissement_aramex"):]
        self.assertIn("rcj_champ_code_sans_photo(", bloc)
        self.assertIn("code_sans_photo: v.code_sans_photo", bloc)
        self.assertIn("rcj_afficher_avertissements(res)", bloc)


class TestRepartition(unittest.TestCase):
    """Plusieurs pièces sur plusieurs colis, FIFO ; reste -> dette restante ; trop -> avoir."""

    C = [{"paiement": "PE-1", "montant": 934.9, "client": "EGES"},
         {"paiement": "PE-2", "montant": 422.3, "client": "EGES"}]

    def test_une_piece_exacte(self):
        r = CEA.repartir(self.C, [{"montant": 1357.2}])
        self.assertEqual(r["allocations"], [{"colis": 0, "piece": 0, "portion": 934.9},
                                            {"colis": 1, "piece": 0, "portion": 422.3}])
        self.assertEqual(r["reliquats"], [])
        self.assertEqual(r["excedents"], [])

    def test_deux_pieces_dont_une_a_cheval(self):
        r = CEA.repartir(self.C, [{"montant": 500}, {"montant": 857.2}])
        self.assertEqual(r["allocations"], [{"colis": 0, "piece": 0, "portion": 500.0},
                                            {"colis": 0, "piece": 1, "portion": 434.9},
                                            {"colis": 1, "piece": 1, "portion": 422.3}])
        self.assertEqual(r["reliquats"], [])
        self.assertEqual(r["excedents"], [])

    def test_recu_insuffisant_donne_une_dette_restante(self):
        r = CEA.repartir(self.C, [{"montant": 1000}])
        self.assertEqual(r["reliquats"], [{"colis": 1, "montant": 357.2}])
        self.assertEqual(r["excedents"], [])
        self.assertEqual(r["allocations"][-1], {"colis": 1, "piece": 0, "portion": 65.1})

    def test_colis_non_couvert_du_tout(self):
        r = CEA.repartir(self.C, [{"montant": 934.9}])
        self.assertEqual(r["reliquats"], [{"colis": 1, "montant": 422.3}])
        self.assertEqual([a["colis"] for a in r["allocations"]], [0])

    def test_recu_excedentaire_donne_un_avoir_par_piece(self):
        r = CEA.repartir(self.C, [{"montant": 1357.2}, {"montant": 50}])
        self.assertEqual(r["excedents"], [{"piece": 1, "montant": 50.0}])
        self.assertEqual(r["reliquats"], [])

    def test_arrondi_au_millime(self):
        r = CEA.repartir([{"paiement": "a", "montant": 10.001}], [{"montant": 10.0}])
        self.assertEqual(r["reliquats"], [{"colis": 0, "montant": 0.001}])
        r = CEA.repartir([{"paiement": "a", "montant": 10.0}], [{"montant": 10.0004}])
        self.assertEqual(r["reliquats"], [])
        self.assertEqual(r["excedents"], [])


class TestContratMultiPieces(unittest.TestCase):
    def test_le_dialogue_envoie_des_paiements(self):
        with open(os.path.join(PAGE, "caisse_journaliere.js"), encoding="utf-8") as f:
            js = f.read()
        bloc = js[js.index("function rcj_encaissement_aramex"):]
        self.assertIn("paiements: JSON.stringify(", bloc)
        self.assertIn("dette restante", bloc.lower())
        self.assertIn("avoir client", bloc.lower())
