#!/usr/bin/env python
"""status.py -- writes .run/status.json (the statusline's line 2 source).

Never writes cost or token fields (D38). Stdlib only; no imports from `pa/`.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import time

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KINDS = ("task", "handoff", "relaunch", "critic", "phase-end", "paused")
WAITING = ("REVIEW.md", "REPLAN.md", "question")
FIELDS = ("generation", "phase", "phase_name", "task", "task_title",
          "expert_agent_type", "coder", "kind", "attempt", "run_id",
          "started", "waiting", "note", "router_session", "updated",
          "resets_at", "resume_at", "resume_cron")
PAUSE_FIELDS = ("resets_at", "resume_at", "resume_cron")
WINDOW_GATE_PCT = 90          # five-hour window % at which the router pauses before a new task


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


def rel(root, path):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_path(root):
    return os.path.join(root, ".run", "status.json")


def load(root):
    try:
        with open(status_path(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    out = dict((k, None) for k in FIELDS)
    for k in FIELDS:
        if k in data:
            out[k] = data[k]
    return out


def save(root, data):
    data["updated"] = now()
    ordered = dict((k, data.get(k)) for k in FIELDS)
    write_text(status_path(root),
               json.dumps(ordered, ensure_ascii=False, indent=1) + "\n")
    return ordered


def plan_facts(root, task_id=None):
    """(generation, phase, phase_name, task_title) read from PHASE_PLAN.md."""
    path = os.path.join(phase_dir(root), "current", "PHASE_PLAN.md")
    gen = phase = name = title = None
    try:
        text = read_text(path)
    except Exception:
        return gen, phase, name, title
    lines = text.split("\n")
    if lines:
        m = re.match(r"^#\s*Phase\s+([^\s—-]+)\s*[—-]+\s*(.*?)\s*(?:\(implements.*)?$",
                     lines[0].strip())
        if m:
            phase, name = m.group(1), m.group(2).strip()
        g = re.search(r"phase\s+([0-9]+(?:\.[0-9]+)*)", lines[0])
        if g:
            gen = g.group(1)
    if task_id:
        for raw in lines:
            if re.match(r"^\s*-\s+" + re.escape(task_id) + r"\s*\|", raw):
                m = re.search(r"\|\s*title\s*:\s*([^|]+)", raw)
                if not m:
                    m = re.search(r"\|\s*done-when\s*:\s*([^|]+)", raw)
                if m:
                    title = m.group(1).strip()[:60]
                break
    return gen, phase, name, title


def cmd_set(a, root):
    data = load(root)
    gen, phase, name, title = plan_facts(root, a.task)
    data["generation"] = a.generation or gen or data.get("generation")
    data["phase"] = a.phase or phase or data.get("phase")
    data["phase_name"] = name or data.get("phase_name")
    data["task"] = a.task
    data["task_title"] = a.title or title
    data["expert_agent_type"] = a.agent
    data["coder"] = a.coder
    data["kind"] = a.kind
    data["attempt"] = a.attempt
    data["run_id"] = None
    data["started"] = now()
    data["note"] = a.note
    data["waiting"] = None
    # the session that stamps a task owns the phase (3.10 T28). 3.11 T20: Claude Code gives its
    # Bash commands CLAUDE_CODE_SESSION_ID; the name read before (CLAUDE_SESSION_ID) is never set,
    # so a developer session carrying the phase stayed "runs in another session"
    owner = data.get("router_session")
    data["router_session"] = a.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or owner
    for k in PAUSE_FIELDS:
        data[k] = None
    save(root, data)
    sys.stdout.write("status: %s %s %s attempt %s\n"
                     % (data["task"], data["expert_agent_type"], data["kind"],
                        data["attempt"]))
    if data["router_session"] != owner:
        sys.stdout.write("router_session=%s\n" % data["router_session"])
    return 0


def cmd_clear(a, root):
    data = load(root)
    for k in ("task", "task_title", "expert_agent_type", "coder", "kind",
              "attempt", "run_id", "started", "waiting", "note") + PAUSE_FIELDS:
        data[k] = None
    # phase fields follow the plan on disk: after a phase or generation close there is no
    # PHASE_PLAN.md, so they clear too (they were sticking as "1.2" during Gen 2 planning).
    gen, phase, name, _title = plan_facts(root)
    data["generation"], data["phase"], data["phase_name"] = gen, phase, name
    save(root, data)
    sys.stdout.write("status: cleared\n")
    return 0


def cmd_wait(a, root):
    if a.what not in WAITING:
        die("wait takes one of %s" % "|".join(WAITING))
    data = load(root)
    data["waiting"] = a.what
    if a.note:
        data["note"] = a.note
    save(root, data)
    sys.stdout.write("status: waiting on developer (%s)\n" % a.what)
    return 0


def _ledger_dir():  # mirrors pa/paths.py home() + usage-ledger; no pa/ import (stdlib only)
    env = os.environ.get("PA_LEDGER_DIR")
    if env:
        return env
    h = os.environ.get("USERPROFILE") if os.name == "nt" else None
    return os.path.join(os.path.abspath(h or os.path.expanduser("~")), ".claude", "usage-ledger")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _epoch(value):  # number, numeric string or ISO-8601 -> epoch seconds, else None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in "Zz" else text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _five_hour(t):  # -> (pct, resets_at epoch) when the usage-API cache is fresh and live, else None
    d = _ledger_dir()
    cache = _read_json(os.path.join(d, "usage_api.json"))
    if not isinstance(cache, dict):
        return None
    poll = ((_read_json(os.path.join(d, "config.json")) or {}).get("usage_api") or {}).get("poll_min")
    poll = poll if isinstance(poll, (int, float)) and not isinstance(poll, bool) else 15
    ts = _epoch(cache.get("ts"))
    if ts is None or not (0 <= t - ts <= 2 * 60 * poll):     # pa/statusline.py freshness rule
        return None
    win = (cache.get("windows") or {}).get("five_hour") if isinstance(cache.get("windows"), dict) else None
    if not isinstance(win, dict):
        return None
    pct, resets = win.get("pct"), _epoch(win.get("resets_at"))
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or resets is None or resets <= t:
        return None
    return float(pct), resets


def cmd_window_gate(a, root):
    """Exit 0 = go; exit 3 = pause until the five-hour window resets (router schedules `continue`)."""
    t = time.time()
    got = _five_hour(t)
    if got is None or got[0] < WINDOW_GATE_PCT:
        data = load(root)
        if data.get("kind") == "paused":
            data["kind"] = None
            for k in PAUSE_FIELDS:
                data[k] = None
            save(root, data)
        sys.stdout.write("go: five-hour window at %d%%\n" % int(round(got[0])) if got
                         else "go: no fresh usage data\n")
        return 0
    pct, resets = got
    reset = datetime.datetime.fromtimestamp(resets).astimezone()
    base = reset.replace(second=0, microsecond=0)
    resume = base + datetime.timedelta(minutes=2)
    if resume.minute in (0, 30):
        resume = base + datetime.timedelta(minutes=3)
    cron = "%d %d %d %d *" % (resume.minute, resume.hour, resume.day, resume.month)
    iso = lambda dt: dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = load(root)
    data["kind"] = "paused"
    data["resets_at"] = iso(reset)
    data["resume_at"] = iso(resume)
    data["resume_cron"] = cron
    save(root, data)
    sys.stdout.write("paused: five-hour window at %d%% until %s; resume scheduled %s; cron: %s\n"
                     % (int(round(pct)), reset.strftime("%H:%M"), resume.strftime("%H:%M"), cron))
    return 3


def _archive(root, src, prefix):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(phase_dir(root), "current", "discussions",
                       "%s-%s.md" % (prefix, stamp))
    d = os.path.dirname(dst)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    shutil.move(src, dst)
    return dst


DISC_INDEX_HEADER = ("# Discussions -- one line per record\n"
                     "# id | topic | date | status | path\n")      # discussion.py:22
PHASE_TOK = r"(?<![\d.])(\d+\.\d+(?:\.\d+)*)(?!\.?\d)"


def _inbox_bullets(text):
    """Top-level `- ` bullets outside comments; indented non-bullet lines join (C0012)."""
    out, cur = [], None
    for raw in re.sub(r"<!--.*?-->", "", text, flags=re.S).split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("- "):
            if cur is not None:
                out.append(cur)
            cur = line[2:].strip()
        elif cur is not None and line[:1] in (" ", "\t") and line.strip() \
                and not line.strip().startswith("- "):
            cur += " " + line.strip()
        elif cur is not None and (not line.strip() or not line[:1] in (" ", "\t")):
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return [b for b in out if b and not re.match(r"^<.*>$", b)]   # template placeholder


def inbox_pending(text):
    """Real bullets of an INBOX.md text: `_inbox_bullets` minus the template placeholder."""
    return [b for b in _inbox_bullets(text) if not re.match(r"^<.*>$", b)]


def _route_context(root):
    """-> (current id, known ids in plan order, open ids, G) or None without a plan."""
    try:
        gtext = read_text(os.path.join(root, "GENERATION_PLAN.md"))
    except Exception:
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _genplan
    rows = [_genplan.fields(l) for l in _genplan.phase_lines(gtext)]
    ids = [f["id"] for f in rows]
    opens = [f["id"] for f in rows if f["status"] == "open"]
    cur = None
    try:
        first = read_text(os.path.join(phase_dir(root), "current", "PHASE_PLAN.md")).split("\n")[0]
        m = re.match(r"^#\s*Phase\s+(\d+(?:\.\d+)+)", first)
        cur = m.group(1) if m else None
    except Exception:
        pass
    cur = cur or _genplan.first_open(gtext)
    if not cur:
        return None
    return cur, ids, opens, int(cur.split(".")[0])


def _route(bullet, ctx):
    """-> ('deferred', P) | ('now', note) for one bullet (INBOX.template.md rule)."""
    if ctx is None:
        return "now", ""
    cur, ids, opens, g = ctx
    ci = ids.index(cur) if cur in ids else len(ids)

    def phase_target(pid):
        if pid not in ids:
            return "now", " (unknown phase %s)" % pid
        return ("deferred", pid) if ids.index(pid) > ci else ("now", "")

    m = re.search(r"(?:->|→)\s*(?:(gen)\s*(\d+)\b|" + PHASE_TOK + ")", bullet, re.I)
    if m:
        if m.group(1):
            n = int(m.group(2))
            return ("deferred", "gen%d" % n) if n > g else ("now", "")
        return phase_target(m.group(3))
    toks = re.findall(PHASE_TOK, bullet)
    for t in toks:
        if t in ids and ids.index(t) > ci:
            return "deferred", t
    if any(t in ids for t in toks):
        return "now", ""  # a resolved phase id wins over a passing Gen-N mention
    m = re.search(r"\bgen\s*(\d+)\b", bullet, re.I)
    if m and int(m.group(1)) > g:
        return "deferred", "gen%s" % int(m.group(1))
    for t in toks:
        if t not in ids:
            return "now", " (unknown phase %s)" % t
    if not toks and re.search(r"\blater\b", bullet, re.I):
        nxt = [p for p in opens if ids.index(p) > ci]
        return "deferred", (nxt[0] if nxt else "gen%d" % (g + 1))
    return "now", ""


def _index_rows(root, dst, bullets):
    """Append one `I<n>` row per bullet to discussions/INDEX.md; -> route lines."""
    idx = os.path.join(phase_dir(root), "current", "discussions", "INDEX.md")
    cum = os.path.join(phase_dir(root), "DISCUSSION_INDEX.md")
    n = 0
    for path, col in ((idx, 0), (cum, 1)):
        try:
            lines = read_text(path).split("\n")
        except Exception:
            continue
        for l in lines:
            parts = [c.strip() for c in l.split("|")]
            m = re.match(r"^I(\d+)$", parts[col]) if len(parts) > col else None
            if m:
                n = max(n, int(m.group(1)))
    ctx = _route_context(root)
    day = datetime.date.today().strftime("%Y-%m-%d")
    rows, routes = [], []
    for b in bullets:
        n += 1
        kind, val = _route(b, ctx)
        status = "deferred:%s" % val if kind == "deferred" else "now"
        rows.append("I%d | %s | %s | %s | %s"
                    % (n, b.replace("|", "/")[:120], day, status, rel(root, dst)))
        routes.append("route: I%d -> %s" % (n, val if kind == "deferred" else "now" + val))
    if rows:
        text = read_text(idx) if os.path.isfile(idx) else DISC_INDEX_HEADER
        if text and not text.endswith("\n"):
            text += "\n"
        write_text(idx, text + "\n".join(rows) + "\n")
    return routes


MARK_STATUS = r"^(?:planned:\S+/T[\d.]+|deferred:\S+|dropped|now)$"


def unresolved_rows(index_path, plan_path):
    """-> [(id, topic)]: `I<n>` rows with status `now` named by no `## Tasks` `- T` line
    and no `## Changes` line of the plan (a missing plan leaves every `now` row pending)."""
    try:
        rows = read_text(index_path).split("\n")
    except Exception:
        return []
    try:
        plan = read_text(plan_path).split("\n")
    except Exception:
        plan = []
    refs, sec = [], None
    for l in plan:
        if l.startswith("## "):
            sec = l[3:].strip()
        elif (sec == "Tasks" and re.match(r"^\s*-\s+T", l)) or sec == "Changes":
            refs.append(l)
    ref = "\n".join(refs)
    out = []
    for l in rows:
        parts = [c.strip() for c in l.split("|")]
        if len(parts) < 4 or not re.match(r"^I\d+$", parts[0]) or parts[3] != "now":
            continue
        if not re.search(r"\b%s\b" % parts[0], ref):
            out.append((parts[0], parts[1]))
    return out


def _pending_lines(root):
    cur = os.path.join(phase_dir(root), "current")
    return ["pending: %s (now, no task or Changes line names it; status.py inbox-mark %s "
            "planned:<phase>/T<n>|deferred:<p>|dropped) -- %s" % (i, i, t[:60])
            for i, t in unresolved_rows(os.path.join(cur, "discussions", "INDEX.md"),
                                        os.path.join(cur, "PHASE_PLAN.md"))]


def cmd_inbox_mark(a, root):
    if not re.match(MARK_STATUS, a.status):
        die("status must be planned:<phase>/T<n>|deferred:<p>|dropped|now, got %r" % a.status)
    idx = os.path.join(phase_dir(root), "current", "discussions", "INDEX.md")
    try:
        lines = read_text(idx).split("\n")
    except Exception:
        die("no %s" % rel(root, idx))
    hit = False
    for i, l in enumerate(lines):
        parts = [c.strip() for c in l.split("|")]
        if len(parts) >= 5 and parts[0] == a.id:
            parts[3] = a.status
            lines[i] = " | ".join(parts)
            hit = True
    if not hit:
        die("no row %s in %s" % (a.id, rel(root, idx)))
    write_text(idx, "\n".join(lines))
    sys.stdout.write("marked: %s -> %s\n" % (a.id, a.status))
    return 0


def cmd_inbox_consume(a, root):
    src = os.path.join(phase_dir(root), "current", "INBOX.md")
    pending = _pending_lines(root)             # before _index_rows: new rows are not flagged
    for p in pending:
        sys.stdout.write(p + "\n")
    if not os.path.isfile(src):
        sys.stdout.write("none\n")
        return 0
    text = read_text(src)
    # only the template header (its comment block, headings, blanks, placeholder) = nothing to consume
    if not inbox_pending(text):
        sys.stdout.write("none\n")
        return 0
    body = text.rstrip("\n").split("\n")
    if len(body) > 34:
        body = body[:33] + ["... (%d more lines)" % (len(body) - 33)]
    bullets = _inbox_bullets(text)
    dst = _archive(root, src, "inbox")
    routes = _index_rows(root, dst, bullets)
    tmpl = os.path.join(root, "templates", "INBOX.template.md")
    if os.path.isfile(tmpl):
        write_text(src, read_text(tmpl))      # the inbox stays in place, emptied to its header
    data = load(root)
    if data.get("waiting") == "REVIEW.md":
        data["waiting"] = None
    save(root, data)
    sys.stdout.write("\n".join(body) + "\n")
    sys.stdout.write("archived: %s\n" % rel(root, dst))
    for r in routes:
        sys.stdout.write(r + "\n")
    return 0


def cmd_review_done(a, root):
    src = os.path.join(phase_dir(root), "current", "REVIEW.md")
    data = load(root)
    data["waiting"] = None
    save(root, data)
    if os.path.isfile(src):
        dst = _archive(root, src, "review")
        sys.stdout.write("review consumed -> %s\n" % rel(root, dst))
    else:
        sys.stdout.write("review consumed (no REVIEW.md)\n")
    return 0


def cmd_replan_written(a, root):
    src = os.path.join(phase_dir(root), "current", "REPLAN.md")
    if not os.path.isfile(src):
        die("no REPLAN.md at %s" % rel(root, src))
    data = load(root)
    data["waiting"] = "REPLAN.md"
    save(root, data)
    sys.stdout.write("status: waiting on developer (REPLAN.md)\n")
    return 0


def cmd_show(a, root):
    data = load(root)
    for k in FIELDS:
        sys.stdout.write("%-17s %s\n" % (k, data.get(k)))
    return 0


def cmd_agent_alive(a, root):
    """Print alive/stale/unknown for a run based on its transcript file age."""
    run_id = a.run_id
    # try the ledger's agent_runs.transcript_path first
    transcript = None
    ledger_dir = os.environ.get("PA_LEDGER_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "usage-ledger")
    db_path = os.path.join(ledger_dir, "usage-ledger.db")
    if os.path.isfile(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path, timeout=2)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT transcript_path FROM agent_runs WHERE run_id=?",
                (run_id,)).fetchone()
            conn.close()
            if row and row["transcript_path"]:
                transcript = row["transcript_path"]
        except Exception:
            pass
    # fallback: running.json
    if not transcript:
        rpath = os.path.join(ledger_dir, "running.json")
        if os.path.isfile(rpath):
            try:
                with open(rpath, encoding="utf-8") as fh:
                    rdoc = json.load(fh)
                for _sid, sess in (rdoc.get("sessions") or {}).items():
                    for aid, ag in (sess.get("agents") or {}).items():
                        if aid == run_id and ag.get("transcript_path"):
                            transcript = ag["transcript_path"]
                            break
                    if transcript:
                        break
            except Exception:
                pass
    if not transcript or not os.path.isfile(transcript):
        sys.stdout.write("unknown\n")
        return 0
    # read liveness_min from config
    cfg_path = os.path.join(ledger_dir, "config.json")
    liveness_min = 5
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cdoc = json.load(fh)
            lm = (cdoc.get("resume") or {}).get("liveness_min")
            if isinstance(lm, (int, float)) and lm > 0:
                liveness_min = lm
        except Exception:
            pass
    mtime = os.path.getmtime(transcript)
    age_s = max(0, datetime.datetime.now(datetime.timezone.utc).timestamp() - mtime)
    age_min = age_s / 60.0
    if age_min < 60:
        age_str = "%.0fm" % age_min
    else:
        age_str = "%.1fh" % (age_min / 60.0)
    if age_min <= liveness_min:
        sys.stdout.write("alive %s\n" % age_str)
    else:
        sys.stdout.write("stale %s\n" % age_str)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="status.py", description="Write .run/status.json (never cost fields).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="stamp the running task")
    s.add_argument("--task", required=True)
    s.add_argument("--agent", default="expert-opus55")
    s.add_argument("--coder")
    s.add_argument("--kind", choices=KINDS, default="task")
    s.add_argument("--attempt", type=int, default=1)
    s.add_argument("--title")
    s.add_argument("--phase")
    s.add_argument("--generation")
    s.add_argument("--note")
    s.add_argument("--session", help="the phase owner (default: CLAUDE_CODE_SESSION_ID, this session)")
    sub.add_parser("clear", help="clear the task fields")
    s = sub.add_parser("wait", help="mark the developer as the blocker")
    s.add_argument("what", help="|".join(WAITING))
    s.add_argument("--note")
    sub.add_parser("inbox-consume", help="print INBOX.md and archive it")
    s = sub.add_parser("inbox-mark", help="set one discussions/INDEX.md row's status")
    s.add_argument("id", help="I<n>")
    s.add_argument("status", help="planned:<phase>/T<n>|deferred:<p>|dropped|now")
    sub.add_parser("review-done", help="archive REVIEW.md and clear waiting")
    sub.add_parser("replan-written", help="mark REPLAN.md as waiting on the developer")
    sub.add_parser("show", help="print the current status.json")
    s = sub.add_parser("agent-alive", help="print alive/stale/unknown for a run_id")
    s.add_argument("run_id", help="the agent run id to check")
    sub.add_parser("window-gate", help="exit 0 go / exit 3 pause: five-hour window >= %d%%%%"
                   % WINDOW_GATE_PCT)
    return p


HANDLERS = {"set": cmd_set, "clear": cmd_clear, "wait": cmd_wait,
            "inbox-consume": cmd_inbox_consume, "inbox-mark": cmd_inbox_mark,
            "review-done": cmd_review_done,
            "replan-written": cmd_replan_written, "show": cmd_show,
            "agent-alive": cmd_agent_alive, "window-gate": cmd_window_gate}


def main(argv=None):
    a = build_parser().parse_args(argv)
    return HANDLERS[a.cmd](a, find_root())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
