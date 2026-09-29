"""Plain-text reports over a LoanDesk."""
from collections import Counter

from .fees import format_cents, loan_fee_cents


def overdue_report(desk, today=None):
    """One line per overdue loan, oldest due first: '<due> <member_id> <isbn> <days>d <fee>'."""
    today = today or desk.today()
    lines = []
    for loan in desk.overdue(today):
        kind = desk.members.get(loan.member_id).kind
        fee = loan_fee_cents(loan, today, kind)
        lines.append(f"{loan.due.isoformat()} {loan.member_id} {loan.isbn} "
                     f"{loan.days_late(today)}d {format_cents(fee)}")
    return lines


def popular(desk, n=10):
    """Top n (isbn, loan count) pairs over all loans ever; ties by ISBN."""
    counts = Counter(loan.isbn for loan in desk.loans)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def member_loans(desk, member_id):
    """Titles a member has borrowed, oldest first, ' (open)' after current loans."""
    out = []
    for loan in sorted(desk.history(member_id), key=lambda loan: (loan.start, loan.loan_id)):
        title = desk.catalog.get(loan.isbn).title
        out.append(title + (" (open)" if loan.is_open else ""))
    return out


def popular_titles(desk, n=10):
    """Old name used by the 0.2 web page; titles only. Prefer popular()."""
    return [desk.catalog.get(isbn).title for isbn, _ in popular(desk, n)]
