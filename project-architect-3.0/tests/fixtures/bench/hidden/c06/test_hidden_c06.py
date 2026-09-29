import unittest
from datetime import date

from helpers import DUNE, FELLOWSHIP, HOBBIT, ORWELL, library
from shelf import reports


class HiddenC06(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        self.desk.checkout(DUNE, "M0001")          # due 2024-03-25
        self.desk.checkout(ORWELL, "M0002")        # due 2024-03-18, student
        self.desk.checkout(HOBBIT, "M0003")        # due 2024-04-15, staff
        done = self.desk.checkout(FELLOWSHIP, "M0001")
        self.desk.checkin(done.loan_id)

    def test_keys_and_order(self):
        self.assertEqual(list(reports.aging_report(self.desk)), ["1-7", "8-30", "31+"])
        self.assertEqual(reports.aging_report(self.desk)["1-7"], {"count": 0, "fees": 0})

    def test_buckets(self):
        r = reports.aging_report(self.desk, date(2024, 3, 27))
        self.assertEqual(r, {"1-7": {"count": 1, "fees": 25},
                             "8-30": {"count": 1, "fees": 80},
                             "31+": {"count": 0, "fees": 0}})
        r = reports.aging_report(self.desk, date(2024, 5, 1))
        self.assertEqual(r, {"1-7": {"count": 0, "fees": 0},
                             "8-30": {"count": 1, "fees": 0},
                             "31+": {"count": 2, "fees": 900 + 430}})

    def test_edges(self):
        r = reports.aging_report(self.desk, date(2024, 3, 25))
        self.assertEqual(r["1-7"]["count"], 1)
        r = reports.aging_report(self.desk, date(2024, 4, 18))
        self.assertEqual((r["8-30"]["count"], r["31+"]["count"]), (1, 1))

    def test_default_today(self):
        self.clock.advance(30)
        r = reports.aging_report(self.desk)
        self.assertEqual(r["8-30"], {"count": 2, "fees": 200 + 150})

    def test_lines(self):
        self.assertEqual(reports.aging_lines(self.desk, date(2024, 3, 27)), [
            "1-7: 1 loan, $0.25", "8-30: 1 loan, $0.80", "31+: 0 loans, $0.00"])


if __name__ == "__main__":
    unittest.main()
