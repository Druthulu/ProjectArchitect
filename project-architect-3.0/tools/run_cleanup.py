#!/usr/bin/env python3
"""run_cleanup.py -- the one-time .run/ cleanup's deleter (3.29.8), run by the mod after the developer's picker yes.

    run_cleanup.py --check [list]   check the list, print its items and total size; exit 1 when any item is refused
    run_cleanup.py [list]           check, then delete exactly the listed items, each by name; print what went
    run_cleanup.py --cites [--all]  report the tracked files citing paths under .run/, each path marked (missing) when
                                    gone; history (journal, phase logs, archived phases, PhaseEnds) only with --all;
                                    always exit 0

``list`` (default ``.run/mod/cleanup.json``) is ``{"paths": ["P32/t3", "wave_ab", ...]}``: paths relative to ``.run/``
(a leading ``.run/`` is accepted). Each item must exist, lie inside ``.run/`` (no ``..``, no absolute path, no link
leading out), and must not be PA3's own (``mod``, ``status.json``, ``health.json``, ``plans``, the plan drafts). Inside
the open phase's ``.run/<phase>/`` only a finished task's folder goes (3.33): ``t<N>`` with any suffix (``t14``,
``t14b``, ``t14_prevc2``), task ``T<N>`` ``done`` in ``phase-ends/current/PHASE_PLAN.md`` and not the task in
``.run/mod/loop.json``. One refused item refuses the whole list; nothing outside the list is ever touched.
Stdlib only.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

OWN = {"mod", "status.json", "health.json", "plans", "PHASE_PLAN.draft.md", "GENERATION_PLAN.draft.md"}
TASK_DIR_RE = re.compile(r"^t([0-9]+)", re.I)                      # 3.33: a task's folder: t14, t14b, t14_prevc2
TASK_LINE_RE = re.compile(r"^\s*-\s+T([0-9]+)\s*\|\s*([^|]*?)\s*\|")  # `- T20 | done | title: ...`
# 3.33: a cite is `.run/` and path characters; a `<placeholder>` is taken whole so a template's cite is skipped
CITE_RE = re.compile(r"(?<![A-Za-z0-9_.\-])\.run/(?:[A-Za-z0-9_.\-/*]|<[^<>\s]*>)*")
CITE_OWN = OWN | {"logs"}
CITE_MAX_BYTES = 5 * 1024 * 1024
HISTORY_RE = re.compile(r"^(?:\.claude-state/journal/|phase-ends/[^/]+/logs/|phase-ends/phase-[^/]*/"
                        r"|phase-ends/PhaseEnd_)")


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


def task_states(root):
    """{N: status} from the open plan's task lines (`- T20 | done | ...`); {} when unreadable."""
    states = {}
    try:
        with open(os.path.join(root, "phase-ends", "current", "PHASE_PLAN.md"), encoding="utf-8") as fh:
            for line in fh:
                m = TASK_LINE_RE.match(line)
                if m:
                    states.setdefault(int(m.group(1)), m.group(2).strip().lower())
    except (OSError, UnicodeDecodeError):
        pass
    return states


def running_task(root):
    """The task id in `.run/mod/loop.json`; '' when none, missing or unreadable."""
    try:
        with open(os.path.join(root, ".run", "mod", "loop.json"), encoding="utf-8") as fh:
            task = json.load(fh).get("task")
        return str(task.get("id") or "").strip() if isinstance(task, dict) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def phase_item_refusal(parts, states, running):
    """Why an item inside the open phase's folder is refused; '' when it lies in a finished task's folder."""
    m = TASK_DIR_RE.match(parts[1]) if len(parts) > 1 else None
    if not m:
        return "the open phase's folder: only a finished task's folder inside it"
    n = int(m.group(1))
    rm = re.match(r"^T([0-9]+)", running, re.I)
    if rm and int(rm.group(1)) == n:
        return "%s is running" % running
    if states.get(n) != "done":
        return "T%d is not done" % n
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
    states = task_states(root) if phase else {}
    running = running_task(root) if phase else ""
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
            why = phase_item_refusal(parts, states, running)
        if not why:
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


def cited_paths(text):
    """['.run/...'] for each cite in text, template placeholders and PA3's own entries left out."""
    out = []
    for m in CITE_RE.finditer(text):
        cite = m.group(0)
        if "<" in cite:
            continue
        rel = cite[len(".run/"):].rstrip("./")
        first = rel.split("/")[0]
        if not rel or first in CITE_OWN or not first.strip("*"):
            continue
        out.append(".run/" + rel)
    return out


def tracked_files(root):
    """(sorted tracked paths, '') or (None, why git failed)."""
    try:
        res = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if res.returncode != 0:
        return None, res.stderr.decode("utf-8", "replace").strip() or "exit %d" % res.returncode
    return sorted(f for f in res.stdout.decode("utf-8", "replace").split("\0") if f), ""


def cite_exists(root, cited):
    parts = cited.split("/")
    if "*" in cited:
        return bool(glob.glob(os.path.join(glob.escape(root), *parts)))
    return os.path.lexists(os.path.join(root, *parts))


def cites_report(root, include_history=False):
    """Print each tracked file citing paths under .run/ with its distinct paths (gone ones marked), then a summary."""
    files, err = tracked_files(root)
    if files is None:
        print("no tracked files read: git ls-files failed (%s)" % err)
        files = []
    blocks, every = [], set()
    for rel in files:
        if not include_history and HISTORY_RE.match(rel):
            continue
        full = os.path.join(root, *rel.split("/"))
        try:
            if not os.path.isfile(full) or os.path.getsize(full) > CITE_MAX_BYTES:
                continue
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b".run/" not in data or b"\0" in data:
            continue
        try:
            cites = cited_paths(data.decode("utf-8"))
        except UnicodeDecodeError:
            continue
        if cites:
            blocks.append((rel, len(cites), sorted(set(cites))))
            every.update(cites)
    exists = {p for p in every if cite_exists(root, p)}
    for rel, n, paths in blocks:
        print("%s (%d cites)" % (rel, n))
        for p in paths:
            print("  %s%s" % (p, "" if p in exists else " (missing)"))
    print("%d files cite %d distinct paths under .run/ (%d exist)" % (len(blocks), len(every), len(exists)))
    return 0


def gb(n):
    return "%.2f GB" % (n / 1e9)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = find_root()
    if "--cites" in argv:
        try:
            sys.stdout.reconfigure(errors="backslashreplace")  # a file name the console can't show keeps the report
        except (AttributeError, ValueError):
            pass
        return cites_report(root, include_history="--all" in argv)
    only_check = "--check" in argv
    args = [a for a in argv if a != "--check"]
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
