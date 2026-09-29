import unittest

from helpers import DUNE, FELLOWSHIP, GOF, HOBBIT, KR, ORWELL, library
from shelf.models import InactiveMember, LimitReached, NotFound, Unavailable


class HiddenC10(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()

    def test_success(self):
        loans = self.desk.checkout_many([HOBBIT, DUNE, HOBBIT], "M0001")
        self.assertEqual([loan.loan_id for loan in loans], [1, 2, 3])
        self.assertEqual([loan.isbn for loan in loans], [HOBBIT, DUNE, HOBBIT])
        self.assertEqual(self.desk.available(HOBBIT), 0)

    def test_empty(self):
        self.assertEqual(self.desk.checkout_many([], "M0001"), [])

    def assertUntouched(self):
        self.assertEqual(self.desk.loans, [])
        self.assertEqual(self.desk.checkout(DUNE, "M0003").loan_id, 1)

    def test_unavailable_rolls_back(self):
        with self.assertRaises(Unavailable):
            self.desk.checkout_many([DUNE, ORWELL, ORWELL], "M0001")
        self.assertUntouched()

    def test_limit_counts_batch(self):
        with self.assertRaises(LimitReached):
            self.desk.checkout_many([HOBBIT, DUNE, GOF, KR], "M0002")
        self.assertUntouched()

    def test_limit_counts_existing(self):
        self.desk.checkout(FELLOWSHIP, "M0002")
        with self.assertRaises(LimitReached):
            self.desk.checkout_many([HOBBIT, DUNE, GOF], "M0002")
        self.assertEqual(len(self.desk.loans), 1)

    def test_first_failure_wins(self):
        # one after another: ORWELL, then ORWELL again is Unavailable before the student limit (3) is hit
        with self.assertRaises(Unavailable):
            self.desk.checkout_many([ORWELL, ORWELL, HOBBIT, DUNE], "M0002")
        self.assertUntouched()

    def test_first_failure_is_the_limit(self):
        with self.assertRaises(LimitReached):
            self.desk.checkout_many([HOBBIT, DUNE, GOF, ORWELL, ORWELL], "M0002")
        self.assertUntouched()

    def test_not_found_and_inactive(self):
        with self.assertRaises(NotFound):
            self.desk.checkout_many([DUNE, "9780306406157"], "M0001")
        self.members.deactivate("M0001")
        with self.assertRaises(InactiveMember):
            self.desk.checkout_many([DUNE], "M0001")
        self.assertUntouched()


if __name__ == "__main__":
    unittest.main()
