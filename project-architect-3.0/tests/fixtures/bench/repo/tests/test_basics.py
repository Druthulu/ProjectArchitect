import unittest
from datetime import date

from shelf import dates, fees, isbn, policy


class IsbnTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(isbn.normalize_isbn("0-9752298-0-x"), "097522980X")
        self.assertEqual(isbn.normalize_isbn("978 0 306 40615 7"), "9780306406157")

    def test_valid(self):
        self.assertTrue(isbn.is_valid_isbn("0-306-40615-2"))
        self.assertTrue(isbn.is_valid_isbn("978-0-306-40615-7"))
        self.assertTrue(isbn.is_valid_isbn("097522980X"))
        self.assertFalse(isbn.is_valid_isbn("0-306-40615-3"))
        self.assertFalse(isbn.is_valid_isbn("978030640615"))


class DatesTest(unittest.TestCase):
    def test_parse_and_format(self):
        d = dates.parse_date(" 2024-02-29 ")
        self.assertEqual(d, date(2024, 2, 29))
        self.assertEqual(dates.format_date(d), "2024-02-29")
        with self.assertRaises(ValueError):
            dates.parse_date("yesterday")

    def test_arithmetic(self):
        self.assertEqual(dates.add_days(date(2024, 2, 28), 2), date(2024, 3, 1))
        self.assertEqual(dates.days_between(date(2024, 3, 10), date(2024, 3, 4)), -6)

    def test_next_business_day(self):
        self.assertEqual(dates.next_business_day(date(2024, 3, 8)), date(2024, 3, 11))
        self.assertEqual(dates.next_business_day(date(2024, 3, 4)), date(2024, 3, 5))


class FeesTest(unittest.TestCase):
    def test_grace_and_rate(self):
        self.assertEqual(fees.late_fee_cents(0), 0)
        self.assertEqual(fees.late_fee_cents(1), 0)
        self.assertEqual(fees.late_fee_cents(3), 50)
        self.assertEqual(fees.late_fee_cents(3, "student"), 20)
        self.assertEqual(fees.late_fee_cents(30, "staff"), 0)

    def test_cap(self):
        self.assertEqual(fees.late_fee_cents(400), policy.FEE_CAP_CENTS)

    def test_format(self):
        self.assertEqual(fees.format_cents(1234), "$12.34")
        self.assertEqual(fees.format_cents(5), "$0.05")

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            fees.late_fee_cents(5, "visitor")


if __name__ == "__main__":
    unittest.main()
