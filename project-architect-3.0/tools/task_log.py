#!/usr/bin/env python
"""task_log.py -- assemble logs, lint task summaries and index them.

assemble T3     assemble logs/T3.md from the ledger, coder logs, research and run logs
finish T3       lint tasks/T3.md, check logs/T3.md exists, append tasks/INDEX.md
lint T3         the same checks, no index write
review T3       write REVIEW.md from the summary's Review: block
coder-finish T3.c1   check the coder log exists
gotchas [--kind harness|generalizable|workflow|binding] [--all | --phase <id>]
                every tagged line in phase-ends/current/tasks/*.md, as path:line
                (the closer's gatherer, 3.1 T17); --all adds the archived
                phase-*/tasks/*.md; --phase <id> scans only phase-<id>/tasks
aside T3 [--restore]
                a task the plan set back keeps its progress out of the next task's
                way: TASK_PROGRESS.md (.patch) to logs/T3.aside.md (.patch); --restore
                moves it back for the task's next start (3.30.1, the mod's loop)
Stdlib only; no imports from `pa/`.
"""

import argparse
import json
import os
import re
import sys

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_LINES = 150
INDEX_HEADER = ("# Task summaries -- this phase\n"
                "# id | status | title | tags | summary | log | research\n")
REQUIRED = ("Status:", "Done:", "Files:")
KINDS = ("harness", "generalizable", "workflow", "binding")
_TAG_RE = re.compile(r"^\s*(?:-\s*)?(?:[A-Za-z][A-Za-z ]*:\s*)?"
                     r"(harness|generalizable|workflow|binding):\s*(.+?)\s*$")
EXPECTED = ("Decisions:", "Deviations:", "Findings:", "Gotchas:", "Research:",
            "Next task needs:", "Full log:")  # 3.25 T4: "Verified:" dropped (no warning; still parsed)
MARKER = "<!-- assembled by task_log.py -->"


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


def ledger_path():
    """The usage ledger (``$PA_LEDGER_DIR`` or ``<config dir>/usage-ledger/ledger.sqlite``)."""
    d = os.environ.get("PA_LEDGER_DIR")
    if not d:
        cfgdir = (os.environ.get("CLAUDE_CONFIG_DIR")
                  or os.path.join(os.path.expanduser("~"), ".claude"))
        d = os.path.join(cfgdir, "usage-ledger")
    return os.path.join(d, "ledger.sqlite")


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


def emit(lines, cap=40):
    out = list(lines)
    if len(out) > cap:
        out = out[:cap - 1] + ["... (%d more lines)" % (len(out) - cap + 1)]
    sys.stdout.write("\n".join(out) + "\n")


def cur(root, *parts):
    return os.path.join(phase_dir(root), "current", *parts)


def field(lines, label):
    for raw in lines:
        if raw.strip().startswith(label):
            return raw.strip()[len(label):].strip()
    return None


_PHASE_RE = re.compile(r"Phase\s+([0-9]+(?:\.[0-9]+)*)")
_APPROVED_RE = re.compile(r"Approved:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")


def _plan_header(root):
    """Parse the plan header's phase id and Approved: date from the first
    lines of PHASE_PLAN.md. Returns (phase_id, approved_date); either is
    None when absent (a draft has no Approved: line)."""
    phase_id = approved = None
    plan_path = cur(root, "PHASE_PLAN.md")
    if os.path.isfile(plan_path):
        for line in read_text(plan_path).split("\n")[:5]:
            if phase_id is None:
                m = _PHASE_RE.search(line)
                if m:
                    phase_id = m.group(1)
            if approved is None:
                m2 = _APPROVED_RE.search(line)
                if m2:
                    approved = m2.group(1)
    return phase_id, approved


