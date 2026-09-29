#!/usr/bin/env python
"""managed.py -- the managed-file record `.claude/pa3-managed.json` (3.9.7 T7).

drift           print `none`, or one line per packaged file that differs from its recorded copy:
                `<rel> | base <v|unknown> | <sha8> | edited in <task ids|—>`
stamp <rel>     keep the edit as the user's version: bump `version:` to `+uN`, record it
decline <rel>   record the edit as declined: drift stays silent until the file changes again

Exit 2 when <rel> is not listed in the record. A thin CLI over `pa.install.managed`.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_root():
    """Walk up from cwd to the project root (has .claude/pa3-managed.json or HOW_WE_WORK.md)."""
    d = os.getcwd()
    while True:
        if (os.path.isfile(os.path.join(d, ".claude", "pa3-managed.json"))
                or os.path.isfile(os.path.join(d, "HOW_WE_WORK.md"))):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


def _managed():
    """<script>/.. when it holds pa/, else ~/.claude/pa3 (as tools/bench.py does)."""
    home = os.path.dirname(HERE)
    if not os.path.isdir(os.path.join(home, "pa")):
        home = os.path.join(os.path.expanduser("~"), ".claude", "pa3")
    if home not in sys.path:
        sys.path.insert(0, home)
    from pa.install import managed
    return managed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="managed.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("drift", help="list packaged files that differ from the record")
    for name in ("stamp", "decline"):
        sp = sub.add_parser(name, help="%s the user's copy of <rel>" % name)
        sp.add_argument("rel")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    managed = _managed()
    root = _find_root()
    if args.cmd == "drift":
        rows = managed.drift(root)
        if not rows:
            print("none")
        for rel, base, sha8, tasks in rows:
            print("%s | base %s | %s | edited in %s"
                  % (rel, base or "unknown", sha8, ",".join(tasks) or "—"))
        return 0
    rel = args.rel.replace("\\", "/")
    fn = managed.stamp if args.cmd == "stamp" else managed.decline
    try:
        user = fn(root, rel)
    except KeyError:
        print("%s is not listed in %s" % (rel, managed.RECORD), file=sys.stderr)
        return 2
    print("%s %s%s" % (user["state"], rel, " " + user["mark"] if user.get("mark") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
