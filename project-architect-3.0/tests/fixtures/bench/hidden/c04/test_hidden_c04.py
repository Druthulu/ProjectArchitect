import unittest

from shelf.fees import format_cents, parse_cents


class HiddenC04(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_cents(0), "$0.00")
        self.assertEqual(format_cents(5), "$0.05")
        self.assertEqual(format_cents(100000), "$1,000.00")
        self.assertEqual(format_cents(123456789), "$1,234,567.89")

    def test_format_negative(self):
        self.assertEqual(format_cents(-1234), "-$12.34")
        self.assertEqual(format_cents(-5), "-$0.05")
        self.assertEqual(format_cents(-123456), "-$1,234.56")
        self.assertEqual(format_cents(-100000), "-$1,000.00")

    def test_parse(self):
        self.assertEqual(parse_cents("$12.34"), 1234)
        self.assertEqual(parse_cents(" 12.34 "), 1234)
        self.assertEqual(parse_cents("-$1,234.50"), -123450)
        self.assertEqual(parse_cents("$5"), 500)
        self.assertEqual(parse_cents("1,000"), 100000)
        self.assertEqual(parse_cents("1234.50"), 123450)
        self.assertEqual(parse_cents("-5"), -500)
        self.assertEqual(parse_cents("-$0.05"), -5)

    def test_parse_rejects(self):
        for bad in ("12.3", "$-5", "", "1.234", "abc", "$", "12.34$"):
            with self.assertRaises(ValueError, msg=bad):
                parse_cents(bad)

    def test_parse_grouping(self):
        # commas, when typed, sit where format_cents puts them: groups of three from the right
        self.assertEqual(parse_cents("$12,345,678.90"), 1234567890)
        self.assertEqual(parse_cents("-1,000,000"), -100000000)
        for bad in ("1,23", "12,34.50", "1,2345", ",123", "1,,000", "123,", "$1,000,00"):
            with self.assertRaises(ValueError, msg=bad):
                parse_cents(bad)

    def test_round_trip(self):
        for cents in (0, 7, -7, 99, 1000, -250000, 987654321):
            self.assertEqual(parse_cents(format_cents(cents)), cents)


if __name__ == "__main__":
    unittest.main()
