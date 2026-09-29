"""Invoices and credit notes.

Each line's net is qty x unit price less the line discount, rounded HALF_UP to
the cent on that line. The invoice net is the sum of the line nets. Tax is the
invoice net times the tax rate, rounded HALF_UP once on the invoice total, never
per line. Numbers run per billing month: INV-2024-03-0001, INV-2024-03-0002, ...
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from depot import DepotError
from depot.dates import period_label
from depot.money import Money, to_decimal, total

DEFAULT_TAX_RATE = Decimal("20")


class AlreadyInvoiced(DepotError):
    """The order already has an invoice (or the invoice a credit note)."""


@dataclass(frozen=True)
class InvoiceLine:
    sku: str
    qty: int
    unit_price: Money
    discount_pct: Decimal
    net: Money


@dataclass(frozen=True)
class Invoice:
    number: str
    order_id: str
    customer: str
    issued_on: object
    lines: tuple
    net: Money
    tax_rate: Decimal
    tax: Money
    credits: str = ""  # for a credit note: the number of the invoice it reverses

    @property
    def gross(self):
        return self.net + self.tax

    @property
    def period(self):
        return period_label(self.issued_on)

    @property
    def is_credit_note(self):
        return bool(self.credits)


def line_net(qty, unit_price, discount_pct):
    """qty x unit price less discount_pct percent, rounded HALF_UP to the cent."""
    return (unit_price * qty).less_percent(discount_pct)


def invoice_lines(order):
    """InvoiceLine per order line, in order-line order."""
    return tuple(InvoiceLine(line.sku, line.qty, line.unit_price, line.discount_pct,
                             line_net(line.qty, line.unit_price, line.discount_pct))
                 for line in order.lines)


def build_invoice(number, order, issued_on, tax_rate=DEFAULT_TAX_RATE):
    """The invoice for an order's lines."""
    if not order.lines:
        raise ValueError(f"{order.order_id} has no lines to invoice")
    rate = to_decimal(tax_rate)
    lines = invoice_lines(order)
    currency = lines[0].net.currency
    net = total((line.net for line in lines), currency)
    tax = net.percent(rate)
    return Invoice(number, order.order_id, order.customer, issued_on, lines, net, rate, tax)


def build_credit_note(number, invoice, issued_on):
    """A credit note reversing every line of invoice."""
    lines = tuple(InvoiceLine(l.sku, -l.qty, l.unit_price, l.discount_pct, -l.net) for l in invoice.lines)
    return Invoice(number, invoice.order_id, invoice.customer, issued_on, lines,
                   -invoice.net, invoice.tax_rate, -invoice.tax, credits=invoice.number)


def instalments(invoice, count, first_due, calendar, spacing_days=30):
    """A payment plan: (due date, amount) x count, amounts summing to the invoice gross.

    Due dates fall spacing_days apart, each rolled forward to a business day.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    amounts = invoice.gross.allocate([1] * count)
    plan = []
    for n, amount in enumerate(amounts):
        due = calendar.add_business_days(first_due + timedelta(days=n * spacing_days), 0)
        plan.append((due, amount))
    return plan


def render_invoice(invoice):
    """The invoice (or credit note) as plain text for the customer."""
    title = "CREDIT NOTE" if invoice.is_credit_note else "INVOICE"
    out = [f"{title} {invoice.number}", f"Customer: {invoice.customer}   Order: {invoice.order_id}",
           f"Date: {invoice.issued_on.isoformat()}"]
    if invoice.is_credit_note:
        out.append(f"Reverses: {invoice.credits}")
    for line in invoice.lines:
        discount = f" less {line.discount_pct}%" if line.discount_pct else ""
        out.append(f"  {line.sku}  {line.qty} x {line.unit_price}{discount}  = {line.net}")
    out.append(f"Net: {invoice.net}")
    out.append(f"Tax {invoice.tax_rate}%: {invoice.tax}")
    out.append(f"Total: {invoice.gross}")
    return "\n".join(out)


class InvoiceRegister:
    """Every invoice and credit note issued, numbered per billing month."""

    def __init__(self, prefix="INV", credit_prefix="CRN"):
        self.prefix = prefix
        self.credit_prefix = credit_prefix
        self._documents = []
        self._counters = {}

    def __iter__(self):
        return iter(list(self._documents))

    def _number(self, prefix, day):
        key = (prefix, period_label(day))
        self._counters[key] = self._counters.get(key, 0) + 1
        return f"{prefix}-{key[1]}-{self._counters[key]:04d}"

    def issue(self, order, day, tax_rate=DEFAULT_TAX_RATE):
        """Invoice an order once."""
        if self.for_order(order.order_id):
            raise AlreadyInvoiced(order.order_id)
        invoice = build_invoice(self._number(self.prefix, day), order, day, tax_rate)
        self._documents.append(invoice)
        return invoice

    def credit(self, order_id, day):
        """A credit note for the order's invoice."""
        invoice = self.for_order(order_id)
        if invoice is None:
            raise ValueError(f"{order_id} has no invoice to credit")
        if any(d.credits == invoice.number for d in self._documents):
            raise AlreadyInvoiced(f"{invoice.number} already credited")
        note = build_credit_note(self._number(self.credit_prefix, day), invoice, day)
        self._documents.append(note)
        return note

    def for_order(self, order_id):
        """The (non-credit) invoice of an order, or None."""
        for doc in self._documents:
            if doc.order_id == order_id and not doc.is_credit_note:
                return doc
        return None

    def in_period(self, label):
        """Documents issued in billing period label ('YYYY-MM'), in issue order."""
        return [doc for doc in self._documents if doc.period == label]

    def statement(self, customer, first, last, currency="EUR"):
        """A customer's documents issued in [first, last] as (number, day, gross, running balance)."""
        rows, balance = [], Money.zero(currency)
        for doc in self._documents:
            if doc.customer == customer and first <= doc.issued_on <= last:
                balance = balance + doc.gross
                rows.append((doc.number, doc.issued_on, doc.gross, balance))
        return rows

    def net_revenue(self, first, last, currency="EUR"):
        """Net of invoices less credit notes issued in [first, last]."""
        return total((d.net for d in self._documents if first <= d.issued_on <= last), currency)
