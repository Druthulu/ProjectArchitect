#!/usr/bin/env python
"""launch.py -- the only way a PA3 session starts.

detect_state -> preflight -> write_seed -> build_cmd -> launch.
Stdlib only; no imports from `pa/` (it may import plan_edit.py, its neighbour).
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_edit as PE                                          # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MIN_CC = (2, 1, 270)
FABLE = "claude-fable-5-1[1m]"
SESSION = "pa-session"                    # the one main-thread agent; router/planner/review are its modes
SONNET = "claude-sonnet-5[1m]"            # the pa-session model (mechanical router); planners and experts run Opus 5.5 / Fable per agent file
PLANNER_LABEL = "claude-opus-5-5/medium"  # was claude-fable-5-1/medium (T18, 2026-09-24), before that /xhigh; planner-gen / planner-phase run as medium subagents of pa-session
MODES = {
    "planner-gen":   {"agent": SESSION, "model": SONNET, "effort": "medium", "perm": None},
    "planner-phase": {"agent": SESSION, "model": SONNET, "effort": "medium", "perm": None},
    "review":        {"agent": SESSION, "model": SONNET, "effort": "medium", "perm": None},
    "router":        {"agent": SESSION, "model": SONNET, "effort": "medium", "perm": "auto"},
    "plain":         {"agent": "plain", "model": FABLE, "effort": "high",   "perm": None},
}


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def rel(root, path):
    return PE.rel(root, path)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
def gen_phases(root):
    """[(id, name, status, raw)] from GENERATION_PLAN.md '## Phases'."""
    path = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(path):
        return None
    out = []
    inside = False
    for raw in PE.read_text(path).split("\n"):
        body = PE.split_eol(raw)[0]
        if body.startswith("## "):
            inside = body[3:].strip().lower().startswith("phases")
            continue
        if not inside:
            continue
        m = re.match(r"^\s*-\s+([0-9]+(?:\.[0-9]+)*)\s+([^|]*)", body)
        if m:
            st = re.search(r"status:\s*(\S+)", body)
            out.append((m.group(1), m.group(2).strip(),
                        (st.group(1) if st else "open"), body))
    return out


def detect_state(root, conf, mode=None):
    """-> (mode, reason, consumed_next_mode)."""
    cur = os.path.join(PE.phase_dir(root, conf), "current")
    if mode:
        return mode, "--mode", False
    nxt = os.path.join(root, ".run", "next_mode")
    if os.path.isfile(nxt):
        want = PE.read_text(nxt).strip()
        os.remove(nxt)
        if want in MODES:
            return want, ".run/next_mode (consumed)", True
    phases = gen_phases(root)
    if phases is None or not phases or all(p[2] == "closed" for p in phases):
        return "planner-gen", "no open generation phase", False
    if os.path.isfile(os.path.join(cur, "REPLAN.md")):
        return "planner-phase", "REPLAN.md present", False
    if os.path.isfile(os.path.join(cur, "REVIEW.md")):
        return "review", "REVIEW.md present", False
    plan = os.path.join(cur, "PHASE_PLAN.md")
    if not os.path.isfile(plan):
        return "planner-phase", "no PHASE_PLAN.md", False
    if not PE.is_approved(PE.read_text(plan)):
        return "planner-phase", "PHASE_PLAN.md not approved", False
    return "router", "approved plan", False


def phase_id(root, conf):
    plan = PE.plan_path(root, conf)
    if os.path.isfile(plan):
        first = PE.read_text(plan).split("\n")[0]
        m = re.match(r"^#\s*Phase\s+(\S+)", first.strip())
        if m:
            return m.group(1)
    phases = gen_phases(root) or []
    for pid, _n, st, _r in phases:
        if st != "closed":
            return pid
    return "0"


def generation_id(root, conf):
    plan = PE.plan_path(root, conf)
    if os.path.isfile(plan):
        m = re.search(r"phase\s+([0-9]+(?:\.[0-9]+)*)",
                      PE.read_text(plan).split("\n")[0])
        if m:
            return m.group(1).split(".")[0]
    phases = gen_phases(root) or []
    if phases:
        return phases[0][0].split(".")[0]
    return "0"


# --------------------------------------------------------------------------- #
def _claude_version():
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True,
                             text=True, timeout=20)
    except Exception:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _pa_sessions(root):
    try:
        out = subprocess.run(["claude", "agents", "--json"], capture_output=True,
                             text=True, timeout=20, cwd=root)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        data = json.loads(out.stdout)
    except Exception:
        return []
    found = []

    def walk(node):
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str) and name.startswith("pa:"):
                found.append(name)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return found


def check_fable(notes):
    path = os.path.join(os.path.expanduser("~"), ".claude", "usage-ledger",
                        "summary.json")
    if not os.path.isfile(path):
        notes.append("warn: no usage-ledger summary.json; Fable-window check skipped")
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        notes.append("warn: summary.json unreadable (%s)" % type(exc).__name__)
        return
    worst = 0.0
    txt = json.dumps(data)
    for m in re.finditer(r'"(?:pct|used_percentage|utilization)"\s*:\s*([0-9.]+)', txt):
        try:
            worst = max(worst, float(m.group(1)))
        except ValueError:
            pass
    if "fable" in txt.lower() and worst >= 95:
        notes.append("warn: Fable window at %.0f%% -- consider --model claude-opus-5" % worst)
    else:
        notes.append("ok: Fable window %.0f%%" % worst)


def preflight(root, conf, mode, args, notes):
    if not os.path.isfile(os.path.join(root, ".claude", "pa.json")):
        die("no .claude/pa.json above %s (is this a PA3 project?)" % os.getcwd())
    if not os.path.isdir(os.path.join(root, ".git")):
        notes.append("warn: %s is not a git worktree" % rel(root, root))
    if os.environ.get("PA_SKIP_PREFLIGHT") == "1":
        notes.append("warn: preflight probes skipped (PA_SKIP_PREFLIGHT=1)")
        if args.check_fable:
            check_fable(notes)
        return
    ver = _claude_version()
    if ver is None:
        notes.append("warn: `claude --version` unavailable; version gate skipped")
    elif ver < MIN_CC:
        if not args.force:
            die("claude %s < %s (--force to override)"
                % (".".join(map(str, ver)), ".".join(map(str, MIN_CC))))
        notes.append("warn: claude %s < required %s" % (ver, MIN_CC))
    else:
        notes.append("ok: claude %s" % ".".join(map(str, ver)))
    if not args.resume and not args.seed_only:
        live = _pa_sessions(root)
        if live and not args.force:
            die("a PA session is already open in this worktree: %s (--force)" % live[0])
    if mode.startswith("planner"):
        try:
            out = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                n = len([x for x in out.stdout.split("\n") if x.strip()])
                notes.append("warn: %d uncommitted change(s) at planner start" % n)
        except Exception:
            pass
    if args.check_fable:
        check_fable(notes)


# --------------------------------------------------------------------------- #
SEED_HEAD = """# PA3 session seed -- {mode} -- Phase {phase} (Gen {gen})

