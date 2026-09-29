"""SessionEnd: close the books on a session.

Input: ``session_id, reason`` (+ the usual paths).  Budget 1.5 s, timeout 20 s.

Order matters: tail the main transcript one last time (from the stored byte
offset) so the final turns are attributed, then take the session's cost from the
**last ``cost-state`` record** -- the harness's own total, which recon V2 showed
includes ``system/away_summary`` recaps and ``ai-title`` calls that never appear
in any transcript.  The difference between that total and the attributed turns
is booked as one ``turns`` row with ``kind='residual'`` instead of being hidden.

Then: finish the small deferred locks (``state/pending_locks.jsonl``), drain the
spool, rebuild ``summary.json``, checkpoint the WAL and drop the session from
``running.json``.
"""

import os

from .. import log, running
from . import (close_db, drain_spool, governed, now_iso, open_db, read_offset,
               sid_of, status_patch, waiting_clear, write_offset)


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    reason = inp.get("reason") or "other"
    path = inp.get("transcript_path")

    conn = open_db()
    try:
        _ingest(conn, cfg, inp, sid, path)
        cost, source, harness, fresh = _final_cost(conn, sid, path)
        _close_session(conn, sid, reason, cost, source, inp, harness, fresh)
        _interrupt_running(conn, sid, inp)
        _finish_pending(conn, cfg)
        drain_spool(conn, sid, cfg)
        _refresh_children(conn, cfg, sid)
        _rebuild(conn, cfg)
        from .. import db

        db.wal_checkpoint(conn, "TRUNCATE")
    finally:
        close_db(conn)

    running.remove_session(sid)
    waiting_clear(sid)
    root = governed(inp)
    if root:
        try:
            from .. import warmer
            warmer.stop(root, sid)             # 3.9.5 T4: the session's warmer ends with it
        except Exception as exc:
            log.log("warmer_stop_failed", session_id=sid, error=str(exc)[:200])
        # keep run_id/task/attempt/expert_agent_type: the harness kills every subagent with the
        # process (T7.1) -- a later SubagentStart/spawn overwrites the run as today.
        status_patch(root, {"killed_at": now_iso(), "killed_reason": reason})
    log.log("session_end", session=sid, reason=reason)
    return None


# --------------------------------------------------------------------------- turns / cost

def _ingest(conn, cfg, inp, sid, path):
    from .. import accounts, config, db, transcript

    if not path or not os.path.exists(path):
        return 0
    try:
        reqs, end = transcript.read_new_requests(path, start_offset=read_offset(sid))
    except Exception:
        log.log("session_end_tail_failed", session=sid)
        return 0
    account, _src = accounts.account_for(sid)
    rows = [db.turn_row_from_request(r, run_id=sid, session_id=sid, account=account,
                                     ttl_default=config.ttl_for_role("main", cfg))
            for r in reqs]
    if rows:
        db.upsert_turns(conn, rows)
    write_offset(sid, end)
    return len(rows)


def _final_cost(conn, sid, path):
    """``(cost, source, harness, fresh)`` -- the turns sum is the session's cost (T30).

    ``harness`` is the ``cost-state`` total (fresh, else stale, else the last
    statusline figure, else None), a cross-check only: the harness bills a
    ``[1m]`` launch model at the long-context premium every turn.  ``fresh`` says
    a fresh cost-state was seen (it keys the residual booking).
    """
    from .. import runs, transcript

    turns_sum = runs.session_turn_cost(conn, sid)
    if path and os.path.exists(path):
        try:
            rec, fresh, _offset = transcript.cost_state_fresh(path)
        except Exception:
            rec, fresh = None, False
        if isinstance(rec, dict):
            for key in ("totalCostUSD", "total_cost_usd", "costUSD"):
                val = rec.get(key)
                if val is None and isinstance(rec.get("costState"), dict):
                    val = rec["costState"].get(key)
                if val is not None:
                    try:
                        cost_val = float(val)
                    except (TypeError, ValueError):
                        continue
                    if fresh:
                        return turns_sum, "turns", cost_val, True
                    # T30: a stale total is no longer a floor under the turns sum; kept as harness
                    log.log("cost_state_stale", session=sid, stale_total=cost_val,
                            turns_sum=turns_sum)
                    return turns_sum, "turns", cost_val, False
    row = conn.execute("SELECT harness_cost_usd FROM sessions WHERE session_id=?",
                       (sid,)).fetchone()
    harness = float(row[0]) if row and row[0] is not None else None
    return turns_sum, "turns", harness, False


