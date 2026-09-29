"""The daily summary the warehouse manager reads each evening."""
from dataclasses import dataclass
from decimal import Decimal

from depot.dates import billing_periods, period_label
from depot.money import total
from depot.orders import OrderState
from depot.shipping import grams_to_kg


@dataclass(frozen=True)
class DailySummary:
    day: object
    orders_by_state: dict     # state value -> count, every state listed
    shipped_orders: tuple     # order ids shipped that day
    shipped_weight_kg: Decimal
    revenue: object           # Money: invoices less credit notes issued that day
    backorders: tuple         # (order_id, sku, units short)
    low_stock: tuple          # (sku, available)


def orders_by_state(orders):
    """{state value: count} over every state, in state-machine order."""
    counts = {state.value: 0 for state in OrderState}
    for order in orders:
        counts[order.state.value] += 1
    return counts


def backorders(orders):
    """(order_id, sku, units short) for open orders still waiting on stock."""
    rows = []
    for order in orders:
        if order.is_open:
            rows.extend((order.order_id, sku, short) for sku, short in sorted(order.backorder.items()))
    return rows


def shipped_weight_g(orders):
    """Total parcel weight in grams of orders that have a shipment."""
    return sum(order.shipment.weight_g for order in orders if order.shipment is not None)


def daily_summary(day, order_book, inventory, catalog, invoices, threshold):
    """The summary for day; low stock covers active products at or under threshold."""
    shipped = order_book.entered_on(OrderState.SHIPPED, day)
    active = [product.sku for product in catalog if product.active]
    return DailySummary(
        day=day,
        orders_by_state=orders_by_state(order_book),
        shipped_orders=tuple(order.order_id for order in shipped),
        shipped_weight_kg=grams_to_kg(shipped_weight_g(shipped)),
        revenue=invoices.net_revenue(day, day),
        backorders=tuple(backorders(order_book)),
        low_stock=tuple(inventory.low_stock(threshold, active)),
    )


@dataclass(frozen=True)
class PeriodLine:
    label: str          # billing period 'YYYY-MM'
    first: object
    last: object
    invoices: int       # invoices issued (credit notes not counted)
    credit_notes: int
    net: object         # Money: invoice nets less credit notes
    tax: object         # Money: invoice tax less credited tax


def period_summary(invoices, first, last, currency="EUR"):
    """One PeriodLine per billing month overlapping [first, last]."""
    out = []
    for start, end in billing_periods(first, last):
        docs = [d for d in invoices if start <= d.issued_on <= end]
        out.append(PeriodLine(
            label=period_label(start), first=start, last=end,
            invoices=sum(1 for d in docs if not d.is_credit_note),
            credit_notes=sum(1 for d in docs if d.is_credit_note),
            net=total((d.net for d in docs), currency),
            tax=total((d.tax for d in docs), currency),
        ))
    return out


def stock_valuation(inventory, catalog):
    """(sku, units on hand, value at current price) per product with stock, and the grand total."""
    rows = []
    for product in catalog:
        units = inventory.on_hand(product.sku)
        if units:
            rows.append((product.sku, units, product.price * units))
    currency = rows[0][2].currency if rows else "EUR"
    return rows, total((value for _, _, value in rows), currency)


def render_periods(lines):
    """Period lines as a plain-text table, with a total row."""
    out = [f"{'period':<8} {'inv':>4} {'crn':>4} {'net':>16} {'tax':>16}"]
    for line in lines:
        out.append(f"{line.label:<8} {line.invoices:>4} {line.credit_notes:>4} {str(line.net):>16} {str(line.tax):>16}")
    if lines:
        currency = lines[0].net.currency
        net = total((line.net for line in lines), currency)
        tax = total((line.tax for line in lines), currency)
        count = sum(line.invoices for line in lines)
        credits = sum(line.credit_notes for line in lines)
        out.append(f"{'total':<8} {count:>4} {credits:>4} {str(net):>16} {str(tax):>16}")
    return "\n".join(out)


def render_valuation(rows, grand_total):
    """stock_valuation's result as plain text, largest value first (SKU breaks ties)."""
    out = []
    for sku, units, value in sorted(rows, key=lambda row: (-row[2].cents, row[0])):
        out.append(f"  {sku}  {units:>6}  {str(value):>16}")
    out.append(f"  {'total':<8}  {'':>6}  {str(grand_total):>16}")
    return "\n".join(out)


def render(summary):
    """The summary as plain text."""
    out = [f"Depot summary for {summary.day.isoformat()}", "Orders:"]
    for state, count in summary.orders_by_state.items():
        if count:
            out.append(f"  {state:<10} {count:>4}")
    out.append(f"Shipped: {len(summary.shipped_orders)} orders, {summary.shipped_weight_kg} kg")
    out.append(f"Revenue: {summary.revenue}")
    if summary.backorders:
        out.append("Backorders:")
        out.extend(f"  {oid} {sku} short {units}" for oid, sku, units in summary.backorders)
    if summary.low_stock:
        out.append("Low stock:")
        out.extend(f"  {sku} {units}" for sku, units in summary.low_stock)
    return "\n".join(out)
