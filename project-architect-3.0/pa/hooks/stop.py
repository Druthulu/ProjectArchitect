"""Stop: fold the main thread's new turns in, then tell the developer.

Input (recon V5): ``session_id, agent_type?, last_assistant_message,
background_tasks[], stop_hook_active``.

Reads only the *new* bytes of the main transcript (``state/offsets/<sid>.json``),
so a long session costs the same here at turn 200 as at turn 2.  The session's
cost comes from the statusline's last sample (``cost.total_cost_usd`` is the
harness's own number, V2) and falls back to the sum of the attributed turns.

Toast policy (design A section C.2):
  * ``stop_hook_active`` -> never (we are inside a stop hook loop);
  * a ``background_tasks`` entry with ``status: "running"`` means the router is
    idle-waiting on an expert, not on the developer -> skip unless
    ``notify.toast_on_stop_with_background``;
  * a question / ``needs-developer`` / ``Recommended:`` ending, or a REPLAN.md /
    REVIEW.md newer than the session start -> **always** toast and write
    ``state/waiting/<sid>.json`` for statusline line 4;
  * otherwise one ordinary toast, rate-limited to one per 20 s per session;
  * 3.9.5 T2: each toast names a cause (question|review|replan|stop; ``empty`` for a
    blank or ``.`` message -- the warmer's pings -- which never sends) and the
    pa.json ``toast`` level decides (see :func:`pa.hooks.toast`).
"""

import json
import os
import time

from .. import log, notify, running
from . import (close_db, drain_spool, governed, now_iso, open_db, pinned_for,
               project_config, project_name, read_offset, role_of, session_pre_install,
               sid_of, status_patch, toast, waiting_clear, waiting_write, write_offset)

_QUESTION_RE = None            # compiled on first use (T5: module-level stdlib ⊆ json, os, sys, time)


def _question_re():
    global _QUESTION_RE
    if _QUESTION_RE is None:
        import re

        _QUESTION_RE = re.compile(r"(needs-developer|Recommended:|\?\s*$)", re.I | re.M)
    return _QUESTION_RE

_TAIL_CHARS = 300
_CAUSES = {"question": "question", "REVIEW.md": "review", "REPLAN.md": "replan"}


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    message = str(inp.get("last_assistant_message") or "")
    if not message.strip():                      # omitted while a background agent runs (2026-09-20)
        message = _last_assistant_text(inp.get("transcript_path"))
    root = governed(inp)
    pcfg = project_config(root)

    live = running.session(sid)
    waiting = _waiting_reason(message, root, live.get("started"))
    background = _running_children(inp.get("background_tasks"))

    # waiting marker and toast before the ledger: the 8 s watchdog ate a close toast (3.9.5 T6)
    if waiting:
        waiting_write(sid, {"reason": waiting, "agent": inp.get("agent_type") or "main",
                            "message": notify.plain(message, 300)})
        if root:
            status_patch(root, {"waiting": waiting})
    else:
        waiting_clear(sid)
    _maybe_toast(cfg, inp, sid, message, waiting, background, pcfg)

    conn = open_db()
    try:
        cost = _latest_sample_cost(sid)
        _ingest(conn, cfg, inp, sid)
        _refresh_children(conn, cfg, sid)
        _stamp_cost(conn, sid, cost)
        _book_main_savings(conn, cfg, sid, root, live.get("started"))
        drain_spool(conn, sid, cfg)
        _rebuild(conn, cfg)
        _restamp_phase(conn, sid, root)
    finally:
        close_db(conn)

    running.touch(sid)
    _repair_savings_flag(cfg, inp, sid, root, pcfg)

    # sweep finished agents that missed their SubagentStop
    try:
        from .. import liveness
        liveness.sweep_if_due(sid, cfg)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- ledger

