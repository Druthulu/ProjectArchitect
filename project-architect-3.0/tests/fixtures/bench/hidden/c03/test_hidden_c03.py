import unittest

from helpers import DUNE, FELLOWSHIP, HOBBIT, KR, library
from shelf.models import Book


class HiddenC03(unittest.TestCase):
    def setUp(self):
        self.catalog = library()[0]

    def isbns(self, *args, **kw):
        return [b.isbn for b in self.catalog.search(*args, **kw)]

    def test_words_across_fields(self):
        self.assertEqual(self.isbns("tolkien hobbit"), [HOBBIT])
        self.assertEqual(self.isbns("  HOBBIT   tolkien "), [HOBBIT])
        self.assertEqual(self.isbns("tolkien ring"), [FELLOWSHIP])
        self.assertEqual(self.isbns("herbert hobbit"), [])

    def test_sorted_by_title(self):
        self.assertEqual(self.isbns("the"), [KR, FELLOWSHIP, HOBBIT])

    def test_blank(self):
        self.assertEqual(self.isbns(""), [])
        self.assertEqual(self.isbns("   "), [])

    def test_tag(self):
        self.assertEqual(self.isbns("the", tag="fantasy"), [FELLOWSHIP, HOBBIT])
        self.assertEqual(self.isbns("tolkien", tag="scifi"), [])

    def test_given_tag_always_filters(self):
        # tag="" is given, and no book carries an empty tag; tags match whole, not as substrings
        self.assertEqual(self.isbns("the", tag=""), [])
        self.assertEqual(self.isbns("tolkien", tag="fan"), [])
        self.assertEqual(self.isbns("tolkien", tag=None), [FELLOWSHIP, HOBBIT])

    def test_word_stays_inside_one_field(self):
        # "hobbitj" only occurs across the title/author boundary
        self.assertEqual(self.isbns("hobbitj"), [])
        self.assertEqual(self.isbns("hobbit j.r.r."), [HOBBIT])

    def test_ties_by_isbn(self):
        self.catalog.add(Book("9780306406157", "Dune", "Someone Else"))
        self.assertEqual(self.isbns("dune"), ["9780306406157", DUNE])


if __name__ == "__main__":
    unittest.main()
