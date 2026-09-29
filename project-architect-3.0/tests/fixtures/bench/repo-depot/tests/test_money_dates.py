import unittest
from datetime import date, datetime
from decimal import Decimal

from depot.dates import Calendar, billing_periods, month_bounds, period_label, within_returns_window
from depot.money import CurrencyMismatch, Money, parse_amount, round_half_up, total


class MoneyTest(unittest.TestCase):
    def test_parse_and_str(self):
        self.assertEqual(Money.of("12.5").cents, 1250)
        self.assertEqual(str(Money(-99)), "-0.99 EUR")
        self.assertEqual(parse_amount("3"), 300)
        with self.assertRaises(ValueError):
            parse_amount("1.005")
        with self.assertRaises(TypeError):
            Money.of(1.5)

    def test_arithmetic(self):
        a, b = Money(1250), Money(300)
        self.assertEqual(a + b, Money(1550))
        self.assertEqual(a - b, Money(950))
        self.assertEqual(3 * b, Money(900))
        self.assertTrue(b < a)
        self.assertEqual(total([a, b, b]), Money(1850))
        with self.assertRaises(CurrencyMismatch):
            a + Money(1, "USD")

    def test_half_up(self):
        self.assertEqual(round_half_up(Decimal("2.5")), 3)
        self.assertEqual(round_half_up(Decimal("-2.5")), -3)
        self.assertEqual(Money(125).percent(10), Money(13))
        self.assertEqual(Money(2000).less_percent("12.5"), Money(1750))

    def test_allocate(self):
        parts = Money(1000).allocate([1, 1, 1])
        self.assertEqual([p.cents for p in parts], [334, 333, 333])
        self.assertEqual(total(parts), Money(1000))


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.cal = Calendar()

    def test_business_days_skip_easter(self):
        self.assertEqual(self.cal.next_business_day(date(2024, 3, 28)), date(2024, 4, 2))
        self.assertEqual(self.cal.add_business_days(date(2024, 3, 4), 5), date(2024, 3, 11))
        self.assertEqual(self.cal.business_days_between(date(2024, 3, 28), date(2024, 4, 3)), 2)

    def test_promised_date(self):
        self.assertEqual(self.cal.promised_date(date(2024, 3, 4), "standard"), date(2024, 3, 7))
        self.assertEqual(self.cal.promised_date(datetime(2024, 3, 4, 16, 0), "express"), date(2024, 3, 6))
        self.assertEqual(self.cal.promised_date(date(2024, 3, 9), "express"), date(2024, 3, 12))
        with self.assertRaises(ValueError):
            self.cal.promised_date(date(2024, 3, 4), "overnight")

    def test_billing_periods(self):
        self.assertEqual(month_bounds(date(2024, 2, 10)), (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(billing_periods(date(2024, 1, 20), date(2024, 3, 5)),
                         [(date(2024, 1, 20), date(2024, 1, 31)),
                          (date(2024, 2, 1), date(2024, 2, 29)),
                          (date(2024, 3, 1), date(2024, 3, 5))])
        self.assertEqual(period_label(date(2024, 12, 31)), "2024-12")

    def test_returns_window(self):
        delivered = date(2024, 3, 7)
        self.assertTrue(within_returns_window(delivered, date(2024, 3, 12)))
        self.assertFalse(within_returns_window(delivered, date(2024, 4, 21)))
        self.assertFalse(within_returns_window(delivered, date(2024, 3, 6)))


if __name__ == "__main__":
    unittest.main()
