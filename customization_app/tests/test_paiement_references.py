"""Fusion des lignes de référence en double d'un paiement (01/10/2026) — tests purs."""
from __future__ import annotations

import types
import unittest

from customization_app import paiement_references as PR


def _l(nom, montant, doctype="Sales Invoice", terme=None):
    return types.SimpleNamespace(reference_doctype=doctype, reference_name=nom, allocated_amount=montant,
                                 payment_term=terme, payment_request=None, idx=0)


class TestFusion(unittest.TestCase):
    def test_trois_lignes_vers_la_meme_facture_deviennent_une(self):
        """ACC-PAY-2026-07487 : 249,4 + 829,8 + 198,129 sur ACC-SINV-2026-01556."""
        lignes = [_l("ACC-SINV-2026-01046", 1722.671), _l("ACC-SINV-2026-01556", 249.4),
                  _l("ACC-SINV-2026-01556", 829.8), _l("ACC-SINV-2026-01556", 198.129)]
        out = PR.fusionner_references(lignes)
        self.assertEqual([(r.reference_name, r.allocated_amount) for r in out],
                         [("ACC-SINV-2026-01046", 1722.671), ("ACC-SINV-2026-01556", 1277.329)])

    def test_une_echeance_differente_n_est_pas_un_doublon(self):
        out = PR.fusionner_references([_l("F1", 10, terme="T1"), _l("F1", 20, terme="T2"), _l("F1", 5, terme="T2")])
        self.assertEqual([(r.payment_term, r.allocated_amount) for r in out], [("T1", 10), ("T2", 25)])

    def test_sans_doublon_rien_ne_change(self):
        lignes = [_l("F1", 10), _l("SO1", 20, doctype="Sales Order"), _l("F2", 30)]
        self.assertEqual(PR.fusionner_references(lignes), lignes)

    def test_un_paiement_valide_n_est_jamais_touche(self):
        doc = types.SimpleNamespace(docstatus=1, references=[_l("F1", 10), _l("F1", 20)],
                                    get=lambda k: getattr(doc, k), set=lambda k, v: setattr(doc, k, v))
        PR.before_validate(doc)
        self.assertEqual(len(doc.references), 2)

    def test_un_brouillon_est_compacte_et_renumerote(self):
        doc = types.SimpleNamespace(docstatus=0, references=[_l("F1", 10), _l("F1", 20), _l("F2", 1)],
                                    get=lambda k: getattr(doc, k), set=lambda k, v: setattr(doc, k, v))
        PR.before_validate(doc)
        self.assertEqual([(r.reference_name, r.allocated_amount, r.idx) for r in doc.references],
                         [("F1", 30, 1), ("F2", 1, 2)])
