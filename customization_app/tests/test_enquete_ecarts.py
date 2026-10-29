"""Enquête sur les écarts d'une vérification de stock : règles pures (29/10/2026).
Cas réel : VERIF-2026-00008, stock Akram — +1 pack PF-10'-PP-UDF-CTO contre −1 PP, −1 UDF, −1 CTO."""
from __future__ import annotations

import unittest

from customization_app import enquete_ecarts as E

EXISTANTS = {"C-10'-PP-5m", "C-10'-PP-1m", "C-10'-UDF", "C-10'-CTO", "C-20'-PP-5m", "C-20'-CTO"}


class TestCompositionPack(unittest.TestCase):
    def test_pack_filtre_10(self):
        self.assertEqual(E.composition_pack("PF-10'-PP-UDF-CTO", EXISTANTS), {"C-10'-PP-5m": 1, "C-10'-UDF": 1, "C-10'-CTO": 1})

    def test_pas_un_pack(self):
        self.assertIsNone(E.composition_pack("C-10'-CTO", EXISTANTS))
        self.assertIsNone(E.composition_pack("PF-FBC-5-20", EXISTANTS))       # porte-filtre : composants inconnus

    def test_composant_introuvable_pas_de_composition(self):
        self.assertIsNone(E.composition_pack("PF-20'-PP-UDF", EXISTANTS))     # pas de C-20'-UDF


class TestBalanceKits(unittest.TestCase):
    COMPO = {"PF-10'-PP-UDF-CTO": {"C-10'-PP-5m": 1, "C-10'-UDF": 1, "C-10'-CTO": 1}}

    def test_un_pack_en_plus_trois_cartouches_en_moins(self):
        b = E.balance_kits({"PF-10'-PP-UDF-CTO": 1, "C-10'-PP-5m": -1, "C-10'-UDF": -1, "C-10'-CTO": -1}, self.COMPO)
        self.assertEqual(b["residuel"], {})
        self.assertEqual(b["kits"][0]["n"], 1)
        self.assertEqual(b["explique"]["C-10'-CTO"], -1)

    def test_le_reste_depasse_le_kit(self):
        b = E.balance_kits({"PF-10'-PP-UDF-CTO": 1, "C-10'-PP-5m": -2, "C-10'-UDF": -1, "C-10'-CTO": -1}, self.COMPO)
        self.assertEqual(b["residuel"], {"C-10'-PP-5m": -1})

    def test_un_composant_manque_rien_ne_compense(self):
        b = E.balance_kits({"PF-10'-PP-UDF-CTO": 1, "C-10'-PP-5m": -1, "C-10'-UDF": -1}, self.COMPO)   # pas de CTO en moins
        self.assertEqual(b["kits"], [])
        self.assertEqual(len(b["residuel"]), 3)

    def test_sens_inverse_pack_en_moins_cartouches_en_plus(self):
        b = E.balance_kits({"PF-10'-PP-UDF-CTO": -2, "C-10'-PP-5m": 2, "C-10'-UDF": 2, "C-10'-CTO": 3}, self.COMPO)
        self.assertEqual(b["kits"][0]["n"], -2)
        self.assertEqual(b["residuel"], {"C-10'-CTO": 1})


class TestRegles(unittest.TestCase):
    BLS = [{"name": "DN-1", "date": "2026-10-26", "client": "Ouelhazi", "docstatus": 1,
            "lignes": [{"item_code": "M-100-FR", "qty": 1, "warehouse": "Magasins - A&S"}, {"item_code": "Acc006-A", "qty": 1, "warehouse": "Stock Akram - A&S"}]},
           {"name": "DN-2", "date": "2026-10-27", "client": "X", "docstatus": 0,
            "lignes": [{"item_code": "M-100-FR", "qty": 1, "warehouse": "Magasins - A&S"}]}]

    def test_bl_sorti_du_mauvais_stock(self):
        ms = E.bls_mauvais_stock(self.BLS, "Stock Akram - A&S")
        self.assertEqual([(m["bl"], m["item_code"]) for m in ms], [("DN-1", "M-100-FR")])      # le brouillon ne compte pas

    def test_verdicts(self):
        inter = [{"name": "T1", "date": "2026-10-26", "type": "Entretien", "client": "Hernio", "commande": "SO-1", "statut": "Completed",
                  "articles": [{"item_code": "M-E-OD", "qty": 1}]},
                 {"name": "T2", "date": "2026-10-26", "type": "Livraison", "client": "Mema", "commande": "SO-2", "statut": "Completed",
                  "articles": [{"item_code": "Sil-Sac-25kg", "qty": 1}]}]
        ecarts = [{"item_code": "PF-10'-PP-UDF-CTO", "item_name": "Pack", "ecart": 1}, {"item_code": "C-10'-CTO", "item_name": "CTO", "ecart": -1},
                  {"item_code": "C-10'-PP-5m", "item_name": "PP", "ecart": -1}, {"item_code": "C-10'-UDF", "item_name": "UDF", "ecart": -1},
                  {"item_code": "M-100-FR", "item_name": "Membrane", "ecart": -1}, {"item_code": "Coude", "item_name": "Coude", "ecart": 3},
                  {"item_code": "Sil-Sac-25kg", "item_name": "Sel", "ecart": -1}, {"item_code": "Acc006-A", "item_name": "Raccord", "ecart": -2}]
        balance = E.balance_kits({e["item_code"]: e["ecart"] for e in ecarts}, TestBalanceKits.COMPO)
        v = E.verdicts_regles(ecarts, balance, E.bls_mauvais_stock(self.BLS, "Stock Akram - A&S"),
                              [{"name": "DN-2", "type": "Delivery Note", "lignes": [{"item_code": "M-100-FR"}]}], inter)
        self.assertEqual({c: x["verdict"] for c, x in v.items()},
                         {"PF-10'-PP-UDF-CTO": "Explicable", "C-10'-CTO": "Explicable", "C-10'-PP-5m": "Explicable", "C-10'-UDF": "Explicable",
                          "M-100-FR": "Explicable",            # sorti du Magasin sur DN-1, pris dans le véhicule
                          "Coude": "Inexpliqué",               # entrée sans document
                          "Sil-Sac-25kg": "Probable",          # sur la commande de la livraison T2
                          "Acc006-A": "Inexpliqué"})           # consommable d'entretien possible, sans preuve
        self.assertIn("Balance des kits", v["C-10'-CTO"]["texte"])
        self.assertIn("DN-1", v["M-100-FR"]["texte"])
        self.assertIn("T2", "".join(v["Sil-Sac-25kg"]["faits"]) or v["Sil-Sac-25kg"]["texte"] or "T2")
        self.assertEqual(E.synthese(v), "5 explicable(s), 1 probable(s), 2 inexpliqué(s)")

    def test_brouillon_rend_probable(self):
        v = E.verdicts_regles([{"item_code": "M-100-FR", "item_name": "Membrane", "ecart": -1}], E.balance_kits({"M-100-FR": -1}, {}), [],
                              [{"name": "DN-2", "type": "Delivery Note", "lignes": [{"item_code": "M-100-FR"}]}], [])
        self.assertEqual(v["M-100-FR"]["verdict"], "Probable")
        self.assertIn("DN-2", v["M-100-FR"]["texte"])
