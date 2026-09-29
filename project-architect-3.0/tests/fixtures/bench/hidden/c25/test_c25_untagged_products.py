import unittest

from depot.catalog import Catalog
from depot.money import Money


class UntaggedProductsTest(unittest.TestCase):
    def test_tagging_one_untagged_product_leaves_the_next_untouched(self):
        catalog = Catalog()
        first = catalog.add("TRY-0001", "Serving tray", 900, Money(1450))
        catalog.tag("TRY-0001", "fragile")
        second = catalog.add("NAP-0002", "Napkins", 200, Money(399))
        self.assertEqual(first.tags, ["fragile"])
        self.assertEqual(second.tags, [])
        catalog.tag("NAP-0002", "linen")
        self.assertEqual(catalog.get("TRY-0001").tags, ["fragile"])
        self.assertEqual(catalog.get("NAP-0002").tags, ["linen"])

    def test_separate_catalogs_do_not_share_tags(self):
        one, two = Catalog(), Catalog()
        one.add("TRY-0001", "Serving tray", 900, Money(1450))
        one.tag("TRY-0001", "fragile")
        self.assertEqual(two.add("TRY-0001", "Serving tray", 900, Money(1450)).tags, [])

    def test_given_tags_are_copied(self):
        catalog = Catalog()
        tags = ["kitchen"]
        product = catalog.add("TRY-0001", "Serving tray", 900, Money(1450), tags)
        tags.append("sale")
        self.assertEqual(product.tags, ["kitchen"])


if __name__ == "__main__":
    unittest.main()
