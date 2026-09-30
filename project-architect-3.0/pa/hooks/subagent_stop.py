"""SubagentStop: lock the finished run, book its retrieval or its savings.

Input: ``session_id, agent_id, agent_type, agent_transcript_path,
last_assistant_message, cwd`` (probe fixtures).

Path A -- the transcript is at most ``ledger.lock_inline_max_bytes`` (20 MB):
parse it once (streaming, keep-LAST per ``message.id``), write every request into
``turns`` and lock the ``agent_runs`` row.  Path B -- larger (recon V8 found a
256 MB subagent file): queue it in ``state/pending_locks.jsonl`` with
``status='pending_lock'``; ``pa-ledger recalc --pending`` finishes it off the hot
path.

Then, by role:
  * **retriever** -- ``kept_out_tokens`` per design E.1 ``results_share``
    (growth = ctx_at_end - seed_ctx, split by the share of the run's visible
    output that was tool results, minus the answer it returned) into
    ``retrievals``, and ``R``/``returned_at`` into the parent's live entry so the
    expert's next PostToolUse starts counting avoided re-sends.
  * **expert/coder/critic** -- the live savings figure is locked into
    ``savings``, the live entry is dropped and the project's ``.run/status.json``
    loses its ``run_id``.

Finally the session's spool is drained and ``summary.json`` rebuilt.
"""

import json
import os

from .. import log, running
from . import (close_db, drain_spool, governed, now_iso, open_db, pinned_for,
               project_config, project_name, role_of, session_pre_install, sid_of,
               status_patch, toast)

CONTRACT_TOKEN_BUDGET = 300


def run(inp, cfg):
    inp = inp or {}
    agent_id = inp.get("agent_id")
    if not agent_id:
        return None
    sid = sid_of(inp)
    agent_type = inp.get("agent_type")
    role = role_of(inp, cfg)
    path = inp.get("agent_transcript_path") or _built_path(inp, agent_id)
    answer = inp.get("last_assistant_message") or ""
    answer_tokens = len(str(answer)) // 4
    if answer_tokens > CONTRACT_TOKEN_BUDGET:
        log.log("contract_oversize", session=sid, agent=agent_id, role=role,
                tokens=answer_tokens)

    live = running.agent(sid, agent_id)
    conn = open_db()
    try:
        if not (path and os.path.exists(path)) and not live and not _started(conn, agent_id):
            # the harness fires SubagentStop for the main thread's own turns when the session runs
            # as a named agent (2.1.278, observed 2026-09-20): no start, no transcript, no row
            log.log("subagent_stop_phantom", session=sid, agent=agent_id, agent_type=agent_type)
            return None
        _close_in_db(conn, cfg, inp, sid, agent_id, role, path, live, answer_tokens)
    finally:
        close_db(conn)

    close_follow_tab(agent_id)
    _cleanup(inp, cfg, sid, agent_id, role)
    _warm_end(inp, sid, agent_id)
    if (str(agent_type or "") in ("discuss", "discuss-high", "discuss-max")     # 3.9.5 T2/T13: the discussion's turn ended; no other type toasts
            and str(answer).strip() != "."):                                   # 3.14.1: a warmer ping's reply is no turn for the developer
        toast(cfg, "PA3 · %s · discussion turn ended" % (project_name(inp) or "session"),
              str(answer)[-240:] or "(no message)", kind="waiting", session_id=sid,
              project_cfg=project_config(governed(inp)), cause="discussion", run_id=agent_id)

    # sweep other finished agents that missed their SubagentStop
    try:
        from .. import liveness
        liveness.sweep_if_due(sid, cfg)
    except Exception:
        pass
    return None


def close_run(cfg, sid, agent_id, agent_type, path, live=None, cwd=None):
    """Close a finished agent run: lock, book, cleanup.

    Shared entry point for SubagentStop and the liveness sweep.  When *path* is
    ``None`` or absent the entry is just dropped from ``running.json``.
    """
    from .. import config as _config

    role = _config.role_from_agent_type(agent_type, cfg)
    live = live if live is not None else running.agent(sid, agent_id)
    inp = {"session_id": sid, "agent_id": agent_id, "agent_type": agent_type,
           "cwd": cwd or ""}
    if path and os.path.exists(path):
        conn = open_db()
        try:
            _close_in_db(conn, cfg, inp, sid, agent_id, role, path, live, 0)
        finally:
            close_db(conn)
    close_follow_tab(agent_id)
    _cleanup(inp, cfg, sid, agent_id, role)


