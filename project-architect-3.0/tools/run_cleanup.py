#!/usr/bin/env python3
"""run_cleanup.py -- the one-time .run/ cleanup's deleter (3.29.8), run by the mod after the developer's picker yes.

    run_cleanup.py --check [list]   check the list, print its items and total size; exit 1 when any item is refused
    run_cleanup.py [list]           check, then delete exactly the listed items, each by name; print what went

``list`` (default ``.run/mod/cleanup.json``) is ``{"paths": ["P32/t3", "wave_ab", ...]}``: paths relative to ``.run/``
(a leading ``.run/`` is accepted). Each item must exist, lie inside ``.run/`` (no ``..``, no absolute path, no link
leading out), and must not be PA3's own (``mod``, ``status.json``, ``health.json``, ``plans``, the plan drafts) or the
open phase's ``.run/<phase>/``. One refused item refuses the whole list; nothing outside the list is ever touched.
Stdlib only.
"""
import json
import os
import re
import shutil
import sys

OWN = {"mod", "status.json", "health.json", "plans", "PHASE_PLAN.draft.md", "GENERATION_PLAN.draft.md"}


def find_root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, ".claude", "pa.json")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start or os.getcwd())
        d = up


def open_phase(root):
    try:
        with open(os.path.join(root, "phase-ends", "current", "PHASE_PLAN.md"), encoding="utf-8") as fh:
            m = re.match(r"^#\s*Phase\s+([A-Za-z0-9._-]+)", fh.readline())
            return m.group(1) if m else ""
    except OSError:
        return ""


def size_of(path):
    if os.path.isfile(path) or os.path.islink(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(base, f)).st_size
            except OSError:
                pass
    return total


def check(root, items):
    """[(rel, abs, size)] for good items, and the refusals as lines."""
    run = os.path.realpath(os.path.join(root, ".run"))
    phase = open_phase(root)
    good, bad = [], []
    for raw in items:
        rel = str(raw).replace("\\", "/").strip()
        absolute = rel.startswith("/") or bool(re.match(r"^[A-Za-z]:", rel))
        if rel.startswith(".run/"):
            rel = rel[len(".run/"):]
        rel = rel.rstrip("/")
        parts = rel.split("/") if rel else []
        why = ""
        if not rel or absolute or ".." in parts or "." in parts or "" in parts:
            why = "not a plain path inside .run/"
        elif parts[0] in OWN:
            why = "PA3's own entry"
        elif phase and parts[0] == phase:
            why = "the open phase's folder"
        else:
            full = os.path.join(run, *parts)
            real = os.path.realpath(full)
            if not os.path.lexists(full):
                why = "not found"
            elif real != run and not real.startswith(run + os.sep):
                why = "leads outside .run/"
            elif real == run:
                why = ".run/ itself"
        if why:
            bad.append("refused: .run/%s: %s" % (rel, why))
        else:
            full = os.path.join(run, *parts)
            good.append((rel, full, size_of(full)))
    return good, bad


def gb(n):
    return "%.2f GB" % (n / 1e9)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only_check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    root = find_root()
    path = os.path.join(root, args[0]) if args else os.path.join(root, ".run", "mod", "cleanup.json")
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh).get("paths") or []
    except (OSError, ValueError, AttributeError) as exc:
        print("refused: the list %s cannot be read (%s)" % (path, exc))
        return 1
    if not isinstance(items, list) or not items:
        print("refused: the list names no paths")
        return 1
    good, bad = check(root, items)
    if bad:
        print("\n".join(bad))
        print("nothing deleted: %d of %d items refused" % (len(bad), len(items)))
        return 1
    total = sum(s for _, _, s in good)
    if only_check:
        print("checked %d items, all inside .run/, %s" % (len(good), gb(total)))
        return 0
    gone = 0
    for rel, full, _ in good:
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass
        if os.path.lexists(full):
            print("left: .run/%s (in use or locked)" % rel)
        else:
            gone += 1
    print("deleted %d of %d items, %s, all inside .run/" % (gone, len(good), gb(total)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
