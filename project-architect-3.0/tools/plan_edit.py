#!/usr/bin/env python
"""plan_edit.py -- the only writer of phase-ends/current/PHASE_PLAN.md (PA3).

Every mutation appends a `## Changes` line and rewrites `.run/plan.sha`.
Everything above `## Tasks` is immutable once `Approved:` is stamped.

Stdlib only; no imports from `pa/`; safe under `-X utf8`.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _genplan                                                 # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STATUSES = ("done", "next", "queued", "blocked", "superseded")
KNOWN_KEYS = ("coder", "effort", "title", "files", "done-when", "verify",
              "reads", "deps", "est-ctx", "review", "wait-for", "note")
REQUIRED_KEYS = ("coder", "effort", "files", "done-when", "verify", "deps")
CODERS = ("opus55", "none")  # was ("opus55", "sonnet", "none")
_LEGACY_CODERS = ("opus46", "sonnet")  # accepted by lint on existing plans, not offered by add-task
DASH = "—"          # em dash, the "empty" marker in task lines
TASK_RE = re.compile(r"^\s*-\s+(T[0-9]+(?:\.[0-9]+)*)\s*\|")
SPLIT_RE = re.compile(r"\s+\|\s+")


# --------------------------------------------------------------------------- #
# project plumbing (duplicated in every tool: these scripts ship alone)
# --------------------------------------------------------------------------- #
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


def cfg(root):
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def phase_dir(root, conf=None):
    conf = cfg(root) if conf is None else conf
    name = conf.get("phase_ends_dir") or conf.get("phase_dir") or "phase-ends"
    return os.path.join(root, name)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


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
        more = len(out) - cap + 1
        out = out[:cap - 1] + ["... (%d more lines)" % more]
    sys.stdout.write("\n".join(out) + "\n")


def today():
    return datetime.date.today().isoformat()


# --------------------------------------------------------------------------- #
# credit notes (never raises, never prints)
# --------------------------------------------------------------------------- #
def _cnote(path, **kw):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def _anchor(lines, idx):
    """Chars of the line before and after *idx* (0 when absent)."""
    b = len(lines[idx - 1]) if idx > 0 else 0
    a = len(lines[idx + 1]) if idx + 1 < len(lines) else 0
    return b + a


# --------------------------------------------------------------------------- #
# task-line model
# --------------------------------------------------------------------------- #
class Task(object):
    __slots__ = ("id", "status", "agent", "fields", "cont", "index", "end")

    def __init__(self, tid, status, agent, fields, index):
        self.id = tid
        self.status = status
        self.agent = agent
        self.fields = fields          # dict key -> value (strings)
        self.cont = []                # continuation line numbers
        self.index = index            # line number of the task line
        self.end = index              # last line number owned by this task

    def get(self, key, default=""):
        return self.fields.get(key, default)

    def key(self):
        return id_key(self.id)


def id_key(tid):
    parts = tid[1:].split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def split_eol(raw):
    if raw.endswith("\r"):
        return raw[:-1], "\r"
    return raw, ""


def parse_task_line(raw):
    """-> (id, status, agent, fields) or None."""
    body, _ = split_eol(raw)
    if not TASK_RE.match(body):
        return None
    body = body.strip()
    body = re.sub(r"^-\s+", "", body)
    parts = SPLIT_RE.split(body)
    parts = [p.strip() for p in parts]
    while len(parts) < 3:
        parts.append("")
    tid, status, agent = parts[0], parts[1], parts[2]
    fields = {}
    for p in parts[3:]:
        if not p:
            continue
        if ":" in p:
            k, v = p.split(":", 1)
            fields[k.strip()] = v.strip()
        else:
            fields[p.strip()] = ""
    return tid, status, agent, fields


def parse(text):
    """-> (lines, tasks, headings) where headings maps '## Name' -> line number."""
    lines = text.split("\n")
    headings = {}
    for i, raw in enumerate(lines):
        body, _ = split_eol(raw)
        if body.startswith("## "):
            headings.setdefault(body[3:].strip(), i)
    tasks = []
    start = headings.get("Tasks")
    if start is None:
        return lines, tasks, headings
    stop = len(lines)
    for name, i in headings.items():
        if i > start and i < stop:
            stop = i
    cur = None
    for i in range(start + 1, stop):
        raw = lines[i]
        parsed = parse_task_line(raw)
        if parsed:
            cur = Task(parsed[0], parsed[1], parsed[2], parsed[3], i)
            tasks.append(cur)
            continue
        body, _ = split_eol(raw)
        if cur is not None and body.strip() and body[:1] in (" ", "\t"):
            cur.cont.append(i)
            cur.end = i
        elif body.strip():
            cur = None
    return lines, tasks, headings


def task_block_end(lines, tasks, task):
    """Last line owned by `task` including its T<n>.<k> children."""
    end = task.end
    fam = [t for t in tasks if t.id == task.id or t.id.startswith(task.id + ".")]
    for t in fam:
        end = max(end, t.end)
    return end


def plan_path(root, conf=None):
    return os.path.join(phase_dir(root, conf), "current", "PHASE_PLAN.md")


def load(path):
    if not os.path.isfile(path):
        die("no plan at %s" % path.replace("\\", "/"))
    return read_text(path)


# --------------------------------------------------------------------------- #
# line editing (surgical: untouched lines stay byte-identical)
# --------------------------------------------------------------------------- #
def set_status_in_line(raw, new):
    body, eol = split_eol(raw)
    m = re.match(r"^(\s*-\s+)(\S+)(\s*\|\s*)([^|]*?)(\s*\|)", body)
    if not m:
        return None
    width = len(m.group(4)) + len(m.group(5)) - 1
    field = new.ljust(width) if len(new) <= width else new + " "
    return m.group(1) + m.group(2) + m.group(3) + field + "|" + body[m.end():] + eol


def set_field_in_line(raw, key, value):
    body, eol = split_eol(raw)
    pat = re.compile(r"(\|\s*)(" + re.escape(key) + r")(\s*:\s*)([^|]*)")
    m = pat.search(body)
    if m:
        old = m.group(4)
        trail = old[len(old.rstrip()):] or ""
        body = body[:m.start(4)] + value + trail + body[m.end(4):]
    else:
        body = body.rstrip() + " | %s: %s" % (key, value)
    return body + eol


def render_task(tid, status, agent, fields, order=KNOWN_KEYS):
    parts = ["- %s | %s | %s" % (tid, status, agent)]
    seen = set()
    for k in order:
        if k in fields and fields[k] != "":
            parts.append("%s: %s" % (k, fields[k]))
            seen.add(k)
    for k in fields:
        if k not in seen and fields[k] != "":
            parts.append("%s: %s" % (k, fields[k]))
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# hashes / lock
# --------------------------------------------------------------------------- #
def blank_hash_line(text):
    return re.sub(r"(?m)^(Approved:.*?Plan-hash:).*$", r"\1", text)


def _lf(text):
    """CRLF -> LF: a checkout with core.autocrlf must not change a plan's hash (3.3 T1)."""
    return text.replace("\r\n", "\n")


