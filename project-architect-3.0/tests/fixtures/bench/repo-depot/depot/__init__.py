"""depot: a small warehouse and order-fulfilment library.

Modules: money (cents, HALF_UP), dates (business days, returns window), catalog,
stock (bins, reservations), orders (the state machine), picking, shipping,
billing, importer (stock-count CSV), reports, service (the Depot facade).
"""

__version__ = "1.3.0"


class DepotError(Exception):
    """Base class for every error depot raises on purpose."""
