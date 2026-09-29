"""The Depot facade: the public API, wiring catalog, stock, orders, picking, shipping and billing.

Every date the depot records comes from its clock (a zero-argument callable
returning a date), so callers and tests control "today".
"""
from datetime import date

from depot import DepotError
from depot.billing import DEFAULT_TAX_RATE, InvoiceRegister, instalments
from depot.catalog import Catalog
from depot.dates import Calendar, check_service_level, return_deadline, within_returns_window
from depot.importer import export_counts, import_counts
from depot.money import Money
from depot.orders import InvalidTransition, OrderBook, OrderState
from depot.picking import build_pick_list, check_bin, plan_waves
from depot.reports import daily_summary, period_summary, stock_valuation
from depot.shipping import compare_levels, pack, quote_shipment
from depot.stock import Inventory

RETURNS_BIN = "R-00-01"


class ReturnRefused(DepotError):
    """The return is outside the returns window."""


class NotReady(DepotError):
    """The order cannot be picked yet (it is waiting on stock)."""


class Depot:
    def __init__(self, clock=date.today, calendar=None, tax_rate=DEFAULT_TAX_RATE,
                 low_stock_threshold=5, wave_size=4):
        self.clock = clock
        self.calendar = calendar or Calendar()
        self.tax_rate = tax_rate
        self.low_stock_threshold = low_stock_threshold
        self.wave_size = wave_size
        self.catalog = Catalog()
        self.inventory = Inventory()
        self.orders = OrderBook()
        self.invoices = InvoiceRegister()

    # products and stock

    def add_product(self, sku, name, weight_g, price, tags=None):
        """Add a product; price is Money or a decimal string in EUR ('12.50')."""
        if not isinstance(price, Money):
            price = Money.of(price)
        return self.catalog.add(sku, name, weight_g, price, tags)

    def tag_product(self, sku, tag):
        return self.catalog.tag(sku, tag)

    def product_tags(self, sku):
        return list(self.catalog.get(sku).tags)

    def receive(self, sku, bin_code, qty):
        """Book a delivery from a supplier into a bin."""
        self.catalog.get(sku)
        self.inventory.receive(sku, check_bin(bin_code), qty)

    def available(self, sku):
        return self.inventory.available(sku)

    def transfer(self, sku, from_bin, to_bin, qty):
        """Move stock between bins (e.g. restock a pick face from overflow)."""
        self.catalog.get(sku)
        self.inventory.transfer(sku, check_bin(from_bin), check_bin(to_bin), qty)

    def reprice(self, sku, price):
        """New unit price for future orders; price is Money or a decimal string in EUR."""
        return self.catalog.reprice(sku, price if isinstance(price, Money) else Money.of(price))

    def import_counts(self, text):
        """Apply a stock-count CSV ('sku,bin,qty'); returns the ImportResult."""
        return import_counts(text, self.catalog, self.inventory)

    def count_sheet(self, skus=None):
        """The CSV count sheet for skus (default: every product), ready for import_counts."""
        wanted = [p.sku for p in self.catalog] if skus is None else list(skus)
        return export_counts(self.inventory, wanted)

    # orders

    def create_order(self, customer, lines=(), service_level="standard"):
        """A draft order; lines are (sku, qty) or (sku, qty, discount_pct)."""
        order = self.orders.create(customer, check_service_level(service_level))
        for line in lines:
            self.add_line(order.order_id, *line)
        return order

    def add_line(self, order_id, sku, qty, discount_pct=0):
        """Add a line to a draft at the product's current price."""
        product = self.catalog.get(sku)
        if not product.active:
            raise ValueError(f"{sku} is no longer sold")
        return self.orders.get(order_id).add_line(sku, qty, product.price, discount_pct)

    def place(self, order_id):
        """Place a draft: reserve what stock allows, backorder the rest, promise a date."""
        order = self.orders.get(order_id)
        if not order.can_move_to(OrderState.PLACED):
            raise InvalidTransition(f"{order_id}: {order.state.value} -> placed")
        if not order.lines:
            raise ValueError(f"{order_id} has no lines")
        for sku, qty in order.quantities().items():
            got = self.inventory.reserve(order_id, sku, qty, partial=True)
            if got < qty:
                order.backorder[sku] = qty - got
        today = self.clock()
        order.promised = self.calendar.promised_date(today, order.service_level)
        order.move_to(OrderState.PLACED, today)
        return order

    def fill_backorders(self, order_id):
        """Try again to reserve a placed order's missing units; returns what is still short."""
        order = self.orders.get(order_id)
        if order.state is not OrderState.PLACED:
            raise InvalidTransition(f"{order_id} is {order.state.value}, not placed")
        for sku, short in list(order.backorder.items()):
            got = self.inventory.reserve(order_id, sku, short, partial=True)
            if got == short:
                del order.backorder[sku]
            else:
                order.backorder[sku] = short - got
        return dict(order.backorder)

    def pick(self, *order_ids):
        """One pick list for the given placed orders; they move to PICKED."""
        orders = [self.orders.get(order_id) for order_id in order_ids]
        for order in orders:
            if not order.can_move_to(OrderState.PICKED):
                raise InvalidTransition(f"{order.order_id}: {order.state.value} -> picked")
            if order.backorder:
                raise NotReady(f"{order.order_id} is waiting on stock")
        pick_list = build_pick_list(orders, self.inventory, skip_bins=(RETURNS_BIN,))
        today = self.clock()
        for order in orders:
            order.picks = pick_list.allocations[order.order_id]
            order.move_to(OrderState.PICKED, today)
        return pick_list

    def pick_waves(self):
        """Pick lists for every placed order not waiting on stock, in waves; does not move them."""
        ready = [o for o in self.orders.in_state(OrderState.PLACED) if not o.backorder]
        return plan_waves(ready, self.inventory, self.wave_size, skip_bins=(RETURNS_BIN,))

    def _items(self, order):
        items = []
        for sku, qty in order.quantities().items():
            items.extend([(sku, self.catalog.get(sku).weight_g)] * qty)
        return items

    def quote_shipping(self, order_id):
        """What shipping an order would cost now, without shipping it."""
        order = self.orders.get(order_id)
        return quote_shipment(pack(self._items(order)), order.service_level)

    def ship(self, order_id):
        """Ship a picked order: stock leaves its bins, the order is invoiced; returns the quote."""
        order = self.orders.get(order_id)
        if not order.can_move_to(OrderState.SHIPPED):
            raise InvalidTransition(f"{order_id}: {order.state.value} -> shipped")
        quote = self.quote_shipping(order_id)
        self.inventory.consume(order_id, order.picks)
        today = self.clock()
        order.shipment = quote
        order.move_to(OrderState.SHIPPED, today)
        self.invoices.issue(order, today, self.tax_rate)
        return quote

    def deliver(self, order_id):
        order = self.orders.get(order_id)
        order.move_to(OrderState.DELIVERED, self.clock())
        return order

    def cancel(self, order_id):
        """Cancel a draft or placed order and release its reservations."""
        order = self.orders.get(order_id)
        order.move_to(OrderState.CANCELLED, self.clock())
        self.inventory.release(order_id)
        order.backorder.clear()
        return order

    def return_order(self, order_id):
        """Take a delivered order back within the returns window; returns the credit note."""
        order = self.orders.get(order_id)
        if not order.can_move_to(OrderState.RETURNED):
            raise InvalidTransition(f"{order_id}: {order.state.value} -> returned")
        delivered, today = order.entered(OrderState.DELIVERED), self.clock()
        if not within_returns_window(delivered, today):
            raise ReturnRefused(f"{order_id}: returns closed on {return_deadline(delivered).isoformat()}")
        for sku, qty in order.quantities().items():
            self.inventory.receive(sku, RETURNS_BIN, qty, reason=f"return {order_id}")
        note = self.invoices.credit(order_id, today)
        order.move_to(OrderState.RETURNED, today)
        return note

    def order_status(self, order_id):
        """What the customer portal shows for an order."""
        order = self.orders.get(order_id)
        invoice = self.invoices.for_order(order_id)
        delivered = order.entered(OrderState.DELIVERED)
        status = {
            "order": order.order_id,
            "state": order.state.value,
            "promised": order.promised,
            "backorder": dict(order.backorder),
            "parcels": order.shipment.parcel_count if order.shipment else 0,
            "invoice": invoice.number if invoice else None,
            "returnable_until": None,
        }
        if order.state is OrderState.DELIVERED and delivered:
            status["returnable_until"] = return_deadline(delivered)
        return status

    def invoice(self, order_id):
        """The order's invoice, or None before it ships."""
        self.orders.get(order_id)
        return self.invoices.for_order(order_id)

    def payment_plan(self, order_id, count, first_due=None):
        """Split an order's invoice into count instalments, the first due on first_due (default today)."""
        invoice = self.invoice(order_id)
        if invoice is None:
            raise ValueError(f"{order_id} has not been invoiced")
        return instalments(invoice, count, first_due or self.clock(), self.calendar)

    def customer_statement(self, customer, first, last):
        """(number, day, gross, running balance) for a customer's documents in [first, last]."""
        return self.invoices.statement(customer, first, last)

    def overdue_orders(self):
        """Orders still moving past their promised date, as of today."""
        return self.orders.overdue(self.clock())

    # reporting

    def shipping_options(self, order_id):
        """{service level: price} for an order, cheapest first."""
        return compare_levels(pack(self._items(self.orders.get(order_id))))

    def late_deliveries(self):
        """(order_id, business days late) for delivered orders that missed their promised date."""
        late = []
        for order in self.orders:
            delivered = order.entered(OrderState.DELIVERED)
            if delivered and order.promised:
                days = self.calendar.days_late(order.promised, delivered)
                if days:
                    late.append((order.order_id, days))
        return late

    # reporting

    def daily_report(self, day=None):
        return daily_summary(day or self.clock(), self.orders, self.inventory, self.catalog,
                             self.invoices, self.low_stock_threshold)

    def period_report(self, first, last):
        """Invoice totals per billing month between first and last."""
        return period_summary(self.invoices, first, last)

    def stock_value(self):
        """(rows, total) valuing on-hand stock at current prices."""
        return stock_valuation(self.inventory, self.catalog)
