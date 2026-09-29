import unittest
from datetime import date

from shelf.dates import add_business_days, business_days_between

MON, FRI, SAT, NEXT_MON = date(2024, 3, 4), date(2024, 3, 8), date(2024, 3, 9), date(2024, 3, 11)


class HiddenC02(unittest.TestCase):
    def test_between(self):
        self.assertEqual(business_days_between(MON, NEXT_MON), 5)
        self.assertEqual(business_days_between(FRI, NEXT_MON), 1)
        self.assertEqual(business_days_between(FRI, SAT), 0)
        self.assertEqual(business_days_between(MON, date(2024, 4, 1)), 20)

    def test_between_empty(self):
        self.assertEqual(business_days_between(NEXT_MON, MON), 0)
        self.assertEqual(business_days_between(MON, MON), 0)

    def test_between_holidays(self):
        self.assertEqual(business_days_between(MON, NEXT_MON, [date(2024, 3, 6)]), 4)
        self.assertEqual(business_days_between(MON, NEXT_MON, {SAT}), 5)
        self.assertEqual(business_days_between(MON, NEXT_MON, (d for d in [MON, NEXT_MON])), 4)

    def test_add(self):
        self.assertEqual(add_business_days(FRI, 1), NEXT_MON)
        self.assertEqual(add_business_days(MON, 5), NEXT_MON)
        self.assertEqual(add_business_days(SAT, 1), NEXT_MON)
        self.assertEqual(add_business_days(SAT, 0), SAT)

    def test_add_holidays(self):
        self.assertEqual(add_business_days(MON, 5, [date(2024, 3, 5)]), date(2024, 3, 12))
        self.assertEqual(add_business_days(FRI, 1, (d for d in [NEXT_MON])), date(2024, 3, 12))

    def test_add_negative(self):
        with self.assertRaises(ValueError):
            add_business_days(MON, -1)


if __name__ == "__main__":
    unittest.main()
