import unittest
from datetime import date

from depot.orders import InvalidTransition, OrderLocked, OrderState
from depot.service import NotReady
from helpers import CHAIR, KETTLE, LAMP, MUG, delivered_order, stocked_depot


class OrderFlowTest(unittest.TestCase):
    def setUp(self):
        self.depot, self.clock = stocked_depot()

    def test_happy_path(self):
        order = self.depot.create_order("Ada Lovelace", [(MUG, 2), (KETTLE, 1)])
        self.assertIs(order.state, OrderState.DRAFT)
        self.depot.place(order.order_id)
        self.assertEqual(order.promised, date(2024, 3, 7))
        self.assertEqual(self.depot.available(MUG), 18)
        self.depot.pick(order.order_id)
        self.clock.advance(1)
        self.depot.ship(order.order_id)
        self.assertEqual(self.depot.inventory.on_hand(KETTLE), 7)
        self.clock.advance(2)
        self.depot.deliver(order.order_id)
        self.assertIs(order.state, OrderState.DELIVERED)
        self.assertEqual([s.value for s, _ in order.history], ["placed", "picked", "shipped", "delivered"])
        self.assertEqual(order.entered(OrderState.SHIPPED), date(2024, 3, 5))
        self.assertEqual(self.depot.late_deliveries(), [])

    def test_lines_merge_and_lock(self):
        order = self.depot.create_order("Ben Okri", [(MUG, 1), (MUG, 2), (MUG, 1, 10)])
        self.assertEqual([(l.sku, l.qty) for l in order.lines], [(MUG, 3), (MUG, 1)])
        self.depot.place(order.order_id)
        with self.assertRaises(OrderLocked):
            self.depot.add_line(order.order_id, LAMP, 1)

    def test_cancel_draft(self):
        order = self.depot.create_order("Ada Lovelace", [(LAMP, 1)])
        self.depot.cancel(order.order_id)
        self.assertIs(order.state, OrderState.CANCELLED)
        with self.assertRaises(InvalidTransition):
            self.depot.place(order.order_id)

    def test_cancel_placed_drops_reservation(self):
        order = self.depot.create_order("Ada Lovelace", [(LAMP, 3)])
        self.depot.place(order.order_id)
        self.depot.cancel(order.order_id)
        self.assertIs(order.state, OrderState.CANCELLED)
        self.assertEqual(self.depot.inventory.reservations(order.order_id), {})
        self.assertEqual(self.depot.inventory.reserved(LAMP), 0)

    def test_illegal_transitions(self):
        order = self.depot.create_order("Ada Lovelace", [(MUG, 1)])
        with self.assertRaises(InvalidTransition):
            self.depot.ship(order.order_id)
        self.depot.place(order.order_id)
        with self.assertRaises(InvalidTransition):
            self.depot.deliver(order.order_id)
        with self.assertRaises(InvalidTransition):
            self.depot.return_order(order.order_id)
        with self.assertRaises(InvalidTransition):
            self.depot.place(order.order_id)

    def test_backorder(self):
        order = self.depot.create_order("Cy Twombly", [(CHAIR, 8)])
        self.depot.place(order.order_id)
        self.assertEqual(order.backorder, {CHAIR: 2})
        with self.assertRaises(NotReady):
            self.depot.pick(order.order_id)
        self.depot.receive(CHAIR, "C-01-01", 5)
        self.assertEqual(self.depot.fill_backorders(order.order_id), {})
        pick_list = self.depot.pick(order.order_id)
        self.assertEqual(pick_list.units, 8)

    def test_overdue_and_status(self):
        order = self.depot.create_order("Ada Lovelace", [(MUG, 1)], service_level="express")
        self.depot.place(order.order_id)
        self.clock.advance(3)
        self.assertEqual([o.order_id for o in self.depot.overdue_orders()], [order.order_id])
        other = delivered_order(self.depot, self.clock, [(KETTLE, 1)])
        status = self.depot.order_status(other.order_id)
        self.assertEqual((status["state"], status["parcels"]), ("delivered", 1))
        self.assertEqual(status["invoice"], "INV-2024-03-0001")


if __name__ == "__main__":
    unittest.main()
