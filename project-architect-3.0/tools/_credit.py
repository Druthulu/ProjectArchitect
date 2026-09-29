#!/usr/bin/env python
"""_credit.py -- write credit note files for the governance scripts.

Called by the governance scripts after they read or write governed files.
Notes go to <root>/.run/credit/<epoch_ns>-<pid>.json.
Stdlib only; no imports from `pa/`.
"""

import json
import os
import sys
import time


def find_root(start=None):
    """Nearest ancestor holding .claude/pa.json; else git toplevel; else cwd."""
    env = os.environ.get("PA_PROJECT_ROOT")
    if env:
        return os.path.abspath(env)
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, ".claude", "pa.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=start or os.getcwd(), capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except Exception:
        pass
    return os.path.abspath(start or os.getcwd())


def note(path, *, script, sub=None, removed=0, added=0, anchor=0,
         whole=False, file_chars=None, root=None, kind="script",
         cap_chars=None, emitted=None):
    """Write a credit note file.  Never raises, never prints.

    ``kind="outline"`` (outline.py) also records ``cap_chars`` (chars of the
    first 2,000 lines: the harness's whole-Read cap) and ``emitted_chars``.
    """
    try:
        if root is None:
            root = find_root()
        try:
            rel = os.path.relpath(path, root).replace("\\", "/")
        except ValueError:
            rel = path.replace("\\", "/")
        if file_chars is None:
            try:
                file_chars = os.path.getsize(path)
            except OSError:
                file_chars = 0
        credit_dir = os.path.join(root, ".run", "credit")
        os.makedirs(credit_dir, exist_ok=True)
        ns = int(time.time() * 1e9)
        pid = os.getpid()
        fname = "%d-%d.json" % (ns, pid)
        data = {
            "ts": time.time(),
            "script": script,
            "sub": sub,
            "kind": kind,
            "path": rel,
            "file_chars": file_chars,
            "removed_chars": removed,
            "added_chars": added,
            "anchor_chars": anchor,
            "whole": whole,
        }
        if kind == "outline":
            data["cap_chars"] = int(cap_chars or 0)
            data["emitted_chars"] = int(emitted or 0)
        target = os.path.join(credit_dir, fname)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


# ------------------------------------------------------------------ CLI --- #

def _cli_note(args):
    note(args.path, script=args.script, sub=args.sub,
         removed=args.removed, added=args.added, anchor=args.anchor,
         whole=args.whole, file_chars=args.file_chars)


def _cli_outline(args):
    note(args.path, script="outline.py", kind="outline",
         file_chars=args.file_chars, cap_chars=args.cap_chars,
         emitted=args.emitted)


def _cli_runsh(args):
    try:
        root = find_root()
        credit_dir = os.path.join(root, ".run", "credit")
        os.makedirs(credit_dir, exist_ok=True)
        ns = int(time.time() * 1e9)
        pid = os.getpid()
        fname = "%d-%d.json" % (ns, pid)
        data = {
            "ts": time.time(),
            "script": "run.sh",
            "sub": args.sub,
            "kind": "runsh",
            "kept_chars": args.kept_chars,
        }
        with open(os.path.join(credit_dir, fname), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="_credit.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("note")
    s.add_argument("--path", required=True)
    s.add_argument("--script", required=True)
    s.add_argument("--sub")
    s.add_argument("--removed", type=int, default=0)
    s.add_argument("--added", type=int, default=0)
    s.add_argument("--anchor", type=int, default=0)
    s.add_argument("--whole", action="store_true")
    s.add_argument("--file-chars", type=int, dest="file_chars")

    s = sub.add_parser("outline")
    s.add_argument("path")
    s.add_argument("--file-chars", type=int, required=True, dest="file_chars")
    s.add_argument("--cap-chars", type=int, required=True, dest="cap_chars")
    s.add_argument("--emitted", type=int, required=True)

    s = sub.add_parser("runsh")
    s.add_argument("--sub", required=True)
    s.add_argument("--kept-chars", type=int, required=True, dest="kept_chars")

    a = p.parse_args(argv)
    if a.cmd == "note":
        _cli_note(a)
    elif a.cmd == "outline":
        _cli_outline(a)
    elif a.cmd == "runsh":
        _cli_runsh(a)


if __name__ == "__main__":
    main()
