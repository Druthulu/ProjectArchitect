import unittest

from helpers import DUNE, HOBBIT, ORWELL, library
from shelf.models import Unavailable


class HiddenC11(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        self.members.register("Dee", "dee@example.org")          # M0004

    def test_count(self):
        self.assertEqual(self.desk.holds.count(ORWELL), 0)
        self.desk.place_hold(ORWELL, "M0002")
        self.desk.place_hold(ORWELL, "M0003")
        self.assertEqual(self.desk.holds.count(ORWELL), 2)

    def test_single_copy_queue(self):
        first = self.desk.checkout(ORWELL, "M0001")
        self.desk.place_hold(ORWELL, "M0002")
        self.desk.place_hold(ORWELL, "M0003")
        self.assertEqual(self.desk.notices, [])
        self.assertEqual(self.desk.checkin(first.loan_id), 0)
        self.assertEqual(self.desk.notices, [("M0002", ORWELL)])
        for other in ("M0003", "M0004"):
            with self.assertRaises(Unavailable):
                self.desk.checkout(ORWELL, other)
        self.desk.checkout(ORWELL, "M0002")
        self.assertEqual([h.member_id for h in self.desk.holds.queue(ORWELL)], ["M0003"])

    def test_free_copies_beyond_holds(self):
        self.desk.place_hold(DUNE, "M0002")
        self.desk.checkout(DUNE, "M0001")
        self.desk.checkout(DUNE, "M0003")
        with self.assertRaises(Unavailable):
            self.desk.checkout(DUNE, "M0004")
        self.desk.checkout(DUNE, "M0002")
        self.assertEqual(self.desk.holds.count(DUNE), 0)

    def test_second_copy_notifies_second_holder(self):
        a = self.desk.checkout(HOBBIT, "M0001")
        b = self.desk.checkout(HOBBIT, "M0004")
        self.desk.place_hold(HOBBIT, "M0002")
        self.desk.place_hold(HOBBIT, "M0003")
        self.desk.checkin(a.loan_id)
        self.desk.checkin(b.loan_id)
        self.assertEqual(self.desk.notices, [("M0002", HOBBIT), ("M0003", HOBBIT)])
        self.desk.checkout(HOBBIT, "M0003")
        self.desk.checkout(HOBBIT, "M0002")

    def test_no_holds_no_notice(self):
        loan = self.desk.checkout(DUNE, "M0001")
        self.desk.checkin(loan.loan_id)
        self.assertEqual(self.desk.notices, [])


if __name__ == "__main__":
    unittest.main()
