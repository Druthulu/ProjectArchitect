import unittest
from datetime import date

from helpers import DUNE, FELLOWSHIP, HOBBIT, ORWELL, library
from shelf.models import InactiveMember, LimitReached, NotFound, Unavailable


class LoanDeskTest(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()

    def test_checkout_sets_due_by_kind(self):
        self.assertEqual(self.desk.checkout(HOBBIT, "M0001").due, date(2024, 3, 25))
        self.assertEqual(self.desk.checkout(HOBBIT, "M0002").due, date(2024, 3, 18))
        self.assertEqual(self.desk.checkout(DUNE, "M0003").due, date(2024, 4, 15))
        self.assertEqual(self.desk.available(HOBBIT), 0)

    def test_loan_ids_increase(self):
        ids = [self.desk.checkout(DUNE, m).loan_id for m in ("M0001", "M0002", "M0003")]
        self.assertEqual(ids, [1, 2, 3])

    def test_unavailable(self):
        self.desk.checkout(ORWELL, "M0001")
        with self.assertRaises(Unavailable):
            self.desk.checkout(ORWELL, "M0002")

    def test_limits(self):
        self.members.deactivate("M0003")
        with self.assertRaises(InactiveMember):
            self.desk.checkout(DUNE, "M0003")
        for isbn in (HOBBIT, DUNE, ORWELL):
            self.desk.checkout(isbn, "M0002")
        with self.assertRaises(LimitReached):
            self.desk.checkout(FELLOWSHIP, "M0002")

    def test_checkin_fee(self):
        loan = self.desk.checkout(DUNE, "M0001")
        self.clock.advance(21 + 4)
        self.assertEqual(self.desk.checkin(loan.loan_id), 75)
        self.assertEqual(self.desk.available(DUNE), 3)
        with self.assertRaises(ValueError):
            self.desk.checkin(loan.loan_id)
        with self.assertRaises(NotFound):
            self.desk.checkin(99)

    def test_renew(self):
        loan = self.desk.checkout(DUNE, "M0001")
        self.desk.renew(loan.loan_id)
        self.desk.renew(loan.loan_id)
        self.assertEqual(loan.due, date(2024, 5, 6))
        with self.assertRaises(LimitReached):
            self.desk.renew(loan.loan_id)

    def test_renew_overdue_refused(self):
        loan = self.desk.checkout(DUNE, "M0001")
        self.clock.advance(22)
        with self.assertRaises(LimitReached):
            self.desk.renew(loan.loan_id)

    def test_overdue_order(self):
        a = self.desk.checkout(DUNE, "M0001")
        b = self.desk.checkout(HOBBIT, "M0002")
        self.desk.checkout(ORWELL, "M0003")
        self.clock.advance(28)
        self.assertEqual([loan.loan_id for loan in self.desk.overdue()], [b.loan_id, a.loan_id])

    def test_place_hold(self):
        self.desk.checkout(ORWELL, "M0001")
        hold = self.desk.place_hold("978-0-451-52493-5", "M0002")
        self.assertEqual(hold.isbn, ORWELL)
        self.assertEqual([h.member_id for h in self.desk.holds.queue(ORWELL)], ["M0002"])
        with self.assertRaises(ValueError):
            self.desk.place_hold(ORWELL, "M0002")
        self.assertEqual(self.desk.holds.pop(ORWELL).member_id, "M0002")
        self.assertIsNone(self.desk.holds.pop(ORWELL))


if __name__ == "__main__":
    unittest.main()
