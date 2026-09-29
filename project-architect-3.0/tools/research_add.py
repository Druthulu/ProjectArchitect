#!/usr/bin/env python
"""research_add.py -- allocate, write and index retriever reports.

new   : atomic id allocation (research/.next + O_EXCL on the target file)
index : validate the header and the <=40-line ## Answer, append the INDEX line
find  : grep the phase index, the cumulative index and the legacy archive
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

INDEX_HEADER = ("# Research reports -- this phase\n"
                "# id | task | title | tags | agent | date | lines\n")
TEMPLATE = """# {rid} — {title}
task: {task} · agent: {agent} · model: {model} · date: {date} · tags: {tags}
sources:

## Answer (returned verbatim, <=40 lines)

## Findings

## Dead ends
"""


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


def emit(lines, cap=40):
    out = list(lines)
    if len(out) > cap:
        out = out[:cap - 1] + ["... (%d more lines)" % (len(out) - cap + 1)]
    sys.stdout.write("\n".join(out) + "\n")


def res_dir(root):
    return os.path.join(phase_dir(root), "current", "research")


def phase_number(root):
    plan = os.path.join(phase_dir(root), "current", "PHASE_PLAN.md")
    if os.path.isfile(plan):
        m = re.match(r"^#\s*Phase\s+(\S+)", read_text(plan).split("\n")[0].strip())
        if m:
            return m.group(1)
    else:
        gp = os.path.join(root, "GENERATION_PLAN.md")
        if os.path.isfile(gp):
            _d = os.path.dirname(os.path.abspath(__file__))
            if _d not in sys.path:
                sys.path.insert(0, _d)
            import _genplan
            fo = _genplan.first_open(read_text(gp))
            if fo:
                return fo
    return str(cfg(root).get("phase") or "0")


def _marker(root, rid):
    return os.path.join(root, ".run", "research-reserved", rid)


def allocate(root, phase):
    """Reserve research/R<phase>-<nnn>.md; returns (rid, path).

    The report file is NOT created here: the agent's Write creates it, so Claude Code
    never asks the developer to confirm an overwrite of a file the agent did not read.
    The reservation is a marker under .run/ (O_EXCL, gitignored); `index` removes it.
    """
    d = res_dir(root)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    counter = os.path.join(d, ".next")
    start = 1
    try:
        start = max(1, int(read_text(counter).strip()))
    except Exception:
        pass
    n = start
    while n < start + 10000:
        rid = "R%s-%03d" % (phase, n)
        path = os.path.join(d, "%s.md" % rid)
        marker = _marker(root, rid)
        if os.path.exists(path):
            n += 1
            continue
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        try:
            write_text(counter, "%d\n" % (n + 1))
        except Exception:
            pass
        return rid, path
    die("no free research id after 10000 tries")


def cmd_new(a, root):
    phase = a.phase or phase_number(root)
    rid, path = allocate(root, phase)
    header = TEMPLATE.format(
        rid=rid, title=a.title, task=a.task, agent=a.agent,
        model=a.model or "-", date=datetime.date.today().isoformat(),
        tags=a.tags or "-")
    _cnote(path, script="research_add.py", sub="new", whole=True, root=root)
    # id, path, then the header the report must start with (the file itself is written by the agent)
    sys.stdout.write("%s\n%s\n--- header (start the report with these lines) ---\n%s" % (rid, rel(root, path), header))
    return 0


def parse_report(path):
    text = read_text(path)
    lines = text.split("\n")
    head = lines[0].strip() if lines else ""
    m = re.match(r"^#\s*(R[0-9A-Za-z.]+-[0-9]+)\s*[-—]+\s*(.*)$", head)
    if not m:
        die("%s: first line must be '# R<N>-<nnn> -- <title>'" % os.path.basename(path))
    meta = {}
    for raw in lines[1:6]:
        for part in re.split(r"\s*·\s*|\s\|\s", raw.strip()):
            if ":" in part:
                k, v = part.split(":", 1)
                meta.setdefault(k.strip().lower(), v.strip())
    answer = []
    inside = False
    for raw in lines:
        if raw.startswith("## "):
            inside = raw[3:].strip().lower().startswith("answer")
            continue
        if inside:
            answer.append(raw)
    while answer and not answer[-1].strip():
        answer.pop()
    return m.group(1), m.group(2).strip(), meta, len(lines), len(answer)


def cmd_index(a, root):
    path = os.path.join(res_dir(root), "%s.md" % a.rid)
    if not os.path.isfile(path):
        die("no report at %s" % rel(root, path))
    rid, title, meta, nlines, nanswer = parse_report(path)
    if rid != a.rid:
        die("header id %s does not match the filename %s" % (rid, a.rid))
    if nanswer > 40:
        die("%s: ## Answer is %d lines (max 40)" % (a.rid, nanswer))
    try:
        os.remove(_marker(root, a.rid))
    except OSError:
        pass
    line = "%s | %s | %s | %s | %s | %s | %d lines" % (
        rid, meta.get("task", "-"), title, meta.get("tags", "-"),
        meta.get("agent", "-"), meta.get("date", "-"), nlines)
    index = os.path.join(res_dir(root), "INDEX.md")
    if not os.path.isfile(index):
        write_text(index, INDEX_HEADER)
    existing = read_text(index).split("\n")
    if any(x.startswith(rid + " |") for x in existing):
        out = [x for x in existing if not x.startswith(rid + " |")]
        write_text(index, "\n".join(out).rstrip("\n") + "\n")
    with open(index, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + "\n")
    _cnote(index, script="research_add.py", sub="index", added=len(line), root=root)
    sys.stdout.write(line + "\n")
    return 0


def _parse_pending(path):
    """Parse a pending report file: returns (title, task, agent, tags, body_from_answer).

    The pending shape is: ``# <title>``, then ``task:``, ``agent:``, ``tags:`` lines,
    then ``## Answer`` onward.
    """
    text = read_text(path)
    lines = text.split("\n")
    title = ""
    meta = {}
    answer_start = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if i == 0:
            m = re.match(r"^#\s+(.+)$", stripped)
            title = m.group(1).strip() if m else stripped
            continue
        if answer_start is None:
            if stripped.startswith("## Answer"):
                answer_start = i
            else:
                for part in re.split(r"\s*·\s*|\s\|\s", stripped):
                    if ":" in part:
                        k, v = part.split(":", 1)
                        meta.setdefault(k.strip().lower(), v.strip())
    body = "\n".join(lines[answer_start:]) if answer_start is not None else ""
    return title, meta.get("task", ""), meta.get("agent", ""), meta.get("tags", ""), body


def adopt_pending(phase_directory, task=None):
    """Adopt every ``research/pending/*.md`` into indexed research reports.

    For each file in name order: allocate the next ``R<N>-<nnn>``, write the
    header using ``new``'s own writer, keep everything from ``## Answer`` on
    verbatim, append the ``research/INDEX.md`` line via ``index``'s logic, and
    delete the pending file.

    Returns a list of ``(rid, pending_name)`` tuples.
    """
    # Derive root from the phase directory (phase-ends/current -> root)
    # phase_directory is the phase-ends/current dir
    res = os.path.join(phase_directory, "research")
    pending_dir = os.path.join(res, "pending")
    if not os.path.isdir(pending_dir):
        return []
    files = sorted(f for f in os.listdir(pending_dir) if f.endswith(".md"))
    if not files:
        return []
    # We need the root to call allocate; walk up from phase_directory
    # phase_directory = <root>/phase-ends/current  (or similar)
    root = os.path.dirname(os.path.dirname(phase_directory))
    phase = phase_number(root)
    adopted = []
    for name in files:
        ppath = os.path.join(pending_dir, name)
        title, ptask, agent, tags, body = _parse_pending(ppath)
        ptask = ptask or task or "-"
        rid, rpath = allocate(root, phase)
        header = TEMPLATE.format(
            rid=rid, title=title, task=ptask, agent=agent or "-",
            model="-", date=datetime.date.today().isoformat(),
            tags=tags or "-")
        # Write the report: header + body from ## Answer onward
        full = header.rstrip("\n") + "\n" + body if body else header
        write_text(rpath, full)
        _cnote(rpath, script="research_add.py", sub="adopt", whole=True, root=root)
        # Index it
        _rid, _title, meta, nlines, nanswer = parse_report(rpath)
        try:
            os.remove(_marker(root, rid))
        except OSError:
            pass
        line = "%s | %s | %s | %s | %s | %s | %d lines" % (
            rid, meta.get("task", "-"), title, meta.get("tags", "-"),
            meta.get("agent", "-"), meta.get("date", "-"), nlines)
        index = os.path.join(res, "INDEX.md")
        if not os.path.isfile(index):
            write_text(index, INDEX_HEADER)
        existing = read_text(index).split("\n")
        if any(x.startswith(rid + " |") for x in existing):
            out = [x for x in existing if not x.startswith(rid + " |")]
            write_text(index, "\n".join(out).rstrip("\n") + "\n")
        with open(index, "a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")
        _cnote(index, script="research_add.py", sub="adopt", added=len(line), root=root)
        # Remove the pending file
        os.remove(ppath)
        adopted.append((rid, name))
    # Remove pending dir if empty
    try:
        os.rmdir(pending_dir)
    except OSError:
        pass
    return adopted


def cmd_adopt(a, root):
    pd = os.path.join(phase_dir(root), "current")
    adopted = adopt_pending(pd, task=getattr(a, "task", None))
    for rid, name in adopted:
        sys.stdout.write("adopted: %s <- %s\n" % (rid, name))
    return 0


def cmd_find(a, root):
    targets = [os.path.join(res_dir(root), "INDEX.md"),
               os.path.join(phase_dir(root), "RESEARCH_INDEX.md"),
               os.path.join(root, "docs", "research-archive", "INDEX.md")]
    pdir = phase_dir(root)
    for d in (sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []):
        p = os.path.join(pdir, d, "research", "INDEX.md")
        if os.path.isfile(p):
            targets.append(p)
    term = a.term.lower()
    hits = []
    for t in targets:
        if not os.path.isfile(t):
            continue
        for raw in read_text(t).split("\n"):
            if raw.startswith("#") or not raw.strip():
                continue
            if term in raw.lower():
                hits.append("%s: %s" % (rel(root, t), raw.strip()))
    if not hits:
        sys.stdout.write("no research index line matches '%s'\n" % a.term)
        return 0
    emit(hits)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="research_add.py", description="Allocate, index and find research reports.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new", help="allocate R<N>-<nnn> and write the header")
    s.add_argument("--task", required=True, help="T<n> | T<n>.c<k> | PHASE-END | plan | gen-plan")
    s.add_argument("--title", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--tags")
    s.add_argument("--model")
    s.add_argument("--phase")
    s = sub.add_parser("index", help="validate and append the INDEX.md line")
    s.add_argument("rid")
    s = sub.add_parser("adopt", help="adopt pending/*.md into indexed reports")
    s.add_argument("--task", help="T<n> to fill a missing task: line")
    s = sub.add_parser("find", help="grep the research indexes")
    s.add_argument("term")
    a = p.parse_args(argv)
    root = find_root()
    return {"new": cmd_new, "index": cmd_index, "adopt": cmd_adopt,
            "find": cmd_find}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
