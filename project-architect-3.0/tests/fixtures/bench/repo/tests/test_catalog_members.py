import unittest

from helpers import DUNE, FELLOWSHIP, HOBBIT, library
from shelf.catalog import Catalog
from shelf.members import MemberRegistry
from shelf.models import Book, Member, NotFound


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = library()[0]

    def test_get_and_find(self):
        self.assertEqual(self.catalog.get("978-0-547-92822-7").title, "The Hobbit")
        self.assertIsNone(self.catalog.find("9780306406157"))
        with self.assertRaises(NotFound):
            self.catalog.get("9780306406157")

    def test_add_merges_copies(self):
        book = self.catalog.add(Book(DUNE, "Dune", "Frank Herbert", copies=2))
        self.assertEqual(book.copies, 5)
        self.assertEqual(len(self.catalog), 6)

    def test_add_rejects_bad_isbn(self):
        with self.assertRaises(ValueError):
            Catalog().add(Book("1234567890", "Nope", "Nobody"))

    def test_search(self):
        titles = [b.title for b in self.catalog.search("tolkien")]
        self.assertEqual(titles, ["The Fellowship of the Ring", "The Hobbit"])
        self.assertEqual([b.isbn for b in self.catalog.search("HOBBIT")], [HOBBIT])
        self.assertEqual(self.catalog.search("zzz"), [])

    def test_by_tag_and_remove(self):
        self.assertEqual([b.isbn for b in self.catalog.by_tag("fantasy")], [FELLOWSHIP, HOBBIT])
        self.catalog.remove(FELLOWSHIP)
        self.assertEqual(len(self.catalog), 5)


class MembersTest(unittest.TestCase):
    def test_register_ids(self):
        reg = MemberRegistry()
        self.assertEqual(reg.register("A").member_id, "M0001")
        self.assertEqual(reg.register("B", kind="staff").member_id, "M0002")
        with self.assertRaises(ValueError):
            reg.register("C", kind="visitor")

    def test_add_keeps_ids_unique(self):
        reg = MemberRegistry()
        reg.add(Member("M0041", "Old"))
        self.assertEqual(reg.register("New").member_id, "M0042")

    def test_find_and_deactivate(self):
        members = library()[1]
        self.assertEqual(members.find_by_email(" BEN@example.org").member_id, "M0002")
        self.assertIsNone(members.find_by_email("zed@example.org"))
        members.deactivate("M0002")
        self.assertEqual([m.member_id for m in members.active()], ["M0001", "M0003"])
        with self.assertRaises(NotFound):
            members.get("M0099")


if __name__ == "__main__":
    unittest.main()
