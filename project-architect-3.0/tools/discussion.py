#!/usr/bin/env python
"""discussion.py -- owns discussions/INDEX.md rows.

`status <id> <value>` : sets a record's Status: line and its index row.
`triage <id> <status>` : writes a deferred item's status into its index row (planner-gen triage).
3.24 T3.2: `on`/`off` (the .run/DISCUSSION flag) and `--new-record` left with the discussion guard.
Stdlib only; no imports from `pa/`.
"""

import argparse
import datetime
import json
import os
import re
import sys

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INDEX_HEADER = ("# Discussions -- one line per record\n"
                "# id | topic | date | status | path\n")
VALID_STATUSES = ("executed", "dropped", "failed")
STATUS_PREFIX = ("deferred:", "planned:")  # deferred:<phase>, planned:<phase>


def find_root(start=None):
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


def cfg(root):
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def phase_dir(root):
    c = cfg(root)
    return os.path.join(root, c.get("phase_ends_dir") or c.get("phase_dir") or "phase-ends")


def read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _cnote(path, **kw):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def rel(root, path):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def dis_dir(root):
    return os.path.join(phase_dir(root), "current", "discussions")


def cmd_status(a, root):
    # when called with a record id and value: set the record's status
    did = getattr(a, "did", None)
    if not did or not getattr(a, "value", None):
        die("status needs a record id and a value")
    return _set_record_status(a, root)


def _set_record_status(a, root):
    """Set a discussion record's Status: line and rewrite its index line."""
    did = a.did
    value = a.value
    reason = " ".join(a.reason) if a.reason else None
    # validate status value
    if value not in VALID_STATUSES and not any(value.startswith(p) for p in STATUS_PREFIX):
        die("invalid status: %s (expected executed, deferred:<phase>, "
            "planned:<phase>, dropped, failed)" % value)
    # find the record file
    d = dis_dir(root)
    record = os.path.join(d, "%s.md" % did)
    if not os.path.isfile(record):
        die("no record %s.md in %s" % (did, rel(root, d)))
    # rewrite the Status: line in the record
    text = read_text(record)
    lines = text.split("\n")
    found = False
    for i, line in enumerate(lines):
        if line.startswith("Status:"):
            if reason:
                lines[i] = "Status: %s — %s" % (value, reason)
            else:
                lines[i] = "Status: %s" % value
            found = True
            break
    if not found:
        # insert after the first line (the title)
        if reason:
            lines.insert(1, "Status: %s — %s" % (value, reason))
        else:
            lines.insert(1, "Status: %s" % value)
    write_text(record, "\n".join(lines))
    _cnote(record, script="discussion.py", sub="status", root=root)
    # rewrite the index line
    index = os.path.join(d, "INDEX.md")
    if os.path.isfile(index):
        idx_text = read_text(index)
        idx_lines = idx_text.split("\n")
        for i, line in enumerate(idx_lines):
            if line.startswith(did + " |"):
                parts = [c.strip() for c in line.split("|")]
                # grammar: id | topic | date | status | path
                if len(parts) >= 5:
                    parts[3] = value
                elif len(parts) == 4:
                    # old format without status: id | topic | date | path
                    parts.insert(3, value)
                idx_lines[i] = " | ".join(parts)
                break
        write_text(index, "\n".join(idx_lines))
        _cnote(index, script="discussion.py", sub="status", root=root)
    sys.stdout.write("%s | %s\n" % (did, value))
    return 0


TRIAGE_STATUS_RE = re.compile(r"^(deferred:\S+|planned:[^/\s]+/T[0-9]+|dropped)$")
DISC_CUM_HEADER = "# Discussions -- cumulative\n# phase | id | topic | date | status | path\n"


def _anchor(lines, idx):
    b = len(lines[idx - 1]) if idx > 0 else 0
    a = len(lines[idx + 1]) if idx + 1 < len(lines) else 0
    return b + a


def cmd_triage(a, root):
    """Write one triage decision: the status cell of the index row holding <id>; a PhaseEnd item
    (P<phase>-<k>, no row yet) gets a new DISCUSSION_INDEX.md row."""
    rid, status = a.id, a.value
    if not TRIAGE_STATUS_RE.match(status):
        die("invalid triage status: %s (expected deferred:<phase>|deferred:gen<N>, "
            "planned:<phase>/T<n>, dropped)" % status)
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    import _genplan
    pe_dir = phase_dir(root)
    for p, i, cells, col in _genplan.index_rows(pe_dir):
        if cells[col] != rid:
            continue
        plines = read_text(p).split("\n")
        raw = plines[i]
        body, eol = (raw[:-1], "\r") if raw.endswith("\r") else (raw, "")
        parts = body.split("|")
        _old = len(raw)
        parts[col + 3] = " %s " % status
        plines[i] = "|".join(parts) + eol
        write_text(p, "\n".join(plines))
        _cnote(p, script="discussion.py", sub="triage",
               removed=_old, added=len(plines[i]), anchor=_anchor(plines, i), root=root)
        sys.stdout.write("triage: %s -> %s\n" % (rid, status))
        return 0
    gp = os.path.join(root, "GENERATION_PLAN.md")
    gtext = read_text(gp) if os.path.isfile(gp) else ""
    for it in _genplan.deferred_items(pe_dir, gtext, None, every=True):
        if it["kind"] != "phaseend" or it["id"] != rid:
            continue
        cum = os.path.join(pe_dir, "DISCUSSION_INDEX.md")
        ctext = read_text(cum) if os.path.isfile(cum) else DISC_CUM_HEADER
        if ctext and not ctext.endswith("\n"):
            ctext += "\n"
        pphase = _genplan.PE_RE.match(os.path.basename(it["pe"])).group(1)
        row = "%s | %s | %s | %s | %s | %s" % (pphase, rid, it["text"].replace("|", "/"),
                                               datetime.date.today().isoformat(), status,
                                               rel(root, it["pe"]))
        clines = (ctext + row).split("\n")
        write_text(cum, ctext + row + "\n")
        _cnote(cum, script="discussion.py", sub="triage",
               removed=0, added=len(row), anchor=_anchor(clines, len(clines) - 1), root=root)
        sys.stdout.write("triage: %s -> %s\n" % (rid, status))
        return 0
    die("unknown deferred item: %s (no index row, no newest-PhaseEnd ## Deferred item)" % rid)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="discussion.py", description="Discussion record status and deferred-item triage.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="set a record status")
    s.add_argument("did", nargs="?", help="record id (e.g. D1)")
    s.add_argument("value", nargs="?", help="executed|deferred:<phase>|planned:<phase>|dropped|failed")
    s.add_argument("--reason", nargs="*", help="reason for the status change")
    s = sub.add_parser("triage", help="set a deferred item's index status (planner-gen triage)")
    s.add_argument("id", help="index id (D<n>, ...) or PhaseEnd item P<phase>-<k>")
    s.add_argument("value", help="deferred:<phase>|deferred:gen<N>|planned:<phase>/T<n>|dropped")
    s.add_argument("--reason", nargs="*", help="reason (not written; for the caller's log)")
    a = p.parse_args(argv)
    root = find_root()
    return {"status": cmd_status, "triage": cmd_triage}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