def _timeline_rows(root, tid):
    """Query the ledger for agent_runs rows with task_id = tid, bounded to
    this phase: started >= the plan's Approved: date, and the ledger's own
    phase tag when the row carries one."""
    import sqlite3
    path = ledger_path()
    if not os.path.isfile(path):
        return []
    phase_id, approved = _plan_header(root)
    query = ("SELECT agent_type, model_seen, model_pinned, started, ended, status "
              "FROM agent_runs WHERE task_id = ?")
    params = [tid]
    if approved:
        query += " AND started >= ?"
        params.append("%sT00:00:00Z" % approved)
    if phase_id:
        query += " AND (phase IS NULL OR phase = ?)"
        params.append(phase_id)
    query += " ORDER BY started"
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def _coder_logs(root, tid):
    """Find logs/T<n>.c<k>.md files, return [(name, first_40_lines)]."""
    logs_dir = cur(root, "logs")
    pattern = re.compile(r"^%s\.c\d+\.md$" % re.escape(tid))
    result = []
    if not os.path.isdir(logs_dir):
        return result
    for name in sorted(os.listdir(logs_dir)):
        if pattern.match(name):
            lines = read_text(os.path.join(logs_dir, name)).split("\n")[:40]
            result.append((name, lines))
    return result


def _research_lines(root, tid):
    """Return the task's lines from research/INDEX.md."""
    idx = cur(root, "research", "INDEX.md")
    if not os.path.isfile(idx):
        return []
    out = []
    for line in read_text(idx).split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and parts[1] == tid:
            out.append(line.strip())
    return out


def _run_log_names(root, since_iso, tid):
    """Names of run.sh logs at/after since_iso whose base name starts with tid
    (lower case) followed by '-' or 'c' (t6-unit.log, t6c2-tools.log for T6;
    the separator keeps t11-idx.log out of T1). Newest last. They are in
    .run/<phase>/logs while a plan is open (3.29 T4), else .run/logs."""
    import datetime as _dt
    phase_id, _ = _plan_header(root)
    dirs = [os.path.join(root, ".run", phase_id, "logs")] if phase_id else []
    dirs.append(os.path.join(root, ".run", "logs"))
    dirs = [d for d in dirs if os.path.isdir(d)]
    if not dirs:
        return []
    since_ts = 0
    if since_iso:
        try:
            dt = _dt.datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            since_ts = dt.timestamp()
        except Exception:
            since_ts = 0
    prefix_re = re.compile(r"^%s[-c]" % re.escape(tid.lower()))
    rows = []
    for logs_dir in dirs:
        for name in os.listdir(logs_dir):
            if not name.endswith(".log"):
                continue
            if not prefix_re.match(name.lower()):
                continue
            mtime = os.path.getmtime(os.path.join(logs_dir, name))
            if mtime >= since_ts:
                rows.append((mtime, name))
    rows.sort()
    return [name for _, name in rows]


def _extract_authored(text, key):
    """Extract authored-block text between its heading and the next ## heading."""
    heading_map = {"hypotheses": "## Hypotheses rejected",
                   "state": "## State worth keeping"}
    heading = heading_map[key]
    lines = text.split("\n")
    content, capture = [], False
    for line in lines:
        if line.strip() == heading:
            capture = True
            continue
        if capture:
            if line.startswith("## "):
                break
            content.append(line)
    while content and not content[0].strip():
        content.pop(0)
    while content and not content[-1].strip():
        content.pop()
    result = "\n".join(content)
    return result if result else "{{AUTHORED:%s}}" % key


_REVIEW_KEYS = ("ran", "results", "seen", "decisions", "edits")
# 3.24 T8: the block's header in its variants: `Review:`, `## Review`, `**Review:**`, `**Review**:`, `### Review:`.
_B = r"(?:\*\*|__)?"  # optional bold markup
_REVIEW_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?" + _B + r"Review" + _B + r"\s*:?\s*" + _B + r"\s*(.*)$", re.I)
_KEY_LINE = re.compile(r"^(?:[-*]\s+)?" + _B + r"(%s)" % "|".join(_REVIEW_KEYS) + _B + r"\s*:\s*" + _B + r"\s*(.*)$", re.I)
_LABEL_LINE = re.compile(r"^" + _B + r"[A-Za-z][\w /()-]*" + _B + r"\s*:")
_ITEM = re.compile(r"^(?:[-*]|\d+[.)])\s+")


