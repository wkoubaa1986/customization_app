import unittest
from decimal import Decimal
from customization_app.facturation_paiements import summarize, render_summary


class TestInvoicePayments(unittest.TestCase):
    def test_groups_allocations_and_keeps_debt_out_of_received(self):
        rows = [
            {'mode_of_payment':'Chèque','allocated_amount':851.2},
            {'mode_of_payment':'Chèque','allocated_amount':280.8},
            {'mode_of_payment':'Espèces','allocated_amount':1933.069},
            {'mode_of_payment':'Dette non payée','allocated_amount':534.781},
        ]
        s = summarize(rows, 6.65)
        self.assertEqual(s['payments']['Chèque'], Decimal('1132.000'))
        self.assertEqual(s['received'], Decimal('3065.069'))
        self.assertEqual(s['remaining'], Decimal('541.431'))
        self.assertEqual(s['received'] + s['remaining'], Decimal('3606.500'))

    def test_refund_is_signed_and_html_is_escaped(self):
        s = summarize([{'mode_of_payment':'<Espèces>','allocated_amount':10,'payment_type':'Pay'}],0)
        self.assertEqual(s['received'], Decimal('-10.000'))
        html = render_summary('FAC-08-2026-02168',100,s)
        self.assertIn('&lt;Espèces&gt;',html)
        self.assertIn('FAC-08-2026-02168',html)
