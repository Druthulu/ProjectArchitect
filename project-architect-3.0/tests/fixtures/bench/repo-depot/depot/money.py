"""Money as integer cents in one currency, and the HALF_UP rounding the depot uses everywhere.

Amounts never touch floats: they are built from ints, decimal strings or Decimals,
and every fractional result is rounded to a whole cent with ROUND_HALF_UP
(halves away from zero), the rule the finance team signs off on.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from depot import DepotError

DEFAULT_CURRENCY = "EUR"
_UNIT = Decimal(1)


class CurrencyMismatch(DepotError):
    """Arithmetic or comparison between amounts in different currencies."""


def to_decimal(value):
    """An exact Decimal from an int, a str or a Decimal; floats and bools are refused."""
    if isinstance(value, (bool, float)):
        raise TypeError(f"use an int, str or Decimal, not {type(value).__name__}")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"not a number: {value!r}") from None


def round_half_up(value):
    """Round to the nearest integer, halves away from zero: 2.5 -> 3, -2.5 -> -3, 2.49 -> 2."""
    return int(to_decimal(value).quantize(_UNIT, rounding=ROUND_HALF_UP))


def percent_of(cents, pct):
    """pct percent of an integer number of cents, rounded HALF_UP to a whole cent."""
    return round_half_up(Decimal(cents) * to_decimal(pct) / 100)


def parse_amount(text):
    """Cents from a decimal string such as '12', '12.5', '12.50' or '-0.99' (or an int or Decimal).

    More than two decimal places is an error rather than a silent rounding.
    """
    amount = to_decimal(text.strip() if isinstance(text, str) else text)
    if not amount.is_finite():
        raise ValueError(f"not a finite amount: {text!r}")
    if amount.as_tuple().exponent < -2:
        raise ValueError(f"more than two decimal places: {text!r}")
    return int(amount * 100)


@dataclass(frozen=True)
class Money:
    """An immutable amount: integer cents plus an ISO currency code."""

    cents: int
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self):
        if isinstance(self.cents, bool) or not isinstance(self.cents, int):
            raise TypeError(f"Money cents must be an int, got {type(self.cents).__name__}")
        code = self.currency
        if len(code) != 3 or not code.isascii() or not code.isalpha() or not code.isupper():
            raise ValueError(f"bad currency code: {code!r}")

    @classmethod
    def of(cls, amount, currency=DEFAULT_CURRENCY):
        """Money from whole currency units given as a decimal string, int or Decimal ('12.34')."""
        return cls(parse_amount(amount), currency)

    @classmethod
    def zero(cls, currency=DEFAULT_CURRENCY):
        return cls(0, currency)

    def _same(self, other):
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")
        return other.cents

    def __add__(self, other):
        return Money(self.cents + self._same(other), self.currency)

    def __sub__(self, other):
        return Money(self.cents - self._same(other), self.currency)

    def __neg__(self):
        return Money(-self.cents, self.currency)

    def __mul__(self, qty):
        """Multiply by a whole quantity; fractional factors go through percent()."""
        if isinstance(qty, bool) or not isinstance(qty, int):
            return NotImplemented
        return Money(self.cents * qty, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other):
        return self.cents < self._same(other)

    def __le__(self, other):
        return self.cents <= self._same(other)

    def __gt__(self, other):
        return self.cents > self._same(other)

    def __ge__(self, other):
        return self.cents >= self._same(other)

    def __bool__(self):
        return self.cents != 0

    def percent(self, pct):
        """pct percent of this amount, rounded HALF_UP to the cent (pct may be '7.5' or Decimal)."""
        return Money(percent_of(self.cents, pct), self.currency)

    def less_percent(self, pct):
        """This amount reduced by pct percent, the result rounded HALF_UP once."""
        return self.percent(100 - to_decimal(pct))

    def allocate(self, weights):
        """Split into parts proportional to integer weights, losing no cent.

        Each part is rounded down; the leftover cents go one each to the parts
        with the largest remainders (earlier parts first on ties).
        """
        weights = list(weights)
        if not weights or any(isinstance(w, bool) or not isinstance(w, int) or w < 0 for w in weights):
            raise ValueError("weights must be non-negative ints")
        whole = sum(weights)
        if whole == 0:
            raise ValueError("weights must not all be zero")
        sign, cents = (-1 if self.cents < 0 else 1), abs(self.cents)
        shares = [divmod(cents * w, whole) for w in weights]
        leftover = cents - sum(q for q, _ in shares)
        order = sorted(range(len(shares)), key=lambda i: (-shares[i][1], i))
        parts = [q for q, _ in shares]
        for i in order[:leftover]:
            parts[i] += 1
        return [Money(sign * p, self.currency) for p in parts]

    @property
    def is_negative(self):
        return self.cents < 0

    def __str__(self):
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}{whole}.{frac:02d} {self.currency}"


def total(amounts, currency=DEFAULT_CURRENCY):
    """Sum of Money values; the empty sum is zero in the given currency."""
    result = Money.zero(currency)
    for amount in amounts:
        result = result + amount
    return result