def _close_in_db(conn, cfg, inp, sid, agent_id, role, path, live, answer_tokens):
    """Lock the run in the ledger, book retrieval and savings, drain the spool."""
    totals = _lock(conn, cfg, inp, sid, agent_id, role, path, live)
    _book_retrieval(conn, cfg, inp, sid, agent_id, role, path, totals, answer_tokens, live)
    _book_savings(conn, cfg, inp, sid, agent_id, role, totals, live)
    drain_spool(conn, sid, cfg)
    _rebuild(conn, cfg)


def _warm_end(inp, sid, agent_id):
    """3.9.5 T4: the warmer's watch on this run ends (``{"end": id}`` in ``<sid>.runs``)."""
    root = governed(inp)
    if not root:
        return
    try:
        from .. import fsutil, warmer

        if os.path.isdir(warmer.warmer_dir(root)):
            fsutil.append_line(warmer.path(root, sid, "runs"), json.dumps({"end": agent_id}))
    except Exception as exc:
        log.log("warmer_end_failed", session_id=sid, agent=agent_id, error=str(exc)[:200])


def close_follow_tab(agent_id):
    """3.1 T12.1.1.1: drop the marker that ends a ``pa_ledger.py follow`` tab for this agent."""
    from .. import paths

    if not os.path.exists(paths.follow_path(agent_id)):
        return False
    try:
        with open(paths.follow_path(agent_id, "stop"), "w", encoding="utf-8") as fh:
            fh.write(now_iso() + chr(10))
    except OSError:
        return False
    return True


def _orphan_children(sid, parent_id):
    """A background child cannot outlive its parent (the harness ends it without a SubagentStop):
    drop its live entry and mark its run ``orphaned`` so the statusline never shows it as running."""
    live = running.session(sid)
    kids = [aid for aid, a in (live.get("agents") or {}).items()
            if isinstance(a, dict) and str(a.get("parent") or "") == str(parent_id)
            and str(a.get("status") or "running") == "running"]
    if not kids:
        return []
    conn = open_db()
    try:
        from .. import db

        for aid in kids:
            running.remove_agent(sid, aid)
            db.upsert_agent_run(conn, {"run_id": aid, "status": "orphaned", "ended": now_iso()})
    finally:
        close_db(conn)
    log.log("orphaned", session=sid, parent=parent_id, agents=len(kids))
    return kids


# --------------------------------------------------------------------------- lock

def _started(conn, agent_id):
    """True when SubagentStart booked this run (a real subagent always has a row)."""
    try:
        return conn.execute("SELECT 1 FROM agent_runs WHERE run_id=?", (agent_id,)).fetchone() is not None
    except Exception:
        return False


def _built_path(inp, agent_id):
    from . import agent_transcript

    return agent_transcript(inp, agent_id)


def _limit(cfg):
    try:
        return int((cfg.get("ledger") or {}).get("lock_inline_max_bytes", 20_000_000))
    except (TypeError, ValueError):
        return 20_000_000


