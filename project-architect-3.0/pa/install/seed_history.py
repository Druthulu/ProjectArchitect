"""Past versions of the seed rules 3.2 rewrote (3.24 T5): ``<pkg>/rules-seed/HISTORY.json``.

``{id: [sha256, ...]}`` of every version of ``rules-seed/<id>.md`` in the package's git history
plus the current file (CRLF folded, :func:`managed.sha`), for :data:`project.SEED_REFRESH`.  A
project's ``rules/<id>.md`` equal to one is an untouched seed and takes the current text.  The
table ships with the package (``--root`` copies only ``pa/*.py``; a fresh install copies only
``rules-seed/*.md``).

Public:
    past(rid, pkg)  the set of known shas for ``rid`` (empty when unknown)

Build (from the package's work tree, after a seed rule changes):
    cd project-architect-3.0 && python -m pa.install.seed_history
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = "rules-seed/HISTORY.json"           # under the package root
IDS = ("P3", "P5", "P8", "P10", "H8", "P4", "H7", "X4", "M1",   # project.SEED_REFRESH
       "P7")                                                   # + project.SEED_RETIRED (3.25 T7b)
_CACHE = {}


def past(rid, pkg):
    """The set of known shas of ``rules-seed/<rid>.md`` (empty when the table lacks it)."""
    path = os.path.join(pkg, TABLE)
    if path not in _CACHE:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError):
            _CACHE[path] = {}
    return set(_CACHE[path].get(rid) or ())


def build(pkg):
    """``{id: sorted shas}`` from ``pkg``'s git history and its current seed files."""
    from .managed import sha
    top = subprocess.run(["git", "-C", pkg, "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=True).stdout.strip()
    prefix = os.path.relpath(pkg, top).replace(os.sep, "/")
    prefix = "" if prefix == "." else prefix + "/"
    out = {}
    for rid in IDS:
        path = "%srules-seed/%s.md" % (prefix, rid)
        shas = set()
        commits = subprocess.run(["git", "-C", top, "log", "--format=%H", "--", path],
                                 capture_output=True, text=True, check=True).stdout.split()
        for c in commits:
            got = subprocess.run(["git", "-C", top, "show", "%s:%s" % (c, path)], capture_output=True)
            if got.returncode == 0:
                shas.add(sha(got.stdout))
        cur = os.path.join(pkg, "rules-seed", rid + ".md")
        if os.path.isfile(cur):         # 3.25 T7b: a retired rule has only its history
            with open(cur, "rb") as fh:
                shas.add(sha(fh.read()))
        out[rid] = sorted(shas)
    return out


def main():
    pkg = os.path.dirname(os.path.dirname(HERE))
    table = build(pkg)
    path = os.path.join(pkg, TABLE)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(table, indent=2, sort_keys=True) + "\n")
    print("%s: %s" % (path, ", ".join("%s %d" % (k, len(v)) for k, v in sorted(table.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
