#!/usr/bin/env python
"""journal_rollup.py -- roll past journal day folders into one day file (3.23 T2); read the journal (3.32.15).

    PY tools/journal_rollup.py [--root DIR] [--today YYYY-MM-DD] [--dry-run]
    PY tools/journal_rollup.py entries [--root DIR] [--since YYYY-MM-DD] [--after DATE/STEM] [--match RE]
                                       [--last N] [--budget-chars N] [--max-chars N]

Entries: `<root>/.claude-state/journal/<YYYY-MM-DD>/<HHMMSS>-<seq><writer>-<who>-<kind>.md`
(mod/pa/hooks/journal.ts entryPath). A day folder rolls when it is strictly before --today
(default: the local date) and its newest file is more than 1 h old:
  - `<root>/.claude-state/journal/<YYYY-MM-DD>.md` gets a header line, then per entry in name
    order `## <entry stem>` and the entry's text unchanged (a newline is added only when the
    text lacks a final one); `split_day` splits it back. An existing day file is merged.
  - the rolled entry files are deleted, then the folder when it is empty.
Idempotent; never touches --today or later. Prints `rolled: <date> (<n> entries)` lines
(`would roll:` under --dry-run) or `nothing to roll`; exit 0. Stdlib only.
3.32.15: the month index `INDEX-<YYYY-MM>.md` is retired (nothing read it but /pa-bugreport, and the mod's upkeep
re-read the whole month after every journal write): a roll-up deletes any it finds (`removed:` lines).

`entries` prints the entries whose stem `--match` searches (default: all) from `--since` on, both forms, one JSON
object per line in name order, ASCII: `{"path", "name", "text"}`, `path` the live `.claude-state/journal/<date>/<stem>.md`
or the packed `.claude-state/journal/<date>.md#<stem>`, `name` `<date>/<stem>.md`. `--after` keeps names at or after
`<date>/<HHMMSS>`; `--last` the newest N; `--max-chars` cuts each text; `--budget-chars` keeps the newest lines while
they fit. The mod reads packed days through it: its `$.fs` refuses a file over 4 MiB, and a busy day's passes that.
"""

import argparse
import collections
import datetime
import json
import os
import re
import sys
import time

JOURNAL_DIR = os.path.join(".claude-state", "journal")
J = ".claude-state/journal"     # the paths the mod uses
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# 3.32.9: an external coder's entries (writer x, tools/pa_ext.py) and a watcher's past its 999th (four digits) roll too
STEM_RE = re.compile(r"^[0-9]{6}-[0-9]{3,}[rwx]-(?:T[0-9]+|[a-z]+)-[a-z]+$")
INDEX_RE = re.compile(r"^INDEX-[0-9]{4}-[0-9]{2}\.md$")     # 3.32.15: the retired month index, deleted on sight
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


def iter_entries(jdir, since="", keep=None):
    """3.32.15: ``(date, stem, path, text)`` of each journal entry from ``since`` (YYYY-MM-DD; '' all) on, in date then
    stem order, both forms: a packed day file's entries and a day folder's entry files; a stem in both (a roll-up
    between its write and its delete) once, the folder's. ``keep(date, stem)`` picks entries before their text is
    read (a folder's files are read only then). Any size: the mod's ``$.fs`` refuses a file over 4 MiB."""
    if not os.path.isdir(jdir):
        return
    dates = sorted(set(n[:10] for n in os.listdir(jdir) if DAY_RE.match(n[:10]) and n in (n[:10], n[:10] + ".md")))
    for date in dates:
        if date < since:
            continue
        got = {}
        day_file = os.path.join(jdir, date + ".md")
        if os.path.isfile(day_file):
            for stem, text in split_day(_read(day_file)).items():
                got[stem] = ("%s/%s.md#%s" % (J, date, stem), text)
        folder = os.path.join(jdir, date)
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.endswith(".md") and STEM_RE.match(f[:-3]) and os.path.isfile(os.path.join(folder, f)):
                    got[f[:-3]] = ("%s/%s/%s" % (J, date, f), None)
        for stem in sorted(got):
            if keep is not None and not keep(date, stem):
                continue
            path, text = got[stem]
            if text is None:
                try:
                    text = _read(os.path.join(folder, stem + ".md"))
                except OSError:      # rolled away since the listing: its day file has it now
                    continue
            yield date, stem, path, text


