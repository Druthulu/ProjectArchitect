"""ISBN normalisation and checksums."""


def normalize_isbn(raw):
    """Drop hyphens and spaces; upper-case a trailing 'x' check digit."""
    s = raw.replace("-", "").replace(" ", "").strip()
    return s[:-1] + s[-1].upper() if s else s


def is_valid_isbn10(s):
    """Checksum test for a normalised 10-character ISBN."""
    if len(s) != 10 or not s[:9].isdigit():
        return False
    if not (s[9].isdigit() or s[9] == "X"):
        return False
    total = sum((10 - i) * int(c) for i, c in enumerate(s[:9]))
    total += 10 if s[9] == "X" else int(s[9])
    return total % 11 == 0


def is_valid_isbn13(s):
    """Checksum test for a normalised 13-digit ISBN."""
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def is_valid_isbn(raw):
    """True for a valid ISBN-10 or ISBN-13, hyphens allowed."""
    s = normalize_isbn(raw)
    return is_valid_isbn10(s) if len(s) == 10 else is_valid_isbn13(s)


def check_isbn(raw):
    """Deprecated since 0.3: ISBN-10 only and ignores spaces badly. Use is_valid_isbn."""
    s = raw.replace("-", "")
    return len(s) == 10 and is_valid_isbn10(s)
