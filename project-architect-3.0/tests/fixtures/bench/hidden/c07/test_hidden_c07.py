import json
import unittest
from datetime import date

from shelf import reports, storage

V1 = {
    "format": 1,
    "books": [
        {"isbn": "978-0-441-17271-9", "title": "Dune", "author": "Frank Herbert", "year": 1965, "copies": 2},
        {"isbn": "0-9752298-0-x", "title": "Anon", "author": "Nobody", "year": 1990},
        {"isbn": "9780451524935", "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949},
    ],
    "members": [{"member_id": "M0007", "name": "Ada", "email": "ada@example.org"}],
    "loans": [
        {"loan_id": 2, "isbn": "978 0441 172719", "member_id": "M0007",
         "start": "01/02/2024", "due": "22/02/2024", "returned": "20/02/2024"},
        {"loan_id": 3, "isbn": "978-0-441-17271-9", "member_id": "M0007",
         "start": "04/03/2024", "due": "25/03/2024", "returned": None},
        {"loan_id": 4, "isbn": "097522980x", "member_id": "M0007",
         "start": "01/02/2024", "due": "22/02/2024", "returned": None},
    ],
}


class HiddenC07(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk = storage.loads(json.dumps(V1), lambda: date(2024, 3, 10))

    def test_books(self):
        dune = self.catalog.get("9780441172719")
        self.assertEqual((dune.copies, dune.tags), (2, []))
        self.assertEqual(self.catalog.get("9780451524935").copies, 1)

    def test_members(self):
        m = self.members.get("M0007")
        self.assertEqual((m.kind, m.active), ("standard", True))
        self.assertEqual(self.members.register("New").member_id, "M0008")

    def test_loans(self):
        old, new, anon = self.desk.loans
        self.assertEqual((old.start, old.due, old.returned), (date(2024, 2, 1), date(2024, 2, 22), date(2024, 2, 20)))
        self.assertEqual((new.start, new.due, new.returned, new.renewals), (date(2024, 3, 4), date(2024, 3, 25), None, 0))
        self.assertEqual(self.desk.available("9780441172719"), 1)
        self.assertEqual(self.desk.available("097522980X"), 0)
        self.assertEqual([loan.isbn for loan in self.desk.loans], ["9780441172719", "9780441172719", "097522980X"])

    def test_typed_isbns_work_like_new(self):
        self.assertEqual(self.catalog.get("097522980X").copies, 1)
        self.assertEqual(reports.popular(self.desk), [("9780441172719", 2), ("097522980X", 1)])
        self.assertEqual(reports.member_loans(self.desk, "M0007"), ["Dune", "Anon (open)", "Dune (open)"])
        self.assertEqual(reports.overdue_report(self.desk), ["2024-02-22 M0007 097522980X 17d $4.00"])
        self.desk.checkout("978-0-441-17271-9", "M0007")
        self.assertEqual(self.desk.available("9780441172719"), 0)

    def test_dumps_format_2(self):
        text = storage.dumps(self.catalog, self.members, self.desk)
        data = json.loads(text)
        self.assertEqual(data["format"], 2)
        self.assertEqual(data["loans"][0]["start"], "2024-02-01")
        self.assertEqual([d["isbn"] for d in data["loans"]], ["9780441172719", "9780441172719", "097522980X"])
        again = storage.loads(text)
        self.assertEqual(storage.dumps(*again), text)

    def test_unsupported(self):
        for fmt in (0, 3, None):
            doc = dict(V1, format=fmt)
            if fmt is None:
                del doc["format"]
            with self.assertRaises(ValueError) as cm:
                storage.loads(json.dumps(doc))
            self.assertIn("unsupported format", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
