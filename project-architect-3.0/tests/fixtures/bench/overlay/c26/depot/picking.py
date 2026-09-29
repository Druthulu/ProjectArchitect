"""Pick lists: where to walk and what to take for one wave of orders.

Bins are coded 'Z-AA-BB' (zone letter, aisle, bin). A pick list groups the
lines of its orders by (bin, SKU) and orders them along the walk: zone, aisle,
bin, then SKU as the tie-break, so the list never depends on the order in
which orders or lines were given.
"""
import re
from dataclasses import dataclass, field

from depot.stock import InsufficientStock

BIN_PATTERN = re.compile(r"^([A-Z])-(\d{2})-(\d{2})$")


@dataclass(frozen=True, order=True)
class Location:
    zone: str
    aisle: int
    bin: int

    @classmethod
    def parse(cls, code):
        match = BIN_PATTERN.match(code) if isinstance(code, str) else None
        if not match:
            raise ValueError(f"bad bin code {code!r} (expected e.g. 'A-03-12')")
        return cls(match.group(1), int(match.group(2)), int(match.group(3)))

    @property
    def code(self):
        return f"{self.zone}-{self.aisle:02d}-{self.bin:02d}"


def check_bin(code):
    """The bin code unchanged, or ValueError."""
    Location.parse(code)
    return code


@dataclass(frozen=True)
class PickLine:
    bin: str
    sku: str
    qty: int
    orders: tuple  # order ids served by this line, sorted


@dataclass
class PickList:
    wave: int
    order_ids: tuple
    lines: list
    allocations: dict = field(default_factory=dict)  # order_id -> [(bin, sku, qty)]

    @property
    def units(self):
        return sum(line.qty for line in self.lines)

    def bins(self):
        """Distinct bins in walk order."""
        seen = []
        for line in self.lines:
            if line.bin not in seen:
                seen.append(line.bin)
        return seen


def by_zone(pick_list):
    """{zone: [PickLine]} so each zone's picker gets their own part of the list, in walk order."""
    zones = {}
    for line in pick_list.lines:
        zones.setdefault(Location.parse(line.bin).zone, []).append(line)
    return zones


def walk_order(bins):
    """Bin codes sorted along the walk (zone, aisle, bin)."""
    return sorted(bins, key=Location.parse)


def build_pick_list(orders, inventory, wave=1, skip_bins=()):
    """The pick list for orders, drawing each line from bins in walk order.

    Bin stock is shared across the orders of the wave, so two orders never draw
    the same unit. skip_bins (e.g. the returns bin) are never picked from.
    """
    left = {}     # (bin, sku) -> units not yet allocated in this list
    grouped = {}  # (bin, sku) -> [qty, set of order ids]
    allocations = {}
    for order in orders:
        taken = allocations.setdefault(order.order_id, [])
        for sku, need in order.quantities().items():
            for code in walk_order(inventory.bins_for(sku)):
                if need == 0:
                    break
                if code in skip_bins:
                    continue
                spare = left.setdefault((code, sku), inventory.on_hand(sku, code))
                take = min(need, spare)
                if not take:
                    continue
                left[(code, sku)] = spare - take
                need -= take
                taken.append((code, sku, take))
                entry = grouped.setdefault((code, sku), [0, set()])
                entry[0] += take
                entry[1].add(order.order_id)
            if need:
                raise InsufficientStock(f"{order.order_id}: {need} x {sku} not in any bin")
    lines = [PickLine(code, sku, qty, tuple(sorted(ids))) for (code, sku), (qty, ids) in grouped.items()]
    lines.sort(key=lambda line: Location.parse(line.bin))
    ids = tuple(sorted(order.order_id for order in orders))
    return PickList(wave, ids, lines, allocations)


def waves(order_ids, size):
    """Consecutive groups of at most size order ids."""
    if size < 1:
        raise ValueError("wave size must be >= 1")
    ids = list(order_ids)
    return [tuple(ids[i:i + size]) for i in range(0, len(ids), size)]


def plan_waves(orders, inventory, size, skip_bins=()):
    """Pick lists for orders in waves of at most size, earliest promise first."""
    queue = sorted(orders, key=lambda o: (o.promised is None, o.promised or 0, o.order_id))
    by_id = {order.order_id: order for order in queue}
    return [build_pick_list([by_id[i] for i in group], inventory, number, skip_bins)
            for number, group in enumerate(waves(by_id, size), 1)]


def render(pick_list):
    """The pick list as the picker's sheet: one line per bin and SKU."""
    out = [f"Wave {pick_list.wave}: {', '.join(pick_list.order_ids)}"]
    for line in pick_list.lines:
        out.append(f"  {line.bin}  {line.sku}  x{line.qty}  ({', '.join(line.orders)})")
    out.append(f"  {pick_list.units} units from {len(pick_list.bins())} bins")
    return "\n".join(out)
