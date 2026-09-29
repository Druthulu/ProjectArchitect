"""Legacy PhaseEnd index: the same table ``phaseend_index.py legacy-index`` writes.

Public:
    HEADER              the two comment lines at the top
    rows(phase_dir)  -> [(label, line)]   sorted by :func:`natural_sort.key`
    text(rows)          the full file text (header + one line per row + trailing newline)

Each *line* is ``label | title | milestone | date | path`` extracted from the
first 40 lines of each ``PhaseEnd_*.md`` in *phase_dir*, sorted by
:mod:`natural_sort`.

Stdlib only; nothing touches the filesystem at import time.
"""

import os
import re

from . import natural_sort


HEADER = ("# Legacy PhaseEnds (pre-PA3)\n"
          "# phase | title | milestone | date | path\n")


def rows(phase_dir, rel_root=None):
    """Return ``[(label, line)]`` for every ``PhaseEnd_*.md`` in *phase_dir*.

    *rel_root* is the root for relative paths in the output; when ``None``,
    paths are relative to *phase_dir*'s parent.
    """
    if rel_root is None:
        rel_root = os.path.dirname(phase_dir)

    result = []
    for name in os.listdir(phase_dir):
        if not (name.startswith("PhaseEnd_") and name.endswith(".md")):
            continue

        label = name[len("PhaseEnd_"):-3]
        label = re.sub(r"^Phase[_\-]?", "", label)
        label = re.sub(r"[_\-]+", ".", label).strip(".")

        path = os.path.join(phase_dir, name)
        title = milestone = date = "-"

        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= 40:
                    break
                b = raw.split("\r")[0].rstrip("\n").strip()
                if title == "-" and b.startswith("#"):
                    title = b.lstrip("#").strip()
                elif milestone == "-" and b.lower().startswith("milestone"):
                    milestone = b.split(":", 1)[-1].strip()[:90]
                elif date == "-":
                    dm = re.match(r"(?i)^(?:closed|date)\s*:\s*(.+)$", b)
                    if dm:
                        date = dm.group(1).strip()[:30]
                    else:
                        dm = re.search(r"\b(20\d\d-\d\d-\d\d)\b", b)
                        if dm:
                            date = dm.group(1)

        rel_path = os.path.relpath(path, rel_root).replace("\\", "/")
        line = "%s | %s | %s | %s | %s" % (label, title, milestone, date, rel_path)
        result.append((label, line))

    result.sort(key=lambda r: natural_sort.key(r[0]))
    return result


def text(row_list):
    """Return the full index file text from a ``rows()`` result."""
    return HEADER + "\n".join(r[1] for r in row_list) + "\n"
