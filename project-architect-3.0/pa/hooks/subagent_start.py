"""SubagentStart: open the ``agent_runs`` row for a child that just spawned.

Input: ``session_id, agent_id, agent_type, cwd`` (probe fixtures).  The sibling
``agent-<id>.meta.json`` adds ``spawnDepth``, ``toolUseId``, ``description`` and
``model`` when the harness has written it already -- it usually has, but this
hook never waits for it.

Parent (heuristic; the authoritative one arrives with the parent's ``Agent``
PostToolUse, recon V4): depth >= 2 -> the expert currently running in this
session, otherwise the session's main run.  Experts are serial by D33, so the
heuristic is exact for the live case and is overwritten later anyway.

Governance: an expert's run id is pushed into ``.run/status.json`` so the
statusline's line 2 can show which run the phase task is on.

Focus (3.1 T12.1.1.1): with ``pa.json follow_agents: on``, a planner, expert or
critic (never a retriever or coder) gets a new focused Windows Terminal tab in the
current window (``wt.exe -w 0 nt``) running ``pa_ledger.py follow <run_id>``, a
read-only live view of its transcript; SubagentStop drops the marker that closes it.
"""

import json
import os
import re
import sys

from .. import log, running
from . import (close_db, governed, now_iso, open_db, pinned_for, project_config,
               role_of, sid_of, status_patch, status_read)

_TASK_ID_RE = re.compile(r"^(T\d+(?:\.\d+)*)(?:\.c\d+)?\b")


def _task_id_from_description(description):
    """Task id prefix of an Agent's description ('T5.c1 guard rules' -> 'T5'),
    or None when absent.  Six coders spawned in one turn all read the same
    ``status.json`` task, so the description (recorded at
    ``post_tool_use.py:80-87`` for the parent side, mirrored here through the
    child's ``agent-<id>.meta.json``) is the per-child source of truth when it
    names a task (3.7 T7.c3)."""
    if not description:
        return None
    m = _TASK_ID_RE.match(str(description).strip())
    return m.group(1) if m else None


