import unittest
from datetime import date
from decimal import Decimal

from depot.money import Money
from depot.reports import render
from depot.service import ReturnRefused
from helpers import ANVIL, KETTLE, LAMP, MUG, delivered_order, stocked_depot


class BillingTest(unittest.TestCase):
    def setUp(self):
        self.depot, self.clock = stocked_depot()

    def test_invoice_on_ship(self):
        order = delivered_order(self.depot, self.clock, [(MUG, 2), (LAMP, 1, 10), (KETTLE, 3, 5)])
        invoice = self.depot.invoice(order.order_id)
        self.assertEqual([l.net for l in invoice.lines], [Money(2500), Money(3600), Money(8550)])
        self.assertEqual(invoice.net, Money(14650))
        self.assertEqual(invoice.tax_rate, Decimal("20"))
        self.assertEqual(invoice.tax, Money(2930))
        self.assertEqual(invoice.gross, Money(17580))
        self.assertEqual(invoice.number, "INV-2024-03-0001")

    def test_no_invoice_before_ship(self):
        order = self.depot.create_order("Ada Lovelace", [(MUG, 1)])
        self.assertIsNone(self.depot.invoice(order.order_id))

    def test_return_within_window(self):
        order = delivered_order(self.depot, self.clock, [(MUG, 4)])
        self.clock.advance(5)
        note = self.depot.return_order(order.order_id)
        self.assertEqual((note.number, note.net, note.tax), ("CRN-2024-03-0001", Money(-5000), Money(-1000)))
        self.assertEqual(self.depot.inventory.on_hand(MUG, "R-00-01"), 4)
        self.assertEqual(self.depot.order_status(order.order_id)["state"], "returned")

    def test_return_too_late(self):
        order = delivered_order(self.depot, self.clock, [(MUG, 1)])
        self.clock.advance(45)
        with self.assertRaises(ReturnRefused):
            self.depot.return_order(order.order_id)

    def test_payment_plan(self):
        order = delivered_order(self.depot, self.clock, [(LAMP, 1)])
        plan = self.depot.payment_plan(order.order_id, 3)
        self.assertEqual([a.cents for _, a in plan], [1600, 1600, 1600])
        self.assertEqual([d for d, _ in plan], [date(2024, 3, 4), date(2024, 4, 3), date(2024, 5, 3)])


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.depot, _ = stocked_depot()

    def test_valid_rows_set_counts(self):
        result = self.depot.import_counts("sku,bin,qty\nMUG-0001,A-01-01,17\n\nKTL-0003,B-01-04,2\n")
        self.assertTrue(result.ok)
        self.assertEqual([row[4] for row in result.applied], [-3, 2])
        self.assertEqual(self.depot.available(MUG), 17)
        self.assertEqual(self.depot.inventory.bins_for(KETTLE), {"B-01-02": 8, "B-01-04": 2})

    def test_unknown_sku_reported(self):
        result = self.depot.import_counts("sku,bin,qty\nXYZ-9999,A-01-01,3\nLMP-0002,A-02-03,12\n")
        self.assertEqual([(e.line, e.message) for e in result.errors], [(2, "unknown sku 'XYZ-9999'")])
        self.assertEqual(self.depot.available(LAMP), 12)
        self.assertEqual(result.summary(), "1 rows applied, 1 rejected")

    def test_count_sheet_round_trip(self):
        sheet = self.depot.count_sheet([MUG, LAMP])
        self.assertEqual(sheet, "sku,bin,qty\nMUG-0001,A-01-01,20\nLMP-0002,A-02-03,10\n")
        self.assertEqual(self.depot.import_counts(sheet).summary(), "2 rows applied, 0 rejected")


class ReportTest(unittest.TestCase):
    def test_daily_summary(self):
        depot, clock = stocked_depot()
        delivered_order(depot, clock, [(KETTLE, 4)])
        waiting = depot.create_order("Ben Okri", [(LAMP, 12)])
        depot.place(waiting.order_id)
        summary = depot.daily_report()
        self.assertEqual(summary.orders_by_state["delivered"], 1)
        self.assertEqual(summary.orders_by_state["placed"], 1)
        self.assertEqual(summary.shipped_weight_kg, Decimal("6"))
        self.assertEqual(summary.revenue, Money(12000))
        self.assertEqual(summary.backorders, (("SO-0002", LAMP, 2),))
        self.assertEqual(summary.low_stock, ((ANVIL, 2), (KETTLE, 4), (LAMP, 0)))
        self.assertIn("Revenue: 120.00 EUR", render(summary))

    def test_period_and_valuation(self):
        depot, clock = stocked_depot()
        delivered_order(depot, clock, [(MUG, 2)])
        lines = depot.period_report(date(2024, 2, 15), date(2024, 3, 31))
        self.assertEqual([(l.label, l.invoices, l.net) for l in lines],
                         [("2024-02", 0, Money(0)), ("2024-03", 1, Money(2500))])
        rows, grand = depot.stock_value()
        self.assertEqual(rows[-1], (MUG, 18, Money(22500)))
        self.assertEqual(grand, Money(30000 + 40000 + 24000 + 51000 + 22500))


if __name__ == "__main__":
    unittest.main()
