import unittest

from depot.money import Money
from depot.shipping import pack, quote_parcel, quote_shipment
from helpers import CHAIR, stocked_depot


class MultiParcelQuoteTest(unittest.TestCase):
    def test_three_chairs_ship_as_two_parcels(self):
        depot, _ = stocked_depot()
        order = depot.create_order("Ada Lovelace", [(CHAIR, 3)])
        quote = depot.quote_shipping(order.order_id)
        self.assertEqual(quote.parcel_count, 2)
        self.assertEqual(list(quote.prices), [Money(1780), Money(1040)])
        self.assertEqual(quote.total, Money(2820))

    def test_one_chair_unchanged(self):
        depot, _ = stocked_depot()
        order = depot.create_order("Ada Lovelace", [(CHAIR, 1)])
        self.assertEqual(depot.quote_shipping(order.order_id).total, Money(1040))

    def test_total_is_sum_of_parcel_quotes(self):
        parcels = pack([("BOX-0001", 15000), ("BOX-0002", 15000), ("BOX-0003", 1500)])
        self.assertEqual(len(parcels), 2)
        for level in ("express", "standard", "economy"):
            quote = quote_shipment(parcels, level)
            self.assertEqual(list(quote.prices), [quote_parcel(p, level) for p in parcels], level)
            self.assertEqual(quote.total, quote.prices[0] + quote.prices[1], level)


if __name__ == "__main__":
    unittest.main()
