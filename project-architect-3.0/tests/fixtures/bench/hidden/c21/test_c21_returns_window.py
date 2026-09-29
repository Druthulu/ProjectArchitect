import unittest
from datetime import date, timedelta

from depot.dates import within_returns_window
from depot.orders import OrderState
from depot.service import ReturnRefused
from helpers import MUG, delivered_order, stocked_depot


class ReturnsWindowBoundaryTest(unittest.TestCase):
    def test_day_30_after_delivery_is_accepted(self):
        depot, clock = stocked_depot()
        order = delivered_order(depot, clock, [(MUG, 2)])
        self.assertEqual(depot.order_status(order.order_id)["returnable_until"], date(2024, 4, 3))
        clock.advance(30)
        depot.return_order(order.order_id)
        self.assertIs(order.state, OrderState.RETURNED)

    def test_day_31_after_delivery_is_refused(self):
        depot, clock = stocked_depot()
        order = delivered_order(depot, clock, [(MUG, 2)])
        clock.advance(31)
        with self.assertRaises(ReturnRefused):
            depot.return_order(order.order_id)
        self.assertIs(order.state, OrderState.DELIVERED)

    def test_window_function_boundaries(self):
        delivered = date(2024, 3, 7)
        self.assertTrue(within_returns_window(delivered, delivered))
        self.assertTrue(within_returns_window(delivered, delivered + timedelta(days=30)))
        self.assertFalse(within_returns_window(delivered, delivered + timedelta(days=31)))
        self.assertFalse(within_returns_window(delivered, delivered - timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