def plan_hash(text):
    return hashlib.sha256(blank_hash_line(_lf(text)).encode("utf-8")).hexdigest()


def header_text(text):
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        if split_eol(raw)[0].strip() == "## Tasks":
            return "\n".join(lines[:i])
    return text


def header_hash(text):
    return hashlib.sha256(header_text(_lf(text)).encode("utf-8")).hexdigest()


def is_approved(text):
    m = re.search(r"(?m)^Approved:\s*(\S+)", text)
    # the template header `Approved: <date>` is a placeholder, not an approval
    return bool(m and m.group(1) not in ("-", DASH, "TBD", "none")
                and not m.group(1).startswith("<"))


def sha_file(root):
    return os.path.join(root, ".run", "plan.sha")


def write_sha(root, path, text):
    write_text(sha_file(root), "plan %s\nheader %s\nfile %s\n"
               % (plan_hash(text), header_hash(text), rel(root, path)))


def read_sha(root):
    out = {}
    try:
        for line in read_text(sha_file(root)).split("\n"):
            bits = line.strip().split(" ", 1)
            if len(bits) == 2:
                out[bits[0]] = bits[1]
    except Exception:
        pass
    return out


def check_lock(root, path, text, explicit):
    """Refuse when the header changed out of band after approval."""
    if explicit or not is_approved(text):
        return None
    rec = read_sha(root)
    if rec.get("file") != rel(root, path) or "header" not in rec:
        return None
    if rec["header"] != header_hash(text):
        return ("the header above ## Tasks changed since Approved: "
                "structural changes go through REPLAN.md -> planner-phase")
    return None


# --------------------------------------------------------------------------- #
# changes log
# --------------------------------------------------------------------------- #
def append_change(lines, headings, by, what, why=None):
    line = "- %s %s: %s" % (today(), by, what)
    if why:
        line += " %s %s" % (DASH, why)
    idx = headings.get("Changes")
    if idx is None:
        if lines and lines[-1].strip() == "":
            lines.insert(len(lines) - 1, "## Changes")
            lines.insert(len(lines) - 1, line)
        else:
            lines.extend(["", "## Changes", line])
        return line
    end = len(lines)
    while end > idx + 1 and lines[end - 1].strip() == "":
        end -= 1
    lines.insert(end, line)
    return line


def save(root, path, lines, explicit=False):
    text = "\n".join(lines)
    write_text(path, text)
    if not explicit:
        write_sha(root, path, text)
    return text


# --------------------------------------------------------------------------- #
# deps
# --------------------------------------------------------------------------- #
def dep_ids(task):
    raw = task.get("deps", "")
    if not raw or raw in ("-", DASH, "none"):
        return []
    return [d.strip() for d in raw.split(",") if d.strip() and d.strip() not in ("-", DASH)]


def deps_done(task, by_id):
    for d in dep_ids(task):
        t = by_id.get(d)
        if t is None:
            return False
        if t.status == "done":
            continue
        if t.status == "superseded":
            kids = [x for x in by_id.values() if x.id.startswith(d + ".")]
            if kids and all(k.status == "done" for k in kids):
                continue
        return False
    return True


def show_entry(task, lines):
    out = [split_eol(lines[task.index])[0].strip().lstrip("- ").rstrip()]
    if task.agent in _RETIRED_AGENTS:
        out[0] = out[0].replace("| %s |" % task.agent, "| %s |" % _run_agent(task.agent), 1)
    for i in task.cont:
        out.append(split_eol(lines[i])[0].rstrip())
    return out + wait_for_lines(task)