def _parse_review_block(lines):
    """Parse the Review: block from summary lines into {ran, results, seen, decisions, edits}.

    The plain form is a flush-left `Review:` line with indented sub-keys, ended by the first flush-left line. A heading or
    bold header (`## Review`, `**Review:**`; 3.24 T8) may have its sub-keys flush-left, bold or as list items; it ends at
    the next heading or at a flush-left label that is not a sub-key (`Done:`, `**Gotchas:**`).
    """
    result = {}
    form = None  # 'plain' or 'marked' once the header is seen
    current_sub = None
    for line in lines:
        stripped = line.strip()
        if form is None:
            m = _REVIEW_HEAD.match(line)
            if m and m.group(1).strip() == "":
                form = "plain" if stripped == "Review:" else "marked"
            elif m and stripped.startswith("Review:"):
                form = "plain"
            continue
        if not stripped:
            continue
        flush = not line[0].isspace()
        if stripped.startswith("#"):
            break
        km = _KEY_LINE.match(stripped)
        if km and (not flush or form == "marked"):
            current_sub = km.group(1).lower()
            result[current_sub] = km.group(2).strip()
            continue
        if flush and (form == "plain" or _LABEL_LINE.match(stripped)):
            break
        if current_sub and _ITEM.match(stripped):
            prev = result.get(current_sub, "")
            result[current_sub] = (prev + "\n" + stripped).lstrip("\n")
    return result


def cmd_assemble(a, root):
    import datetime as _dt
    tid = a.task
    tmpl_path = os.path.join(root, "templates", "log.template.md")
    if not os.path.isfile(tmpl_path):
        die("no template at %s" % rel(root, tmpl_path))
    log_path = cur(root, "logs", "%s.md" % tid)

    # Preserve authored blocks from an existing assembled log
    existing_authored = {}
    if os.path.isfile(log_path):
        existing = read_text(log_path)
        if MARKER not in existing:
            die("log exists without assembly marker; refusing to overwrite %s"
                % rel(root, log_path))
        for key in ("hypotheses", "state"):
            existing_authored[key] = _extract_authored(existing, key)

    # Timeline from the ledger
    rows = _timeline_rows(root, tid)
    earliest = None
    tl = ["- agent_type | model | spawned_at | returned_at | status"]
    for r in rows:
        model = r["model_seen"] or r["model_pinned"] or "-"
        tl.append("- %s | %s | %s | %s | %s" % (
            r["agent_type"] or "-", model,
            r["started"] or "-", r["ended"] or "-", r["status"] or "-"))
        if earliest is None and r["started"]:
            earliest = r["started"]
    if len(tl) == 1:
        tl.append("- (no agent_runs in ledger)")

    # Coder briefs
    clogs = _coder_logs(root, tid)
    coder_lines = []
    for name, lines in clogs:
        coder_lines.append("### %s" % name)
        coder_lines.extend(lines)
    if not coder_lines:
        coder_lines = ["- (no coder logs)"]

    # Research
    research = _research_lines(root, tid)
    if not research:
        research = ["- (no research lines)"]

    # Run logs: bounded to the phase (Approved date) and the task prefix, not
    # every .run/logs/*.log of the repo (the bound used to fall away when the
    # task had no agent_runs rows and no task summary yet, T7.c3)
    _, approved = _plan_header(root)
    since = ("%sT00:00:00Z" % approved) if approved else earliest
    cmd_names = _run_log_names(root, since, tid)
    cmd_lines = ["- %s" % n for n in cmd_names] if cmd_names else ["- (none)"]

    # Authored blocks
    hypo = existing_authored.get("hypotheses", "{{AUTHORED:hypotheses}}")
    state = existing_authored.get("state", "{{AUTHORED:state}}")

    today = _dt.date.today().isoformat()
    out = [
        "# %s full log — %s" % (tid, today),
        MARKER,
        "",
        "## Timeline",
    ] + tl + [
        "",
        "## Hypotheses rejected",
        hypo,
        "",
        "## Commands run",
    ] + cmd_lines + [
        "",
        "## Coder briefs sent",
    ] + coder_lines + [
        "",
        "## Retriever questions asked",
    ] + ["- %s" % r for r in research] + [
        "",
        "## State worth keeping",
        state,
        "",
    ]

    write_text(log_path, "\n".join(out))
    _cnote(log_path, script="task_log.py", sub="assemble", root=root)
    sys.stdout.write("assembled %s (%d lines)\n" % (rel(root, log_path), len(out)))
    return 0


