"""The loan desk: checkout, checkin, renewals, holds."""
from datetime import date

from . import fees, policy
from .dates import add_days
from .holds import HoldQueue
from .models import InactiveMember, LimitReached, Loan, NotFound, Unavailable


class LoanDesk:
    def __init__(self, catalog, members, clock=None):
        self.catalog = catalog
        self.members = members
        self.clock = clock or date.today
        self.loans = []
        self.holds = HoldQueue()
        self._next_id = 1

    def today(self):
        return self.clock()

    def available(self, isbn):
        """Free copies of the book: copies minus open loans."""
        book = self.catalog.get(isbn)
        out = sum(1 for loan in self.loans if loan.isbn == book.isbn and loan.is_open)
        return book.copies - out

    def open_loans(self, member_id=None):
        return [loan for loan in self.loans
                if loan.is_open and (member_id is None or loan.member_id == member_id)]

    def checkout(self, isbn, member_id):
        member = self.members.get(member_id)
        if not member.active:
            raise InactiveMember(member_id)
        limit = policy.max_loans(member.kind)
        if len(self.open_loans(member_id)) >= limit:
            raise LimitReached(f"{member_id} already has {limit} loans")
        book = self.catalog.get(isbn)
        if self.available(book.isbn) <= 0:
            raise Unavailable(book.isbn)
        today = self.today()
        due = add_days(today, policy.loan_days(member.kind))
        loan = Loan(self._next_id, book.isbn, member_id, today, due)
        self._next_id += 1
        self.loans.append(loan)
        return loan

    def checkin(self, loan_id):
        """Close the loan today; returns the late fee in cents."""
        loan = self._loan(loan_id)
        if not loan.is_open:
            raise ValueError(f"loan {loan_id} already returned")
        loan.returned = self.today()
        kind = self.members.get(loan.member_id).kind
        return fees.loan_fee_cents(loan, loan.returned, kind)

    def renew(self, loan_id):
        """Extend an open, not-overdue loan by one loan period."""
        loan = self._loan(loan_id)
        if not loan.is_open:
            raise ValueError(f"loan {loan_id} already returned")
        if loan.is_overdue(self.today()):
            raise LimitReached(f"loan {loan_id} is overdue")
        if loan.renewals >= policy.MAX_RENEWALS:
            raise LimitReached(f"loan {loan_id} renewed {loan.renewals} times")
        kind = self.members.get(loan.member_id).kind
        loan.due = add_days(loan.due, policy.loan_days(kind))
        loan.renewals += 1
        return loan

    def overdue(self, today=None):
        today = today or self.today()
        return sorted((loan for loan in self.loans if loan.is_overdue(today)),
                      key=lambda loan: (loan.due, loan.loan_id))

    def history(self, member_id):
        return [loan for loan in self.loans if loan.member_id == member_id]

    def place_hold(self, isbn, member_id):
        book = self.catalog.get(isbn)
        self.members.get(member_id)
        return self.holds.place(book.isbn, member_id, self.today())

    def _loan(self, loan_id):
        for loan in self.loans:
            if loan.loan_id == loan_id:
                return loan
        raise NotFound(f"no loan {loan_id}")
