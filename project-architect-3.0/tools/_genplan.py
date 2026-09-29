#!/usr/bin/env python
"""_genplan.py -- shared GENERATION_PLAN.md phase-line parser.

Imported by plan_edit.py (cmd_gen, show --gen-phase, grammar), genend_index.py and
research_add.py so the `- <G>.<n> <name> | key: value | …` line grammar
(templates/GENERATION_PLAN.template.md:8-10) is parsed in one place. Also the deferred-item
scan (discussion indexes + newest PhaseEnd) shared by launch.py seeds and plan_edit.py approve.
Stdlib only; no imports from `pa/`.
"""

import os
import re

PHASE_RE = re.compile(r"^\s*-\s+([0-9]+(?:\.[0-9]+)+)\s+[^|]*\|")


def phase_lines(text):
    """-> the `- <G>.<n> <name> | …` lines of a GENERATION_PLAN.md text, in order."""
    out = []
    for raw in text.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        if PHASE_RE.match(line):
            out.append(raw)
    return out


def fields(line):
    """-> dict(id, name, milestone, scope, depends, status, phase_end) for one phase line."""
    body = line[:-1] if line.endswith("\r") else line
    body = re.sub(r"^\s*-\s+", "", body.strip())
    parts = [p.strip() for p in body.split(" | ")]
    m = re.match(r"^(\S+)\s+(.*)$", parts[0]) if parts else None
    out = {"id": m.group(1) if m else (parts[0] if parts else ""),
           "name": m.group(2).strip() if m else "",
           "milestone": "", "scope": "", "depends": "", "status": "", "phase_end": ""}
    for p in parts[1:]:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        k = k.strip().lower().replace("-", "_")
        if k in out:
            out[k] = v.strip()
    return out


def first_open(text):
    """-> the id of the first phase line (document order) whose status is `open`, else None."""
    for line in phase_lines(text):
        f = fields(line)
        if f["status"] == "open":
            return f["id"]
    return None


# --------------------------------------------------------------------------- #
# deferred items (launch.py planner seed, plan_edit.py approve Triage check)
# --------------------------------------------------------------------------- #
PE_RE = re.compile(r"^PhaseEnd_Phase(.+)\.md$")
FROM_T_RE = re.compile(r"^\s*-\s+from\s+T\d+[^:]*:\s*(.*)$")


def _read(path):
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError:
        return None


def index_files(pe_dir):
    """-> [(path, id column)]: DISCUSSION_INDEX.md (phase | id | …), discussions/INDEX.md (id | …)."""
    return [(os.path.join(pe_dir, "DISCUSSION_INDEX.md"), 1),
            (os.path.join(pe_dir, "current", "discussions", "INDEX.md"), 0)]


def index_rows(pe_dir):
    """-> [(path, line no, cells, id column)] for every data row of both discussion indexes."""
    out = []
    for path, col in index_files(pe_dir):
        text = _read(path)
        if text is None:
            continue
        for i, raw in enumerate(text.split("\n")):
            s = raw.rstrip("\r").strip()
            if not s or s.startswith("#") or s.startswith("<!--"):
                continue
            cells = [c.strip() for c in s.split("|")]
            if len(cells) >= col + 4 and cells[col]:
                out.append((path, i, cells, col))
    return out


def newest_phaseend(pe_dir):
    """-> the newest (mtime) PhaseEnd_Phase*.md under pe_dir or one level below, else None."""
    found = []
    if os.path.isdir(pe_dir):
        for d in [pe_dir] + [os.path.join(pe_dir, s) for s in os.listdir(pe_dir)]:
            if os.path.isdir(d):
                found += [os.path.join(d, n) for n in os.listdir(d) if PE_RE.match(n)]
    if not found:
        return None
    found.sort(key=lambda p: (os.path.getmtime(p), p))
    return found[-1]


def _due(status, phase, order):
    """True when an index status is a deferral due at or before `phase` (GENERATION_PLAN order)."""
    if status == "deferred":
        return True
    if not status.startswith("deferred:"):
        return False
    target = status.split(":", 1)[1].strip()
    if not target:
        return True
    if target.startswith("gen"):
        return False
    if target not in order or phase not in order:
        return True
    return order.index(target) <= order.index(phase)


def deferred_items(pe_dir, gtext, phase, every=False):
    """-> [dict(id, text, kind, status, pe)] the deferred items due for `phase`, in index then
    PhaseEnd order. kind 'index': a discussion-index row; kind 'phaseend': a newest-PhaseEnd
    `## Deferred` `- from T<n>: <text>` line with no index row yet (id P<phase>-<k>, pe = its path).
    every=True (planner-gen triage): any status starting `deferred` is open, `deferred:gen<N>` too."""
    order = [fields(l)["id"] for l in phase_lines(gtext or "")]
    rows = index_rows(pe_dir)
    known = set(cells[col] for _p, _i, cells, col in rows)
    out, seen = [], set()
    for _p, _i, cells, col in rows:
        rid, status = cells[col], cells[col + 3]
        due = status.startswith("deferred") if every else _due(status, phase, order)
        if rid in seen or not due:
            continue
        seen.add(rid)
        out.append({"id": rid, "text": cells[col + 1], "kind": "index", "status": status, "pe": None})
    pe = newest_phaseend(pe_dir)
    if pe:
        pphase = PE_RE.match(os.path.basename(pe)).group(1)
        inside, k = False, 0
        for raw in (_read(pe) or "").split("\n"):
            line = raw.rstrip("\r")
            if line.startswith("## "):
                inside = line.strip() == "## Deferred"
                continue
            m = FROM_T_RE.match(line) if inside else None
            if not m:
                continue
            k += 1
            pid = "P%s-%d" % (pphase, k)
            text = m.group(1).strip()
            if text.endswith(" (unconsumed)"):
                text = text[:-len(" (unconsumed)")].rstrip()
            if pid not in known and pid not in seen:
                seen.add(pid)
                out.append({"id": pid, "text": text, "kind": "phaseend", "status": "", "pe": pe})
    return out