def wait_for_lines(task):
    """``WAIT-FOR: <wf>`` when the task names something to wait on, else nothing."""
    wf = task.get("wait-for", "")
    if wf and wf not in ("-", DASH, "none", "no"):
        return ["WAIT-FOR: %s" % wf]
    return []


def brief_line(task):
    """One-line summary for ``next --brief``."""
    r = task.get("review", "no")
    rv = "y" if r.strip().lower() in ("yes", "y") else "n"
    wf = task.get("wait-for", DASH)
    title = task.get("title", "-")[:100]
    return "%s | %s | %s | coder: %s | effort: %s | review: %s | wait-for: %s | title: %s" % (
        task.id, task.status, _run_agent(task.agent),
        task.get("coder", "-"), task.get("effort", "-"),
        rv, wf or DASH, title)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_show(a, root, path, text, explicit):
    _cnote(path, script="plan_edit.py", sub="show", root=root)
    if getattr(a, "gen_phase", None) is not None:
        gp = _gen_plan(root)
        gtext = read_text(gp)
        _cnote(gp, script="plan_edit.py", sub="show", root=root)
        pid = a.gen_phase or _genplan.first_open(gtext)
        if not pid:
            die("no open phase in GENERATION_PLAN.md")
        f = None
        for pl in _genplan.phase_lines(gtext):
            d = _genplan.fields(pl)
            if d["id"] == pid:
                f = d
                break
        if f is None:
            die("no '- %s ' line in GENERATION_PLAN.md" % pid)
        emit(["id: %s" % f["id"], "name: %s" % f["name"],
              "milestone: %s" % f["milestone"], "scope: %s" % f["scope"],
              "depends: %s" % f["depends"], "status: %s" % f["status"],
              "phase-end: %s" % f["phase_end"]])
        return 0
    lines, tasks, headings = parse(text)
    if a.section:
        idx = headings.get(a.section)
        if idx is None:
            if a.section in ("Milestone", "Approved"):      # header lines, not sections
                for ln in lines[:6]:
                    if ln.startswith(a.section + ":"):
                        sys.stdout.write(ln.rstrip() + "\n")
                        return 0
            die("no section '## %s'" % a.section)
        stop = len(lines)
        for name, i in headings.items():
            if i > idx:
                stop = min(stop, i)
        sys.stdout.write("\n".join(lines[idx:stop]).rstrip() + "\n")
        return 0
    if a.next:
        return cmd_next(a, root, path, text, explicit)
    if a.task:
        for t in tasks:
            if t.id == a.task:
                emit(show_entry(t, lines))
                return 0
        die("no task %s" % a.task)
    if a.tasks:
        out = []
        col4 = "title" if getattr(a, "titles", False) else "done-when"
        for t in tasks:
            row = "%s | %s | %s | %s" % (t.id, t.status, _run_agent(t.agent), t.get(col4, ""))
            out.append(row[:118])
        emit(out or ["(no tasks)"])
        return 0
    head = split_eol(lines[0])[0] if lines else ""
    out = [head.lstrip("# ").strip()]
    for raw in lines[:6]:
        body = split_eol(raw)[0]
        if body.startswith("Milestone:") or body.startswith("Approved:"):
            out.append(body.strip())
    counts = {}
    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
    out.append("Tasks: %d (%s)" % (len(tasks), ", ".join(
        "%s %d" % (s, counts[s]) for s in STATUSES if counts.get(s))))
    by_id = dict((t.id, t) for t in tasks)
    nxt = [t for t in tasks if t.status == "next" and deps_done(t, by_id)]
    out.append("Next: %s" % (nxt[0].id if nxt else "-"))
    emit(out)
    return 0


def cmd_next(a, root, path, text, explicit):
    lines, tasks, headings = parse(text)
    by_id = dict((t.id, t) for t in tasks)
    order = sorted(tasks, key=lambda t: (t.key(), t.index))
    brief = getattr(a, "brief", False)
    for t in order:
        if t.status == "next" and deps_done(t, by_id):
            if brief:
                emit([brief_line(t)] + wait_for_lines(t))
            else:
                emit(show_entry(t, lines))
            return 0
    for t in order:
        if t.status == "queued" and deps_done(t, by_id):
            _old_tl = lines[t.index]
            _anch = _anchor(lines, t.index)
            new = set_status_in_line(lines[t.index], "next")
            if new is None:
                die("cannot rewrite the status field of %s" % t.id)
            lines[t.index] = new
            save(root, path, lines, explicit)
            _cnote(path, script="plan_edit.py", sub="next",
                   removed=len(_old_tl), added=len(new), anchor=_anch, root=root)
            t.status = "next"
            if brief:
                emit([brief_line(t)] + wait_for_lines(t)
                     + ["PROMOTED: %s queued -> next" % t.id])
            else:
                emit(show_entry(t, lines) + ["PROMOTED: %s queued -> next" % t.id])
            return 0
    left = [t for t in tasks if t.status not in ("done", "superseded")]
    if not left:
        sys.stdout.write("NONE -- every task is done or superseded\n")
    else:
        why = ", ".join("%s(%s)" % (t.id, t.status) for t in left[:6])
        sys.stdout.write("NONE -- nothing runnable; waiting on: %s\n" % why)
    return 0


