"""Save and load the whole library as JSON (format 2)."""
import json

from .catalog import Catalog
from .dates import format_date, parse_date
from .loans import LoanDesk
from .members import MemberRegistry
from .models import Book, Loan, Member

FORMAT = 2


def _book_to_dict(book):
    return {"isbn": book.isbn, "title": book.title, "author": book.author,
            "year": book.year, "copies": book.copies, "tags": list(book.tags)}


def _member_to_dict(member):
    return {"member_id": member.member_id, "name": member.name, "email": member.email,
            "kind": member.kind, "active": member.active}


def _loan_to_dict(loan):
    return {"loan_id": loan.loan_id, "isbn": loan.isbn, "member_id": loan.member_id,
            "start": format_date(loan.start), "due": format_date(loan.due),
            "returned": format_date(loan.returned) if loan.returned else None,
            "renewals": loan.renewals}


def _loan_from_dict(d):
    returned = d.get("returned")
    return Loan(d["loan_id"], d["isbn"], d["member_id"], parse_date(d["start"]),
                parse_date(d["due"]), parse_date(returned) if returned else None,
                d["renewals"])


def dumps(catalog, members, desk):
    data = {"format": FORMAT,
            "books": [_book_to_dict(b) for b in catalog],
            "members": [_member_to_dict(m) for m in members],
            "loans": [_loan_to_dict(loan) for loan in desk.loans]}
    return json.dumps(data, indent=2, sort_keys=True)


def loads(text, clock=None):
    """Rebuild (catalog, members, desk) from dumps() output."""
    data = json.loads(text)
    if data.get("format") != FORMAT:
        raise ValueError(f"unsupported format {data.get('format')}")
    catalog = Catalog()
    for d in data["books"]:
        catalog.add(Book(**d))
    members = MemberRegistry()
    for d in data["members"]:
        members.add(Member(**d))
    desk = LoanDesk(catalog, members, clock)
    desk.loans = [_loan_from_dict(d) for d in data["loans"]]
    return catalog, members, desk


def save(path, catalog, members, desk):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(catalog, members, desk))


def load(path, clock=None):
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read(), clock)
