"""Natural sort key for PhaseEnd file names (``sort -V``-like).

Public:
    key(name)   a sort key: ``_`` and ``-`` read as ``.``, integers before
                decimals before words, stable for equal keys.

The key agrees with ``phaseend_index.py:natural_key`` by construction; both
split on ``[._-]+`` and rank (integer, fractional-suffix, word) tuples.
"""

import re


_SUFFIX_RE = re.compile(r"(\.[A-Za-z~][A-Za-z0-9~]*)+$")   # GNU sort -V ignores such suffixes
_RUN_RE = re.compile(r"\d+|[^\d]+")


def key(name):
    """Return a comparison key for *name* suitable for ``sorted(…, key=…)``.

    A trailing file-name suffix (``.md``) is dropped first, as ``sort -V`` does.
    Separators ``_``, ``-`` and ``.`` are token boundaries, and every token is
    split into digit and non-digit runs (``Phase23`` -> ``phase``, ``23``), so
    ``Phase9`` < ``Phase10`` and ``Phase23.md`` < ``Phase23.5.md`` < ``Phase24.md``.
    Each run becomes ``(type, int_val, text)``: ``0`` for a number, ``1`` for a
    word, which puts numeric families before alphabetic labels (3.3 T4 fix).
    """
    out = []
    base = _SUFFIX_RE.sub("", str(name))
    for part in re.split(r"[._\-]+", base):
        for run in _RUN_RE.findall(part):
            if run.isdigit():
                out.append((0, int(run), ""))
            else:
                out.append((1, 0, run.lower()))
    return out