You are the {mode} session. Read this file, then run your loop; do not read it twice.
Rules live in the project-architect skill (already in your context). Pointers only below.

Files:  {py} tools/card.py slice router | PROJECT_CONTEXT.md | GENERATION_PLAN.md
        {plan}
Index:  rules/INDEX.md | cookbook/INDEX.md | {pe}/TASK_INDEX.md | {pe}/RESEARCH_INDEX.md
Tools:  python tools/plan_edit.py | tools/status.py | tools/run.sh | tools/commit_task.sh
"""


def own_session_id(root):
    """This session's id or None: the ``~/.claude/sessions/<CLAUDE_PID>.json`` registry
    ``sessionId``, else the newest ``.run/warmer/<sid>.pid`` naming a live pid."""
    sid = None
    try:
        reg = os.path.join(os.path.expanduser("~"), ".claude", "sessions",
                           "%s.json" % os.environ["CLAUDE_PID"])
        with open(reg, encoding="utf-8") as fh:
            sid = json.load(fh).get("sessionId")
    except Exception:
        sid = None
    if not sid:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from pa.warmer import pid_alive
        except Exception:
            pid_alive = None
        wdir = os.path.join(root, ".run", "warmer")
        try:
            pids = sorted((f for f in os.listdir(wdir) if f.endswith(".pid")),
                          key=lambda f: (os.path.getmtime(os.path.join(wdir, f)), f), reverse=True)
        except OSError:
            pids = []
        for f in pids:
            try:
                with open(os.path.join(wdir, f), encoding="utf-8") as fh:
                    pid = int(fh.read().strip() or 0)
            except (OSError, ValueError):
                continue
            if pid_alive is None or pid_alive(pid):
                sid = f[:-4]
                break
    return sid


def _arm_line(root):
    """The seed's ``Arm now:`` Monitor line with the session id (3.9.5 T6: a seed without it
    left the trial's pa-session unarmed).  Sid: ``own_session_id``, else ``<sid>``."""
    sid = own_session_id(root)
    line ="Arm now: Monitor on `tail -n0 -F .run/warmer/%s.wake` (timeout 30 min), then continue." % (
        sid or "<sid>")
    if not sid:
        line += " (sid: .run/status.json router_session or the newest .run/warmer/*.pid)"
    return line + "\n"


