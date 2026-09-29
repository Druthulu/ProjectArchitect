import unittest

from helpers import LAMP, MUG, stocked_depot


class MalformedCountRowsTest(unittest.TestCase):
    def setUp(self):
        self.depot, _ = stocked_depot()

    def test_malformed_qty_is_rejected_and_stock_kept(self):
        sheet = "sku,bin,qty\nMUG-0001,A-01-01,twelve\nLMP-0002,A-02-03,9\n"
        result = self.depot.import_counts(sheet)
        self.assertEqual([e.line for e in result.errors], [2])
        self.assertEqual([row[:4] for row in result.applied], [(3, LAMP, "A-02-03", 9)])
        self.assertEqual(result.summary(), "1 rows applied, 1 rejected")
        self.assertEqual(self.depot.available(MUG), 20)
        self.assertEqual(self.depot.available(LAMP), 9)

    def test_other_malformed_forms(self):
        for raw in ("-3", "4.5", "", "1e3"):
            result = self.depot.import_counts(f"sku,bin,qty\nMUG-0001,A-01-01,{raw}\n")
            self.assertEqual([e.line for e in result.errors], [2], raw)
            self.assertEqual(result.applied, [], raw)
            self.assertEqual(self.depot.available(MUG), 20, raw)


if __name__ == "__main__":
    unittest.main()
