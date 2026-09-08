import unittest
from unittest.mock import patch
import frappe
from customization_app import facturation_sms as sms


class TestFacturationSMS(unittest.TestCase):
    def test_message_uses_invoice_month_number_and_three_decimal_amount(self):
        doc = frappe._dict(posting_date='2026-08-31', custom_numero_facture='2168', grand_total=2986.7)
        message = sms.message_facture(doc)
        self.assertIn('FAC-08-2026-02168', message)
        self.assertIn('2986.700 TND', message)
        self.assertIn('e-mail', message)

    def test_dev_never_loads_invoice_or_sends(self):
        with patch.object(sms.frappe, 'conf', frappe._dict(developer_mode=1)), patch.object(sms.frappe, 'get_doc') as get_doc:
            self.assertEqual(sms.send_invoice_ready('INV'), {'skipped': 'developer_mode'})
            get_doc.assert_not_called()
