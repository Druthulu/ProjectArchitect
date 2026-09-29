import unittest
from datetime import date

from helpers import DUNE, HOBBIT, ORWELL, library
from shelf import fees, policy
from shelf.models import LimitReached, NotFound


class HiddenC13(unittest.TestCase):
    def test_caps(self):
        self.assertEqual(policy.fee_cap("student"), 400)
        self.assertEqual(policy.fee_cap("staff"), 0)
        with self.assertRaises(ValueError):
            policy.fee_cap("visitor")
        self.assertEqual(fees.late_fee_cents(30, "student"), 290)
        self.assertEqual(fees.late_fee_cents(41, "student"), 400)
        self.assertEqual(fees.late_fee_cents(400, "student"), 400)
        self.assertEqual(fees.late_fee_cents(400, "standard"), 1000)
        self.assertEqual(fees.late_fee_cents(400, "staff"), 0)

    def test_outstanding(self):
        catalog, members, desk, clock = library()
        desk.checkout(DUNE, "M0001")
        hobbit = desk.checkout(HOBBIT, "M0001")
        desk.checkout(ORWELL, "M0002")
        desk.checkout(DUNE, "M0003")
        clock.advance(30)
        self.assertEqual(fees.outstanding_cents(desk, "M0001"), 400)
        self.assertEqual(fees.outstanding_cents(desk, "M0002"), 150)
        self.assertEqual(fees.fee_summary(desk), {"M0001": 400, "M0002": 150})
        self.assertEqual(list(fees.fee_summary(desk)), ["M0001", "M0002"])
        desk.checkin(hobbit.loan_id)
        self.assertEqual(fees.outstanding_cents(desk, "M0001"), 200)
        self.assertEqual(fees.outstanding_cents(desk, "M0002", date(2024, 6, 1)), 400)
        self.assertEqual(fees.fee_summary(desk, date(2024, 3, 20)), {"M0002": 10})
        with self.assertRaises(NotFound):
            fees.outstanding_cents(desk, "M0099")

    def test_renew_refused_while_owing(self):
        catalog, members, desk, clock = library()
        dune = desk.checkout(DUNE, "M0001")                 # due 2024-03-25
        clock.advance(7)
        hobbit = desk.checkout(HOBBIT, "M0001")             # due 2024-04-01
        clock.advance(15)                                   # 2024-03-26: Dune 1 day late, inside grace
        self.assertEqual(desk.renew(hobbit.loan_id).renewals, 1)
        clock.advance(2)                                    # 2024-03-28: Dune owes $0.50
        with self.assertRaises(LimitReached):
            desk.renew(hobbit.loan_id)
        self.assertEqual((hobbit.renewals, hobbit.due), (1, date(2024, 4, 22)))
        desk.checkin(dune.loan_id)                          # the fee left the open loans
        self.assertEqual(desk.renew(hobbit.loan_id).renewals, 2)

    def test_staff_overdue_owe_nothing(self):
        catalog, members, desk, clock = library()
        desk.checkout(DUNE, "M0003")                        # due 2024-04-15
        clock.advance(7)
        orwell = desk.checkout(ORWELL, "M0003")             # due 2024-04-22
        clock.advance(40)                                   # 2024-04-20: Dune 5 days late, staff fee 0
        self.assertEqual(desk.renew(orwell.loan_id).renewals, 1)


if __name__ == "__main__":
    unittest.main()