def _close_session(conn, sid, reason, cost, source, inp, harness=None, fresh=False):
    from .. import accounts, db, runs

    account, _src = accounts.account_for(sid)
    now = now_iso()
    row = {"session_id": sid, "ended": now, "end_reason": reason,
           "cost_usd": cost, "cost_source": source, "cost_updated": now}
    if harness is not None:
        row["harness_cost_usd"] = harness
    db.upsert_session(conn, row)
    db.upsert_agent_run(conn, {"run_id": sid, "session_id": sid, "ended": now,
                               "status": "completed", "locked": 1,
                               "lock_source": "session_end"})
    _sum_main_run(conn, sid)
    if fresh and harness is not None:          # T30: keyed on a fresh cost-state, not on source
        runs.residual_row(conn, sid, harness, account=account, ts=now)
    return cost


def _interrupt_running(conn, sid, inp=None):
    """A subagent still marked running when its session ends died with it (a Ctrl-C, a /clear, a
    crash): mark it ``interrupted`` and book its transcript when one exists (T1.c2)."""
    try:
        rows = conn.execute(
            "SELECT run_id, transcript_path FROM agent_runs"
            " WHERE session_id=? AND run_id != ? AND status='running'",
            (sid, sid)).fetchall()
    except Exception:
        return
    for row in rows:
        rid = row["run_id"]
        path = row["transcript_path"]
        if not path or not os.path.exists(path):
            # try to find the transcript from the hooks' path builder (T1.1: resolves workflows)
            try:
                from . import agent_transcript as _at

                path = _at(inp, rid)
            except Exception:
                path = None
        if path and os.path.exists(path):
            try:
                from .. import runs

                totals = runs.lock_run_from_transcript(
                    conn, rid, path, session_id=sid,
                    status="interrupted", lock_source="session_end")
                if totals is not None:
                    continue           # lock_run_from_transcript wrote the row
            except Exception:
                pass
        # no transcript or lock failed: plain status update
        try:
            conn.execute(
                "UPDATE agent_runs SET status='interrupted', ended=COALESCE(ended, ?)"
                " WHERE run_id=?", (now_iso(), rid))
        except Exception:
            pass


def _refresh_children(conn, cfg, sid):
    """Re-lock subagent runs whose transcript grew since SubagentStop (T1.c2)."""
    try:
        from .. import runs

        runs.refresh_grown_runs(conn, sid, cfg, max_age_s=None, budget_s=1.0)
    except Exception:
        pass


def _sum_main_run(conn, sid):
    """Roll this session's main-thread turns into its ``agent_runs`` row."""
    from .. import db

    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(cache_write_5m),0), "
        "COALESCE(SUM(cache_write_1h),0), COALESCE(SUM(cache_read),0), "
        "COALESCE(SUM(output),0), COALESCE(SUM(thinking),0), COALESCE(SUM(cost_usd),0), "
        "MAX(ctx) FROM turns WHERE run_id=? AND kind='api'", (sid,)).fetchone()
    if not row or not row[0]:
        return None
    db.upsert_agent_run(conn, {
        "run_id": sid, "turns": row[0], "input": row[1], "cache_write_5m": row[2],
        "cache_write_1h": row[3], "cache_read": row[4], "output": row[5],
        "thinking": row[6], "cost_usd": row[7], "ctx_at_end": row[8]})
    return row[0]


# --------------------------------------------------------------------------- pending locks

def _finish_pending(conn, cfg):
    """Lock queued transcripts that now fit inside the inline budget."""
    from .. import runs

    rows = runs.pending_locks()
    if not rows:
        return 0
    limit = 0
    try:
        limit = int((cfg.get("ledger") or {}).get("lock_inline_max_bytes", 20_000_000))
    except (TypeError, ValueError):
        limit = 20_000_000
    left, done = [], 0
    for row in rows:
        path = row.get("path")
        try:
            size = os.path.getsize(path) if path else None
        except OSError:
            size = None
        if size is None or size > limit:
            left.append(row)
            continue
        from .. import accounts, config

        account, _src = accounts.account_for(row.get("session_id"))
        try:
            runs.lock_run_from_transcript(
                conn, row.get("run_id"), path, session_id=row.get("session_id"),
                account=account,
                ttl_default=config.ttl_for_role(row.get("role") or "other", cfg),
                lock_source="pending")
            done += 1
        except Exception:
            left.append(row)
    runs.clear_pending()
    for row in left:
        runs.defer_lock(row)
    return done


def _rebuild(conn, cfg):
    from .. import summary

    summary.request_rebuild(cfg)                   # T5: detached single-flight, not inline
