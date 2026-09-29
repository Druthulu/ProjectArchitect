"""Packing orders into parcels and quoting the carrier's price.

Weights are integer grams everywhere inside the depot; the carrier's rate card
is in kilograms, so a parcel's weight is converted to kg exactly once, where it
is priced. A parcel holds at most 20 kg; a single item heavier than that
travels alone. The price of a parcel is its rate band's base plus a per-kg rate
for every started kg, a surcharge if it carries an item over 25 kg, and the
service level's factor. A shipment costs the sum of its parcels.
"""
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from depot.dates import check_service_level
from depot.money import Money, total

MAX_PARCEL_G = 20_000
HEAVY_ITEM_G = 25_000
HEAVY_SURCHARGE = Money(1500)

# (up to kg inclusive or None for no limit, base, per started kg)
RATE_BANDS = (
    (Decimal(2), Money(450), Money(40)),
    (Decimal(5), Money(520), Money(45)),
    (Decimal(10), Money(640), Money(50)),
    (Decimal(20), Money(900), Money(55)),
    (None, Money(1800), Money(75)),
)

LEVEL_FACTOR_PCT = {"express": 150, "standard": 100, "economy": 85}


def grams_to_kg(grams):
    """Exact kilograms (a Decimal) from integer grams."""
    return Decimal(grams) / 1000


def started_kg(kg):
    """Whole kilograms charged for kg: every started kg counts, and at least one."""
    return max(1, int(kg.to_integral_value(rounding=ROUND_CEILING)))


def rate_band(kg):
    """The (limit, base, per_kg) band a weight in kg falls into."""
    for band in RATE_BANDS:
        if band[0] is None or kg <= band[0]:
            return band
    raise AssertionError("RATE_BANDS must end with an open band")


def price_for_kg(kg, heavy=False, level="standard"):
    """The carrier price of one parcel weighing kg kilograms."""
    if kg <= 0:
        raise ValueError(f"parcel weight must be positive, got {kg} kg")
    _, base, per_kg = rate_band(kg)
    price = base + per_kg * started_kg(kg)
    if heavy:
        price = price + HEAVY_SURCHARGE
    return price.percent(LEVEL_FACTOR_PCT[check_service_level(level)])


@dataclass
class Parcel:
    items: list = field(default_factory=list)  # (sku, weight_g) per unit

    @property
    def weight_g(self):
        return sum(w for _, w in self.items)

    @property
    def heaviest_g(self):
        return max((w for _, w in self.items), default=0)

    @property
    def has_heavy_item(self):
        return self.heaviest_g > HEAVY_ITEM_G

    def skus(self):
        """{sku: units} in this parcel."""
        counts = {}
        for sku, _ in self.items:
            counts[sku] = counts.get(sku, 0) + 1
        return counts


def pack(items, limit_g=MAX_PARCEL_G):
    """Parcels for items ((sku, weight_g) per unit), first-fit by decreasing weight.

    An item heavier than limit_g gets a parcel of its own. The result depends
    only on the items, not their order.
    """
    parcels = []
    for sku, weight in sorted(items, key=lambda item: (-item[1], item[0])):
        if weight <= 0:
            raise ValueError(f"{sku}: item weight must be positive")
        if weight > limit_g:
            parcels.append(Parcel([(sku, weight)]))
            continue
        for parcel in parcels:
            if parcel.heaviest_g <= limit_g and parcel.weight_g + weight <= limit_g:
                parcel.items.append((sku, weight))
                break
        else:
            parcels.append(Parcel([(sku, weight)]))
    return parcels


def quote_parcel(parcel, level="standard"):
    """The price of one parcel."""
    return price_for_kg(grams_to_kg(parcel.weight_g), parcel.has_heavy_item, level)


@dataclass(frozen=True)
class ShipmentQuote:
    parcels: tuple
    prices: tuple  # Money per parcel, same order
    total: Money
    level: str

    @property
    def weight_g(self):
        return sum(p.weight_g for p in self.parcels)

    @property
    def parcel_count(self):
        return len(self.parcels)


def quote_shipment(parcels, level="standard"):
    """The quote for a whole shipment: each parcel priced on its own, total = their sum."""
    if not parcels:
        raise ValueError("a shipment needs at least one parcel")
    if len(parcels) == 1:
        prices = [quote_parcel(parcels[0], level)]
    else:
        prices = [price_for_kg(Decimal(p.weight_g), p.has_heavy_item, level) for p in parcels]
    return ShipmentQuote(tuple(parcels), tuple(prices), total(prices), level)


def compare_levels(parcels):
    """{level: total price} for every service level, cheapest first."""
    quotes = {level: quote_shipment(parcels, level).total for level in LEVEL_FACTOR_PCT}
    return dict(sorted(quotes.items(), key=lambda item: (item[1].cents, item[0])))


def packing_slip(quote):
    """Per parcel: its number and the units of each SKU it holds, for the packing bench."""
    out = []
    for number, parcel in enumerate(quote.parcels, 1):
        contents = ", ".join(f"{sku} x{units}" for sku, units in sorted(parcel.skus().items()))
        heavy = "  HEAVY" if parcel.has_heavy_item else ""
        out.append(f"Parcel {number}: {contents} ({parcel.weight_g} g){heavy}")
    return out


def label(quote, order_id):
    """Printable parcel labels, one line per parcel ('1/2', '2/2', ...)."""
    count = quote.parcel_count
    return [f"{order_id} {n}/{count} {grams_to_kg(p.weight_g)} kg {price}"
            for n, (p, price) in enumerate(zip(quote.parcels, quote.prices), 1)]
