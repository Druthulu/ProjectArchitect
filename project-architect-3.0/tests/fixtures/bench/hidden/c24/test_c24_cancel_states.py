import unittest

from depot.orders import InvalidTransition, OrderState
from helpers import LAMP, stocked_depot


class CancelStatesTest(unittest.TestCase):
    def setUp(self):
        self.depot, self.clock = stocked_depot()
        self.order = self.depot.create_order("Ada Lovelace", [(LAMP, 2)])
        self.oid = self.order.order_id

    def refused(self, state):
        with self.assertRaises(InvalidTransition):
            self.depot.cancel(self.oid)
        self.assertIs(self.order.state, state)

    def test_draft_and_placed_can_be_cancelled(self):
        self.depot.cancel(self.oid)
        self.assertIs(self.order.state, OrderState.CANCELLED)
        other = self.depot.create_order("Ada Lovelace", [(LAMP, 2)])
        self.depot.place(other.order_id)
        self.depot.cancel(other.order_id)
        self.assertIs(other.state, OrderState.CANCELLED)
        self.assertEqual(self.depot.available(LAMP), 10)

    def test_picked_order_cannot_be_cancelled(self):
        self.depot.place(self.oid)
        self.depot.pick(self.oid)
        self.refused(OrderState.PICKED)
        self.assertEqual(self.depot.available(LAMP), 8)
        self.depot.ship(self.oid)
        self.assertIs(self.order.state, OrderState.SHIPPED)

    def test_shipped_order_cannot_be_cancelled(self):
        self.depot.place(self.oid)
        self.depot.pick(self.oid)
        self.depot.ship(self.oid)
        self.refused(OrderState.SHIPPED)

    def test_delivered_order_cannot_be_cancelled(self):
        self.depot.place(self.oid)
        self.depot.pick(self.oid)
        self.depot.ship(self.oid)
        self.depot.deliver(self.oid)
        self.refused(OrderState.DELIVERED)


if __name__ == "__main__":
    unittest.main()
