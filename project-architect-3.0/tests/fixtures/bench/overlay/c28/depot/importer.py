"""Stock-count import: CSV text from the handheld scanners, 'sku,bin,qty' with a header.

Each valid row sets the counted quantity of that SKU in that bin. A row that
cannot be applied (unknown SKU, bad bin code, a qty that is not a non-negative
integer, the wrong number of fields) is reported with its line number and
changes nothing; the other rows still apply.
"""
import csv
import io
from dataclasses import dataclass, field

from depot import DepotError
from depot.picking import check_bin

HEADER = ("sku", "bin", "qty")


class ImportFormatError(DepotError):
    """The file as a whole is unusable (missing or wrong header)."""


@dataclass(frozen=True)
class RowError:
    line: int
    message: str


@dataclass
class ImportResult:
    applied: list = field(default_factory=list)  # (line, sku, bin, qty, delta)
    errors: list = field(default_factory=list)   # RowError

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        return f"{len(self.applied)} rows applied, {len(self.errors)} rejected"


def parse_qty(raw):
    """A counted quantity: a non-negative integer written with ASCII digits only."""
    text = raw.strip()
    if not text or not text.isascii() or not text.isdigit():
        raise ValueError(f"qty must be a non-negative integer, got {raw!r}")
    return int(text)


def read_rows(text):
    """(line number, [fields]) for each non-blank data row; checks the header."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or tuple(h.strip().lower() for h in header) != HEADER:
        raise ImportFormatError(f"expected header {','.join(HEADER)}, got {header!r}")
    for fields in reader:
        if not fields or all(not f.strip() for f in fields):
            continue
        yield reader.line_num, [f.strip() for f in fields]


def export_counts(inventory, skus):
    """A count sheet for skus in the import format: one row per bin holding stock, sorted by bin then SKU.

    Counters fill in the qty column and the sheet comes back through import_counts.
    """
    rows = sorted((bin_code, sku, qty) for sku in skus for bin_code, qty in inventory.bins_for(sku).items())
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows((sku, bin_code, qty) for bin_code, sku, qty in rows)
    return out.getvalue()


def import_counts(text, catalog, inventory, source="count import"):
    """Apply a stock-count CSV to inventory; returns the ImportResult."""
    result = ImportResult()
    for line, fields in read_rows(text):
        if len(fields) != len(HEADER):
            result.errors.append(RowError(line, f"expected {len(HEADER)} fields, got {len(fields)}"))
            continue
        sku, bin_code, raw_qty = fields
        if sku not in catalog:
            result.errors.append(RowError(line, f"unknown sku {sku!r}"))
            continue
        try:
            check_bin(bin_code)
        except ValueError as exc:
            result.errors.append(RowError(line, str(exc)))
            continue
        try:
            qty = parse_qty(raw_qty)
        except ValueError:
            qty = 0
        delta = inventory.set_count(sku, bin_code, qty, reason=f"{source} line {line}")
        result.applied.append((line, sku, bin_code, qty, delta))
    return result


def variance_report(result):
    """Plain-text lines for the rows whose count differed from the book stock, then the errors."""
    out = [f"{sku} {bin_code}: counted {qty} ({delta:+d})"
           for _, sku, bin_code, qty, delta in result.applied if delta]
    out.extend(f"line {err.line}: {err.message}" for err in result.errors)
    return out
