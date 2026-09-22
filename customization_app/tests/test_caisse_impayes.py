"""Régression impayés, à lancer sur un site de test contenant une pièce impayée.

Les écritures sont toujours annulées ; aucun encaissement de test n'est conservé.
"""
import unittest

import frappe
from frappe.utils import nowdate

from customization_app.caisse_impayes import IMPAYES, _montant, _soldes, encaisser


class TestMontants(unittest.TestCase):
    def test_montants_invalides(self):
        for valeur in (0, -1, 'NaN', 'Infinity', 'incorrect', 100.001):
            with self.subTest(valeur=valeur), self.assertRaises(frappe.ValidationError):
                _montant(valeur, 100)

    def test_partiel_et_solde(self):
        self.assertEqual(_montant('50.125', 100), 50.125)
        self.assertEqual(_montant(100, 100), 100)


class TestModes(unittest.TestCase):
    """Règles pures des six modes et du fractionnement."""

    def test_numero_et_banque_de_la_piece_impayee(self):
        from customization_app.caisse_impayes import numero_et_banque
        self.assertEqual(numero_et_banque("0001170-BIAT / BR:90028502 / Impayé FT26259 du 2026-09-16"), ("0001170", "BIAT"))
        self.assertEqual(numero_et_banque("4000608 / BR:90028502"), ("4000608", ""))
        self.assertEqual(numero_et_banque(None), ("", ""))

    def test_reference_du_transfert(self):
        from customization_app.caisse_impayes import reference_transfert as R
        self.assertEqual(R("Espèces", "ACC-PAY-1", "0001170-BIAT / BR:1"), "ACC-PAY-1")
        self.assertEqual(R("Redépôt du même chèque", "ACC-PAY-1", "0001170-BIAT / BR:1"), "0001170-BIAT / Redépôt de ACC-PAY-1")
        self.assertEqual(R("Nouveau chèque", "ACC-PAY-1", "x", n_piece="0001173", banque="BIAT"), "0001173-BIAT / Remplace ACC-PAY-1")
        self.assertEqual(R("Traite bancaire", "ACC-PAY-1", "x", n_piece="998877"), "998877 / Remplace ACC-PAY-1")
        self.assertEqual(R("Virement", "ACC-PAY-1", "x", n_piece="FT26265ABC"), "Virement reçu N: FT26265ABC / Remplace ACC-PAY-1")
        self.assertEqual(R("Virement", "ACC-PAY-1", "x"), "Virement / Remplace ACC-PAY-1")
        self.assertEqual(R("Carte de crédit", "ACC-PAY-1", "x", n_piece="TPE-12"), "Ticket TPE TPE-12 / Remplace ACC-PAY-1")

    def test_motifs_de_refus(self):
        from customization_app.caisse_impayes import motif_refus_mode as M
        self.assertIsNone(M("Espèces", "0001170-BIAT"))
        self.assertIsNone(M("Redépôt du même chèque", "0001170-BIAT"))
        self.assertIn("Nouveau chèque", M("Redépôt du même chèque", ""))
        self.assertIn("numéro", M("Nouveau chèque", "x"))
        self.assertIn("chiffres", M("Nouveau chèque", "x", n_piece="12", banque="BIAT"))
        self.assertIn("banque", M("Nouveau chèque", "x", n_piece="0001173", banque=" "))
        self.assertIsNone(M("Nouveau chèque", "x", n_piece="0001173", banque="BIAT"))
        self.assertIn("échéance", M("Traite bancaire", "x", n_piece="998877"))
        self.assertIsNone(M("Traite bancaire", "x", n_piece="998877", echeance="2026-12-31"))
        self.assertIsNone(M("Virement", "x"))                       # référence facultative
        self.assertIn("lettres", M("Virement", "x", n_piece="a"))     # mais contrôlée si saisie
        self.assertIsNone(M("Carte de crédit", "x", n_piece="TPE-12"))
        self.assertIn("inconnu", M("Troc", "x"))

    def test_fractionnement(self):
        from customization_app.caisse_impayes import normaliser_paiements as N
        lignes = N([{"mode": "Espèces", "montant": "100"}, {"mode": "Traite bancaire", "montant": 16, "n_piece": "998877", "date_piece": "2026-12-31"}], 116)
        self.assertEqual([(l["mode"], l["montant"]) for l in lignes], [("Espèces", 100.0), ("Traite bancaire", 16.0)])
        self.assertEqual(lignes[1]["n_piece"], "998877")
        lignes = N('[{"mode": "Espèces", "montant": 50}]', 116)       # JSON du navigateur
        self.assertEqual(lignes[0]["montant"], 50.0)
        with self.assertRaises(frappe.ValidationError):
            N([{"mode": "Espèces", "montant": 100}, {"mode": "Virement", "montant": 17}], 116)   # dépasse
        with self.assertRaises(frappe.ValidationError):
            N([{"mode": "Espèces", "montant": 0}], 116)
        with self.assertRaises(frappe.ValidationError):
            N([], 116)


class TestReglementImpaye(unittest.TestCase):
    def test_reglement_et_restitutions(self):
        from customization_app.api import get_relance_detail, _repartition_par_compte
        from customization_app.rapport_caisse_journaliere import _paiements_anciennes_commandes

        ancien_user = frappe.session.user
        frappe.set_user('Administrator')
        frappe.db.savepoint('test_reglement_impaye')
        try:
            clients = frappe.db.sql(
                """SELECT DISTINCT pe.party FROM `tabPayment Entry` pe
                   JOIN `tabGL Entry` gl ON gl.voucher_no = pe.name
                   AND gl.voucher_type = 'Payment Entry'
                   WHERE pe.docstatus = 1 AND pe.party_type = 'Customer'
                   AND gl.account = %s AND gl.is_cancelled = 0""", IMPAYES)
            source = next((p for (client,) in clients for p in _soldes(client)
                           if p.restant > 1), None)
            if not source:
                self.skipTest('Une pièce impayée de plus de 1 TND est nécessaire.')
            origine = frappe.get_doc('Payment Entry', source.name).as_dict()
            avant = _repartition_par_compte(source.customer, [source.name])['cheques']
            resultat = encaisser(source.customer, source.name, .5)
            self.assertEqual(resultat['restant'], round(source.restant - .5, 3))
            self.assertEqual(round(_repartition_par_compte(source.customer, [source.name])['cheques'], 3),
                             round(avant - .5, 3))
            detail = get_relance_detail(source.customer)
            self.assertEqual(next(r for r in detail if r['voucher_no'] == source.name)['restant_impaye'],
                             resultat['restant'])
            rapport = _paiements_anciennes_commandes(nowdate(), nowdate(), set())
            ligne = next(r for r in rapport['paiements'] if r['name'] == resultat['name'])
            self.assertEqual((ligne['amount'], ligne['mode'], ligne['customer']),
                             (.5, 'Espèces', source.customer))
            with self.assertRaises(frappe.ValidationError):
                encaisser(source.customer, source.name, resultat['restant'] + 1)
            encaisser(source.customer, source.name, resultat['restant'])
            self.assertFalse(_soldes(source.customer, source.name))
            with self.assertRaises(frappe.ValidationError):
                encaisser(source.customer, source.name, .5)
            self.assertEqual(frappe.get_doc('Payment Entry', source.name).as_dict(), origine)
        finally:
            frappe.db.rollback(save_point='test_reglement_impaye')
            frappe.set_user(ancien_user)
