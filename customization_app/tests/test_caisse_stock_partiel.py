"""Dépense de caisse avec une part en stock (10/11/2026) : ventilation et partage du paiement. Sans site."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import caisse_stock_partiel as S


class TestVentilation(unittest.TestCase):

    def test_lignes_valides(self):
        lignes = S.lignes_valides('[{"item_code": " A ", "qty": 2, "rate": "10.5", "designation": "x"}, {"item_code": "", "qty": 1, "rate": 1}, {"item_code": "B", "qty": 0, "rate": 1}]')
        self.assertEqual(lignes, [{"item_code": "A", "qty": 2.0, "rate": 10.5, "designation": "x", "bl_cle": ""}])
        self.assertEqual(S.lignes_valides(None), [])

    def test_ventiler_la_depense_reste_entiere_le_stock_est_ht(self):
        v = S.ventiler_stock(1191.0, 190.0, [{"qty": 2, "rate": 100}, {"qty": 1, "rate": 50}])
        self.assertEqual((v["stock_ht"], v["ht_total"]), (250.0, 1001.0))
        # ce que la facture d'achat finale ne portera pas : le HT hors stock et TOUTE la TVA
        self.assertEqual((v["depense_ht"], v["depense_tva"], v["depense_ttc"]), (751.0, 190.0, 941.0))
        self.assertEqual(v["ht_total"] - v["depense_ht"], v["stock_ht"])

    def test_remarque(self):
        v = S.ventiler_stock(119.0, 19.0, [{"qty": 1, "rate": 50}])
        r = S.remarque(v, 1)
        self.assertIn("Part mise en stock : 50 HT (1 article(s))", r)
        self.assertIn("tout le HT (100.0) et toute la TVA (19.0)", r)


class TestCablage(unittest.TestCase):

    def setUp(self):
        self.base = os.path.dirname(inspect.getfile(customization_app))

    def _lire(self, *chemin):
        with open(os.path.join(self.base, *chemin), encoding="utf-8") as fh:
            return fh.read()

    def test_creer_porte_la_part_stock(self):
        from customization_app import caisse_depenses as C
        src = inspect.getsource(C.creer)
        self.assertIn("articles_stock=None", src)
        self.assertIn("S.preparer(articles_stock, montant, tva)", src)
        self.assertIn("S.creer_part_stock(stock", src)
        self.assertIn('"purchase_order": part["purchase_order"]', src)
        self.assertIn("or stock or bls:", src)
        # BL déjà reçus que la facture couvre (25/11/2026) : contrôlés sous verrou, puis soldés
        self.assertIn("bls_couverts=None", src)
        self.assertIn("B.valider(", src)
        self.assertIn("B.couvrir_par_depense(", src)
        # la dépense reste entière : ni montant, ni TVA, ni paiement ne sont réduits par la part stock
        self.assertNotIn("regs_depense", src)
        self.assertNotIn("stock_ttc", src)

    def test_la_commande_est_sans_tva_et_l_avance_est_une_ecriture_sur_la_commande(self):
        src = inspect.getsource(S.creer_part_stock)
        self.assertNotIn('po.append("taxes"', src)
        self.assertEqual(src.count("_sans_taxes(po)"), 2)
        self.assertIn("po.disable_rounded_total = 1", src)
        self.assertFalse(hasattr(S, "_facture_immediate"))      # aucune facture d'achat automatique
        self.assertNotIn("Purchase Invoice", src)
        self.assertIn('"is_advance": "Yes"', src)
        self.assertIn('"reference_type": "Purchase Order"', src)
        # BL : l'avance est financée par le compte de charge, hors du rapport de caisse (aucune ligne de caisse)
        self.assertIn('{"account": ctx["compte"], "credit_in_account_currency": stock["stock_ht"]', src)
        self.assertIn("prefixe=PREFIXE_FINANCEMENT", src)
        from customization_app import caisse_depenses as C
        self.assertFalse(S.PREFIXE_FINANCEMENT.startswith(("Dépense caisse", C.PREFIXE_A_PAYER)))
        # pièce déjà facturée : le geste commun « solder la réception » (25/11/2026)
        self.assertIn("solder_reception(fiche.name", src)
        solde = inspect.getsource(S.solder_reception)
        self.assertIn('po.update_status("Closed")', solde)
        self.assertIn('pr.update_status("Closed")', solde)                  # la réception aussi, désormais
        self.assertIn('"statut": "Saisie"', solde)
        self.assertIn("B.lignes_solde(valeur, ctx[\"compte\"], compte_stock_recu_non_facture()", solde)
        self.assertIn("prefixe=B.PREFIXE_SOLDE", solde)                      # hors du rapport de caisse
        self.assertIn("B.valeur_recu(recu)", solde)                          # soldée à la valeur du reçu
        from customization_app import caisse_depenses as C
        self.assertIn("S.avance_en_journal(nom)", inspect.getsource(C.po_convertir_avances))
        self.assertIn("S.avance_en_journal(nom)", inspect.getsource(C.pi_marquer_fiche_saisie))

    def test_fiches(self):
        import json
        dap = json.load(open(os.path.join(self.base, "customize_erpnext", "doctype", "depense_a_payer", "depense_a_payer.json"), encoding="utf-8"))
        fas = json.load(open(os.path.join(self.base, "customize_erpnext", "doctype", "facture_achat_a_saisir", "facture_achat_a_saisir.json"), encoding="utf-8"))
        self.assertTrue({"purchase_order", "fiche_achat", "part_stock_ht", "articles_stock"} <= {f["fieldname"] for f in dap["fields"]})
        self.assertTrue({"depense_a_payer", "part_depense_ht", "part_depense_tva", "part_depense_ttc"} <= {f["fieldname"] for f in fas["fields"]})

    def test_dialogue(self):
        js = self._lire("customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js")
        for m in ("zone_articles", 'caisse_stock_partiel") + ".lire_articles"', "articles_stock:",
                  "zone_bls", "bls_couverts:", "caisse_bl.bls_ouverts", "rcj_ligne_deja_recue"):
            self.assertIn(m, js, m)
        from customization_app import caisse_depenses as C
        self.assertIn("bls_cites", inspect.getsource(C._classifier))
        self.assertIn('\\"bl\\"', inspect.getsource(S.lire_articles))


class TestRemises(unittest.TestCase):
    """La part stock au prix NET payé (25/11/2026 : « elle n'a pas lu la remise »)."""

    def test_remise_de_ligne_par_le_montant_imprime(self):
        r = S.prix_nets([{"designation": "REDUCTION LAITON", "quantite": 5, "prix_unitaire_ht": 8.75,
                          "remise_pct": 20, "montant_ht": 35.0}])
        l = r["lignes"][0]
        self.assertEqual((l["rate"], l["rate_brut"], l["remise_pct"], l["montant_ht"]), (7.0, 8.75, 20.0, 35.0))
        self.assertEqual((r["facteur"], r["remise_globale"]), (1.0, 0.0))

    def test_remise_de_ligne_sans_montant_ou_montant_brut_recopie(self):
        sans = S.prix_nets([{"quantite": 2, "prix_unitaire_ht": 10, "remise_pct": 10}])["lignes"][0]
        self.assertEqual(sans["rate"], 9.0)
        recopie = S.prix_nets([{"quantite": 2, "prix_unitaire_ht": 10, "remise_pct": 10, "montant_ht": 20}])["lignes"][0]
        self.assertEqual(recopie["rate"], 9.0)            # montant = brut × qté alors qu'une remise est lue

    def test_remise_globale_repartie_au_prorata(self):
        r = S.prix_nets([{"quantite": 1, "prix_unitaire_ht": 100, "montant_ht": 100},
                         {"quantite": 2, "prix_unitaire_ht": 50, "montant_ht": 100}], remise_globale=20)
        self.assertEqual([l["rate"] for l in r["lignes"]], [90.0, 45.0])
        self.assertEqual((r["remise_globale"], r["somme_lignes"]), (20.0, 200.0))
        self.assertEqual(r["lignes"][0]["remise_pct"], 10.0)

    def test_remise_non_detaillee_deduite_du_total_net(self):
        r = S.prix_nets([{"quantite": 1, "prix_unitaire_ht": 100}, {"quantite": 1, "prix_unitaire_ht": 300}],
                        total_ht_net=360)
        self.assertEqual([l["rate"] for l in r["lignes"]], [90.0, 270.0])
        # Total net plus GRAND que les lignes (une ligne a pu échapper) : on ne gonfle jamais les prix.
        self.assertEqual([l["rate"] for l in S.prix_nets([{"quantite": 1, "prix_unitaire_ht": 100}],
                                                         total_ht_net=150)["lignes"]], [100.0])

    def test_sans_remise_rien_ne_change(self):
        r = S.prix_nets([{"designation": "MARTEAU", "quantite": 1, "prix_unitaire_ht": 16.315, "montant_ht": 16.315}],
                        total_ht_net=16.315)
        self.assertEqual((r["lignes"][0]["rate"], r["lignes"][0]["remise_pct"], r["remise_globale"]), (16.315, 0.0, 0.0))
        self.assertEqual(S.prix_nets([])["lignes"], [])
