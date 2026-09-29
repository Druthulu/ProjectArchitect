import unittest
from datetime import date

from helpers import DUNE, HOBBIT, Clock, library
from shelf.dates import next_open_day
from shelf.loans import LoanDesk


def d(month, day):
    return date(2024, month, day)


class HiddenC18(unittest.TestCase):
    def test_next_open_day(self):
        self.assertEqual(next_open_day(d(3, 9)), d(3, 11))
        self.assertEqual(next_open_day(d(3, 8)), d(3, 8))
        self.assertEqual(next_open_day(d(3, 11), [d(3, 11), d(3, 12)]), d(3, 13))
        self.assertEqual(next_open_day(d(3, 29), (x for x in [d(3, 29), d(4, 1)])), d(4, 2))

    def desk(self, start, holidays=None):
        catalog, members, _, _ = library()
        if holidays is None:
            return LoanDesk(catalog, members, Clock(start))
        return LoanDesk(catalog, members, Clock(start), holidays=holidays)

    def test_holiday_due(self):
        desk = self.desk(d(3, 4), (x for x in [d(3, 25), d(4, 16)]))
        loan = desk.checkout(DUNE, "M0001")
        self.assertEqual(loan.due, d(3, 26))
        desk.renew(loan.loan_id)
        self.assertEqual(loan.due, d(4, 17))

    def test_generator_holidays_used_again(self):
        desk = self.desk(d(3, 4), (x for x in [d(3, 25), d(3, 26), d(4, 17)]))
        first = desk.checkout(DUNE, "M0001")
        second = desk.checkout(HOBBIT, "M0001")
        self.assertEqual((first.due, second.due), (d(3, 27), d(3, 27)))
        desk.renew(first.loan_id)
        self.assertEqual(first.due, d(4, 18))

    def test_weekend_due(self):
        desk = self.desk(d(3, 9))
        self.assertEqual(desk.checkout(DUNE, "M0001").due, d(4, 1))
        self.assertEqual(desk.checkout(HOBBIT, "M0002").due, d(3, 25))

    def test_open_day_unchanged(self):
        desk = self.desk(d(3, 7), [])
        self.assertEqual(desk.checkout(DUNE, "M0002").due, d(3, 21))


if __name__ == "__main__":
    unittest.main()
