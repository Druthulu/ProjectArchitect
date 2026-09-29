"""Orders, their lines, and the state machine every order moves through.

DRAFT -> PLACED -> PICKED -> SHIPPED -> DELIVERED, with CANCELLED reachable only
from DRAFT or PLACED (nothing has left the shelves yet) and RETURNED only from
DELIVERED. Any other move raises InvalidTransition.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from depot import DepotError
from depot.money import Money, to_decimal, total


class OrderState(str, Enum):
    DRAFT = "draft"
    PLACED = "placed"
    PICKED = "picked"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


TRANSITIONS = {
    OrderState.DRAFT: frozenset({OrderState.PLACED, OrderState.CANCELLED}),
    OrderState.PLACED: frozenset({OrderState.PICKED, OrderState.CANCELLED}),
    OrderState.PICKED: frozenset({OrderState.SHIPPED}),
    OrderState.SHIPPED: frozenset({OrderState.DELIVERED}),
    OrderState.DELIVERED: frozenset({OrderState.RETURNED}),
    OrderState.CANCELLED: frozenset(),
    OrderState.RETURNED: frozenset(),
}


class InvalidTransition(DepotError):
    """The order cannot move from its current state to the requested one."""


class UnknownOrder(DepotError):
    """No order with that id."""


class OrderLocked(DepotError):
    """The order's lines can only change while it is a draft."""


@dataclass
class OrderLine:
    sku: str
    qty: int
    unit_price: Money
    discount_pct: Decimal = Decimal(0)

    @property
    def gross(self):
        return self.unit_price * self.qty


@dataclass
class Order:
    order_id: str
    customer: str
    service_level: str = "standard"
    lines: list = field(default_factory=list)
    state: OrderState = OrderState.DRAFT
    history: list = field(default_factory=list)   # (state, day) in the order entered
    promised: object = None                       # date promised at placing
    backorder: dict = field(default_factory=dict)  # sku -> units still to reserve
    picks: list = field(default_factory=list)      # (bin, sku, qty) allocated at picking
    shipment: object = None                        # shipping.ShipmentQuote once shipped

    def add_line(self, sku, qty, unit_price, discount_pct=0):
        """Add units of a SKU; a line with the same SKU, price and discount is merged."""
        if self.state is not OrderState.DRAFT:
            raise OrderLocked(f"{self.order_id} is {self.state.value}")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError(f"qty must be a positive int, got {qty!r}")
        discount = to_decimal(discount_pct)
        if not 0 <= discount < 100:
            raise ValueError(f"discount must be in [0, 100), got {discount}")
        for line in self.lines:
            if (line.sku, line.unit_price, line.discount_pct) == (sku, unit_price, discount):
                line.qty += qty
                return line
        line = OrderLine(sku, qty, unit_price, discount)
        self.lines.append(line)
        return line

    def remove_line(self, sku):
        """Remove every line for a SKU from a draft."""
        if self.state is not OrderState.DRAFT:
            raise OrderLocked(f"{self.order_id} is {self.state.value}")
        kept = [line for line in self.lines if line.sku != sku]
        if len(kept) == len(self.lines):
            raise ValueError(f"{self.order_id} has no line for {sku}")
        self.lines = kept

    def can_move_to(self, state):
        return state in TRANSITIONS[self.state]

    def move_to(self, state, day):
        """Enter state on day, or raise InvalidTransition."""
        if not self.can_move_to(state):
            raise InvalidTransition(f"{self.order_id}: {self.state.value} -> {state.value}")
        self.state = state
        self.history.append((state, day))

    def entered(self, state):
        """The day the order last entered state, or None."""
        for seen, day in reversed(self.history):
            if seen is state:
                return day
        return None

    def quantities(self):
        """{sku: total units ordered}, SKUs in first-line order."""
        wanted = {}
        for line in self.lines:
            wanted[line.sku] = wanted.get(line.sku, 0) + line.qty
        return wanted

    @property
    def units(self):
        return sum(line.qty for line in self.lines)

    @property
    def gross(self):
        currency = self.lines[0].unit_price.currency if self.lines else "EUR"
        return total((line.gross for line in self.lines), currency)

    @property
    def is_open(self):
        return self.state not in (OrderState.CANCELLED, OrderState.RETURNED, OrderState.DELIVERED)

    def describe(self):
        """One line per order line under a header, for support staff."""
        out = [f"{self.order_id} {self.customer} [{self.state.value}] {self.service_level}"]
        for line in self.lines:
            discount = f" -{line.discount_pct}%" if line.discount_pct else ""
            out.append(f"  {line.sku} x{line.qty} @ {line.unit_price}{discount}")
        for sku, short in sorted(self.backorder.items()):
            out.append(f"  backorder {sku} x{short}")
        return "\n".join(out)


class OrderBook:
    """Orders by id; ids are the prefix plus a zero-padded sequence number."""

    def __init__(self, prefix="SO"):
        self.prefix = prefix
        self._orders = {}
        self._next = 1

    def __len__(self):
        return len(self._orders)

    def __iter__(self):
        """Orders in id order."""
        return iter([self._orders[k] for k in sorted(self._orders)])

    def create(self, customer, service_level="standard"):
        if not customer or not customer.strip():
            raise ValueError("customer is required")
        order_id = f"{self.prefix}-{self._next:04d}"
        self._next += 1
        order = Order(order_id, customer.strip(), service_level)
        self._orders[order_id] = order
        return order

    def get(self, order_id):
        try:
            return self._orders[order_id]
        except KeyError:
            raise UnknownOrder(order_id) from None

    def in_state(self, *states):
        """Orders currently in any of states, in id order."""
        return [order for order in self if order.state in states]

    def for_customer(self, customer):
        """A customer's orders (name compared case-insensitively), in id order."""
        wanted = customer.strip().lower()
        return [order for order in self if order.customer.lower() == wanted]

    def overdue(self, today):
        """Placed, picked or shipped orders whose promised date has passed, earliest promise first."""
        moving = self.in_state(OrderState.PLACED, OrderState.PICKED, OrderState.SHIPPED)
        late = [order for order in moving if order.promised and order.promised < today]
        return sorted(late, key=lambda order: (order.promised, order.order_id))

    def entered_on(self, state, day):
        """Orders that entered state on day (whatever their state now), in id order."""
        return [o for o in self if any(s is state and d == day for s, d in o.history)]
