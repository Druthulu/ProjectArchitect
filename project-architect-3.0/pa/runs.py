"""``agent_runs`` lifecycle: open, lock from a transcript, link, residual.

Public:
    open_run(conn, run_id, **fields) -> dict
    lock_run_from_transcript(conn, run_id, path, ...) -> dict | None
    aggregate(path, ttl_default="5m", account=None, session_id=None, run_id=None)
    link_parent(conn, run_id, parent_run_id, parent_source="agent_tool", **fields)
    residual_row(conn, session_id, total_cost, account=None, model=None, ts=None)
    defer_lock(payload)                     append to state/pending_locks.jsonl
    pending_locks(), clear_pending()

``lock_run_from_transcript`` streams the file through
``pa.transcript.iter_requests`` (keep-LAST per ``message.id``, recon V1), writes
every request into ``turns`` (INSERT OR REPLACE -- re-locking the same run is
idempotent) and merges the totals into ``agent_runs``.  Memory is bounded by the
longest line, so a 256 MB subagent transcript streams; the *caller* decides
whether the file is small enough to parse inline
(``ledger.lock_inline_max_bytes``) or has to be deferred with
:func:`defer_lock`.
"""

import json
import os
import time


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# --------------------------------------------------------------------------- rows

def open_run(conn, run_id, **fields):
    """Create/merge an ``agent_runs`` row for a run that just started."""
    from . import db

    row = {"run_id": run_id, "status": "running", "started": _now()}
    row.update({k: v for k, v in fields.items() if v is not None})
    db.upsert_agent_run(conn, row)
    return row


def link_parent(conn, run_id, parent_run_id, parent_source="agent_tool", **fields):
    """Record child -> parent (``tool_response.agentId`` is authoritative, V4)."""
    from . import db

    row = {"run_id": run_id, "parent_run_id": parent_run_id, "parent_source": parent_source}
    row.update({k: v for k, v in fields.items() if v is not None})
    db.upsert_agent_run(conn, row)
    return row


# --------------------------------------------------------------------------- transcript lock

def aggregate(path, ttl_default="5m", account=None, session_id=None, run_id=None,
              effort=None):
    """Stream a transcript into ``(turn_rows, totals)`` without touching sqlite."""
    from . import db, transcript

    rows = []
    totals = {"turns": 0, "seed_ctx": None, "ctx_at_end": 0, "input": 0,
              "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0,
              "thinking": 0, "cost_usd": 0.0, "model_seen": None, "effort": None,
              "started": None, "ended": None, "last_msg_id": None}
    for req in transcript.iter_requests(path):
        row = db.turn_row_from_request(req, run_id=run_id, session_id=session_id,
                                       account=account, ttl_default=ttl_default,
                                       effort=effort or req.get("effort"))
        totals["effort"] = row.get("effort") or totals["effort"]
        rows.append(row)
        totals["turns"] += 1
        if totals["seed_ctx"] is None:
            totals["seed_ctx"] = int(req.get("ctx") or 0)
            totals["started"] = req.get("ts_iso")
        totals["ctx_at_end"] = int(req.get("ctx") or 0)
        totals["input"] += int(row.get("input") or 0)
        totals["cache_write_5m"] += int(row.get("cache_write_5m") or 0)
        totals["cache_write_1h"] += int(row.get("cache_write_1h") or 0)
        totals["cache_read"] += int(row.get("cache_read") or 0)
        totals["output"] += int(row.get("output") or 0)
        totals["thinking"] += int(row.get("thinking") or 0)
        totals["cost_usd"] += float(row.get("cost_usd") or 0.0)
        totals["model_seen"] = row.get("model") or totals["model_seen"]
        totals["ended"] = req.get("ts_iso") or totals["ended"]
        totals["last_msg_id"] = row.get("msg_id") or totals["last_msg_id"]
    if totals["seed_ctx"] is None:
        totals["seed_ctx"] = 0
    return rows, totals


def lock_run_from_transcript(conn, run_id, path, session_id=None, account=None,
                             ttl_default="5m", status="completed", effort=None,
                             lock_source="transcript", extra=None):
    """Parse ``path``, write its ``turns`` and lock the ``agent_runs`` row.

    Returns the totals dict (``None`` when the file is missing/empty).
    """
    from . import db

    if not path or not os.path.exists(path):
        return None
    rows, totals = aggregate(path, ttl_default=ttl_default, account=account,
                             session_id=session_id, run_id=run_id, effort=effort)
    if rows:
        db.upsert_turns(conn, rows)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    row = {"run_id": run_id, "session_id": session_id, "status": status,
           "turns": totals["turns"], "seed_ctx": totals["seed_ctx"],
           "ctx_at_end": totals["ctx_at_end"], "input": totals["input"],
           "cache_write_5m": totals["cache_write_5m"],
           "cache_write_1h": totals["cache_write_1h"],
           "cache_read": totals["cache_read"], "output": totals["output"],
           "thinking": totals["thinking"], "cost_usd": totals["cost_usd"],
           "model_seen": totals["model_seen"], "effort": effort or totals.get("effort"),
           "ended": totals["ended"] or _now(),
           "locked": 1, "lock_source": lock_source,
           "transcript_path": path, "transcript_bytes": size}
    if totals["started"]:
        row["started"] = totals["started"]
    if isinstance(extra, dict):
        row.update({k: v for k, v in extra.items() if v is not None})
    db.upsert_agent_run(conn, row)
    totals["transcript_bytes"] = size
    return totals


