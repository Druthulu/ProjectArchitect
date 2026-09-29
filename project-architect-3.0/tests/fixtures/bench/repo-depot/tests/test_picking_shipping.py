import unittest
from decimal import Decimal

from depot.money import Money
from depot.picking import Location, by_zone, render, waves
from depot.shipping import Parcel, grams_to_kg, pack, price_for_kg, quote_parcel, started_kg
from helpers import ANVIL, CHAIR, KETTLE, LAMP, MUG, stocked_depot


class PickingTest(unittest.TestCase):
    def setUp(self):
        self.depot, self.clock = stocked_depot()

    def _placed(self, customer, lines):
        order = self.depot.create_order(customer, lines)
        self.depot.place(order.order_id)
        return order.order_id

    def test_location(self):
        self.assertEqual(Location.parse("B-03-12"), Location("B", 3, 12))
        self.assertEqual(Location.parse("B-03-12").code, "B-03-12")
        with self.assertRaises(ValueError):
            Location.parse("B3-12")

    def test_pick_list_walk_order(self):
        first = self._placed("Ada Lovelace", [(KETTLE, 1), (MUG, 2)])
        second = self._placed("Ben Okri", [(LAMP, 1), (CHAIR, 5)])
        pick_list = self.depot.pick(second, first)
        self.assertEqual([(l.bin, l.sku, l.qty) for l in pick_list.lines], [
            ("A-01-01", MUG, 2), ("A-02-03", LAMP, 1), ("B-01-02", KETTLE, 1),
            ("B-03-01", CHAIR, 4), ("C-01-01", CHAIR, 1)])
        self.assertEqual(pick_list.lines[0].orders, (first,))
        self.assertEqual(pick_list.order_ids, (first, second))
        self.assertEqual(list(by_zone(pick_list)), ["A", "B", "C"])
        self.assertIn("9 units from 5 bins", render(pick_list))

    def test_waves(self):
        self.assertEqual(waves(["a", "b", "c", "d", "e"], 2), [("a", "b"), ("c", "d"), ("e",)])
        ids = [self._placed(f"Customer {n}", [(MUG, 1)]) for n in range(3)]
        self.depot.wave_size = 2
        planned = self.depot.pick_waves()
        self.assertEqual([p.order_ids for p in planned], [tuple(ids[:2]), (ids[2],)])


class ShippingTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(grams_to_kg(1500), Decimal("1.5"))
        self.assertEqual(started_kg(Decimal("2.001")), 3)
        self.assertEqual(started_kg(Decimal("0.2")), 1)

    def test_parcel_prices(self):
        self.assertEqual(price_for_kg(Decimal("0.7")), Money(490))
        self.assertEqual(price_for_kg(Decimal("2.7")), Money(655))
        self.assertEqual(price_for_kg(Decimal("2.7"), level="express"), Money(983))
        self.assertEqual(price_for_kg(Decimal("16")), Money(1780))

    def test_pack_single_parcel(self):
        parcels = pack([(CHAIR, 8000), (MUG, 350), (CHAIR, 8000)])
        self.assertEqual(len(parcels), 1)
        self.assertEqual(parcels[0].weight_g, 16350)
        self.assertEqual(quote_parcel(parcels[0]), Money(1835))

    def test_heavy_item_travels_alone(self):
        parcels = pack([(ANVIL, 30000)])
        self.assertEqual(len(parcels), 1)
        self.assertTrue(parcels[0].has_heavy_item)
        self.assertEqual(quote_parcel(parcels[0]), Money(5550))
        self.assertFalse(Parcel([(CHAIR, 8000)]).has_heavy_item)

    def test_depot_ship_quote(self):
        depot, _ = stocked_depot()
        order = depot.create_order("Ada Lovelace", [(LAMP, 1), (KETTLE, 1)], service_level="express")
        self.assertEqual(depot.shipping_options(order.order_id),
                         {"economy": Money(557), "standard": Money(655), "express": Money(983)})
        depot.place(order.order_id)
        depot.pick(order.order_id)
        quote = depot.ship(order.order_id)
        self.assertEqual((quote.parcel_count, quote.weight_g, quote.total), (1, 2700, Money(983)))


if __name__ == "__main__":
    unittest.main()