def _ingest(conn, cfg, inp, sid):
    """Append the main run's new requests to ``turns`` from the byte offset."""
    from .. import accounts, config, db, transcript

    path = inp.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0
    start = read_offset(sid)
    try:
        reqs, end = transcript.read_new_requests(path, start_offset=start)
    except Exception:
        log.log("stop_tail_failed", session=sid)
        return 0
    # Re-check the credentials key each turn; the first Stop after a mid-session
    # account switch books its whole batch to the new account.
    account, _changed, _prev = accounts.refresh_account(sid, cfg, conn)
    rows = [db.turn_row_from_request(r, run_id=sid, session_id=sid, account=account,
                                     ttl_default=config.ttl_for_role("main", cfg))
            for r in reqs]
    if rows:
        db.upsert_turns(conn, rows)
        _model_check(conn, cfg, inp, sid, rows[-1].get("model"))
    write_offset(sid, end)
    return len(rows)


def _restamp_phase(conn, sid, root):
    """Heal ``sessions.phase`` after a router relaunch or takeover restamps router_session (T28)."""
    if not root:
        return
    try:
        from .. import phases
        from . import status_read

        tag = phases.session_phase(root, sid, status_read(root) or {})
        if tag:
            conn.execute("UPDATE sessions SET phase=? WHERE session_id=? AND phase IS NOT ?",
                         (tag, sid, tag))
            phases.restamp_runs(conn, sid, tag)  # T31: the session's runs follow its stamp
    except Exception:
        log.log("restamp_failed", session=sid)


def _refresh_children(conn, cfg, sid):
    """Re-lock subagent runs whose transcript grew since SubagentStop (T1.c2)."""
    try:
        from .. import runs

        runs.refresh_grown_runs(conn, sid, cfg, max_age_s=900, budget_s=0.25)
    except Exception:
        log.log("refresh_failed", session=sid)


def _model_check(conn, cfg, inp, sid, seen):
    """D48 on the main thread (a session launched with ``--agent``)."""
    from .post_tool_use import _claim_once, mismatch

    pinned = pinned_for(inp, cfg)
    if not mismatch(pinned, seen):
        return False
    if not _claim_once("%s-main-%s" % (sid, seen)):
        return False
    from .. import db

    db.insert_event(conn, "model_mismatch",
                    {"agent": inp.get("agent_type") or "main", "expected": pinned,
                     "seen": seen, "source": "main"}, session_id=sid, run_id=sid)
    toast(cfg, "PA3 · model fallback",
          "%s expected %s, request ran on %s" % (inp.get("agent_type") or "main", pinned, seen),
          kind="model_mismatch", session_id=sid,
          project_cfg=project_config(governed(inp)), cause="model", run_id=sid)
    return True


def _last_assistant_text(path):
    """The last assistant text block of the main transcript, read from the tail only."""
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 131072))
            chunk = fh.read()
    except OSError:
        return ""
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw or b'"assistant"' not in raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [b.get("text") for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip()]
            if texts:
                return str(texts[-1])
    return ""


def _latest_sample_cost(sid):
    """``cost.total_cost_usd`` from the newest statusline sample in the spool."""
    from .. import paths

    path = paths.spool_path(sid)
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 65536))
            chunk = fh.read()
    except OSError:
        return None
    cost = None
    for raw in chunk.split(b"\n"):
        raw = raw.strip()
        if not raw or b"session_cost" not in raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("session_cost") is not None:
            try:
                cost = float(row["session_cost"])
            except (TypeError, ValueError):
                continue
    return cost


def _stamp_cost(conn, sid, cost):
    from .. import db, runs

    # T30: the ledger's per-turn pricing is the session cost; the statusline (harness cost-state,
    # which bills a [1m] launch model at the long-context premium) is a cross-check column only.
    row = {"session_id": sid, "cost_usd": runs.session_turn_cost(conn, sid),
           "cost_source": "turns", "cost_updated": now_iso()}
    if cost is not None:
        row["harness_cost_usd"] = cost
    db.upsert_session(conn, row)
    return row["cost_usd"]


def _book_main_savings(conn, cfg, sid, root, started=None):
    """The main run's requests avoid every finished agent's tokens (3.1): recomputed at each
    Stop from the ledger, never locked, so the session's saved figure grows with the router."""
    if not root:                                             # T11: ungoverned sessions get no savings
        return None
    if session_pre_install(root, cfg, started):               # T10.1: session predates the install
        return None
    from .. import db, savings
    from . import status_read

    detail = savings.measured_saved(conn, sid)
    usd = float(detail.get("saved_usd") or 0.0)
    if usd <= 0:
        return None
    detail["source"] = "measured"
    detail["role"] = "main"
    db.upsert_savings(conn, {
        "run_id": sid, "session_id": sid,
        "phase": ((status_read(root) or {}).get("phase") if root else None),
        "measured_saved_usd": usd,
        "measured_detail_json": json.dumps(detail, ensure_ascii=False, default=str),
        "locked": 0, "updated": now_iso()})
    return usd