def _lock(conn, cfg, inp, sid, agent_id, role, path, live):
    from .. import config, db, runs

    from .. import transcript

    status = "handoff" if live.get("handoff_fired") else "completed"
    base = {"run_id": agent_id, "session_id": sid, "kind": role,
            "agent_type": inp.get("agent_type"), "model_pinned": pinned_for(inp, cfg),
            "handoff_fired": 1 if live.get("handoff_fired") else 0,
            "ended": now_iso(), "transcript_path": path}
    meta = transcript.read_meta(path) if path else {}
    # workflow agent: parent = session unless meta says otherwise, description carries wf:<id>
    wf_id = transcript.workflow_id_of(path) if path else None
    if wf_id:
        desc = meta.get("description") or ""
        base["description"] = ("wf:%s %s" % (wf_id, desc)).rstrip()
        if not meta.get("parentAgentId"):
            base["parent_run_id"], base["parent_source"] = sid, "workflow"
    if meta.get("parentAgentId"):                       # the harness's own parent link wins
        base["parent_run_id"], base["parent_source"] = str(meta["parentAgentId"]), "meta"
    try:
        if meta.get("spawnDepth") is not None:
            base["spawn_depth"] = int(meta["spawnDepth"])
    except (TypeError, ValueError):
        pass
    size = None
    try:
        size = os.path.getsize(path) if path else None
    except OSError:
        size = None

    if not path or size is None:
        base["status"] = status
        db.upsert_agent_run(conn, base)
        log.log("subagent_stop_no_transcript", session=sid, agent=agent_id, path=path)
        return None

    if size > _limit(cfg):
        base["status"] = "pending_lock"
        base["transcript_bytes"] = size
        db.upsert_agent_run(conn, base)
        runs.defer_lock({"run_id": agent_id, "session_id": sid, "path": path,
                         "role": role, "agent_type": inp.get("agent_type"),
                         "bytes": size, "reason": "oversize"})
        log.log("lock_deferred", session=sid, agent=agent_id, bytes=size)
        return None

    from .. import accounts

    account, _src = accounts.account_for(sid)
    totals = runs.lock_run_from_transcript(
        conn, agent_id, path, session_id=sid, account=account,
        ttl_default=config.ttl_for_role(role, cfg), status=status,
        effort=_effort(inp), extra=base)
    return totals


def _effort(inp):
    from . import effort_of

    return effort_of(inp)


# --------------------------------------------------------------------------- retriever

def kept_out_tokens(growth, result_tokens, own_output, answer_tokens, mode="results_share"):
    """Design E.1 through :func:`pa.savings.kept_out_tokens` (growth as two ctx marks)."""
    from .. import savings

    kept, _detail = savings.kept_out_tokens(
        [{"ctx": 0}, {"ctx": max(0, int(growth or 0))}],
        result_tokens, own_output, answer_tokens, mode)
    return kept


def _book_retrieval(conn, cfg, inp, sid, agent_id, role, path, totals, answer_tokens, live):
    from .. import db, savings, transcript

    # T1.1: workflow agents are cost, never savings — void with mode 'workflow'
    is_workflow = bool(transcript.workflow_id_of(path) if path else None)
    root = governed(inp)                                      # T11
    is_ungoverned = not root
    is_pre_install = bool(root) and session_pre_install(
        root, cfg, running.session(sid).get("started"))        # T10.1

    mode = (cfg.get("savings") or {}).get("kept_out_mode") or "results_share"
    if is_workflow:
        mode = "workflow"
    elif role != "retriever":
        mode = "all_growth"      # an expert, planner, critic or coder keeps its whole growth out
    seed = int((totals or {}).get("seed_ctx") or 0)
    end = int((totals or {}).get("ctx_at_end") or 0)
    growth = max(0, end - seed)
    counts = {"result_tokens": 0, "own_output": 0}
    if not is_workflow and role == "retriever" and path and os.path.exists(path) and totals is not None:
        try:
            counts = transcript.tool_result_tokens(path)
        except Exception:
            counts = {"result_tokens": 0, "own_output": 0}
    parent = _parent_of(conn, agent_id, live, sid, path)
    returned = now_iso()
    row = savings.retrieval_row(
        agent_id, parent_run_id=parent, agent_type=inp.get("agent_type"),
        model=(totals or {}).get("model_seen"),
        turns=[{"ctx": seed}, {"ctx": end}],
        result_tokens_est=counts.get("result_tokens"),
        own_output_est=counts.get("own_output"), answer_tokens=answer_tokens,
        status=(("handoff" if live.get("handoff_fired") else "completed")
                if totals is not None else "error"), mode=mode,
        spawned_at=live.get("started"), returned_at=returned)
    if is_workflow:
        row["void"] = 1
        row["kept_out_mode"] = "workflow"
        row["kept_out_tokens"] = 0
    elif is_pre_install:                                     # T10.1: session predates the install
        row["void"] = 1
        row["kept_out_mode"] = "pre-install"
        row["kept_out_tokens"] = 0
    elif is_ungoverned:                                      # T11: cost stays, savings voided
        row["void"] = 1
        row["kept_out_mode"] = "ungoverned"
        row["kept_out_tokens"] = 0
    kept = row["kept_out_tokens"]
    void = row["void"]
    db.upsert_retrieval(conn, row)
    if parent and parent != sid and not is_workflow and not is_ungoverned and not is_pre_install:
        running.add_retrieval(sid, parent, agent_id,
                              {"R": kept, "returned_at": returned, "n_after": 0,
                               "void": bool(void)})
    log.log("retrieval", session=sid, agent=agent_id, parent=parent, R=kept,
            growth=growth, answer=answer_tokens, void=void)
    return kept