def cmd_set_status(a, root, path, text, explicit):
    lines, tasks, headings = parse(text)
    by_id = dict((t.id, t) for t in tasks)
    t = by_id.get(a.task)
    if t is None:
        die("no task %s" % a.task)
    if a.status not in STATUSES:
        die("status must be one of %s" % "|".join(STATUSES))
    locked = check_lock(root, path, text, explicit)
    if locked:
        die(locked)
    live = (os.path.normcase(os.path.abspath(path))
            == os.path.normcase(os.path.abspath(plan_path(root))))
    if live:
        tasks_dir = os.path.join(phase_dir(root), "current", "tasks")
    else:                                   # --file scratch plan: its own tasks/, if any
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "tasks")
    if a.status == "done" and (live or os.path.isdir(tasks_dir)):
        summary = os.path.join(tasks_dir, "%s.md" % t.id)
        if not os.path.isfile(summary):
            die("%s has no summary at %s" % (t.id, rel(root, summary)))
        ok = False
        for line in read_text(summary).split("\n"):
            if split_eol(line)[0].lstrip().startswith("Verified:"):
                ok = True
                break
        if not ok:
            die("%s has no 'Verified:' line (run the task's verify: command first)"
                % rel(root, summary))
    old = t.status
    if old == a.status:
        sys.stdout.write("%s already %s\n" % (t.id, a.status))
        return 0
    _old_tl = lines[t.index]
    _anch = _anchor(lines, t.index)
    new = set_status_in_line(lines[t.index], a.status)
    if new is None:
        die("cannot rewrite the status field of %s" % t.id)
    lines[t.index] = new
    _cl = append_change(lines, headings, a.by, "%s %s -> %s" % (t.id, old, a.status), a.why)
    save(root, path, lines, explicit)
    _cnote(path, script="plan_edit.py", sub="set-status",
           removed=len(_old_tl), added=len(new) + len(_cl), anchor=_anch, root=root)
    sys.stdout.write("%s: %s -> %s\n" % (t.id, old, a.status))
    return 0


def cmd_append_change(a, root, path, text, explicit):
    lines, tasks, headings = parse(text)
    locked = check_lock(root, path, text, explicit)
    if locked:
        die(locked)
    _cl = append_change(lines, headings, a.by, a.text)
    save(root, path, lines, explicit)
    _cnote(path, script="plan_edit.py", sub="append-change",
           added=len(_cl), root=root)
    sys.stdout.write(_cl + "\n")
    return 0


# presets without a hard rung: mirrors pa/install/ladder.PRESETS where expert-fable is None
# (tools ship alone, no import from pa/)
_NO_HARD_RUNG = ("max5", "pro")


def _preset(root):
    return cfg(root).get("preset") or "max20"


def _agent_for(effort, preset=None):
    if preset in _NO_HARD_RUNG:
        return "expert-opus55"
    return "expert-fable" if (effort or "").strip() == "high" else "expert-opus55"  # was expert-fable-high


def _preset_refusal(preset, tid, effort, agent):
    """One line when a task line needs the hard rung the preset lacks, else None."""
    if preset not in _NO_HARD_RUNG:
        return None
    if (effort or "").strip() == "high":
        what = "effort: high"
    elif agent in ("expert-fable", "expert-fable-high"):
        what = "agent %s" % agent
    else:
        return None
    return "preset %s has no hard rung: %s carries %s; mark it medium or split it" % (preset, tid, what)


# effort -> the agents that may carry it; expert-fable-high was the opt-in a task line named (retired 3.9.5 T15)
_AGENTS_FOR_EFFORT = {"high": ("expert-fable", "expert-fable-high")}
# retired agent -> the agent that runs a line naming it (lint accepts it with a note)
_RETIRED_AGENTS = {"expert-fable-high": "expert-fable"}


def _run_agent(agent):
    """The agent that actually runs a task line's agent column."""
    return _RETIRED_AGENTS.get(agent, agent)


def _check_coder(coder):
    """--coder takes any string; a retired or unknown value is refused in one line."""
    if coder in _LEGACY_CODERS:
        die("coder '%s' is retired: use opus55 or none" % coder)
    if coder and coder not in CODERS:
        die("unknown coder '%s': use opus55 or none" % coder)


