import json
import unittest
from datetime import date

from helpers import DUNE, HOBBIT, ORWELL, library
from shelf import reports, storage


class StorageTest(unittest.TestCase):
    def test_round_trip(self):
        catalog, members, desk, clock = library()
        loan = desk.checkout(DUNE, "M0001")
        desk.checkout(HOBBIT, "M0002")
        clock.advance(7)
        desk.checkin(loan.loan_id)
        text = storage.dumps(catalog, members, desk)
        catalog2, members2, desk2 = storage.loads(text, clock)
        self.assertEqual(storage.dumps(catalog2, members2, desk2), text)
        self.assertEqual(desk2.loans[0].returned, date(2024, 3, 11))
        self.assertEqual(desk2.available(HOBBIT), 1)
        self.assertEqual(members2.get("M0002").kind, "student")

    def test_rejects_unknown_format(self):
        catalog, members, desk, _ = library()
        data = json.loads(storage.dumps(catalog, members, desk))
        data["format"] = 99
        with self.assertRaises(ValueError):
            storage.loads(json.dumps(data))


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()

    def test_overdue_report(self):
        self.desk.checkout(DUNE, "M0001")
        self.desk.checkout(ORWELL, "M0002")
        self.clock.advance(24)
        self.assertEqual(reports.overdue_report(self.desk), [
            "2024-03-18 M0002 9780451524935 10d $0.90",
            "2024-03-25 M0001 9780441172719 3d $0.50",
        ])

    def test_popular_and_member_loans(self):
        first = self.desk.checkout(DUNE, "M0001")
        self.desk.checkout(DUNE, "M0002")
        self.desk.checkout(HOBBIT, "M0001")
        self.desk.checkin(first.loan_id)
        self.assertEqual(reports.popular(self.desk, 1), [(DUNE, 2)])
        self.assertEqual(reports.member_loans(self.desk, "M0001"), ["Dune", "The Hobbit (open)"])


if __name__ == "__main__":
    unittest.main()
