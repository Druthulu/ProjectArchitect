"""The catalog: books keyed by normalised ISBN."""
from .isbn import is_valid_isbn, normalize_isbn
from .models import NotFound


class Catalog:
    def __init__(self):
        self._books = {}

    def add(self, book):
        """Add a book, or add its copies to the existing record. Returns the stored Book."""
        isbn = normalize_isbn(book.isbn)
        if not is_valid_isbn(isbn):
            raise ValueError(f"invalid ISBN: {book.isbn!r}")
        existing = self._books.get(isbn)
        if existing is not None:
            existing.copies += book.copies
            return existing
        book.isbn = isbn
        self._books[isbn] = book
        return book

    def get(self, isbn):
        """The book with this ISBN; raises NotFound."""
        book = self._books.get(normalize_isbn(isbn))
        if book is None:
            raise NotFound(f"no book {isbn}")
        return book

    def find(self, isbn):
        """Like get, but returns None when absent."""
        return self._books.get(normalize_isbn(isbn))

    def remove(self, isbn):
        self._books.pop(self.get(isbn).isbn)

    def search(self, text):
        """Books whose title or author contains text (case-insensitive), sorted by title."""
        needle = text.strip().lower()
        hits = [b for b in self._books.values()
                if needle in b.title.lower() or needle in b.author.lower()]
        return sorted(hits, key=lambda b: b.title.lower())

    def by_tag(self, tag):
        return sorted((b for b in self._books.values() if tag in b.tags), key=lambda b: b.isbn)

    def __len__(self):
        return len(self._books)

    def __iter__(self):
        return iter(sorted(self._books.values(), key=lambda b: b.isbn))

    def _title_match(self, text):
        """Prefix-only title matcher from 0.1; search() replaced it. Unused."""
        return [b for b in self._books.values() if b.title.startswith(text)]
