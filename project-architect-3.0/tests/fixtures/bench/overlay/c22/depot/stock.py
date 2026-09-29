"""Stock on hand per (sku, bin), reservations per order, and memoised availability.

available(sku) = on hand in every bin - reserved by every order. It is read far
more often than stock moves, so it is cached per SKU; every mutator drops the
cache entry of each SKU it touches.
"""
from dataclasses import dataclass

from depot import DepotError


class InsufficientStock(DepotError):
    """Not enough stock available (or in a bin) for the request."""


@dataclass(frozen=True)
class Movement:
    """One change to on-hand stock, kept for the audit trail."""

    sku: str
    bin: str
    delta: int
    reason: str


def _check_qty(qty, allow_zero=False):
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise TypeError(f"quantity must be an int, got {type(qty).__name__}")
    if qty < 0 or (qty == 0 and not allow_zero):
        raise ValueError(f"quantity must be {'>= 0' if allow_zero else '> 0'}, got {qty}")
    return qty


class Inventory:
    """On-hand stock by bin and reservations by order."""

    def __init__(self):
        self._on_hand = {}   # (sku, bin) -> qty > 0
        self._reserved = {}  # order_id -> {sku: qty > 0}
        self._cache = {}     # sku -> available
        self.movements = []

    def _invalidate(self, sku):
        self._cache.pop(sku, None)

    def _move(self, sku, bin_code, delta, reason):
        key = (sku, bin_code)
        new = self._on_hand.get(key, 0) + delta
        if new < 0:
            raise InsufficientStock(f"{sku} in {bin_code}: {new - delta} on hand, {-delta} requested")
        if new:
            self._on_hand[key] = new
        else:
            self._on_hand.pop(key, None)
        self.movements.append(Movement(sku, bin_code, delta, reason))
        self._invalidate(sku)

    # on-hand stock

    def receive(self, sku, bin_code, qty, reason="receipt"):
        """Put qty units of sku into a bin."""
        self._move(sku, bin_code, _check_qty(qty), reason)

    def adjust(self, sku, bin_code, delta, reason="adjustment"):
        """Add (or, negative, remove) units in a bin; the bin cannot go below zero."""
        if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
            raise ValueError(f"delta must be a non-zero int, got {delta!r}")
        self._move(sku, bin_code, delta, reason)

    def set_count(self, sku, bin_code, qty, reason="count"):
        """Set the counted quantity of sku in a bin; returns the delta applied."""
        delta = _check_qty(qty, allow_zero=True) - self.on_hand(sku, bin_code)
        if delta:
            self._move(sku, bin_code, delta, reason)
        else:
            self._invalidate(sku)
        return delta

    def transfer(self, sku, from_bin, to_bin, qty):
        """Move qty units of sku between bins; reservations are per order, not per bin, so they stay."""
        _check_qty(qty)
        if from_bin == to_bin:
            raise ValueError("transfer needs two different bins")
        if self.on_hand(sku, from_bin) < qty:
            raise InsufficientStock(f"{sku} in {from_bin}: {self.on_hand(sku, from_bin)} on hand, {qty} requested")
        self._move(sku, from_bin, -qty, f"transfer to {to_bin}")
        self._move(sku, to_bin, qty, f"transfer from {from_bin}")

    def history(self, sku, bin_code=None):
        """Movements of sku (optionally in one bin), oldest first."""
        return [m for m in self.movements if m.sku == sku and bin_code in (None, m.bin)]

    def on_hand(self, sku, bin_code=None):
        """Units of sku in one bin, or across all bins when bin_code is None."""
        if bin_code is not None:
            return self._on_hand.get((sku, bin_code), 0)
        return sum(q for (s, _), q in self._on_hand.items() if s == sku)

    def bins_for(self, sku):
        """{bin: qty} for every bin holding sku."""
        return {b: q for (s, b), q in self._on_hand.items() if s == sku}

    def contents(self, bin_code):
        """{sku: qty} held in one bin, SKUs sorted."""
        return {s: q for (s, b), q in sorted(self._on_hand.items()) if b == bin_code}

    def skus(self):
        """Every SKU with stock on hand or reserved, sorted."""
        held = {s for s, _ in self._on_hand}
        for lines in self._reserved.values():
            held.update(lines)
        return sorted(held)

    # reservations

    def reserved(self, sku):
        """Units of sku reserved by all orders."""
        return sum(lines.get(sku, 0) for lines in self._reserved.values())

    def available(self, sku):
        """On hand minus reserved, memoised per SKU."""
        if sku not in self._cache:
            self._cache[sku] = self.on_hand(sku) - self.reserved(sku)
        return self._cache[sku]

    def reserve(self, order_id, sku, qty, partial=False):
        """Reserve qty units for an order; returns the units reserved.

        With partial=False a shortfall raises InsufficientStock and reserves nothing;
        with partial=True as many units as are available are reserved.
        """
        _check_qty(qty)
        free = self.available(sku)
        if qty > free and not partial:
            raise InsufficientStock(f"{sku}: {free} available, {qty} requested")
        take = min(qty, max(free, 0))
        if take:
            lines = self._reserved.setdefault(order_id, {})
            lines[sku] = lines.get(sku, 0) + take
            self._invalidate(sku)
        return take

    def holders(self, sku):
        """{order_id: qty} of orders holding a reservation on sku, ids sorted."""
        return {oid: lines[sku] for oid, lines in sorted(self._reserved.items()) if sku in lines}

    def reservations(self, order_id):
        """{sku: qty} reserved for an order (a copy)."""
        return dict(self._reserved.get(order_id, {}))

    def release(self, order_id):
        """Drop every reservation an order holds; returns {sku: qty} released."""
        released = self._reserved.pop(order_id, {})
        return released

    def consume(self, order_id, picks, reason="shipment"):
        """Take picked units out of their bins against the order's reservation.

        picks: (bin, sku, qty) triples. Nothing moves unless every pick is covered by
        both the reservation and the bin.
        """
        held = self.reservations(order_id)
        wanted = {}
        for bin_code, sku, qty in picks:
            _check_qty(qty)
            wanted[sku] = wanted.get(sku, 0) + qty
            if self.on_hand(sku, bin_code) < qty:
                raise InsufficientStock(f"{sku} in {bin_code}: short for {order_id}")
        for sku, qty in wanted.items():
            if held.get(sku, 0) < qty:
                raise InsufficientStock(f"{order_id} reserved {held.get(sku, 0)} of {sku}, picked {qty}")
        for bin_code, sku, qty in picks:
            self._move(sku, bin_code, -qty, f"{reason} {order_id}")
        lines = self._reserved.get(order_id, {})
        for sku, qty in wanted.items():
            lines[sku] -= qty
            if not lines[sku]:
                del lines[sku]
        if not lines:
            self._reserved.pop(order_id, None)

    def low_stock(self, threshold, skus):
        """(sku, available) for each of skus with available <= threshold, in SKU order."""
        return [(sku, self.available(sku)) for sku in sorted(skus) if self.available(sku) <= threshold]
