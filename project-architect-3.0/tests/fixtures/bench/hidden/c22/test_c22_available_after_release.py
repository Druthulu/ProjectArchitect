import unittest

from depot.stock import Inventory
from helpers import LAMP, MUG, stocked_depot


class AvailableAfterReleaseTest(unittest.TestCase):
    def test_cancel_frees_the_reserved_units(self):
        depot, _ = stocked_depot()
        order = depot.create_order("Ada Lovelace", [(MUG, 3), (LAMP, 1)])
        depot.place(order.order_id)
        self.assertEqual((depot.available(MUG), depot.available(LAMP)), (17, 9))
        depot.cancel(order.order_id)
        self.assertEqual((depot.available(MUG), depot.available(LAMP)), (20, 10))

    def test_release_on_the_inventory(self):
        inv = Inventory()
        inv.receive("MUG-0001", "A-01-01", 5)
        inv.reserve("o-1", "MUG-0001", 3)
        self.assertEqual(inv.available("MUG-0001"), 2)
        self.assertEqual(inv.release("o-1"), {"MUG-0001": 3})
        self.assertEqual(inv.available("MUG-0001"), 5)


if __name__ == "__main__":
    unittest.main()
