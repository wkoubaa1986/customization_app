"""Bilan Vente : chèques déjà encaissés dans le total dû, partage manuel des commandes en litige (04/11/2026).

Convention : `unittest.TestCase` pur, aucune base.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe

from customization_app import bilan_vente as BV
from customization_app import partage_commande_partenaire as PCP

ZITOUNA = "STE430127B - Zitouna - A&S"
BANQUES = frozenset({ZITOUNA, "BIAT - A&S"})


def _paiement(mode, montant, compte):
    return frappe._dict(amount=montant, mode_of_payment=mode, reference_no="R", name="PE-" + compte[:3],
                        posting_date=None, paid_to=compte)


def _commande(paiements, achat_unitaire=0.0):
    so = frappe._dict(name="SO-1", customer="C", delivery_date=None, grand_total=300.0)
    article = frappe._dict(item_code="A", item_name="A", qty=1, rate=300.0, item_group="X")
    return BV._build_order(so, [article], paiements, [], lambda code, date: achat_unitaire, 0.0, [], BANQUES)


def _flt(valeur, precision=None):
    # Sans site, frappe.utils.flt(x, 3) lit la méthode d'arrondi dans les réglages système et rend 0.0.
    v = float(valeur or 0)
    return round(v, precision) if precision is not None else v


class TestChequesEncaisses(unittest.TestCase):
    def setUp(self):
        p = patch.object(BV, "flt", _flt)
        p.start()
        self.addCleanup(p.stop)

    def test_seul_le_cheque_encaisse_en_banque_compte(self):
        o = _commande([_paiement("Espèces", 100.0, "Espèces - A&S"),
                       _paiement("Chèque", 48.0, ZITOUNA),                       # encaissé : remis à la banque
                       _paiement("Chèque", 30.0, "Chèques - A&S"),               # en portefeuille
                       _paiement("Chèque", 20.0, "Economiq Aqua Solution - A&S")])  # chez le partenaire
        self.assertEqual(o["totals"]["cheques"], 98.0)
        self.assertEqual(o["totals"]["cheques_banque"], 48.0)
        self.assertEqual([c["en_banque"] for c in o["cheques"]], [True, False, False])

    def test_total_du_par_aqua_world(self):
        o = _commande([_paiement("Espèces", 100.0, "Espèces - A&S"), _paiement("Chèque", 48.0, ZITOUNA)],
                      achat_unitaire=60.0)
        s = BV._build_section("aqua_world", BV.HOST_LABEL, [o], 0.0, None, "dû par AW", "dû par Eco",
                              cheques_banque_dus=True)
        self.assertEqual((s["solde"]["due_by"], s["solde"]["amount"]), ("dû par AW", 88.0))   # 100 + 48 − 60
        self.assertEqual(s["solde"]["cheques_banque"], 48.0)

    def test_section_economiq_inchangee(self):
        o = _commande([_paiement("Espèces", 100.0, "Espèces - A&S"), _paiement("Chèque", 48.0, ZITOUNA)],
                      achat_unitaire=60.0)
        s = BV._build_section("economiq", BV.PARTNER_LABEL, [o], 0.1, None, "dû par Eco", "dû par AW")
        self.assertEqual(s["solde"]["amount"], 40.0)                                     # 100 − 60
        self.assertEqual(s["solde"]["cheques_banque"], 0.0)


class TestPartageManuel(unittest.TestCase):
    def test_etat(self):
        self.assertEqual(PCP.etat(None), PCP.AUCUN)
        self.assertEqual(PCP.etat({"write": 0}), PCP.MANUEL)     # posé par le bouton : se retire
        self.assertEqual(PCP.etat({"write": 1}), PCP.TACHE)      # tâche partenaire : ne se retire pas ici


if __name__ == "__main__":
    unittest.main()
