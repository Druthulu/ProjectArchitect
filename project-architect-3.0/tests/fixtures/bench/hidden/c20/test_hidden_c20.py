import json
import unittest
from datetime import date

from helpers import DUNE, HOBBIT, ORWELL, library
from shelf import storage
from shelf.models import Loan, NotFound


def loan(loan_id):
    return Loan(loan_id, DUNE, "M0001", date(2024, 3, 4), date(2024, 3, 25))


class HiddenC20(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()

    def test_ids_continue_after_load(self):
        for isbn in (DUNE, HOBBIT, ORWELL):
            self.desk.checkout(isbn, "M0001")
        data = json.loads(storage.dumps(self.catalog, self.members, self.desk))
        data["loans"].reverse()
        _, _, desk2 = storage.loads(json.dumps(data), self.clock)
        self.assertEqual(sorted(x.loan_id for x in desk2.loans), [1, 2, 3])
        self.assertEqual(desk2.checkout(DUNE, "M0002").loan_id, 4)
        self.assertEqual(desk2.get_loan(2).isbn, HOBBIT)

    def test_load_with_gaps_and_duplicates(self):
        for isbn in (DUNE, HOBBIT, ORWELL):
            self.desk.checkout(isbn, "M0001")
        data = json.loads(storage.dumps(self.catalog, self.members, self.desk))
        data["loans"][0]["loan_id"], data["loans"][1]["loan_id"] = 7, 2
        data["loans"] = data["loans"][:2]
        _, _, desk2 = storage.loads(json.dumps(data), self.clock)
        self.assertEqual(desk2.checkout(DUNE, "M0002").loan_id, 8)
        data["loans"][1]["loan_id"] = 7
        with self.assertRaises(ValueError):
            storage.loads(json.dumps(data), self.clock)
        data["loans"] = []
        _, _, desk3 = storage.loads(json.dumps(data), self.clock)
        self.assertEqual(desk3.checkout(DUNE, "M0002").loan_id, 1)

    def test_restore(self):
        self.desk.restore(x for x in [loan(5), loan(2)])
        self.assertEqual(sorted(x.loan_id for x in self.desk.loans), [2, 5])
        self.assertEqual(self.desk.checkout(HOBBIT, "M0002").loan_id, 6)
        self.assertEqual(self.desk.get_loan(5).loan_id, 5)
        with self.assertRaises(NotFound):
            self.desk.get_loan(3)

    def test_restore_duplicates(self):
        self.desk.restore([loan(1)])
        with self.assertRaises(ValueError):
            self.desk.restore([loan(4), loan(4)])
        self.assertEqual([x.loan_id for x in self.desk.loans], [1])
        self.assertEqual(self.desk.checkout(HOBBIT, "M0002").loan_id, 2)

    def test_restore_empty(self):
        self.desk.checkout(HOBBIT, "M0002")
        self.desk.restore([])
        self.assertEqual(self.desk.loans, [])
        self.assertEqual(self.desk.checkout(HOBBIT, "M0002").loan_id, 1)


if __name__ == "__main__":
    unittest.main()