def _parent_of(conn, agent_id, live, sid, path=None):
    """The retriever's parent: the harness's meta link, else a live or booked parent that is not
    the session itself, else the running expert, else the session."""
    from .. import transcript

    meta_parent = (transcript.read_meta(path) if path else {}).get("parentAgentId")
    if meta_parent:
        return str(meta_parent)
    parent = live.get("parent")
    if parent and parent != sid:
        return parent
    row = conn.execute("SELECT parent_run_id FROM agent_runs WHERE run_id=?",
                       (agent_id,)).fetchone()
    if row and row[0] and row[0] != sid:
        return row[0]
    expert = running.session(sid).get("expert")
    return expert or parent or sid


# --------------------------------------------------------------------------- savings

def _book_savings(conn, cfg, inp, sid, agent_id, role, totals, live):
    """Lock this worker's measured savings (design E.1 / spec 3.9.2).

    The authoritative figure comes from :func:`pa.savings.measured_saved`
    (``retrievals`` x the expert's own requests after each return, priced at the
    expert's cache-read rate).  The live sum in ``running.json`` is recorded
    alongside it: the design says the two agree at close, so a gap between them
    is worth seeing in ``measured_detail_json`` rather than averaging away.
    """
    root = governed(inp)                                      # T11: ungoverned sessions get no savings
    if not root:
        return None
    if session_pre_install(root, cfg, running.session(sid).get("started")):    # T10.1
        return None
    from .. import db, savings

    rets = live.get("retrievals") or {}
    detail = savings.measured_saved(conn, agent_id)
    measured = float(detail.get("saved_usd") or 0.0)
    live_usd = float(live.get("saved_live_usd") or 0.0)
    detail["source"] = "measured"
    detail["role"] = role
    detail["agent_type"] = inp.get("agent_type")
    detail["live_saved_usd"] = live_usd
    detail["ctx_at_end"] = (totals or {}).get("ctx_at_end")
    if measured <= 0 and live_usd > 0 and not detail.get("per_retrieval"):
        # the retrievals rows are not in yet (deferred lock): keep the live sum
        measured = live_usd
        detail["source"] = "live"
    if measured <= 0 and not rets and not detail.get("per_retrieval"):
        # A12: lock any run whose own retrievals row has kept_out_tokens > 0
        try:
            own = conn.execute(
                "SELECT kept_out_tokens FROM retrievals"
                " WHERE run_id=? AND COALESCE(void, 0)=0",
                (agent_id,)).fetchone()
        except Exception:
            own = None
        if not own or not own[0] or int(own[0]) <= 0:
            return None
    db.upsert_savings(conn, {
        "run_id": agent_id, "session_id": sid, "phase": _phase(inp),
        "measured_saved_usd": measured,
        "measured_detail_json": json.dumps(detail, ensure_ascii=False, default=str),
        "locked": 1, "updated": now_iso()})
    log.log("savings_locked", session=sid, agent=agent_id, role=role, usd=round(measured, 6))
    return measured


def _phase(inp):
    root = governed(inp)
    if not root:
        return None
    from . import status_read

    return (status_read(root) or {}).get("phase")


# --------------------------------------------------------------------------- cleanup

def _rebuild(conn, cfg):
    from .. import summary

    summary.request_rebuild(cfg)                   # T5: detached single-flight, not inline


def _cleanup(inp, cfg, sid, agent_id, role):
    running.remove_agent(sid, agent_id)
    _orphan_children(sid, agent_id)
    if role in ("expert", "coder", "critic"):
        live = running.session(sid)
        if live.get("expert") in (None, agent_id) and role == "expert":
            running.set_expert(sid, None)
            root = governed(inp)
            if root:
                status_patch(root, {"run_id": None})
    return None