def run(inp, cfg):
    inp = inp or {}
    agent_id = inp.get("agent_id")
    if not agent_id:
        return None
    sid = sid_of(inp)
    agent_type = inp.get("agent_type")
    role = role_of(inp, cfg)
    pinned = pinned_for(inp, cfg)
    meta = _meta(inp, agent_id)
    depth = _int(meta.get("spawnDepth"))
    live = running.session(sid)
    expert = live.get("expert")
    if meta.get("parentAgentId"):                       # the harness says so (meta.json, 2.1.278)
        parent, source = str(meta["parentAgentId"]), "meta"
    elif (depth or 0) >= 2 and expert:
        parent, source = expert, "heuristic"
    else:
        parent, source = sid, "main"
    now = now_iso()
    root = governed(inp)
    status = (status_read(root) or {}) if root else {}         # the router's stamp (3.1 T10/T11)

    running.update(lambda data: _live(data, sid, agent_id, agent_type, role, parent,
                                      pinned, meta, now, source))

    conn = open_db()
    try:
        from .. import db, prices, savings

        prior = savings.prior_kept(conn, sid, now, exclude_run=agent_id)
        prior_cold = True
        try:
            last = conn.execute("SELECT model FROM turns WHERE session_id=? AND kind='api'"
                                " ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
            if last and last[0] and pinned:
                prior_cold = prices.model_key(last[0]) != prices.model_key(pinned)
        except Exception:
            prior_cold = True
        # phase: status.json's (else the plan's) on the router_session, ``beside <phase>`` elsewhere (T28)
        run_phase = None
        if root:
            from .. import phases as _phases
            run_phase = _phases.session_phase(root, sid, status)
        # refresh sessions.phase when the plan's phase differs from what was stored
        if run_phase and root:
            s_row = conn.execute("SELECT phase FROM sessions WHERE session_id=?", (sid,)).fetchone()
            if s_row and s_row["phase"] != run_phase:
                conn.execute("UPDATE sessions SET phase=? WHERE session_id=?", (run_phase, sid))

        desc_task_id = _task_id_from_description(meta.get("description"))
        db.upsert_agent_run(conn, {
            "run_id": agent_id, "session_id": sid, "parent_run_id": parent,
            "parent_source": source,
            "kind": role, "agent_type": agent_type,
            "description": meta.get("description"), "tool_use_id": meta.get("toolUseId"),
            "spawn_depth": depth, "model_pinned": pinned,
            "model_seen": meta.get("model"), "started": now, "status": "running",
            "transcript_path": _transcript(inp, agent_id),
            "task_id": desc_task_id or status.get("task") or None, "phase": run_phase,
        })
    finally:
        close_db(conn)
    if prior:
        running.set_agent(sid, agent_id, {"prior_kept": int(prior), "prior_cold": bool(prior_cold)})
    # vanilla-net-v3: seed_ctx in the live entry so live_saved can start at minus the seed carry;
    # the actual value is the first turn's ctx, set by PostToolUse; 0 until then.
    running.set_agent(sid, agent_id, {"seed_ctx": 0, "seed_carry_usd": 0.0})

    if role == "expert":
        running.set_expert(sid, agent_id)
        if root:
            status_patch(root, {"run_id": agent_id, "expert_agent_type": agent_type})
    if root and follow_wanted(project_config(root), role):
        open_follow_tab(inp, sid, agent_id, agent_type, role, status)
    if root:
        _warm(root, sid, cfg, agent_id, agent_type, role)
    log.log("subagent_start", session=sid, agent=agent_id, role=role, depth=depth)
    return None


def _warm(root, sid, cfg, agent_id, agent_type, role):
    """3.9.5 T4: tell the session's warmer of the run (``<sid>.runs``); start it when none lives."""
    try:
        from .. import fsutil, warmer

        fsutil.append_line(warmer.path(root, sid, "runs"), json.dumps(
            {"add": agent_id, "agent_type": agent_type, "role": role}))
        warmer.start_if_needed(root, sid, cfg, os.environ.get("CLAUDE_PID"))
    except Exception as exc:           # fail soft: the run is only not warmed
        log.log("warmer_start_failed", session_id=sid, agent=agent_id, error=str(exc)[:200])


def follow_wanted(pcfg, role):
    """``pa.json follow_agents: on`` and a planner, expert or critic (never a retriever or coder)."""
    flag = str((pcfg or {}).get("follow_agents") or "off").strip().lower()
    if flag not in ("on", "true", "1", "yes"):
        return False
    role = str(role or "")
    return role in ("expert", "critic") or role.startswith("planner")


def wt_exe():
    """Windows Terminal: ``$PA_WT_EXE`` (tests), PATH, else the WindowsApps alias; None elsewhere."""
    forced = os.environ.get("PA_WT_EXE")
    if forced:
        return forced
    from .. import notify

    found = notify._which("wt")
    if found:
        return found
    cand = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Microsoft", "WindowsApps", "wt.exe")
    return cand if os.path.isfile(cand) else None


def open_follow_tab(inp, sid, agent_id, agent_type, role, status):
    """A new focused tab in the current Windows Terminal window that follows the agent, read-only."""
    from .. import notify, paths

    wt = wt_exe()
    if not wt:
        log.log("follow_no_wt", session=sid, agent=agent_id)
        return False
    pa3 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    title = ("%s %s" % (agent_type or role, (status or {}).get("task") or "")).strip()
    cmd = [wt, "-w", "0", "nt", "--title", title, sys.executable, "-X", "utf8",
           os.path.join(pa3, "pa_ledger.py"), "follow", str(agent_id),
           "--transcript", _transcript(inp, agent_id) or "", "--session", sid]
    try:
        marker = paths.follow_path(agent_id)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({"agent": agent_type, "role": role, "session": sid,
                       "opened": now_iso(), "title": title}, fh)
    except OSError:
        pass
    sent = notify.spawn_detached(cmd, {})
    log.log("follow_open", session=sid, agent=agent_id, sent=bool(sent))
    return bool(sent)


def _live(data, sid, agent_id, agent_type, role, parent, pinned, meta, now, source="main"):
    entry = running.agent_ref(data, sid, agent_id, agent_type=agent_type, role=role,
                              parent=parent, model_pinned=pinned, started=now)
    entry.update({"agent_type": agent_type, "role": role, "parent": parent,
                  "parent_source": source,
                  "model_pinned": pinned, "status": "running",
                  "description": meta.get("description"),
                  "spawn_depth": _int(meta.get("spawnDepth"))})
    running.session_ref(data, sid)["touched"] = now
    return data


def _meta(inp, agent_id):
    from .. import transcript

    path = _transcript(inp, agent_id)
    return transcript.read_meta(path) if path else {}


def _transcript(inp, agent_id):
    from . import agent_transcript

    return agent_transcript(inp, agent_id)


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
