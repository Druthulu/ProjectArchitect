#!/usr/bin/env python
"""discussion.py -- owns .run/DISCUSSION and discussions/INDEX.md.

`on`  : the PreToolUse guard denies edits and non-read-only shell while the flag exists.
`off` : removes the flag; `--new-record` writes discussions/D<n>.md and indexes it.
`triage <id> <status>` : writes a deferred item's status into its index row (planner-gen triage).
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
TEMPLATE = """# {did} — {topic} ({date}, session {kind})
Status: open

## Decisions
- (one line each)

## Plan edits
- (plan_edit.py commands; executed: yes/no)

## Open
- (unresolved items)

## Deferred
- deferred: <what> → <phase>
"""

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


def flag_path(root):
    return os.path.join(root, ".run", "DISCUSSION")


def dis_dir(root):
    return os.path.join(phase_dir(root), "current", "discussions")


def next_id(root):
    d = dis_dir(root)
    used = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            m = re.match(r"^D([0-9]+)\.md$", name)
            if m:
                used.append(int(m.group(1)))
    return 1 + max(used or [0])


def cmd_on(a, root):
    path = flag_path(root)
    if os.path.isfile(path):
        sys.stdout.write("discussion already on (since %s)\n"
                         % read_text(path).strip().split("\n")[0])
        return 0
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    write_text(path, "%s\ntopic: %s\n" % (stamp, a.topic or "-"))
    sys.stdout.write("discussion ON -- edits and non-read-only shell are denied "
                     "until `discussion.py off`\n")
    return 0


def cmd_off(a, root):
    path = flag_path(root)
    topic = a.topic or "-"
    kind = a.kind or os.environ.get("PA_MODE") or "router"
    if os.path.isfile(path):
        first = read_text(path).split("\n")
        for line in first:
            if line.startswith("topic:") and not a.topic:
                topic = line.split(":", 1)[1].strip() or "-"
        os.remove(path)
        sys.stdout.write("discussion OFF\n")
    else:
        sys.stdout.write("discussion was already off\n")
    if not a.new_record:
        return 0
    did = "D%d" % next_id(root)
    target = os.path.join(dis_dir(root), "%s.md" % did)
    date = datetime.date.today().isoformat()
    write_text(target, TEMPLATE.format(did=did, topic=topic, date=date, kind=kind))
    _cnote(target, script="discussion.py", sub="off", whole=True, root=root)
    index = os.path.join(dis_dir(root), "INDEX.md")
    if not os.path.isfile(index):
        write_text(index, INDEX_HEADER)
    line = "%s | %s | %s | open | %s" % (did, topic, date, rel(root, target))
    with open(index, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + "\n")
    _cnote(index, script="discussion.py", sub="off", added=len(line), root=root)
    sys.stdout.write("%s\n%s\n" % (rel(root, target), line))
    return 0


def cmd_status(a, root):
    # when called with a record id and value: set the record's status
    did = getattr(a, "did", None)
    if did:
        return _set_record_status(a, root)
    path = flag_path(root)
    if os.path.isfile(path):
        body = read_text(path).strip().replace("\n", " | ")
        sys.stdout.write("on | %s\n" % body)
    else:
        sys.stdout.write("off\n")
    return 0


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
        prog="discussion.py", description="Read-only discussion mode (/discuss, /proceed).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("on", help="raise .run/DISCUSSION")
    s.add_argument("--topic")
    s = sub.add_parser("off", help="drop the flag; --new-record writes discussions/D<n>.md")
    s.add_argument("--new-record", dest="new_record", action="store_true")
    s.add_argument("--topic")
    s.add_argument("--kind")
    s = sub.add_parser("status", help="on|off or set a record status")
    s.add_argument("did", nargs="?", help="record id (e.g. D1)")
    s.add_argument("value", nargs="?", help="executed|deferred:<phase>|planned:<phase>|dropped|failed")
    s.add_argument("--reason", nargs="*", help="reason for the status change")
    s = sub.add_parser("triage", help="set a deferred item's index status (planner-gen triage)")
    s.add_argument("id", help="index id (D<n>, ...) or PhaseEnd item P<phase>-<k>")
    s.add_argument("value", help="deferred:<phase>|deferred:gen<N>|planned:<phase>/T<n>|dropped")
    s.add_argument("--reason", nargs="*", help="reason (not written; for the caller's log)")
    a = p.parse_args(argv)
    root = find_root()
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status,
            "triage": cmd_triage}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