def cmd_review(a, root):
    tid = a.task
    summary_path = cur(root, "tasks", "%s.md" % tid)
    if not os.path.isfile(summary_path):
        die("no summary at %s" % rel(root, summary_path))
    tmpl_path = os.path.join(root, "templates", "REVIEW.template.md")
    if not os.path.isfile(tmpl_path):
        die("no template at %s" % rel(root, tmpl_path))

    review = _parse_review_block(read_text(summary_path).split("\n"))
    if not review:
        die("no Review: block in %s" % rel(root, summary_path))

    # Phase number from the plan
    phase_id, _ = _plan_header(root)
    phase = phase_id or "?"

    ran = review.get("ran") or "<commands, hosts, wall time>"
    results = review.get("results") or "<paths; never pasted inline>"
    seen = review.get("seen") or "<the Done: line from tasks/T<n>.md, verbatim>"
    decisions = review.get("decisions") or "1. <decision> — Recommended: <option, one line why>"
    edits = review.get("edits") or "- <tools/plan_edit.py … command, not yet run>"

    out = "\n".join([
        "# REVIEW — Phase %s, %s" % (phase, tid),
        "",
        "What ran: %s" % ran,
        "Results at: %s" % results,
        "What the expert saw: %s" % seen,
        "",
        "Decisions needed:",
        decisions,
        "",
        "Plan edits proposed:",
        edits,
        "",
    ])

    review_path = cur(root, "REVIEW.md")
    write_text(review_path, out)
    _cnote(review_path, script="task_log.py", sub="review", root=root)
    sys.stdout.write("wrote %s\n" % rel(root, review_path))
    return 0


def lint_summary(root, tid):
    """-> (errors, warnings, facts)."""
    path = cur(root, "tasks", "%s.md" % tid)
    errs, warns = [], []
    if not os.path.isfile(path):
        return ["no summary at %s" % rel(root, path)], [], {}
    text = read_text(path)
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) > MAX_LINES:
        errs.append("%s is %d lines (max %d) -- move detail to logs/%s.md"
                    % (rel(root, path), len(lines), MAX_LINES, tid))
    head = lines[0].strip() if lines else ""
    m = re.match(r"^#\s*(%s)\s*[-—]+\s*(.+)$" % re.escape(tid), head)
    if not m:
        errs.append("first line must be '# %s -- <title>'" % tid)
    for label in REQUIRED:
        if field(lines, label) is None:
            errs.append("missing '%s' line" % label)
    for label in EXPECTED:
        if field(lines, label) is None:
            warns.append("no '%s' line" % label)
    for i, raw in enumerate(lines):
        if re.match(r"^\s*\|[-\s|:]+\|\s*$", raw):
            errs.append("line %d: markdown table (summaries carry no tables)" % (i + 1))
            break
    for i, raw in enumerate(lines):
        if re.search(r"\.run/(?:[^/\s]+/)?logs", raw) and not raw.strip().startswith(("Full log:", "-", "Commands")):
            warns.append("line %d: mentions .run/logs -- paths only, never dumps" % (i + 1))
            break
    log = cur(root, "logs", "%s.md" % tid)
    if not os.path.isfile(log):
        errs.append("no full log at %s" % rel(root, log))
    facts = {
        "title": (m.group(2).strip() if m else tid),
        "status": (field(lines, "Status:") or "").split("|")[0].strip() or "done",
        "tags": field(lines, "Tags:") or "-",
        "research": field(lines, "Research:") or "-",
        "verified": field(lines, "Verified:"),
        "lines": len(lines),
        "path": path,
        "log": log,
    }
    ids = re.findall(r"R[0-9A-Za-z.]+-[0-9]{3}", facts["research"])
    facts["research_ids"] = ",".join(ids) if ids else "-"
    return errs, warns, facts


