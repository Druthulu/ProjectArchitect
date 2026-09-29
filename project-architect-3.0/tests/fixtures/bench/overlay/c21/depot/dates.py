"""Calendar rules: business days, promised delivery dates, billing periods and the returns window.

A business day is Monday to Friday and not in the calendar's holiday set. Orders
placed at or after the cut-off hour, or on a non-business day, start counting
from the next business day.
"""
from datetime import date, datetime, timedelta

WEEKEND = frozenset({5, 6})
CUTOFF_HOUR = 15

HOLIDAYS_2024 = frozenset({
    date(2024, 1, 1),
    date(2024, 3, 29),
    date(2024, 4, 1),
    date(2024, 5, 1),
    date(2024, 5, 9),
    date(2024, 5, 20),
    date(2024, 10, 3),
    date(2024, 12, 25),
    date(2024, 12, 26),
})

# business days from dispatch day to promised delivery
SERVICE_LEVELS = {"express": 1, "standard": 3, "economy": 6}

# a delivered line may be returned up to and including this many days after delivery
RETURNS_WINDOW_DAYS = 30


def check_service_level(level):
    """The level unchanged, or ValueError naming the known levels."""
    if level not in SERVICE_LEVELS:
        known = ", ".join(sorted(SERVICE_LEVELS))
        raise ValueError(f"unknown service level {level!r} (known: {known})")
    return level


class Calendar:
    """Business-day arithmetic over a fixed holiday set."""

    def __init__(self, holidays=HOLIDAYS_2024, cutoff_hour=CUTOFF_HOUR):
        self.holidays = frozenset(holidays)
        self.cutoff_hour = cutoff_hour

    def is_business_day(self, day):
        return day.weekday() not in WEEKEND and day not in self.holidays

    def next_business_day(self, day):
        """The first business day strictly after day."""
        day += timedelta(days=1)
        while not self.is_business_day(day):
            day += timedelta(days=1)
        return day

    def add_business_days(self, day, count):
        """The business day count business days after day (count >= 0; 0 rolls a non-business day forward)."""
        if count < 0:
            raise ValueError("count must be >= 0")
        if count == 0:
            return day if self.is_business_day(day) else self.next_business_day(day)
        for _ in range(count):
            day = self.next_business_day(day)
        return day

    def business_days_between(self, start, end):
        """Business days in the half-open range (start, end]; 0 when end <= start."""
        count, day = 0, start
        while day < end:
            day += timedelta(days=1)
            if self.is_business_day(day):
                count += 1
        return count

    def dispatch_day(self, placed):
        """The business day an order placed at `placed` (a date or datetime) is dispatched."""
        if isinstance(placed, datetime):
            day = placed.date()
            if placed.hour >= self.cutoff_hour:
                return self.next_business_day(day)
        else:
            day = placed
        return self.add_business_days(day, 0)

    def promised_date(self, placed, level="standard"):
        """The delivery date promised to the customer for an order placed at `placed`."""
        days = SERVICE_LEVELS[check_service_level(level)]
        return self.add_business_days(self.dispatch_day(placed), days)


    def days_late(self, promised, delivered):
        """Business days a delivery came after its promised date; 0 when on time or early."""
        return self.business_days_between(promised, delivered)


def parse_day(text):
    """A date from 'YYYY-MM-DD', with the offending text in the error."""
    try:
        return date.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        raise ValueError(f"not a YYYY-MM-DD date: {text!r}") from None


def month_bounds(day):
    """(first, last) day of the calendar month containing day."""
    first = day.replace(day=1)
    if first.month == 12:
        following = first.replace(year=first.year + 1, month=1)
    else:
        following = first.replace(month=first.month + 1)
    return first, following - timedelta(days=1)


def period_label(day):
    """The billing period a day belongs to, as 'YYYY-MM'."""
    return f"{day.year:04d}-{day.month:02d}"


def billing_periods(start, end):
    """The monthly billing periods (first, last) overlapping [start, end], clipped to the range."""
    if end < start:
        raise ValueError("end before start")
    periods = []
    day = start
    while day <= end:
        first, last = month_bounds(day)
        periods.append((max(first, start), min(last, end)))
        day = last + timedelta(days=1)
    return periods


def return_deadline(delivered):
    """The last day a delivery may be returned."""
    return delivered + timedelta(days=RETURNS_WINDOW_DAYS)


def within_returns_window(delivered, on):
    """True when a delivery made on `delivered` may be returned on `on` (deadline day included)."""
    return delivered <= on < return_deadline(delivered)