def cmd_reopen(a, root, path, text, explicit):
    _check_coder(a.coder)
    lines, tasks, headings = parse(text)
    by_id = dict((t.id, t) for t in tasks)
    parent = by_id.get(a.task)
    if parent is None:
        die("no task %s" % a.task)
    locked = check_lock(root, path, text, explicit)
    if locked:
        die(locked)
    _old_tl = lines[parent.index]
    _anch = _anchor(lines, parent.index)
    kids = [t for t in tasks if t.id.startswith(parent.id + ".")]
    k = 1 + max([id_key(t.id)[-1] for t in kids] or [0])
    new_id = "%s.%d" % (parent.id, k)
    fields = dict(parent.fields)
    fields["title"] = a.title
    fields["done-when"] = a.done_when
    if a.coder:
        fields["coder"] = a.coder
    if a.effort:
        fields["effort"] = a.effort
    if a.files:
        fields["files"] = a.files
    if a.reads:
        fields["reads"] = a.reads
    if a.verify:
        fields["verify"] = a.verify
    fields["deps"] = fields.get("deps", DASH)
    preset = _preset(root)
    agent = _agent_for(fields.get("effort"), preset)
    refusal = _preset_refusal(preset, new_id, fields.get("effort"), agent)
    if refusal:
        die(refusal)
    tl = render_task(new_id, "next", agent, fields)
    at = task_block_end(lines, tasks, parent)
    lines.insert(at + 1, tl)
    _added = len(tl)
    _removed = 0
    if parent.status != "superseded":
        pos = parent.index
        new = set_status_in_line(lines[pos], "superseded")
        if new is not None:
            _removed = len(_old_tl)
            _added += len(new)
            lines[pos] = new
    lines, tasks2, headings = parse("\n".join(lines))
    _cl = append_change(lines, headings, a.by,
                        "reopened %s as %s" % (parent.id, new_id), a.title)
    _added += len(_cl)
    save(root, path, lines, explicit)
    _cnote(path, script="plan_edit.py", sub="reopen",
           removed=_removed, added=_added, anchor=_anch, root=root)
    sys.stdout.write("%s created (status next, agent %s)\n" % (new_id, agent))
    return 0


def cmd_add_task(a, root, path, text, explicit):
    _check_coder(a.coder)
    lines, tasks, headings = parse(text)
    if headings.get("Tasks") is None:
        die("no '## Tasks' section")
    locked = check_lock(root, path, text, explicit)
    if locked:
        die(locked)
    ints = [id_key(t.id)[0] for t in tasks]
    new_id = "T%d" % (1 + max(ints or [0]))
    fields = {
        "coder": a.coder or "opus55",
        "effort": a.effort or "medium",
        "title": a.title,
        "files": a.files or DASH,
        "done-when": a.done_when,
        "verify": a.verify or DASH,
        "reads": a.reads or DASH,
        "deps": a.deps or DASH,
        "est-ctx": a.est_ctx or DASH,
        "review": a.review or "no",
        "wait-for": a.wait_for or DASH,
    }
    preset = _preset(root)
    refusal = _preset_refusal(preset, new_id, fields["effort"], None)
    if refusal:
        die(refusal)
    line = render_task(new_id, "queued", _agent_for(fields["effort"], preset), fields)
    at = None
    if a.after:
        for t in tasks:
            if t.id == a.after:
                at = task_block_end(lines, tasks, t)
        if at is None:
            die("no task %s to insert after" % a.after)
    else:
        at = max([t.end for t in tasks] or [headings["Tasks"]])
    _anch = ((len(lines[at]) if at >= 0 else 0) +
             (len(lines[at + 1]) if at + 1 < len(lines) else 0))
    lines.insert(at + 1, line)
    lines, tasks2, headings = parse("\n".join(lines))
    _cl = append_change(lines, headings, a.by, "added %s" % new_id, a.title)
    save(root, path, lines, explicit)
    _cnote(path, script="plan_edit.py", sub="add-task",
           added=len(line) + len(_cl), anchor=_anch, root=root)
    sys.stdout.write("%s added (status queued)\n" % new_id)
    return 0


TRIAGE_RE = re.compile(r"^-\s+(\S+?):\s+(T[0-9]+(?:\.[0-9]+)*|postpone:\s*(\S+)|drop)(?:\s+--\s+.*)?$")
DISC_CUM_HEADER = "# Discussions -- cumulative\n# phase | id | topic | date | status | path\n"


def triage_lines(lines, headings):
    """-> ([(line no, body)] non-comment lines of `## Triage`, or None without the section)."""
    start = headings.get("Triage")
    if start is None:
        return None
    stop = min([i for i in headings.values() if i > start] or [len(lines)])
    out, in_comment = [], False
    for i in range(start + 1, stop):
        body = split_eol(lines[i])[0].strip()
        if in_comment or body.startswith("<!--"):
            in_comment = "-->" not in body
            continue
        if body:
            out.append((i, body))
    return out


def _triage_status(m, phase):
    """TRIAGE_RE match -> the index status it writes."""
    if m.group(3):
        return "deferred:%s" % m.group(3)
    if m.group(2) == "drop":
        return "dropped"
    return "planned:%s/%s" % (phase, m.group(2))


