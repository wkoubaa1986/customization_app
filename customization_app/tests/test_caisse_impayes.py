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
