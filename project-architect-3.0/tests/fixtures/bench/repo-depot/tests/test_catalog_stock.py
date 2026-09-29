import unittest

from depot.catalog import Catalog, DuplicateProduct, UnknownProduct
from depot.money import Money
from depot.stock import InsufficientStock, Inventory
from helpers import CHAIR, KETTLE, LAMP, MUG, stocked_depot


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.catalog.add(MUG, "Stoneware mug", 350, Money(1250), ["Kitchen", " gift  set "])
        self.catalog.add(LAMP, "Desk lamp", 1200, Money(4000), ["office"])

    def test_add_normalises_tags(self):
        self.assertEqual(self.catalog.get(MUG).tags, ["kitchen", "gift-set"])
        self.assertTrue(self.catalog.get(MUG).has_tag("Gift Set"))

    def test_add_without_tags(self):
        product = self.catalog.add(KETTLE, "Kettle", 1500, Money(3000))
        self.assertEqual(product.tags, [])

    def test_add_rejects(self):
        with self.assertRaises(DuplicateProduct):
            self.catalog.add(MUG, "Mug again", 350, Money(1250), ["kitchen"])
        with self.assertRaises(ValueError):
            self.catalog.add("mug-1", "Mug", 350, Money(1250), ["kitchen"])
        with self.assertRaises(ValueError):
            self.catalog.add(CHAIR, "Chair", 0, Money(8500), ["office"])
        with self.assertRaises(UnknownProduct):
            self.catalog.get("NOP-0000")

    def test_tag_and_find(self):
        self.catalog.tag(LAMP, "Kitchen")
        self.catalog.tag(LAMP, "kitchen")
        self.assertEqual(self.catalog.get(LAMP).tags, ["office", "kitchen"])
        self.assertEqual([p.sku for p in self.catalog.find_by_tag("kitchen")], [LAMP, MUG])
        self.catalog.untag(MUG, "kitchen")
        self.assertEqual([p.sku for p in self.catalog.find_by_tag("kitchen")], [LAMP])
        self.assertEqual(self.catalog.tag_counts(), {"gift-set": 1, "kitchen": 1, "office": 1})

    def test_load_csv(self):
        text = "sku,name,weight_g,price,tags\nKTL-0003,Kettle,1500,30.00,kitchen|electric\n"
        added = self.catalog.load_csv(text)
        self.assertEqual([p.tags for p in added], [["kitchen", "electric"]])
        with self.assertRaises(ValueError):
            self.catalog.load_csv("sku,name,weight_g,price,tags\nCHR-0004,Chair,heavy,85.00,\n")
        self.assertNotIn(CHAIR, self.catalog)


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.inv = Inventory()
        self.inv.receive(MUG, "A-01-01", 10)
        self.inv.receive(MUG, "A-01-02", 5)

    def test_on_hand_and_available(self):
        self.assertEqual(self.inv.on_hand(MUG), 15)
        self.assertEqual(self.inv.on_hand(MUG, "A-01-02"), 5)
        self.assertEqual(self.inv.available(MUG), 15)
        self.inv.adjust(MUG, "A-01-01", -3, "breakage")
        self.assertEqual(self.inv.available(MUG), 12)

    def test_reserve(self):
        self.assertEqual(self.inv.reserve("SO-0001", MUG, 4), 4)
        self.assertEqual(self.inv.available(MUG), 11)
        with self.assertRaises(InsufficientStock):
            self.inv.reserve("SO-0002", MUG, 12)
        self.assertEqual(self.inv.reserve("SO-0002", MUG, 12, partial=True), 11)
        self.assertEqual(self.inv.available(MUG), 0)
        self.assertEqual(self.inv.holders(MUG), {"SO-0001": 4, "SO-0002": 11})

    def test_set_count_and_transfer(self):
        self.assertEqual(self.inv.set_count(MUG, "A-01-02", 7), 2)
        self.assertEqual(self.inv.available(MUG), 17)
        self.inv.transfer(MUG, "A-01-02", "A-01-01", 7)
        self.assertEqual(self.inv.bins_for(MUG), {"A-01-01": 17})
        self.assertEqual(self.inv.available(MUG), 17)

    def test_consume(self):
        self.inv.reserve("SO-0001", MUG, 3)
        self.inv.consume("SO-0001", [("A-01-02", MUG, 3)])
        self.assertEqual(self.inv.on_hand(MUG, "A-01-02"), 2)
        self.assertEqual(self.inv.reservations("SO-0001"), {})
        self.assertEqual(self.inv.available(MUG), 12)
        with self.assertRaises(InsufficientStock):
            self.inv.consume("SO-0001", [("A-01-01", MUG, 1)])

    def test_depot_receive_checks(self):
        depot, _ = stocked_depot()
        with self.assertRaises(UnknownProduct):
            depot.receive("NOP-0000", "A-01-01", 1)
        with self.assertRaises(ValueError):
            depot.receive(MUG, "shelf 3", 1)
        self.assertEqual(depot.available(CHAIR), 6)


if __name__ == "__main__":
    unittest.main()
