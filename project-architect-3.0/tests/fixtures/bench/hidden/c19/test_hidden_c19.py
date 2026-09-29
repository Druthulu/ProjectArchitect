import unittest
from datetime import date

from helpers import DUNE, HOBBIT, ORWELL, library
from shelf import reports
from shelf.models import Loan, NotFound


class HiddenC19(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        self.desk.checkout(DUNE, "M0001")              # 1, due 2024-03-25
        self.desk.checkout(ORWELL, "M0002")            # 2
        self.clock.advance(7)
        self.desk.checkout(HOBBIT, "M0001")            # 3, due 2024-04-01
        self.clock.advance(21)
        self.desk.checkin(1)                           # 7 days late

    def test_status(self):
        loan = Loan(1, DUNE, "M0001", date(2024, 3, 4), date(2024, 3, 25))
        self.assertEqual(loan.status(date(2024, 3, 25)), "open")
        self.assertEqual(loan.status(date(2024, 3, 26)), "overdue")
        loan.returned = date(2024, 3, 30)
        self.assertEqual(loan.status(date(2024, 4, 30)), "returned")

    def test_statement(self):
        self.assertEqual(reports.member_statement(self.desk, "M0001", date(2024, 4, 5)), [
            "Statement for Ada Lovelace (M0001, standard) on 2024-04-05",
            "1 Dune | due 2024-03-25 | returned | fee $1.50",
            "3 The Hobbit | due 2024-04-01 | overdue | fee $0.75",
            "Total due: $0.75",
        ])

    def test_default_today(self):
        self.assertEqual(reports.member_statement(self.desk, "M0001"), [
            "Statement for Ada Lovelace (M0001, standard) on 2024-04-01",
            "1 Dune | due 2024-03-25 | returned | fee $1.50",
            "3 The Hobbit | due 2024-04-01 | open",
            "Total due: $0.00",
        ])

    def test_student_and_empty(self):
        lines = reports.member_statement(self.desk, "M0002", date(2024, 3, 28))
        self.assertEqual(lines[1:], ["2 Nineteen Eighty-Four | due 2024-03-18 | overdue | fee $0.90",
                                     "Total due: $0.90"])
        self.assertEqual(reports.member_statement(self.desk, "M0003", date(2024, 4, 5)), [
            "Statement for Cy Twombly (M0003, staff) on 2024-04-05", "Total due: $0.00"])

    def test_unknown(self):
        with self.assertRaises(NotFound):
            reports.member_statement(self.desk, "M0099")


if __name__ == "__main__":
    unittest.main()