def _triage_writeback(root, phase, decisions, items):
    """Write each `## Triage` decision to the discussion index row holding its id; a PhaseEnd
    item (P<phase>-<k>) gets a new DISCUSSION_INDEX.md row. -> lines for stdout."""
    pe_dir = phase_dir(root)
    rows = _genplan.index_rows(pe_dir)
    by_path = {}
    for p, i, cells, col in rows:
        if cells[col] in decisions:
            by_path.setdefault(p, []).append((i, col, decisions[cells[col]]))
    done, out = set(), []
    for p, edits in by_path.items():
        plines = read_text(p).split("\n")
        for i, col, status in edits:
            body, eol = split_eol(plines[i])
            parts = body.split("|")
            rid = parts[col].strip()
            _old = len(plines[i])
            parts[col + 3] = " %s " % status
            plines[i] = "|".join(parts) + eol
            write_text(p, "\n".join(plines))
            _cnote(p, script="plan_edit.py", sub="approve",
                   removed=_old, added=len(plines[i]), anchor=_anchor(plines, i), root=root)
            done.add(rid)
            out.append("triage: %s -> %s" % (rid, status))
    cum = os.path.join(pe_dir, "DISCUSSION_INDEX.md")
    for it in items:
        if it["kind"] != "phaseend" or it["id"] in done or it["id"] not in decisions:
            continue
        ctext = read_text(cum) if os.path.isfile(cum) else DISC_CUM_HEADER
        if ctext and not ctext.endswith("\n"):
            ctext += "\n"
        row = "%s | %s | %s | %s | %s | %s" % (phase, it["id"], it["text"].replace("|", "/"),
                                               today(), decisions[it["id"]], rel(root, it["pe"]))
        clines = (ctext + row).split("\n")
        write_text(cum, ctext + row + "\n")
        _cnote(cum, script="plan_edit.py", sub="approve",
               removed=0, added=len(row), anchor=_anchor(clines, len(clines) - 1), root=root)
        out.append("triage: %s -> %s (new row)" % (it["id"], decisions[it["id"]]))
    return out


def cmd_approve(a, root, path, text, explicit):
    if is_approved(text):
        die("already approved (a new plan replaces it via REPLAN.md)")
    lines, tasks, headings = parse(text)
    preset = _preset(root)
    for t in tasks:
        refusal = _preset_refusal(preset, t.id, t.get("effort"), t.agent)
        if refusal:
            die(refusal)
    # ## Triage: every deferred item due for this phase needs one line; decisions go to the index
    m1 = re.match(r"^#\s*Phase\s+(\d+(?:\.\d+)*)", split_eol(lines[0])[0] if lines else "")
    phase = m1.group(1) if m1 else None
    gp = os.path.join(root, "GENERATION_PLAN.md")
    items = _genplan.deferred_items(phase_dir(root), read_text(gp) if os.path.isfile(gp) else "",
                                    phase)
    decisions = {}
    for _i, body in triage_lines(lines, headings) or []:
        tm = TRIAGE_RE.match(body)
        if tm:
            decisions[tm.group(1)] = _triage_status(tm, phase)
    missing = [it["id"] for it in items if it["id"] not in decisions]
    if missing:
        die("## Triage has no line for deferred item(s): %s" % ", ".join(missing))
    _old_line = ""
    stamp = "Approved: %s   Planner: %s   Plan-hash: " % (today(), a.planner)
    at = None
    for i, raw in enumerate(lines):
        if split_eol(raw)[0].startswith("Approved:"):
            at = i
            _old_line = lines[at]
            break
    if at is None:
        for i, raw in enumerate(lines):
            if split_eol(raw)[0].startswith("Milestone:"):
                at = i + 1
                lines.insert(at, stamp)
                break
        if at is None:
            lines.insert(1, stamp)
            at = 1
    else:
        lines[at] = stamp
    _anch = _anchor(lines, at)
    text2 = "\n".join(lines)
    digest = plan_hash(text2)
    _new_line = stamp + digest
    lines[at] = _new_line
    text3 = "\n".join(lines)
    write_text(path, text3)
    write_sha(root, path, text3)
    _cnote(path, script="plan_edit.py", sub="approve",
           removed=len(_old_line), added=len(_new_line), anchor=_anch, root=root)
    sys.stdout.write("approved %s\nPlan-hash: %s\n.run/plan.sha written\n"
                     % (today(), digest))
    tri = _triage_writeback(root, phase, decisions, items)
    if tri:
        sys.stdout.write("\n".join(tri) + "\n")
    return 0


