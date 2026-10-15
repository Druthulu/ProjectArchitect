#!/usr/bin/env python
"""journal_rollup.py -- roll past journal day folders into one day file (3.23 T2).

    PY tools/journal_rollup.py [--root DIR] [--today YYYY-MM-DD] [--dry-run]

Entries: `<root>/.claude-state/journal/<YYYY-MM-DD>/<HHMMSS>-<seq><writer>-<who>-<kind>.md`
(mod/pa/hooks/journal.ts entryPath). A day folder rolls when it is strictly before --today
(default: the local date) and its newest file is more than 1 h old:
  - `<root>/.claude-state/journal/<YYYY-MM-DD>.md` gets a header line, then per entry in name
    order `## <entry stem>` and the entry's text unchanged (a newline is added only when the
    text lacks a final one); `split_day` splits it back. An existing day file is merged.
  - each `INDEX-<YYYY-MM>.md` line (journal.ts buildIndex: `ts | who | kind | first | path`)
    whose path is `<prefix><date>/<stem>.md` is rewritten to `<prefix><date>.md#<stem>`.
  - the rolled entry files are deleted, then the folder when it is empty.
Idempotent; never touches --today or later. Prints `rolled: <date> (<n> entries)` lines
(`would roll:` under --dry-run) or `nothing to roll`; exit 0. Stdlib only.
"""

import argparse
import datetime
import os
import re
import sys
import time

JOURNAL_DIR = os.path.join(".claude-state", "journal")
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STEM_RE = re.compile(r"^[0-9]{6}-[0-9]{3}[rw]-(?:T[0-9]+|[a-z]+)-[a-z]+$")
INDEX_RE = re.compile(r"^INDEX-[0-9]{4}-[0-9]{2}\.md$")
LINE_RE = re.compile(r"^([^|]*) \| ([^|]*) \| ([^|]*) \| (.*) \| ([^|]+)$")   # journal.ts parseIndex
PATH_RE = re.compile(r"^(.*?)([0-9]{4}-[0-9]{2}-[0-9]{2})/([^/#]+)\.md$")
MIN_AGE_S = 3600


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def split_day(text):
    """-> {stem: entry text} from a day file (the inverse of render_day)."""
    out, stem, buf = {}, None, []
    for line in text.splitlines(True):
        m = re.match(r"^## (\S+)\r?\n?$", line)
        if m and STEM_RE.match(m.group(1)):
            if stem is not None:
                out[stem] = "".join(buf)
            stem, buf = m.group(1), []
        elif stem is not None:
            buf.append(line)
    if stem is not None:
        out[stem] = "".join(buf)
    return out


def render_day(date, entries):
    """Header line, then `## <stem>` + text per entry in name order."""
    parts = ["# Journal %s (%d entries, rolled by tools/journal_rollup.py)\n" % (date, len(entries))]
    for stem in sorted(entries):
        text = entries[stem]
        parts.append("## %s\n%s%s" % (stem, text, "" if text.endswith("\n") else "\n"))
    return "".join(parts)


def due_days(jdir, today, now):
    """-> [(date, folder, [entry stems])] for the day folders that roll."""
    out = []
    if not os.path.isdir(jdir):
        return out
    for name in sorted(os.listdir(jdir)):
        folder = os.path.join(jdir, name)
        if not (DAY_RE.match(name) and os.path.isdir(folder)) or name >= today:
            continue
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        files = [f for f in files if os.path.isfile(f)]
        if not files or now - max(os.path.getmtime(f) for f in files) <= MIN_AGE_S:
            continue
        stems = sorted(os.path.basename(f)[:-3] for f in files
                       if f.endswith(".md") and STEM_RE.match(os.path.basename(f)[:-3]))
        if stems:
            out.append((name, folder, stems))
    return out


def rewrite_index(text, dates):
    """Index text with each rolled day's entry paths in the `<date>.md#<stem>` form."""
    out = []
    for line in text.splitlines(True):
        body = line.rstrip("\r\n")
        m = LINE_RE.match(body)
        p = PATH_RE.match(m.group(5)) if m else None
        if p and p.group(2) in dates and STEM_RE.match(p.group(3)):
            new = "%s%s.md#%s" % (p.group(1), p.group(2), p.group(3))
            line = body[:m.start(5)] + new + line[len(body):]
        out.append(line)
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="journal_rollup.py", description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--today", default=datetime.date.today().isoformat(), help="YYYY-MM-DD (default: local date)")
    ap.add_argument("--dry-run", action="store_true", help="print what would roll; write nothing")
    a = ap.parse_args(argv)
    if not DAY_RE.match(a.today):
        sys.stdout.write("refused: --today %s is not YYYY-MM-DD\n" % a.today)
        return 1
    jdir = os.path.join(os.path.abspath(a.root), JOURNAL_DIR)
    days = due_days(jdir, a.today, time.time())
    if not days:
        sys.stdout.write("nothing to roll\n")
        return 0
    for date, folder, stems in days:
        if a.dry_run:
            sys.stdout.write("would roll: %s (%d entries)\n" % (date, len(stems)))
            continue
        day_file = os.path.join(jdir, date + ".md")
        entries = split_day(_read(day_file)) if os.path.isfile(day_file) else {}
        for stem in stems:
            entries[stem] = _read(os.path.join(folder, stem + ".md"))
        _write(day_file, render_day(date, entries))
        sys.stdout.write("rolled: %s (%d entries)\n" % (date, len(stems)))
    if a.dry_run:
        return 0
    dates = set(d for d, _f, _s in days)
    for name in sorted(os.listdir(jdir)):
        if INDEX_RE.match(name):
            path = os.path.join(jdir, name)
            text = _read(path)
            new = rewrite_index(text, dates)
            if new != text:
                _write(path, new)
    for _date, folder, stems in days:
        for stem in stems:
            os.remove(os.path.join(folder, stem + ".md"))
        if not os.listdir(folder):
            os.rmdir(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
