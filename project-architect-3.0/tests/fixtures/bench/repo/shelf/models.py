"""Domain records and the errors shelf raises."""
from dataclasses import dataclass, field
from datetime import date


class ShelfError(Exception):
    """Base class for every error shelf raises on purpose."""


class NotFound(ShelfError):
    """No book, member or loan with that key."""


class Unavailable(ShelfError):
    """No free copy of the book."""


class LimitReached(ShelfError):
    """The member is at a policy limit (loans or renewals)."""


class InactiveMember(ShelfError):
    """The member has been deactivated."""


@dataclass
class Book:
    isbn: str
    title: str
    author: str
    year: int = 0
    copies: int = 1
    tags: list = field(default_factory=list)


@dataclass
class Member:
    member_id: str
    name: str
    email: str = ""
    kind: str = "standard"
    active: bool = True


@dataclass
class Loan:
    loan_id: int
    isbn: str
    member_id: str
    start: date
    due: date
    returned: date | None = None
    renewals: int = 0

    @property
    def is_open(self):
        return self.returned is None

    def is_overdue(self, today):
        return self.is_open and today > self.due

    def days_late(self, today):
        """Days past due at return (or at today while still open); never negative."""
        end = self.returned or today
        return max(0, (end - self.due).days)