def _repair_savings_flag(cfg, inp, sid, root, pcfg):
    """T11.1: ``<project>/.run/health.json`` carries ``savings_missing`` (the statusline
    showed ``-``): run ``doctor --repair savings`` in-process; when the row stays FAIL
    stamp ``hook_repair``/``row`` into the flag for the router.  Toast the row either way.
    One exists check when no flag; never raises."""
    project = root or (inp or {}).get("cwd")
    if not project:
        return None
    hpath = os.path.join(project, ".run", "health.json")
    if not os.path.exists(hpath):
        return None
    try:
        from .. import fsutil

        health = fsutil.read_json(hpath, None)
        flag = health.get("savings_missing") if isinstance(health, dict) else None
        if not isinstance(flag, dict):
            return None
        from ..ledger_cli import repair_savings

        ok, row = repair_savings(flag.get("session") or sid, project)
        if not ok:
            health = fsutil.read_json(hpath, None) or {}
            flag = health.get("savings_missing")
            if isinstance(flag, dict):
                flag["hook_repair"] = time.time()
                flag["row"] = row
                fsutil.atomic_write_json(hpath, health, indent=1)
        toast(cfg, "PA3 · %s · savings display" % (project_name(inp) or "session"), row,
              kind="waiting", session_id=sid, project_cfg=pcfg,
              cause="savings" if ok else "crash")
        return ok
    except Exception:
        log.log("stop_repair_savings_failed", session=sid)
        return None


def _rebuild(conn, cfg):
    from .. import summary

    summary.request_rebuild(cfg, sessions_only=True)   # T5: detached single-flight, not inline


# --------------------------------------------------------------------------- waiting / toast

def _waiting_reason(message, root, started=None):
    """``question`` / ``REPLAN.md`` / ``REVIEW.md`` / None.

    A REPLAN/REVIEW file counts only when it is newer than the session start --
    an old one left in ``phase-ends/current/`` is history, not a request.
    """
    tail = str(message or "")[-_TAIL_CHARS:]
    if tail and _question_re().search(tail):
        return "question"
    if not root:
        return None
    import time as _time

    from .. import fsutil, paths

    pp = paths.project_paths(root)
    for name, path in (("REPLAN.md", pp["replan"]), ("REVIEW.md", pp["review"])):
        mt = fsutil.mtime(path)
        if not mt:
            continue
        if not started:
            return name
        stamp = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(mt))
        if stamp >= str(started):
            return name
    return None


def _running_children(background_tasks):
    if not isinstance(background_tasks, list):
        return []
    return [t for t in background_tasks
            if isinstance(t, dict) and str(t.get("status") or "").lower() == "running"]


def _maybe_toast(cfg, inp, sid, message, waiting, background, pcfg):
    skip = "stop_hook_active" if inp.get("stop_hook_active") else None
    allow_bg = bool((cfg.get("notify") or {}).get("toast_on_stop_with_background"))
    if background and not waiting and not allow_bg and not skip:   # non-waiting causes only
        log.log("stop_toast_skipped", session=sid, running=len(background))
        skip = "background"
    cause = _CAUSES.get(waiting, "stop")
    if str(message or "").strip() in ("", "."):  # blank, or the warmer's "." ping
        cause = "empty"
    who = inp.get("agent_type") or "main"
    title = "PA3 · %s · %s" % (project_name(inp) or "session", who)
    if waiting:
        title = "PA3 · waiting on you · %s" % (waiting,)
    kind = "waiting" if waiting in ("REPLAN.md", "REVIEW.md") else ("question" if waiting else "stop")
    return toast(cfg, title, message or "(no message)", kind=kind, session_id=sid,
                 project_cfg=pcfg, cause=cause, run_id=sid, skip=skip)
