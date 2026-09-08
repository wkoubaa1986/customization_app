import json
import unittest
from unittest.mock import Mock, patch

import frappe
from customization_app import ristourne_facture as rf


class Invoice(frappe._dict):
    def precision(self, field):
        return 3

    def set(self, key, value):
        self[key] = value


class TestOrderDiscounts(unittest.TestCase):
    def setUp(self):
        self.doc = Invoice(
            name="INV", creation="2026-09-08 12:00:00", docstatus=0,
            items=[frappe._dict(sales_order="A"), frappe._dict(sales_order="A"),
                   frappe._dict(sales_order="B")],
            currency="TND", company="Company", discount_amount=0,
            additional_discount_percentage=0, apply_discount_on="Grand Total",
        )
        self.doc.get_doc_before_save = Mock(return_value=None)
        self.doc.calculate_taxes_and_totals = Mock()
        self.doc.set_payment_schedule = Mock()
        self.doc.set_advances = Mock()
        self.discounts = {"A": 55.798, "B": 10.002}
        self.used = set()
        self.sql = patch.object(rf.frappe, "db", Mock())
        self.db = self.sql.start()
        self.db.sql.side_effect = self.query
        self.message = patch.object(rf.frappe, "msgprint").start()
        patch.object(rf, "_", lambda text: text).start()
        patch.object(rf.frappe, "get_system_settings", return_value="Commercial Rounding").start()
        self.addCleanup(patch.stopall)

    def query(self, sql, args, **kwargs):
        if "FROM `tabSales Order`" in sql:
            return [frappe._dict(discount_amount=self.discounts[args[0]], currency="TND", company="Company")]
        return [("OTHER",)] if args[0] in self.used else []

    def saved_automatic(self, amount=65.8):
        self.doc.discount_amount = amount
        self.doc.get_doc_before_save.return_value = frappe._dict(
            custom_ristourne_commandes=json.dumps({"amount": amount, "orders": ["A", "B"]}))

    def test_reallocates_advances_after_discount_without_exceeding_total(self):
        self.doc.allocate_advances_automatically = 1
        self.doc.advances = [frappe._dict(allocated_amount=100)]
        def calculate():
            allocated = sum(row.allocated_amount for row in self.doc.advances)
            self.assertLessEqual(allocated, 34.2)
        def allocate():
            self.doc.advances = [frappe._dict(allocated_amount=34.2)]
        self.doc.calculate_taxes_and_totals.side_effect = calculate
        self.doc.set_advances.side_effect = allocate
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.advances[0].allocated_amount, 34.2)
        self.doc.set_advances.assert_called_once()

    def test_sums_distinct_orders(self):
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 65.8)
        self.assertEqual(json.loads(self.doc.custom_ristourne_commandes)["orders"], ["A", "B"])
        self.doc.set_payment_schedule.assert_called_once()

    def test_already_invoiced_order_excluded(self):
        self.used.add("A")
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 10.002)

    def test_manual_amount_preserved(self):
        self.doc.discount_amount = 20
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 20)
        self.message.assert_called_once()
        self.doc.calculate_taxes_and_totals.assert_not_called()

    def test_manual_percentage_preserved(self):
        self.doc.additional_discount_percentage = 5
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.additional_discount_percentage, 5)
        self.doc.calculate_taxes_and_totals.assert_not_called()

    def test_resave_idempotent(self):
        self.saved_automatic()
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 65.8)

    def test_removed_order_recalculates(self):
        self.saved_automatic()
        self.doc["items"] = [frappe._dict(sales_order="B")]
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 10.002)

    def test_all_orders_removed_clears_automatic_discount(self):
        self.saved_automatic()
        self.doc["items"] = []
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 0)
        self.assertIsNone(self.doc.custom_ristourne_commandes)

    def test_manual_override_of_automatic_preserved(self):
        self.saved_automatic()
        self.doc.discount_amount = 12
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 12)
        self.assertIsNone(self.doc.custom_ristourne_commandes)

    def test_returns_pos_cancelled_ignored(self):
        for field, value in [("is_return", 1), ("is_pos", 1), ("docstatus", 2)]:
            self.doc[field] = value
            rf.apply_order_discounts(self.doc)
            self.db.sql.assert_not_called()
            self.doc[field] = 0

    def test_no_orders_no_discount(self):
        self.doc["items"] = []
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 0)
        self.doc.calculate_taxes_and_totals.assert_not_called()

    def test_automatic_advances_recalculated(self):
        self.doc.allocate_advances_automatically = 1
        rf.apply_order_discounts(self.doc)
        self.doc.set_advances.assert_called_once()

    def test_client_state_cannot_override_manual_discount(self):
        self.doc.discount_amount = 20
        self.doc.custom_ristourne_commandes = json.dumps({"amount": 20})
        rf.apply_order_discounts(self.doc)
        self.assertEqual(self.doc.discount_amount, 20)
        self.assertIsNone(self.doc.custom_ristourne_commandes)
