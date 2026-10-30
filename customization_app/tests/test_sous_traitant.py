"""Sous-traitants (décisions utilisateur 30/10/2026) : règles pures, câblage des fichiers."""
from __future__ import annotations

import json
import os
import unittest

from customization_app import sous_traitant as ST
from customization_app import stock_entrepots as SE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArticles(unittest.TestCase):
    STOCK = {"F-T33-M": 5, "Acc001-B": 20}
    PRIX = {"F-T33-M": 25.0, "Acc001-B": 1.5, "X": 0}
    STOCK_X = {"F-T33-M": 5, "Acc001-B": 20, "X": 1}

    def test_lignes_valides_et_cumul_des_doublons(self):
        lignes, err = ST.lignes_articles([{"item_code": "F-T33-M", "qty": 2}, {"item_code": "F-T33-M", "qty": 3},
                                          {"item_code": "Acc001-B", "qty": 1}], self.STOCK, self.PRIX)
        self.assertEqual(err, [])
        self.assertEqual(lignes, [{"item_code": "F-T33-M", "qty": 2.0, "rate": 25.0},
                                  {"item_code": "F-T33-M", "qty": 3.0, "rate": 25.0},
                                  {"item_code": "Acc001-B", "qty": 1.0, "rate": 1.5}])

    def test_refus_stock_insuffisant_prix_absent_quantite_nulle(self):
        lignes, err = ST.lignes_articles([{"item_code": "F-T33-M", "qty": 6}, {"item_code": "X", "qty": 1},
                                          {"item_code": "Acc001-B", "qty": 0}], self.STOCK_X, self.PRIX)
        self.assertEqual(lignes, [])
        self.assertEqual(len(err), 3)
        self.assertIn("6.0 demandé, 5.0 dans votre stock", err[0])
        self.assertIn("pas de prix de vente", err[1])
        self.assertEqual(ST.lignes_articles([], {}, {})[1], ["Aucun article."])


class TestReglement(unittest.TestCase):
    def test_fractionne_qui_tombe_juste(self):
        lignes, err = ST.lignes_paiement([{"mode": "Espèces", "montant": 40},
                                          {"mode": "Chèque", "montant": 60.5, "numero": "123", "banque": "BIAT"}],
                                         100.5, "2026-10-30")
        self.assertEqual(err, [])
        self.assertEqual(lignes[1], {"mode_of_payment": "Chèque", "payment_amount": 60.5, "due_date": "2026-10-31",
                                     "custom__n_chèque__transaction": "123", "custom_banque": "BIAT"})
        self.assertNotIn("custom__n_chèque__transaction", lignes[0])
        self.assertEqual(lignes[0]["due_date"], "2026-10-30")      # fractionné : J, J+1… (ERPNext refuse deux échéances le même jour)

    def test_refus_virement_carte_cheque_sans_numero_et_ecart(self):
        _, err = ST.lignes_paiement([{"mode": "Virement", "montant": 10}, {"mode": "Carte de crédit", "montant": 10},
                                     {"mode": "Chèque", "montant": 10}], 30)
        self.assertEqual(len(err), 4)                                    # 3 refus + aucun règlement
        self.assertIn("non permis : Virement", err[0])
        self.assertIn("numéro est obligatoire", err[2])
        _, err = ST.lignes_paiement([{"mode": "Espèces", "montant": 10}, {"mode": "Dette non payée", "montant": 15}], 30)
        self.assertEqual(err, ["Les règlements font 25.0, la commande 30.0."])
        self.assertEqual(ST.MODES_PERMIS, ("Espèces", "Chèque", "Traite bancaire LC", "Dette non payée"))


class TestNomsEtCouleurs(unittest.TestCase):
    def test_noms(self):
        self.assertEqual(ST.nom_entrepot("  Ali   Ben Salah "), "Stock Ali Ben Salah")
        self.assertEqual(ST.nom_compte(" Ali  Ben Salah"), "Ali Ben Salah")

    def test_couleur_stable_et_dans_la_palette(self):
        self.assertEqual(ST.couleur_par_defaut("HR-EMP-00020"), ST.couleur_par_defaut("HR-EMP-00020"))
        self.assertIn(ST.couleur_par_defaut("HR-EMP-00020"), ST.PALETTE)


class TestVerificationQuinzaine(unittest.TestCase):
    def test_saute_si_une_verification_dans_les_13_jours(self):
        self.assertTrue(SE.quinzaine_a_sauter("2026-11-09", ["2026-11-02"]))
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", ["2026-10-26"]))
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", ["2026-11-09"]))      # la même date : déjà filtrée avant
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", []))
        self.assertEqual((SE.HEBDO, SE.QUINZAINE), ("Chaque semaine", "Toutes les deux semaines"))


class TestCablage(unittest.TestCase):
    def test_hooks_droits_scripts_et_pages(self):
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            hooks = f.read()
        for attendu in ('"Tache de travail": "customization_app.sous_traitant.permission_query_conditions_tache"',
                        '"Sales Order": "customization_app.sous_traitant.has_permission_commande"',
                        'boot_session = "customization_app.sous_traitant.boot_session"',
                        '_js("cloture_sous_traitant.js")', '_js("sous_traitant_admin.js")', '_js("sous_traitant.js")'):
            self.assertIn(attendu, hooks)
        with open(os.path.join(BASE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.sous_traitant_role", f.read())
        with open(os.path.join(BASE, "fixtures", "server_script.json"), encoding="utf-8") as f:
            scripts = {s["name"]: s["script"] for s in json.load(f)}
        # le routage (1 lecture + son commentaire) dans chaque script, et la règle de verrou dans le second
        self.assertEqual(scripts["Generation payement"].count("custom_compte_caisse"), 2)
        self.assertEqual(scripts["re-generate payment after sales order"].count("custom_compte_caisse"), 3)
        with open(os.path.join(BASE, "customize_erpnext", "page", "stock_entrepots", "stock_entrepots.json"),
                  encoding="utf-8") as f:
            self.assertIn({"role": "Sous-traitant"}, json.load(f)["roles"])
        with open(os.path.join(BASE, "customize_erpnext", "doctype", "config_stock_entrepot_verification",
                               "config_stock_entrepot_verification.json"), encoding="utf-8") as f:
            self.assertIn("periodicite", json.load(f)["field_order"])
        from customization_app import caisse_cloture
        self.assertIn("Sous-traitant", caisse_cloture.ROLES)
        self.assertIn("Sous-traitant", SE.LECTEURS)