def cmd_lint(a, root, path, text, explicit):
    problems = []
    warns = []
    notes = []
    lines, tasks, headings = parse(text)
    if not lines or not split_eol(lines[0])[0].startswith("# Phase"):
        problems.append("line 1: expected '# Phase <N> -- <name>'")
    if not re.search(r"(?m)^Milestone:", text):
        problems.append("header: no 'Milestone:' line")
    if headings.get("Tasks") is None:
        problems.append("no '## Tasks' section")
    if not tasks:
        problems.append("no task lines")
    tri = triage_lines(lines, headings)
    if tri is None and not is_approved(text):      # approved plans predating ## Triage still lint
        problems.append("no '## Triage' section (one line per seed Deferred: id, or '- (none)')")
    for i, body in tri or []:
        if body != "- (none)" and not TRIAGE_RE.match(body):
            problems.append("line %d: triage line not '- <id>: T<n>|postpone: <phase>|drop "
                            "[-- reason]' or '- (none)'" % (i + 1))
    seen = {}
    preset = _preset(root)
    for t in tasks:
        n = t.index + 1
        refusal = _preset_refusal(preset, t.id, t.get("effort"), t.agent)
        if refusal:
            problems.append("line %d: %s" % (n, refusal))
        if t.id in seen:
            problems.append("line %d: duplicate id %s (ids are immutable)" % (n, t.id))
        seen[t.id] = t
        if t.status not in STATUSES:
            problems.append("line %d: %s bad status '%s' (%s)"
                            % (n, t.id, t.status, "|".join(STATUSES)))
        if not t.agent:
            problems.append("line %d: %s no agent field" % (n, t.id))
        for k in REQUIRED_KEYS:
            if k not in t.fields:
                problems.append("line %d: %s missing '%s:'" % (n, t.id, k))
        if "title" not in t.fields:
            # 3.11 T13 (developer): a warning, never a failure, so approvals and the bench's grading stay as they were
            warns.append("line %d: %s no 'title:' (the statusline and the expert's label show it)" % (n, t.id))
        coder = t.get("coder")
        if coder and coder not in CODERS and coder not in _LEGACY_CODERS:
            problems.append("line %d: %s coder '%s' not in %s"
                            % (n, t.id, coder, "|".join(CODERS)))
        eff = t.get("effort")
        # agent/effort consistency: high -> expert-fable (or the retired expert-fable-high), else expert-opus55
        if eff and (
                t.agent.startswith("expert-fable") or t.agent.startswith("expert-opus")
        ) and t.agent not in _AGENTS_FOR_EFFORT.get(eff, (_agent_for(eff, preset),)):
            warns.append("line %d: %s effort '%s' but agent '%s'" % (n, t.id, eff, t.agent))
        if t.agent in _RETIRED_AGENTS:
            notes.append("note: %s names the retired %s; it runs %s"
                         % (t.id, t.agent, _RETIRED_AGENTS[t.agent]))
        for k in t.fields:
            if k not in KNOWN_KEYS:
                warns.append("line %d: %s unknown key '%s'" % (n, t.id, k))
    for t in tasks:
        for d in dep_ids(t):
            if d not in seen:
                problems.append("line %d: %s dangling dep '%s'" % (t.index + 1, t.id, d))
    locked = check_lock(root, path, text, explicit)
    if locked:
        problems.append("header: " + locked)
    out = ["ERROR " + p for p in problems] + ["WARN  " + w for w in warns] + notes
    if problems:
        out.append("FAILED: %d problem(s), %d warning(s)" % (len(problems), len(warns)))
        emit(out)
        return 1
    if warns or notes:
        emit(out + ["OK"])
        return 0
    sys.stdout.write("OK\n")
    return 0


def _gen_plan(root):
    p = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(p):
        die("no GENERATION_PLAN.md at the project root")
    return p


def cmd_gen(a, root, path, text, explicit):
    want = "closed" if a.cmd == "gen-close" else "open"
    gp = _gen_plan(root)
    gtext = read_text(gp)
    glines = gtext.split("\n")
    target = None
    for pl in _genplan.phase_lines(gtext):
        if _genplan.fields(pl)["id"] == a.phase:
            target = pl
            break
    if target is None:
        die("no '- %s ' line in GENERATION_PLAN.md" % a.phase)
    hit = glines.index(target)
    _old_gl = glines[hit]
    _anch = _anchor(glines, hit)
    body, eol = split_eol(glines[hit])
    if re.search(r"status:\s*\S+", body):
        body = re.sub(r"(status:\s*)(\S+)", lambda m: m.group(1) + want, body, count=1)
    else:
        body = body.rstrip() + " | status: " + want
    glines[hit] = body + eol
    _, _, gheads = parse("\n".join(glines))
    _cl = append_change(glines, gheads, a.by, "phase %s status -> %s" % (a.phase, want))
    write_text(gp, "\n".join(glines))
    _cnote(gp, script="plan_edit.py", sub=a.cmd,
           removed=len(_old_gl), added=len(glines[hit]) + len(_cl),
           anchor=_anch, root=root)
    sys.stdout.write("%s: status -> %s\n" % (a.phase, want))
    return 0


SECTIONS = ("Context", "Rationale", "Interfaces", "Cookbook", "Research",
            "Developer decides", "Triage", "Tasks", "Risks", "Changes")


def cmd_grammar(a, root, path, text, explicit):
    """The template's task-line grammar comment block, then the plan's section list."""
    tpath = os.path.join(root, "templates", "PHASE_PLAN.template.md")
    if not os.path.isfile(tpath):
        die("no templates/PHASE_PLAN.template.md at the project root")
    ttext = read_text(tpath)
    _cnote(tpath, script="plan_edit.py", sub="grammar", root=root)
    m = re.search(r"(?s)<!--\s*Task-line grammar.*?-->", ttext)
    if not m:
        die("no Task-line grammar comment in templates/PHASE_PLAN.template.md")
    out = m.group(0).split("\n")
    out += ["", "Sections: " + ", ".join(SECTIONS)]
    sys.stdout.write("\n".join(out) + "\n")
    return 0