# --------------------------------------------------------------------------- residual

def session_turn_cost(conn, session_id, kind="api"):
    """Sum of ``turns.cost_usd`` attributed to one session."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM turns "
                       "WHERE session_id=? AND kind=?", (session_id, kind)).fetchone()
    return float(row[0] or 0.0) if row else 0.0


def residual_row(conn, session_id, total_cost, account=None, model=None, ts=None,
                 min_usd=0.005):
    """Book the gap between ``cost-state`` and the attributed turns (recon V2).

    ``away_summary`` recaps and ``ai-title`` calls are billed but never appear in
    a transcript, so a session's true cost exceeds the sum of its turns by
    2.7-4.5 %.  One ``turns`` row with ``kind='residual'`` (idempotent id
    ``residual:<sid>``) keeps the books honest without inventing requests.
    """
    from . import db

    try:
        total = float(total_cost or 0.0)
    except (TypeError, ValueError):
        return None
    attributed = session_turn_cost(conn, session_id)
    gap = total - attributed
    if gap <= float(min_usd):
        return None
    row = {"msg_id": "residual:%s" % session_id, "request_id": None,
           "run_id": session_id, "session_id": session_id, "account": account,
           "ts": ts or _now(), "model": model, "input": 0, "cache_write_5m": 0,
           "cache_write_1h": 0, "cache_write": 0, "cache_read": 0, "output": 0,
           "ctx": 0, "new_tokens": 0, "cost_usd": gap, "kind": "residual"}
    db.upsert_turn(conn, row)
    db.insert_event(conn, "residual", {"session_id": session_id, "total": total,
                                       "attributed": attributed, "residual": gap},
                    session_id=session_id, account=account)
    return row


# --------------------------------------------------------------------------- refresh grown runs

def refresh_grown_runs(conn, sid, cfg, max_age_s=None, budget_s=0.25):
    """Re-lock runs whose transcript file grew since the original lock.

    For every locked run of the session with a ``transcript_path`` (skip
    run_id == sid), compare ``os.path.getsize(path)`` with
    ``agent_runs.transcript_bytes``; when the file grew, re-lock it with
    ``lock_run_from_transcript`` (keeps the row's status/effort/kind,
    ``lock_source='refresh'``; ``db.upsert_turns`` adds only the new rows
    because ``msg_id`` is the key), then recompute the savings row.
    Stops after ``budget_s`` of wall time (log the leftover).
    """
    from . import config, db, log, savings

    t0 = time.time()
    try:
        rows = conn.execute(
            "SELECT run_id, transcript_path, transcript_bytes, status, effort, kind, ended"
            " FROM agent_runs WHERE session_id=? AND run_id != ?"
            " AND locked=1 AND transcript_path IS NOT NULL",
            (sid, sid)).fetchall()
    except Exception:
        return 0
    refreshed = 0
    for row in rows:
        if time.time() - t0 > float(budget_s):
            left = len(rows) - refreshed
            log.log("refresh_budget", session=sid, left=left)
            break
        path = row["transcript_path"]
        if not path or not os.path.exists(path):
            continue
        old_bytes = int(row["transcript_bytes"] or 0)
        try:
            cur_bytes = os.path.getsize(path)
        except OSError:
            continue
        if cur_bytes <= old_bytes:
            continue
        if max_age_s is not None and row["ended"]:
            import re as _re
            ended = str(row["ended"])
            # quick ISO parse: strip trailing Z, parse as UTC
            m = _re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", ended)
            if m:
                import calendar
                parts = tuple(int(x) for x in m.groups())
                ended_ts = calendar.timegm(parts)
                if time.time() - ended_ts > float(max_age_s):
                    continue
        from . import accounts

        account, _src = accounts.account_for(sid)
        rid = row["run_id"]
        old_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE run_id=? AND kind='api'", (rid,)).fetchone()[0]
        totals = lock_run_from_transcript(
            conn, rid, path, session_id=sid, account=account,
            ttl_default=config.ttl_for_role(row["kind"] or "other", cfg),
            status=row["status"], effort=row["effort"],
            lock_source="refresh")
        if totals is None:
            continue
        new_turns = totals["turns"]
        added = new_turns - old_turns
        # recompute savings
        detail = savings.measured_saved(conn, rid)
        usd = float(detail.get("saved_usd") or 0.0)
        if usd > 0 or conn.execute(
                "SELECT 1 FROM savings WHERE run_id=?", (rid,)).fetchone():
            detail["source"] = "measured"
            db.upsert_savings(conn, {
                "run_id": rid, "session_id": sid,
                "measured_saved_usd": usd,
                "measured_detail_json": json.dumps(detail, ensure_ascii=False, default=str),
                "locked": 1, "updated": _now()})
        log.log("run_refreshed", session=sid, agent=rid, added=added)
        refreshed += 1
    return refreshed


# --------------------------------------------------------------------------- deferred locks

def defer_lock(payload):
    """Queue a transcript too large to parse in a hook (``pending_locks.jsonl``)."""
    from . import fsutil, paths

    row = dict(payload or {})
    row.setdefault("ts", _now())
    fsutil.append_line(paths.pending_locks_path(),
                       json.dumps(row, ensure_ascii=False, default=str))
    return row


def pending_locks():
    """Every queued lock request (skips corrupt lines)."""
    from . import paths

    out = []
    try:
        with open(paths.pending_locks_path(), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def clear_pending():
    from . import paths

    try:
        os.remove(paths.pending_locks_path())
        return True
    except OSError:
        return False
