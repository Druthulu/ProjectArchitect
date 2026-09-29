"""Products and the catalogue that holds them.

SKUs look like 'ABC-1234'. Tags are normalised to lower-case words joined by
hyphens, kept in insertion order and never duplicated on one product.
"""
import csv
import io
import re
from dataclasses import dataclass

from depot import DepotError
from depot.money import Money

SKU_PATTERN = re.compile(r"^[A-Z]{3}-\d{4}$")
PRODUCT_COLUMNS = ("sku", "name", "weight_g", "price", "tags")


class UnknownProduct(DepotError):
    """No product with that SKU."""


class DuplicateProduct(DepotError):
    """A product with that SKU is already in the catalogue."""


def normalise_tag(tag):
    """'  Fragile Goods ' -> 'fragile-goods'; an empty tag is a ValueError."""
    words = str(tag).strip().lower().split()
    if not words:
        raise ValueError("empty tag")
    return "-".join(words)


def check_sku(sku):
    """The SKU unchanged, or ValueError if it is not of the form ABC-1234."""
    if not isinstance(sku, str) or not SKU_PATTERN.match(sku):
        raise ValueError(f"bad SKU {sku!r} (expected e.g. 'ABC-1234')")
    return sku


@dataclass
class Product:
    sku: str
    name: str
    weight_g: int
    price: Money
    tags: list
    active: bool = True

    def has_tag(self, tag):
        return normalise_tag(tag) in self.tags


class Catalog:
    """Products by SKU."""

    def __init__(self):
        self._products = {}

    def __len__(self):
        return len(self._products)

    def __contains__(self, sku):
        return sku in self._products

    def __iter__(self):
        """Products in SKU order."""
        return iter([self._products[sku] for sku in sorted(self._products)])

    def add(self, sku, name, weight_g, price, tags=None):
        """Add and return a new product; tags=None gives it an empty tag list of its own."""
        check_sku(sku)
        if sku in self._products:
            raise DuplicateProduct(sku)
        if not name or not name.strip():
            raise ValueError("product name is required")
        if isinstance(weight_g, bool) or not isinstance(weight_g, int) or weight_g <= 0:
            raise ValueError(f"weight_g must be a positive int, got {weight_g!r}")
        if not isinstance(price, Money) or price.cents <= 0:
            raise ValueError(f"price must be positive Money, got {price!r}")
        clean = [] if tags is None else list(dict.fromkeys(normalise_tag(t) for t in tags))
        product = Product(sku, name.strip(), weight_g, price, clean)
        self._products[sku] = product
        return product

    def get(self, sku):
        try:
            return self._products[sku]
        except KeyError:
            raise UnknownProduct(sku) from None

    def tag(self, sku, tag):
        """Add a tag to a product (no-op if it already has it); returns the product."""
        product = self.get(sku)
        tag = normalise_tag(tag)
        if tag not in product.tags:
            product.tags.append(tag)
        return product

    def untag(self, sku, tag):
        """Remove a tag from a product; a tag it does not have is a ValueError."""
        product = self.get(sku)
        tag = normalise_tag(tag)
        if tag not in product.tags:
            raise ValueError(f"{sku} is not tagged {tag!r}")
        product.tags.remove(tag)
        return product

    def find_by_tag(self, tag, include_inactive=False):
        """Products carrying tag, in SKU order."""
        tag = normalise_tag(tag)
        return [p for p in self if tag in p.tags and (p.active or include_inactive)]

    def search(self, text):
        """Active products whose name contains text (case-insensitive), in SKU order."""
        needle = text.strip().lower()
        return [p for p in self if p.active and needle in p.name.lower()]

    def reprice(self, sku, price):
        """Set a new unit price; open orders keep the price they were created with."""
        if not isinstance(price, Money) or price.cents <= 0:
            raise ValueError(f"price must be positive Money, got {price!r}")
        product = self.get(sku)
        if price.currency != product.price.currency:
            raise ValueError(f"{sku} is priced in {product.price.currency}")
        product.price = price
        return product

    def deactivate(self, sku):
        """Stop selling a product; it stays in the catalogue for history and returns."""
        product = self.get(sku)
        product.active = False
        return product

    def load_csv(self, text):
        """Add products from CSV text 'sku,name,weight_g,price,tags' (tags separated by '|').

        All rows are checked before any is added: a bad row raises ValueError naming its
        line and the catalogue is left unchanged. Returns the products added.
        """
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or [f.strip() for f in reader.fieldnames] != list(PRODUCT_COLUMNS):
            raise ValueError(f"expected header {','.join(PRODUCT_COLUMNS)}")
        rows, seen = [], set(self._products)
        for row in reader:
            line = reader.line_num
            try:
                sku = check_sku(row["sku"].strip())
                if sku in seen:
                    raise DuplicateProduct(sku)
                weight = int(row["weight_g"])
                price = Money.of(row["price"])
            except (ValueError, TypeError, DuplicateProduct) as exc:
                raise ValueError(f"line {line}: {exc}") from None
            tags = [t for t in (row["tags"] or "").split("|") if t.strip()]
            seen.add(sku)
            rows.append((sku, row["name"], weight, price, tags))
        return [self.add(*row) for row in rows]

    def tag_counts(self):
        """{tag: number of active products carrying it}, tags in sorted order."""
        counts = {}
        for product in self:
            if product.active:
                for tag in product.tags:
                    counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items()))
