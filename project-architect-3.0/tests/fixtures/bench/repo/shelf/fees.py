"""Late fees. Amounts are integer cents throughout."""
from . import policy


def late_fee_cents(days_late, kind="standard"):
    """Fee for a return days_late days after due: grace days free, then a daily rate, capped."""
    billable = days_late - policy.GRACE_DAYS
    if billable <= 0:
        return 0
    return min(billable * policy.daily_fee(kind), policy.FEE_CAP_CENTS)


def loan_fee_cents(loan, today, kind="standard"):
    """Fee a loan has accrued as of today (or as of its return)."""
    return late_fee_cents(loan.days_late(today), kind)


def format_cents(cents):
    """1234 -> '$12.34'."""
    return f"${cents // 100}.{cents % 100:02d}"


def flat_fee(days):
    """DEPRECATED since 0.3: flat 50c per day, no grace, no cap. No caller left."""
    return max(0, days) * 50
