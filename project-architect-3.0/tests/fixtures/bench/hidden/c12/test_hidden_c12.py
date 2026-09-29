import unittest

from helpers import DUNE, HOBBIT, library
from shelf import policy
from shelf.models import LimitReached, NotFound


class HiddenC12(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()

    def test_policy(self):
        self.assertEqual(policy.MAX_RENEWALS, {"standard": 2, "student": 1, "staff": 5})
        self.assertEqual([policy.max_renewals(k) for k in ("standard", "student", "staff")], [2, 1, 5])
        with self.assertRaises(ValueError):
            policy.max_renewals("visitor")

    def test_student_once(self):
        loan = self.desk.checkout(DUNE, "M0002")
        self.desk.renew(loan.loan_id)
        with self.assertRaises(LimitReached):
            self.desk.renew(loan.loan_id)

    def test_staff_five(self):
        loan = self.desk.checkout(DUNE, "M0003")
        for _ in range(5):
            self.desk.renew(loan.loan_id)
        self.assertEqual(loan.renewals, 5)
        with self.assertRaises(LimitReached):
            self.desk.renew(loan.loan_id)

    def test_renewals_left(self):
        loan = self.desk.checkout(DUNE, "M0001")
        self.assertEqual(self.desk.renewals_left(loan.loan_id), 2)
        self.desk.renew(loan.loan_id)
        self.assertEqual(self.desk.renewals_left(loan.loan_id), 1)
        staff = self.desk.checkout(HOBBIT, "M0003")
        self.assertEqual(self.desk.renewals_left(staff.loan_id), 5)
        self.clock.advance(20)
        self.assertEqual(self.desk.renewals_left(loan.loan_id), 1)
        self.clock.advance(23)
        self.assertEqual(self.desk.renewals_left(loan.loan_id), 0)
        self.assertEqual(self.desk.renewals_left(staff.loan_id), 0)
        self.desk.checkin(loan.loan_id)
        self.assertEqual(self.desk.renewals_left(loan.loan_id), 0)
        with self.assertRaises(NotFound):
            self.desk.renewals_left(99)


if __name__ == "__main__":
    unittest.main()