def report(root, tid, errs, warns, facts):
    out = ["ERROR %s" % e for e in errs] + ["WARN  %s" % w for w in warns]
    if errs:
        out.append("FAILED: %d problem(s)" % len(errs))
        emit(out)
        return 1
    out.append("OK %s (%s lines)" % (tid, facts.get("lines")))
    emit(out)
    return 0


def cmd_lint(a, root):
    errs, warns, facts = lint_summary(root, a.task)
    return report(root, a.task, errs, warns, facts)


def cmd_finish(a, root):
    # Adopt any pending research reports first
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    import research_add as _ra
    adopted = _ra.adopt_pending(cur(root), task=a.task)
    for rid, name in adopted:
        sys.stdout.write("adopted: %s <- %s\n" % (rid, name))
    errs, warns, facts = lint_summary(root, a.task)
    if errs:
        return report(root, a.task, errs, warns, facts)
    # Check for unfilled authored placeholders in the log, inside their own sections only
    # (a coder log quoted under "Coder briefs sent" may name the markers)
    log_check = cur(root, "logs", "%s.md" % a.task)
    if os.path.isfile(log_check):
        log_text = read_text(log_check)
        for heading, marker in (("## Hypotheses rejected", "{{AUTHORED:hypotheses}}"),
                                ("## State worth keeping", "{{AUTHORED:state}}")):
            start = log_text.find(heading)
            if start < 0:
                continue
            end = log_text.find(chr(10) + "## ", start + len(heading))
            body = log_text[start:end] if end > 0 else log_text[start:]
            if marker in body:
                errs.append("unfilled placeholder %s in %s"
                            % (marker, rel(root, log_check)))
    if errs:
        return report(root, a.task, errs, warns, facts)
    line = "%s | %s | %s | %s | %s | %s | %s" % (
        a.task, facts["status"], facts["title"], facts["tags"],
        rel(root, facts["path"]).split("current/")[-1],
        rel(root, facts["log"]).split("current/")[-1],
        facts["research_ids"])
    index = cur(root, "tasks", "INDEX.md")
    if not os.path.isfile(index):
        write_text(index, INDEX_HEADER)
    existing = read_text(index).split("\n")
    _old_idx = ""
    if any(x.startswith(a.task + " |") for x in existing):
        _old_idx = next(x for x in existing if x.startswith(a.task + " |"))
        keep = [x for x in existing if not x.startswith(a.task + " |")]
        write_text(index, "\n".join(keep).rstrip("\n") + "\n")
    with open(index, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + "\n")
    _cnote(index, script="task_log.py", sub="finish",
           removed=len(_old_idx), added=len(line), root=root)
    out = ["WARN  %s" % w for w in warns]
    out.append(line)
    emit(out)
    return 0


def cmd_coder_finish(a, root):
    m = re.match(r"^(T[0-9]+(?:\.[0-9]+)*)\.(c[0-9]+)$", a.run)
    if not m:
        die("coder run id must look like T3.c1")
    log = cur(root, "logs", "%s.%s.md" % (m.group(1), m.group(2)))
    if not os.path.isfile(log):
        die("no coder log at %s" % rel(root, log))
    n = len(read_text(log).split("\n"))
    sys.stdout.write("OK %s (%s, %d lines)\n" % (a.run, rel(root, log), n))
    return 0


def cmd_gotchas(a, root):
    pdir = phase_dir(root)
    subs = sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []
    if a.phase:
        if not os.path.isdir(os.path.join(pdir, "phase-" + a.phase, "tasks")):
            die("no archived phase at %s" % rel(root, os.path.join(pdir, "phase-" + a.phase, "tasks")))
        scan = ["phase-" + a.phase]
    elif a.all:
        scan = ["current"] + [s for s in subs if s.startswith("phase-")]
    else:
        scan = ["current"]
    files = []
    for sub in scan:
        tdir = os.path.join(pdir, sub, "tasks")
        if not os.path.isdir(tdir):
            continue
        files += [os.path.join(tdir, n) for n in sorted(os.listdir(tdir))
                  if n.endswith(".md") and n != "INDEX.md"]
    out = []
    for path in files:
        for i, line in enumerate(read_text(path).split("\n"), 1):
            m = _TAG_RE.match(line)
            if not m or (a.kind and m.group(1) != a.kind):
                continue
            out.append("%s:%d %s: %s" % (rel(root, path), i, m.group(1), m.group(2)))
    if not out:
        sys.stdout.write("(no %s lines in %d summaries)\n" % (a.kind or "tagged", len(files)))
        return 0
    for line in out:
        sys.stdout.write(line + "\n")
    sys.stdout.write("%d line(s) in %d summaries\n" % (len(out), len(files)))
    return 0


