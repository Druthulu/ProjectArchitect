import unittest

from depot.money import Money
from helpers import delivered_order, stocked_depot

PEN = "PEN-0006"
PENCIL = "PCL-0007"


class InvoiceTaxRoundingTest(unittest.TestCase):
    def setUp(self):
        self.depot, self.clock = stocked_depot()
        self.depot.add_product(PEN, "Ballpoint pen", 10, "0.12", ["office"])
        self.depot.add_product(PENCIL, "Pencil", 5, "0.07", ["office"])
        self.depot.receive(PEN, "A-05-01", 50)
        self.depot.receive(PENCIL, "A-05-02", 50)

    def test_tax_rounded_once_on_the_invoice_net(self):
        order = delivered_order(self.depot, self.clock, [(PEN, 1), (PENCIL, 1)])
        invoice = self.depot.invoice(order.order_id)
        self.assertEqual(invoice.net, Money(19))
        self.assertEqual(invoice.tax, Money(4))
        self.assertEqual(invoice.gross, Money(23))

    def test_three_small_lines(self):
        order = delivered_order(self.depot, self.clock, [(PEN, 1), (PENCIL, 1), (PEN, 2, 50)])
        invoice = self.depot.invoice(order.order_id)
        self.assertEqual(len(invoice.lines), 3)
        self.assertEqual(invoice.net, Money(31))
        self.assertEqual(invoice.tax, Money(6))
        self.assertEqual(invoice.gross, Money(37))


if __name__ == "__main__":
    unittest.main()
