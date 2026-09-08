import unittest
from unittest.mock import patch
import frappe
from customization_app.facturation_credits import plan_credits
from customization_app.facturation_paiements import render_summary, summarize


class TestCredits(unittest.TestCase):
    def setUp(self):
        patcher=patch.object(frappe,'get_system_settings',return_value='Commercial Rounding')
        patcher.start();self.addCleanup(patcher.stop)

    def test_caps_credit_at_remaining_balance(self):
        entries=[frappe._dict(amount=6.65),frappe._dict(amount=100)]
        self.assertEqual([value for _,value in plan_credits(entries,6.65)],[6.65])

    def test_uses_several_credits_without_overallocating(self):
        entries=[frappe._dict(amount=2),frappe._dict(amount=10)]
        self.assertEqual([value for _,value in plan_credits(entries,6.65)],[2,4.65])
        self.assertEqual(plan_credits(entries,0),[])

    def test_email_credit_is_included_not_added_to_receipts(self):
        summary=summarize([{'mode_of_payment':'Espèces','allocated_amount':1939.719}],0)
        html=render_summary('FAC-08-2026-02195',3606.5,summary,prior_credit=6.65)
        self.assertIn('1 939,719 TND',html)
        self.assertIn('6,650 TND',html)
        self.assertIn('déjà inclus',html)
