"""Shared fixtures for the shelf tests."""
from datetime import date, timedelta

from shelf.catalog import Catalog
from shelf.loans import LoanDesk
from shelf.members import MemberRegistry
from shelf.models import Book

MONDAY = date(2024, 3, 4)

HOBBIT = "9780547928227"
FELLOWSHIP = "9780261103573"
DUNE = "9780441172719"
ORWELL = "9780451524935"
KR = "9780131103627"
GOF = "9780201633610"

BOOKS = [
    (HOBBIT, "The Hobbit", "J.R.R. Tolkien", 1937, 2, ["fantasy", "classic"]),
    (FELLOWSHIP, "The Fellowship of the Ring", "J.R.R. Tolkien", 1954, 1, ["fantasy"]),
    (DUNE, "Dune", "Frank Herbert", 1965, 3, ["scifi"]),
    (ORWELL, "Nineteen Eighty-Four", "George Orwell", 1949, 1, ["classic", "dystopia"]),
    (KR, "The C Programming Language", "Kernighan, Brian", 1978, 1, ["computing"]),
    (GOF, "Design Patterns", "Gamma, Erich", 1994, 2, ["computing"]),
]


class Clock:
    """A settable clock for LoanDesk; starts on a Monday."""

    def __init__(self, today=MONDAY):
        self.today = today

    def __call__(self):
        return self.today

    def advance(self, days):
        self.today += timedelta(days=days)


def library(clock=None):
    """(catalog, members, desk, clock): six books, members M0001 standard, M0002 student, M0003 staff."""
    catalog = Catalog()
    for isbn, title, author, year, copies, tags in BOOKS:
        catalog.add(Book(isbn, title, author, year, copies, list(tags)))
    members = MemberRegistry()
    members.register("Ada Lovelace", "ada@example.org")
    members.register("Ben Okri", "ben@example.org", "student")
    members.register("Cy Twombly", "cy@example.org", "staff")
    clock = clock or Clock()
    return catalog, members, LoanDesk(catalog, members, clock), clock
