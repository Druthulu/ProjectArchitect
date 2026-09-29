import unittest

from helpers import DUNE, FELLOWSHIP, HOBBIT, KR, ORWELL, library
from shelf import reports
from shelf.catalog import author_key
from shelf.models import Book

SILM = "9780140449136"


class HiddenC16(unittest.TestCase):
    def setUp(self):
        self.catalog, self.members, self.desk, self.clock = library()
        self.catalog.add(Book(SILM, "The Silmarillion", "Tolkien, J.R.R.", 1977, 3))

    def test_author_key(self):
        for name in ("Kernighan, Brian", "brian  kernighan", "Brian Kernighan", " Kernighan ,Brian "):
            self.assertEqual(author_key(name), "brian kernighan", name)
        self.assertEqual(author_key("J.R.R. Tolkien"), "jrr tolkien")
        self.assertEqual(author_key(" Tolkien,  J.R.R. "), "jrr tolkien")
        self.assertEqual(author_key("Le Guin, Ursula K."), "ursula k le guin")

    def test_by_author(self):
        self.assertEqual([b.isbn for b in self.catalog.by_author("jrr tolkien")], [HOBBIT, FELLOWSHIP, SILM])
        self.assertEqual([b.isbn for b in self.catalog.by_author("Brian Kernighan")], [KR])
        self.assertEqual(self.catalog.by_author("Nobody"), [])

    def test_ranking(self):
        for isbn, member in ((DUNE, "M0001"), (DUNE, "M0002"), (HOBBIT, "M0003"), (SILM, "M0001"),
                             (FELLOWSHIP, "M0002"), (KR, "M0003"), (ORWELL, "M0001")):
            self.desk.checkout(isbn, member)
        self.assertEqual(reports.author_ranking(self.desk), [
            ("Tolkien, J.R.R.", 3), ("Frank Herbert", 2), ("George Orwell", 1), ("Kernighan, Brian", 1)])
        self.assertEqual(reports.author_ranking(self.desk, 2), [("Tolkien, J.R.R.", 3), ("Frank Herbert", 2)])

    def test_first_comma_only(self):
        self.assertEqual(author_key("Tolkien, J.R.R., Jr."), "jrr, jr tolkien")
        self.assertEqual(author_key("J. R. R. Tolkien"), "j r r tolkien")

    def test_display_name_from_whole_catalog(self):
        # the Silmarillion has the smallest Tolkien ISBN; it is never borrowed here
        for isbn, member in ((HOBBIT, "M0001"), (FELLOWSHIP, "M0002"), (HOBBIT, "M0003")):
            self.desk.checkout(isbn, member)
        self.assertEqual(reports.author_ranking(self.desk), [("Tolkien, J.R.R.", 3)])

    def test_ranking_empty(self):
        self.assertEqual(reports.author_ranking(self.desk), [])


if __name__ == "__main__":
    unittest.main()
