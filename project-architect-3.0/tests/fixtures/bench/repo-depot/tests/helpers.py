"""Shared fixtures for the depot tests."""
from datetime import date, timedelta

from depot.service import Depot

MONDAY = date(2024, 3, 4)

MUG = "MUG-0001"
LAMP = "LMP-0002"
KETTLE = "KTL-0003"
CHAIR = "CHR-0004"
ANVIL = "ANV-0005"

PRODUCTS = [
    (MUG, "Stoneware mug", 350, "12.50", ["kitchen"]),
    (LAMP, "Desk lamp", 1200, "40.00", ["office", "Fragile"]),
    (KETTLE, "Kettle", 1500, "30.00", ["kitchen", "electric"]),
    (CHAIR, "Office chair", 8000, "85.00", ["office"]),
    (ANVIL, "Blacksmith anvil", 30000, "150.00", ["heavy"]),
]

# one SKU per bin; the chair is split over two bins
STOCK = [
    (MUG, "A-01-01", 20),
    (LAMP, "A-02-03", 10),
    (KETTLE, "B-01-02", 8),
    (CHAIR, "B-03-01", 4),
    (CHAIR, "C-01-01", 2),
    (ANVIL, "C-02-05", 2),
]


class Clock:
    """A settable clock for Depot; starts on a Monday."""

    def __init__(self, today=MONDAY):
        self.today = today

    def __call__(self):
        return self.today

    def advance(self, days):
        self.today += timedelta(days=days)


def stocked_depot(clock=None):
    """(depot, clock): the five products above, stocked as in STOCK."""
    clock = clock or Clock()
    depot = Depot(clock=clock)
    for sku, name, weight, price, tags in PRODUCTS:
        depot.add_product(sku, name, weight, price, tags)
    for sku, bin_code, qty in STOCK:
        depot.receive(sku, bin_code, qty)
    return depot, clock


def delivered_order(depot, clock, lines):
    """Create, place, pick, ship and deliver an order today; returns it."""
    order = depot.create_order("Ada Lovelace", lines)
    depot.place(order.order_id)
    depot.pick(order.order_id)
    depot.ship(order.order_id)
    depot.deliver(order.order_id)
    return order
