import unittest

from helpers import KETTLE, MUG, stocked_depot

EXPECTED = [("A-01-01", KETTLE, 3), ("A-01-01", MUG, 1), ("B-01-02", KETTLE, 1)]


class SharedBinPickOrderTest(unittest.TestCase):
    def pick(self, kettle_first):
        depot, _ = stocked_depot()
        depot.receive(KETTLE, "A-01-01", 3)
        kettles = depot.create_order("Ada Lovelace", [(KETTLE, 4)])
        mugs = depot.create_order("Grace Hopper", [(MUG, 1)])
        ids = [kettles.order_id, mugs.order_id]
        for oid in ids:
            depot.place(oid)
        pick_list = depot.pick(*(ids if kettle_first else ids[::-1]))
        return [(line.bin, line.sku, line.qty) for line in pick_list.lines]

    def test_same_list_whatever_the_order_of_orders(self):
        self.assertEqual(self.pick(kettle_first=True), EXPECTED)
        self.assertEqual(self.pick(kettle_first=False), EXPECTED)


if __name__ == "__main__":
    unittest.main()