def _tracked(root, path):
    import subprocess
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", "--", path], cwd=root,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _move(root, src, dst):
    """A tracked file is git mv'ed (the rename staged for the next commit), any other renamed; dst is replaced."""
    import subprocess
    if os.path.exists(dst):
        if _tracked(root, dst):
            subprocess.run(["git", "rm", "-q", "-f", "--", dst], cwd=root, capture_output=True, timeout=30)
        if os.path.exists(dst):
            os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if _tracked(root, src):
        r = subprocess.run(["git", "mv", "-f", "--", src, dst], cwd=root, capture_output=True, timeout=30)
        if r.returncode == 0:
            return
    os.replace(src, dst)


def cmd_aside(a, root):
    """3.30.1: a task the plan set back while it was stopped or cut off keeps its progress, out of the way of the task
    that runs meanwhile: TASK_PROGRESS.md and its patch move to logs/<T>.aside.md and .patch. --restore moves them back
    for the task's next start, unless a TASK_PROGRESS.md is there (then the brief names the parked file)."""
    pairs = [(cur(root, "TASK_PROGRESS.md"), cur(root, "logs", "%s.aside.md" % a.task)),
             (cur(root, "TASK_PROGRESS.patch"), cur(root, "logs", "%s.aside.patch" % a.task))]
    if a.restore:
        if not os.path.isfile(pairs[0][1]):
            sys.stdout.write("none: no %s\n" % rel(root, pairs[0][1]))
        elif os.path.exists(pairs[0][0]):
            sys.stdout.write("kept: %s (a TASK_PROGRESS.md is there)\n" % rel(root, pairs[0][1]))
        else:
            for live, parked in pairs:
                if os.path.isfile(parked):
                    _move(root, parked, live)
            sys.stdout.write("restored: %s\n" % rel(root, pairs[0][0]))
        return 0
    moved = []
    for live, parked in pairs:
        if os.path.isfile(live):
            _move(root, live, parked)
            moved.append(rel(root, parked))
    sys.stdout.write("aside: %s\n" % ", ".join(moved) if moved else "none: no TASK_PROGRESS.md\n")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="task_log.py", description="Lint and index task summaries (<=150 lines).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("assemble", help="assemble logs/T<n>.md from the ledger and coder logs")
    s.add_argument("task")
    s = sub.add_parser("finish", help="lint tasks/T<n>.md and append tasks/INDEX.md")
    s.add_argument("task")
    s = sub.add_parser("lint", help="lint tasks/T<n>.md only")
    s.add_argument("task")
    s = sub.add_parser("review", help="write REVIEW.md from the summary Review: block")
    s.add_argument("task")
    s = sub.add_parser("coder-finish", help="check logs/T<n>.c<k>.md exists")
    s.add_argument("run")
    s = sub.add_parser("gotchas", help="tagged lines in the current phase's summaries, as path:line")
    s.add_argument("--kind", choices=KINDS)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true",
                   help="also every archived phase-*/tasks")
    g.add_argument("--phase", metavar="ID",
                   help="only the archived phase-<ID>/tasks (e.g. 3.9.6)")
    s = sub.add_parser("aside", help="park a set-back task's TASK_PROGRESS.md as logs/T<n>.aside.md (--restore: back)")
    s.add_argument("task")
    s.add_argument("--restore", action="store_true")
    a = p.parse_args(argv)
    root = find_root()
    return {"assemble": cmd_assemble, "finish": cmd_finish, "lint": cmd_lint,
            "review": cmd_review, "aside": cmd_aside,
            "coder-finish": cmd_coder_finish, "gotchas": cmd_gotchas}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