def _status_doc(root):
    """``.run/status.json`` as a dict ({} when absent)."""
    try:
        with open(os.path.join(root, ".run", "status.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _curate_seed_line(root, conf):
    """Return a curate: seed line when a GenerationEnd or legacy entries exist."""
    pe_dir = PE.phase_dir(root, conf)
    # find the highest GenerationEnd
    highest_g = None
    if os.path.isdir(pe_dir):
        for name in sorted(os.listdir(pe_dir)):
            m = re.match(r"GenerationEnd_(\d+)\.md$", name)
            if m:
                g = int(m.group(1))
                if highest_g is None or g > highest_g:
                    highest_g = g
    if highest_g is not None:
        return "curate: closing gen %d -> opening gen %d" % (highest_g, highest_g + 1)
    # check LEGACY_INDEX.md for entries (non-comment, non-blank lines)
    legacy = os.path.join(pe_dir, "LEGACY_INDEX.md")
    if os.path.isfile(legacy):
        text = PE.read_text(legacy)
        entries = [l for l in text.split("\n")
                   if l.strip() and not l.strip().startswith("#")]
        if entries:
            return "curate: migration -> gen legacy"
    return None


def _audit_flag_line(root):
    """``Audit flag: <n> flags in PhaseEnd_Phase<N>.md`` when the newest PhaseEnd has flags."""
    pe_dir = os.path.join(root, "phase-ends")
    if not os.path.isdir(pe_dir):
        return None
    # find PhaseEnd files (natural sort by phase number)
    pe_files = []
    for name in os.listdir(pe_dir):
        m = re.match(r"PhaseEnd_Phase(\S+)\.md$", name)
        if m:
            pe_files.append((name, m.group(1)))
    if not pe_files:
        # also look inside phase-<N>/ subdirs
        for sub in os.listdir(pe_dir):
            sub_path = os.path.join(pe_dir, sub)
            if not os.path.isdir(sub_path):
                continue
            for name in os.listdir(sub_path):
                m = re.match(r"PhaseEnd_Phase(\S+)\.md$", name)
                if m:
                    pe_files.append((os.path.join(sub, name), m.group(1)))
    if not pe_files:
        return None
    # natural sort: split phase id into numeric parts
    def _sort_key(item):
        parts = re.split(r"[.\-]", item[1])
        return [int(p) if p.isdigit() else p for p in parts]
    pe_files.sort(key=_sort_key)
    newest_rel, newest_phase = pe_files[-1]
    newest_path = os.path.join(pe_dir, newest_rel)
    # count '- flag:' lines under '## Audit'
    n_flags = 0
    in_audit = False
    try:
        with open(newest_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("## Audit"):
                    in_audit = True
                    continue
                if in_audit and line.startswith("## "):
                    break
                if in_audit and line.strip().startswith("- flag:"):
                    n_flags += 1
    except OSError:
        return None
    if n_flags:
        return "Audit flag: %d flags in PhaseEnd_Phase%s.md" % (n_flags, newest_phase)
    return None


def _run_age_note(run_id, root, conf):
    """Return ' (last record <age> ago)' or ' (last record <age> ago, stale)' for the seed line."""
    # find the transcript file for this run
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
        return ""
    # compute age
    try:
        mtime = os.path.getmtime(transcript)
        age_s = max(0, datetime.datetime.now(datetime.timezone.utc).timestamp() - mtime)
    except OSError:
        return ""
    age_min = age_s / 60.0
    if age_min < 60:
        age_str = "%.0fm" % age_min
    else:
        age_str = "%.1fh" % (age_min / 60.0)
    # read liveness_min from config
    liveness_min = 5
    cfg_path = os.path.join(ledger_dir, "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cdoc = json.load(fh)
            lm = (cdoc.get("resume") or {}).get("liveness_min")
            if isinstance(lm, (int, float)) and lm > 0:
                liveness_min = lm
        except Exception:
            pass
    if age_min > liveness_min:
        return " (last record %s ago, stale)" % age_str
    return " (last record %s ago)" % age_str


def _card_slice(root, role):
    """Run ``card.py slice <role>`` and return its output, or '' on failure."""
    card_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.py")
    if not os.path.isfile(card_py):
        return ""
    try:
        res = subprocess.run([sys.executable, card_py, "slice", role],
                             cwd=root, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.rstrip()
    except Exception:
        pass
    return ""


def _deferred_items(root, conf, phase, every=False):
    """-> [(id, text)] deferred items due for `phase` (scan: _genplan.deferred_items);
    every=True: every open deferral (planner-gen triage)."""
    gp = os.path.join(root, "GENERATION_PLAN.md")
    gtext = PE.read_text(gp) if os.path.isfile(gp) else ""
    return [(d["id"], d["text"])
            for d in PE._genplan.deferred_items(PE.phase_dir(root, conf), gtext, phase, every=every)]


def _upgrade_line(root):
    """`Upgrade:` seed line when .claude/pa3-upgrade/UPGRADE.md lists unresolved conflicts
    (`- ` lines without ` | resolve:`, T20)."""
    path = os.path.join(root, ".claude", "pa3-upgrade", "UPGRADE.md")
    try:
        with open(path, encoding="utf-8") as f:
            n = sum(1 for ln in f if ln.startswith("- ") and " | resolve:" not in ln)
    except OSError:
        return None
    if not n:
        return None
    return ("Upgrade: %d file(s) in .claude/pa3-upgrade/UPGRADE.md await a resolution "
            "(keep | upstream | merged) by an upgrade expert; the router spawns it before the "
            "first task; nothing is copied by hand" % n)


def write_seed(root, conf, mode, phase, gen, args):
    pe = rel(root, PE.phase_dir(root, conf))
    plan = PE.plan_path(root, conf)
    py = conf.get("python") or "python"
    body = [SEED_HEAD.format(mode=mode, phase=phase, gen=gen, pe=pe,
                             plan=rel(root, plan), py=py)]
    preset = conf.get("preset") or "max20"         # max5/pro have no hard rung (pa/install/ladder.PRESETS)
    body.append("Preset: %s (hard rung: %s)\n"
                % (preset, "none" if preset in ("max5", "pro") else "expert-fable"))
    cur = os.path.join(PE.phase_dir(root, conf), "current")
    if mode == "router":
        body.append(_arm_line(root))             # first, before Run in flight / Running (3.9.5 T6)
        st = _status_doc(root)
        if st.get("task") and st.get("run_id") and st.get("killed_at"):
            progress = os.path.join(cur, "TASK_PROGRESS.md")
            if os.path.isfile(progress):
                progress_note = "PROGRESS: phase-ends/current/TASK_PROGRESS.md (written at the resume from the killed run's transcript)"
            else:
                progress_note = "(no progress file: the transcript was not found; respawn from the plan)"
            age_note = _run_age_note(st["run_id"], root, conf)
            unstop_note = ""
            if st.get("unstop_cleared"):
                unstop_note = " (stoppedByUser cleared by the resume hook)"
            body.append(
                "Run in flight: %s (task %s, %s) was killed with the previous process at %s%s%s; "
                "continue it first: SendMessage(to=\"%s\", message=\"continue: %s -- the session was "
                "resumed; your files and the plan are unchanged\") and end your turn to wait for its "
                "notification; when the send fails (the harness refuses an agent killed with its "
                "process) respawn %s as attempt %d with %s\n"
                % (st["run_id"], st["task"], st.get("expert_agent_type") or "?", st["killed_at"],
                   age_note, unstop_note,
                   st["run_id"], st["task"], st["task"], (st.get("attempt") or 1) + 1, progress_note))
        elif st.get("task") and st.get("run_id") and st.get("kind") in ("task", "handoff", "relaunch"):
            body.append("Running: %s run %s (attempt %s, %s) — a resumed session: SendMessage that run first; "
                        "respawn only if it is unknown\n" % (st["task"], st["run_id"], st.get("attempt") or 1,
                                                             st.get("kind")))
        body.append("## Tasks")
        body.append(_capture(["show", "--tasks", "--titles"], root))
        body.append("\n## Next")
        body.append(_capture(["next"], root))
        # inline the router card slice
        card = _card_slice(root, "router")
        if card:
            body.append("\n## Card")
            body.append(card)
    elif mode.startswith("planner"):
        body.append("## Generation phases")
        for pid, name, st, _raw in (gen_phases(root) or []):
            body.append("- %s %s | status: %s" % (pid, name, st))
        # curate line: a generation is being opened (planner-gen only -- while a generation
        # is open, its predecessor's GenerationEnd still exists; the 3.3 trial ran the curator
        # at a phase-planning launch because of that)
        curate_line = _curate_seed_line(root, conf) if mode == "planner-gen" else None
        if curate_line:
            body.append(curate_line)
        if os.path.isfile(os.path.join(cur, "REPLAN.md")):
            body.append("\nREPLAN.md is present: read it first, then rewrite the plan.")
        # deferred items due for the planned phase (first open); each gets a ## Triage line.
        # planner-gen: every open deferral, triaged by `discuss` first (Triage: line)
        gp = os.path.join(root, "GENERATION_PLAN.md")
        planned = PE._genplan.first_open(PE.read_text(gp)) if os.path.isfile(gp) else None
        items = _deferred_items(root, conf, planned, every=(mode == "planner-gen"))
        if mode == "planner-gen" and items:
            body.append("Triage: %d deferred items" % len(items))
        for did, dtext in items:
            body.append("Deferred: %s %s" % (did, dtext))
        body.append("\nSpawn %s to draft, ask the summary question, stop at approval. "
                    "After approval: python tools/plan_edit.py approve --planner \"%s\""
                    % (mode, PLANNER_LABEL))
    elif mode == "review":
        body.append("Read %s/current/REVIEW.md, answer outcome-first, "
                    "record decisions via plan_edit.py or INBOX.md." % pe)
    for name in ("INBOX.md", "REPLAN.md", "REVIEW.md", "TASK_PROGRESS.md"):
        if os.path.isfile(os.path.join(cur, name)):
            body.append("present: %s/current/%s" % (pe, name))
    upg = _upgrade_line(root)                    # staged install conflicts (3.9.7 T8)
    if upg:
        body.append(upg)
    if mode == "router":
        audit_flag = _audit_flag_line(root)
        if audit_flag:
            body.append(audit_flag)
        body.append("\nLoop from here: INBOX consume -> plan_edit.py next -> "
                    "status.py set -> spawn the expert in the background -> "
                    "one line -> end the turn.")
    text = "\n".join(body).rstrip() + "\n"
    seed = os.path.join(root, ".run", "seed.md")
    PE.write_text(seed, text)
    return seed


def _capture(argv, root):
    """Run plan_edit in-process and capture its ≤40 lines."""
    import io
    buf = io.StringIO()
    old, sys.stdout = sys.stdout, buf
    try:
        PE.main(argv)
    except SystemExit:
        pass
    except Exception as exc:
        buf.write("(plan_edit %s: %s)\n" % (type(exc).__name__, exc))
    finally:
        sys.stdout = old
    return buf.getvalue().rstrip("\n")


# --------------------------------------------------------------------------- #
def build_cmd(mode, project, phase, conf=None, args=None):
    spec = dict(MODES[mode])
    conf = conf or {}
    if mode == "router":
        spec["perm"] = conf.get("router_permission_mode") or spec["perm"]
    if args is not None:
        if args.model:
            spec["model"] = args.model
        if args.effort:
            spec["effort"] = args.effort
        if args.perm:
            spec["perm"] = args.perm
    name = "pa:%s:%s:P%s" % (project, mode, phase)
    if args is not None and args.resume:
        return ["claude", "--resume", args.resume, "--effort", spec["effort"]], spec
    cmd = ["claude"]
    if spec["agent"]:
        cmd += ["--agent", spec["agent"]]
    cmd += ["--model", spec["model"], "--effort", spec["effort"]]
    if spec["perm"]:
        cmd += ["--permission-mode", spec["perm"]]
    cmd += ["--name", name]
    spec["name"] = name
    return cmd, spec


def display(cmd):
    out = []
    quote_next = False
    for c in cmd:
        if quote_next or " " in c:
            out.append('"%s"' % c)
        else:
            out.append(c)
        quote_next = (c == "--name")
    return " ".join(out)


def child_env(mode, phase):
    env = dict(os.environ)
    env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "3"
    env["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] = "off"
    env["PA_MODE"] = mode
    env["PA_PHASE"] = str(phase)
    env["PA_LAUNCH_TS"] = now()
    return env


# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="launch.py", description="Detect the session state and start it.")
    p.add_argument("--mode", choices=sorted(MODES))
    p.add_argument("--effort")
    p.add_argument("--model")
    p.add_argument("--perm", choices=["acceptEdits", "default", "plan", "bypass", "auto"])
    p.add_argument("--resume", metavar="SID")
    p.add_argument("--request", metavar="MODE", choices=sorted(MODES),
                   help="write .run/next_mode and exit")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--seed-only", action="store_true",
                   help="write .run/seed.md + launch.json and print the state; no launch "
                        "(the running pa-session calls this)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--plain", action="store_true",
                   help="fallback session: no --agent, Fable high")
    p.add_argument("--check-fable", action="store_true",
                   help="read ~/.claude/usage-ledger/summary.json for the Fable window")
    a = p.parse_args(argv)
    root = PE.find_root()
    conf = PE.cfg(root)

    if a.request:
        PE.write_text(os.path.join(root, ".run", "next_mode"), a.request + "\n")
        sys.stdout.write("next_mode: %s (consumed by the next launch.py)\n" % a.request)
        return 0

    if a.plain:
        mode, reason = "plain", "--plain fallback"
    else:
        mode, reason, _consumed = detect_state(root, conf, a.mode)
    project = conf.get("project") or os.path.basename(root)
    phase = phase_id(root, conf)
    gen = generation_id(root, conf)

    notes = []
    preflight(root, conf, mode, a, notes)
    seed = write_seed(root, conf, mode, phase, gen, a)
    cmd, spec = build_cmd(mode, project, phase, conf, a)
    PE.write_text(os.path.join(root, ".run", "launch.json"),
                  json.dumps({"mode": mode, "reason": reason, "phase": phase,
                              "generation": gen, "project": project,
                              "model": spec["model"], "effort": spec["effort"],
                              "agent": spec["agent"], "name": spec.get("name"),
                              "resume": a.resume, "ts": now(),
                              "cmd": display(cmd)}, indent=1) + "\n")
    if a.seed_only:
        sys.stdout.write("seed=%s\nmode=%s\n" % (rel(root, seed), mode))
        # Phase ownership (3.10 T28): follows whichever session ran `go` last; a relaunched
        # router takes over by restamping .run/status.json router_session (other keys kept).
        sid = own_session_id(root) if mode != "plain" else None
        if sid:
            doc = _status_doc(root)
            doc["router_session"], doc["updated"] = sid, now()
            PE.write_text(os.path.join(root, ".run", "status.json"),
                          json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
            sys.stdout.write("router_session=%s\n" % sid)
        return 0
    out = ["state=%s (%s)" % (mode, reason),
           "project=%s phase=%s generation=%s" % (project, phase, gen),
           "seed=%s" % rel(root, seed)]
    out += notes[:6]
    out.append(display(cmd))
    PE.emit(out)
    if a.dry_run:
        return 0
    env = child_env(mode, phase)
    try:
        return subprocess.call(cmd, env=env, cwd=root)
    except FileNotFoundError:
        die("`claude` is not on PATH")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
