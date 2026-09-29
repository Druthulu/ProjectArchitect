"""Lending policy: every tunable number lives here."""

KINDS = ("standard", "student", "staff")

LOAN_DAYS = {"standard": 21, "student": 14, "staff": 42}
MAX_LOANS = {"standard": 5, "student": 3, "staff": 10}
DAILY_FEE_CENTS = {"standard": 25, "student": 10, "staff": 0}

MAX_RENEWALS = 2
GRACE_DAYS = 1
FEE_CAP_CENTS = 1000


def check_kind(kind):
    """Return kind unchanged, or raise ValueError for an unknown member kind."""
    if kind not in KINDS:
        raise ValueError(f"unknown member kind: {kind!r}")
    return kind


def loan_days(kind):
    return LOAN_DAYS[check_kind(kind)]


def max_loans(kind):
    return MAX_LOANS[check_kind(kind)]


def daily_fee(kind):
    return DAILY_FEE_CENTS[check_kind(kind)]


def loan_period(kind):
    """Deprecated alias of loan_days kept for 0.2 callers; silently defaults to 21 days."""
    return LOAN_DAYS.get(kind, 21)
