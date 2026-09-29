import json
import unittest
from datetime import date

from helpers import DUNE, library
from shelf import reports, storage
from shelf.catalog import Catalog
from shelf.isbn import to_isbn13
from shelf.models import Book, NotFound

TEN, THIRTEEN = "0306406152", "9780306406157"


class HiddenC17(unittest.TestCase):
    def test_to_isbn13(self):
        self.assertEqual(to_isbn13("0-306-40615-2"), THIRTEEN)
        self.assertEqual(to_isbn13("978-0-306-40615-7"), THIRTEEN)
        self.assertEqual(to_isbn13("097522980x"), "9780975229804")
        for bad in ("0306406153", "12345", "", "9780306406158"):
            with self.assertRaises(ValueError, msg=bad):
                to_isbn13(bad)

    def test_catalog_merges_forms(self):
        c = Catalog()
        stored = c.add(Book(TEN, "Test", "X", copies=1))
        self.assertEqual(stored.isbn, THIRTEEN)
        c.add(Book(THIRTEEN, "Test", "X", copies=2))
        self.assertEqual(len(c), 1)
        self.assertEqual(c.get("0-306-40615-2").copies, 3)
        self.assertEqual(c.find(THIRTEEN).isbn, THIRTEEN)
        self.assertIsNone(c.find("030640615X"))
        with self.assertRaises(NotFound):
            c.get("junk")
        with self.assertRaises(ValueError):
            c.add(Book("0306406153", "Bad", "X"))
        c.remove(TEN)
        self.assertEqual(len(c), 0)

    def test_old_library_loads_canonical(self):
        old = {"format": 2,
               "books": [{"isbn": TEN, "title": "Test", "author": "X", "year": 0, "copies": 2, "tags": []}],
               "members": [{"member_id": "M0001", "name": "Ada", "email": "", "kind": "standard", "active": True}],
               "loans": [{"loan_id": 1, "isbn": TEN, "member_id": "M0001", "start": "2024-03-04",
                          "due": "2024-03-25", "returned": None, "renewals": 0},
                         {"loan_id": 2, "isbn": "0-306-40615-2", "member_id": "M0001", "start": "2024-03-01",
                          "due": "2024-03-22", "returned": "2024-03-08", "renewals": 0}]}
        catalog, members, desk = storage.loads(json.dumps(old), lambda: date(2024, 4, 1))
        self.assertEqual(catalog.get(TEN).isbn, THIRTEEN)
        self.assertEqual([loan.isbn for loan in desk.loans], [THIRTEEN, THIRTEEN])
        self.assertEqual(desk.available(THIRTEEN), 1)
        self.assertEqual(reports.overdue_report(desk), [f"2024-03-25 M0001 {THIRTEEN} 7d $1.50"])
        desk.checkout(THIRTEEN, "M0001")
        self.assertEqual(desk.available(TEN), 0)
        self.assertEqual(reports.popular(desk), [(THIRTEEN, 3)])
        text = storage.dumps(catalog, members, desk)
        self.assertEqual({d["isbn"] for d in json.loads(text)["loans"]}, {THIRTEEN})
        self.assertEqual(storage.dumps(*storage.loads(text)), text)

    def test_desk_uses_canonical(self):
        catalog, members, desk, clock = library()
        catalog.add(Book(TEN, "Test", "X", copies=2))
        loan = desk.checkout("0-306-40615-2", "M0001")
        self.assertEqual(loan.isbn, THIRTEEN)
        self.assertEqual(desk.available(TEN), 1)
        self.assertEqual(desk.available(DUNE), 3)


if __name__ == "__main__":
    unittest.main()