# --------------------------------------------------------------------------- #
def cmd_from_draft(a, root, path, text, explicit):
    """Write PHASE_PLAN.md from a planner draft: the only write path before approval.

    Refuses while an approved plan exists unless REPLAN.md is present (then the old
    plan is archived as PHASE_PLAN.v<k>.md first). Lints the result.
    """
    src = os.path.abspath(a.draft)
    if not os.path.isfile(src):
        die("no draft at %s" % rel(root, src))
    draft = read_text(src)
    cur = os.path.dirname(path)
    if os.path.isfile(path) and is_approved(read_text(path)):
        if not os.path.isfile(os.path.join(cur, "REPLAN.md")):
            die("an approved plan exists and no REPLAN.md is present; "
                "structural changes go through REPLAN.md -> planner-phase")
        k = 1
        while os.path.isfile(os.path.join(cur, "PHASE_PLAN.v%d.md" % k)):
            k += 1
        archived = os.path.join(cur, "PHASE_PLAN.v%d.md" % k)
        os.replace(path, archived)
        sys.stdout.write("archived %s\n" % rel(root, archived))
    write_text(path, draft)
    try:
        os.remove(sha_file(root))
    except OSError:
        pass
    sys.stdout.write("wrote %s from %s\n" % (rel(root, path), rel(root, src)))
    a.path = None
    return cmd_lint(a, root, path, draft, False)


def build_parser():
    p = argparse.ArgumentParser(
        prog="plan_edit.py",
        description="The only writer of phase-ends/current/PHASE_PLAN.md.")
    p.add_argument("--file", help="operate on this plan instead of the project's")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="header summary; or --tasks/--next/--task/--section/--gen-phase")
    s.add_argument("--tasks", action="store_true")
    s.add_argument("--titles", action="store_true", help="with --tasks: show title instead of done-when")
    s.add_argument("--next", action="store_true")
    s.add_argument("--task")
    s.add_argument("--section",
                   help="a '## <name>' section; Milestone or Approved print that header line")
    s.add_argument("--gen-phase", dest="gen_phase", nargs="?", const="",
                    help="GENERATION_PLAN.md phase fields (default: first open phase)")

    s = sub.add_parser("next", help="first runnable task (promotes a queued one)")
    s.add_argument("--brief", action="store_true",
                   help="one-line summary: id | status | agent | coder | effort | review | wait-for | title")

    s = sub.add_parser("set-status", help="change one task's status",
                       description="done needs tasks/<id>.md with a 'Verified:' line; a --file plan "
                                   "other than the live one uses <its dir>/tasks/, no gate if that dir is absent.")
    s.add_argument("task")
    s.add_argument("status")
    s.add_argument("--by", default="router")
    s.add_argument("--why")

    s = sub.add_parser("append-change", help="append a ## Changes line")
    s.add_argument("text")
    s.add_argument("--by", default="router")

    s = sub.add_parser("reopen", help="supersede T3 and create T3.1 (status next)")
    s.add_argument("task")
    s.add_argument("--title", required=True)
    s.add_argument("--done-when", dest="done_when", required=True)
    s.add_argument("--coder")
    s.add_argument("--effort")
    s.add_argument("--files")
    s.add_argument("--reads")
    s.add_argument("--verify")
    s.add_argument("--by", default="router")

    s = sub.add_parser("add-task", help="append a task with the next free integer id")
    s.add_argument("--after")
    s.add_argument("--title", required=True)
    s.add_argument("--done-when", dest="done_when", required=True)
    s.add_argument("--coder")
    s.add_argument("--effort")
    s.add_argument("--files")
    s.add_argument("--verify")
    s.add_argument("--reads")
    s.add_argument("--deps")
    s.add_argument("--est-ctx", dest="est_ctx")
    s.add_argument("--review")
    s.add_argument("--wait-for", dest="wait_for")
    s.add_argument("--by", default="planner")

    s = sub.add_parser("from-draft", help="write PHASE_PLAN.md from a planner draft (before approval; archives on REPLAN)")
    s.add_argument("draft")

    s = sub.add_parser("approve", help="stamp Approved/Planner/Plan-hash and lock the header")
    s.add_argument("--planner", required=True)

    s = sub.add_parser("lint", aliases=["validate"], help="check the plan's grammar")
    s.add_argument("path", nargs="?")

    for name in ("gen-close", "gen-open"):
        s = sub.add_parser(name, help="flip a GENERATION_PLAN.md phase status")
        s.add_argument("phase")
        s.add_argument("--by", default="router")

    sub.add_parser("grammar", help="the task-line grammar block and the plan's section list")
    return p


HANDLERS = {
    "show": cmd_show, "next": cmd_next, "set-status": cmd_set_status,
    "append-change": cmd_append_change, "reopen": cmd_reopen,
    "add-task": cmd_add_task, "approve": cmd_approve, "lint": cmd_lint,
    "validate": cmd_lint, "gen-close": cmd_gen, "gen-open": cmd_gen,
}


def main(argv=None):
    a = build_parser().parse_args(argv)
    root = find_root()
    explicit = False
    path = getattr(a, "file", None) or getattr(a, "path", None)
    if path:
        explicit = True
        path = os.path.abspath(path)
    else:
        path = plan_path(root)
    if a.cmd in ("gen-close", "gen-open"):
        return cmd_gen(a, root, path, "", explicit)
    if a.cmd == "from-draft":
        return cmd_from_draft(a, root, path, "", explicit)
    if a.cmd == "grammar":
        return cmd_grammar(a, root, path, "", explicit)
    if a.cmd == "show" and getattr(a, "gen_phase", None) is not None:
        return cmd_show(a, root, path, "", explicit)     # needs GENERATION_PLAN.md only
    text = load(path)
    return HANDLERS[a.cmd](a, root, path, text, explicit)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:                                   # one-line refusal
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
