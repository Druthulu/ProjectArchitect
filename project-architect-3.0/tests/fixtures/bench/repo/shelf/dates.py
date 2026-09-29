"""Date helpers. Dates are datetime.date; text dates are ISO 'YYYY-MM-DD'."""
from datetime import date, timedelta


def parse_date(text):
    """Parse 'YYYY-MM-DD' into a date. Raises ValueError on anything else."""
    parts = text.strip().split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad date: {text!r}")
    year, month, day = (int(p) for p in parts)
    return date(year, month, day)


def format_date(d):
    return d.isoformat()


def add_days(d, n):
    return d + timedelta(days=n)


def days_between(start, end):
    """Whole days from start to end; negative when end is earlier."""
    return (end - start).days


def is_weekend(d):
    return d.weekday() >= 5


def next_business_day(d):
    """The first weekday strictly after d."""
    d = d + timedelta(days=1)
    while is_weekend(d):
        d += timedelta(days=1)
    return d