def cmd_entries(argv):
    """3.32.15: the ``entries`` subcommand (the module docstring)."""
    try:                      # ASCII JSON anyway; LF lines on Windows too
        sys.stdout.reconfigure(newline="\n")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="journal_rollup.py entries", description="print journal entries as JSON lines")
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--since", default="", help="the first day, YYYY-MM-DD (default: every day)")
    ap.add_argument("--after", default="", help="only names `<date>/<stem>` at or after this (e.g. 2026-11-09/103000)")
    ap.add_argument("--match", default="", help="a regular expression searched in each entry's stem")
    ap.add_argument("--last", type=int, default=0, help="only the newest N that match")
    ap.add_argument("--budget-chars", type=int, default=0, help="the newest lines while they fit in N characters")
    ap.add_argument("--max-chars", type=int, default=0, help="each entry's text cut to N characters")
    a = ap.parse_args(argv)
    try:
        pat = re.compile(a.match) if a.match else None
    except re.error as e:
        sys.stdout.write("refused: --match %s: %s\n" % (a.match, e))
        return 1
    jdir = os.path.join(os.path.abspath(a.root), JOURNAL_DIR)

    def keep(date, stem):
        return (a.after == "" or "%s/%s" % (date, stem) >= a.after) and (pat is None or pat.search(stem) is not None)
    rows = collections.deque(maxlen=a.last if a.last > 0 else None)
    for date, stem, path, text in iter_entries(jdir, a.since, keep):
        rows.append(json.dumps({"path": path, "name": "%s/%s.md" % (date, stem),
                                "text": text[:a.max_chars] if a.max_chars > 0 else text}))
    lines = list(rows)
    if a.budget_chars > 0:
        kept, total = [], 0
        for line in reversed(lines):
            if kept and total + len(line) + 1 > a.budget_chars:
                break
            kept.append(line)
            total += len(line) + 1
        lines = kept[::-1]
    sys.stdout.write("".join(line + "\n" for line in lines))
    return 0


def retire_index(jdir):
    """3.32.15: delete the retired month index files; -> their names."""
    gone = []
    for name in sorted(os.listdir(jdir)) if os.path.isdir(jdir) else []:
        if INDEX_RE.match(name):
            try:
                os.remove(os.path.join(jdir, name))
                gone.append(name)
            except OSError:
                pass
    return gone


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["entries"]:
        return cmd_entries(argv[1:])
    ap = argparse.ArgumentParser(prog="journal_rollup.py", description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--today", default=datetime.date.today().isoformat(), help="YYYY-MM-DD (default: local date)")
    ap.add_argument("--dry-run", action="store_true", help="print what would roll; write nothing")
    a = ap.parse_args(argv)
    if not DAY_RE.match(a.today):
        sys.stdout.write("refused: --today %s is not YYYY-MM-DD\n" % a.today)
        return 1
    jdir = os.path.join(os.path.abspath(a.root), JOURNAL_DIR)
    if not a.dry_run:
        for name in retire_index(jdir):
            sys.stdout.write("removed: %s (the month index is retired)\n" % name)
    days = due_days(jdir, a.today, time.time())
    if not days:
        sys.stdout.write("nothing to roll\n")
        return 0
    if a.dry_run:
        return _roll(jdir, days, True)
    lock = _take_lock(os.path.abspath(a.root))
    if lock is None:
        sys.stdout.write("another roll-up is running\n")
        return 0
    try:
        return _roll(jdir, days, False)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


LOCK_STALE_S = 600      # 3.32.9: a roll-up takes seconds; an older lock is a crashed one's


def _take_lock(root):
    """3.32.9: ``<root>/.run/journal_rollup.lock``, created exclusively (an older-than-LOCK_STALE_S one is taken over);
    None while another roll-up holds it: the session start's and the 8-hour timer's, or two sessions' of one project,
    never merge the same day at once."""
    d = os.path.join(root, ".run")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "journal_rollup.lock")
    for _attempt in (0, 1):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) < LOCK_STALE_S:
                    return None
                os.remove(path)
            except OSError:
                return None
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        return path
    return None


def _roll(jdir, days, dry_run):
    """Roll ``days`` (``due_days``): each into its day file, then the entry files deleted."""
    for date, folder, stems in days:
        if dry_run:
            sys.stdout.write("would roll: %s (%d entries)\n" % (date, len(stems)))
            continue
        day_file = os.path.join(jdir, date + ".md")
        entries = split_day(_read(day_file)) if os.path.isfile(day_file) else {}
        for stem in stems:
            entries[stem] = _read(os.path.join(folder, stem + ".md"))
        _write(day_file, render_day(date, entries))
        sys.stdout.write("rolled: %s (%d entries)\n" % (date, len(stems)))
    if dry_run:
        return 0
    for _date, folder, stems in days:
        for stem in stems:
            try:
                os.remove(os.path.join(folder, stem + ".md"))
            except FileNotFoundError:     # 3.32.9: gone already (a hand-run roll-up's)
                pass
        if not os.listdir(folder):
            os.rmdir(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
