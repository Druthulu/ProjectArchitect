#!/usr/bin/env python
"""launch.py -- the session state report the mod reads.

3.24 T3.6: it starts no session (the developer types a bare `claude`; the mod drives it); the launch
modes, preflight, seed.md, launch.json and --mode/--request/--dry-run left with the Gen 3 loop.
Stdlib only; no imports from `pa/` (it may import plan_edit.py, its neighbour).

`--state-json` (3.23 T2, for the mod): a read-only state report on one line; writes nothing
(no seed.md, launch.json, router_session, preflight or `next` promotion; .run/next_mode kept):
  {"mode": str, "reason": str, "next_mode": str|null, "phase": str, "generation": str,
   "curate": str|null, "audit_flag": str|null, "deferred": [{"id": str, "text": str}],
   "run_in_flight": {"task", "run_id", "killed_at", "age_note": str|null, "stale": bool}|null,
   "running": {"task", "run_id", "kind"}|null, "preset": str, "upgrade": str|null}
`mode` is "planner-gen", "router" or "" (a phase plan missing, unapproved, or a REPLAN.md/REVIEW.md
present: neither mode the mod's modeOf compares; the strings planner-phase and review left, 3.24 T3.6);
`mode` ignores .run/next_mode; `next_mode` is its content (a known mode) or null.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_edit as PE                                          # noqa: E402

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The mode strings are the mod's contract (--state-json `mode`; autopilot.tsx modeOf reads router and
# planner-gen). 3.24 T3.6: launch.py starts no session; planner-phase and review left (no mod reader).
MODES = ("planner-gen", "router")


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


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


def detect_state(root, conf):
    """-> (mode, reason); reads only (.run/next_mode is never consumed: no launch follows)."""
    cur = os.path.join(PE.phase_dir(root, conf), "current")
    phases = gen_phases(root)
    if phases is None or not phases or all(p[2] == "closed" for p in phases):
        return "planner-gen", "no open generation phase"
    # 3.24 T3.6: these were planner-phase and review; "" keeps every non-router state apart for modeOf.
    if os.path.isfile(os.path.join(cur, "REPLAN.md")):
        return "", "REPLAN.md present"
    if os.path.isfile(os.path.join(cur, "REVIEW.md")):
        return "", "REVIEW.md present"
    plan = os.path.join(cur, "PHASE_PLAN.md")
    if not os.path.isfile(plan):
        return "", "no PHASE_PLAN.md"
    if not PE.is_approved(PE.read_text(plan)):
        return "", "PHASE_PLAN.md not approved"
    return "router", "approved plan"


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
def _status_doc(root):
    """``.run/status.json`` as a dict ({} when absent)."""
    try:
        with open(os.path.join(root, ".run", "status.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _curate_seed_line(root, conf, mode):
    """Return a curate: seed line: the migration line in any mode while LEGACY_INDEX.md has
    entries, pa.json lacks `memory_routed` (3.14 T2) and `.claude-state/memory` holds a
    memory file (any *.md but MEMORY.md and gen*.md archives; 3.14 T4); else, planner-gen
    only, the closing-gen line when a GenerationEnd exists."""
    pe_dir = PE.phase_dir(root, conf)
    # check LEGACY_INDEX.md for entries (non-comment, non-blank lines); the migration wins
    legacy = os.path.join(pe_dir, "LEGACY_INDEX.md")
    if os.path.isfile(legacy) and not conf.get("memory_routed"):
        text = PE.read_text(legacy)
        entries = [l for l in text.split("\n")
                   if l.strip() and not l.strip().startswith("#")]
        memdir = os.path.join(root, ".claude-state", "memory")
        memories = [n for n in (os.listdir(memdir) if os.path.isdir(memdir) else [])
                    if n.endswith(".md") and n != "MEMORY.md" and not n.startswith("gen")]
        if entries and memories:
            return "curate: migration -> gen legacy"
    if mode != "planner-gen":
        return None
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
    # natural sort, the one rule (3.14.1: a local splitter raised TypeError on ids like 3_5 beside 37.5)
    from phaseend_index import natural_key
    pe_files.sort(key=lambda item: natural_key(item[1]))
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
            "(keep | upstream | merged) by an upgrade expert, run as a task before "
            "the phase's first one; nothing is copied by hand" % n)


def state_json(root, conf):
    """The --state-json report (shape in the module docstring); reads only."""
    mode, reason = detect_state(root, conf)
    nxt = os.path.join(root, ".run", "next_mode")
    want = PE.read_text(nxt).strip() if os.path.isfile(nxt) else ""
    gp = os.path.join(root, "GENERATION_PLAN.md")
    planned = PE._genplan.first_open(PE.read_text(gp)) if os.path.isfile(gp) else None
    st = _status_doc(root)
    flight = running = None
    if st.get("task") and st.get("run_id") and st.get("killed_at"):
        note = _run_age_note(st["run_id"], root, conf).strip()
        flight = {"task": st["task"], "run_id": st["run_id"], "killed_at": st["killed_at"],
                  "age_note": note.strip("()") or None, "stale": note.endswith("stale)")}
    elif st.get("task") and st.get("run_id") and st.get("kind") in ("task", "handoff", "relaunch"):
        running = {"task": st["task"], "run_id": st["run_id"], "kind": st.get("kind")}
    return {"mode": mode, "reason": reason, "next_mode": want if want in MODES else None,
            "phase": phase_id(root, conf), "generation": generation_id(root, conf),
            "curate": _curate_seed_line(root, conf, mode), "audit_flag": _audit_flag_line(root),
            "deferred": [{"id": i, "text": t} for i, t in
                         _deferred_items(root, conf, planned, every=(mode == "planner-gen"))],
            "run_in_flight": flight, "running": running,
            "preset": conf.get("preset") or "max20", "upgrade": _upgrade_line(root)}


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="launch.py", description="Report the session state for the mod (starts no session).")
    p.add_argument("--state-json", dest="state_json", action="store_true",
                   help="print the session state as one JSON object; writes nothing")
    a = p.parse_args(argv)
    if not a.state_json:
        die("launch.py starts no session (3.24): type `claude`; --state-json reports the state")
    root = PE.find_root()
    conf = PE.cfg(root)
    sys.stdout.write(json.dumps(state_json(root, conf), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
