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

Undelivered wake lines (3.15 T6): main session only, each new line of
``<root>/.run/warmer/<sid>.undelivered`` (offset in ``<sid>.undelivered.off``) is
consumed one per Stop call and returns ``{"decision": "block", "reason": …}`` telling
the router to arm its Monitor, even under ``stop_hook_active`` (bounded: one per line).
"""

import json
import os
import time

from .. import log, notify, running
from . import (agent_of, close_db, drain_spool, governed, now_iso, open_db, pinned_for,
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
    _repair_savings_flag(cfg, inp, sid, root)

    # sweep finished agents that missed their SubagentStop
    try:
        from .. import liveness
        liveness.sweep_if_due(sid, cfg)
    except Exception:
        pass
    if root and sid and not agent_of(inp):       # 3.15 T6: an unrelayed wake line blocks once
        return _undelivered_block(root, sid)
    return None


_ARM_REASON = ("Arm `Monitor` on `tail -n0 -F .run/warmer/%s.wake` (timeout 30 min), "
               "then end the turn with `.`")


def _undelivered_block(root, sid):
    """Consume one new line of ``.run/warmer/<sid>.undelivered`` -> a block dict, else None."""
    p = os.path.join(root, ".run", "warmer", "%s.undelivered" % sid)
    off_p = p + ".off"
    try:
        if not os.path.exists(p):
            return None
        try:
            with open(off_p, encoding="utf-8") as fh:
                off = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            off = 0
        if off > os.path.getsize(p):                 # file replaced: start over
            off = 0
        with open(p, "rb") as fh:
            fh.seek(off)
            raw = fh.readline()
        if not raw.endswith(b"\n"):                 # nothing new, or a partial line
            return None
        with open(off_p, "w", encoding="utf-8") as fh:
            fh.write(str(off + len(raw)))
        return {"decision": "block", "reason": _ARM_REASON % sid}
    except Exception:
        log.log("stop_undelivered_failed", session=sid)
        return None


# --------------------------------------------------------------------------- ledger

def ledger_seed(conn, sid):
    """``(prev_ts, start_index)`` of the run's turns already in the ledger.

    The last turn's ts and the turn count, so a new batch's first request gets its
    real gap / cold / rewrite (T7). Any failure -> ``(None, 0)``; never breaks a hook.
    """
    try:
        row = conn.execute("SELECT MAX(ts), COUNT(*) FROM turns WHERE run_id=?", (sid,)).fetchone()
        return (row[0] or None), int(row[1] or 0)
    except Exception:
        return None, 0


def _ingest(conn, cfg, inp, sid):
    """Append the main run's new requests to ``turns`` from the byte offset."""
    from .. import accounts, config, db, transcript

    path = inp.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0
    start = read_offset(sid)
    prev_ts, start_index = ledger_seed(conn, sid)
    try:
        reqs, end = transcript.read_new_requests(path, start_offset=start, prev_ts=prev_ts,
                                                 start_index=start_index)
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


def _repair_savings_flag(cfg, inp, sid, root):
    """T11.1: ``<project>/.run/health.json`` carries ``savings_missing`` (the statusline
    showed ``-``).  The flagged session's ``summary.json`` entry has its ``windows`` block
    again -> drop the flag.  Otherwise start the repair detached (:func:`repair_savings_main`),
    at most once per session per ``summary.savings_repair_cooldown_s``.  Never inline: there
    a recalc of a long session outlived the 8 s watchdog on every Stop, so the flag never
    cleared and the rest of the hook never ran (3.15.3: a 6,700-turn router, 19 of 19 Stops
    killed).  One exists check when no flag; never raises."""
    project = root or (inp or {}).get("cwd")
    if not project:
        return None
    hpath = os.path.join(project, ".run", "health.json")
    if not os.path.exists(hpath):
        return None
    try:
        from .. import config, fsutil, paths

        health = fsutil.read_json(hpath, None)
        flag = health.get("savings_missing") if isinstance(health, dict) else None
        if not isinstance(flag, dict):
            return None
        target = flag.get("session") or sid
        doc = fsutil.read_json(paths.summary_path(), None)
        entry = ((doc.get("sessions") if isinstance(doc, dict) else None) or {}).get(target)
        if isinstance(entry, dict) and "windows" in entry:   # a rebuild landed: the value shows
            health.pop("savings_missing", None)
            fsutil.atomic_write_json(hpath, health, indent=1)
            return True
        cooldown = float(config.get(cfg, "summary.savings_repair_cooldown_s", 900) or 0)
        stamp = paths.state_path("savings_repair", "%s.stamp" % target)
        now = time.time()
        try:
            with open(stamp, encoding="utf-8") as fh:
                last = float(fh.read().strip())
        except (OSError, ValueError):
            last = None
        if last is not None and 0 <= now - last < cooldown:
            return None
        fsutil.atomic_write_text(stamp, "%.3f\n" % now)
        cmd, env = repair_savings_command(target, project)
        return bool(notify.spawn_detached(cmd, env))
    except Exception:
        log.log("stop_repair_savings_failed", session=sid)
        return None


def repair_savings_command(sid, project):
    """``(cmd, env)`` of the detached repair (``python -m pa.hooks.stop --repair-savings``)."""
    import sys

    pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # -s -P, not -I: -I ignores PYTHONPATH, which is how the installed copy is found
    return ([sys.executable, "-s", "-P", "-X", "utf8", "-m", "pa.hooks.stop",
             "--repair-savings", str(sid), str(project)], {"PYTHONPATH": pkg})


def repair_savings_main(argv=None):
    """``python -m pa.hooks.stop --repair-savings <sid> <project>``: the Stop hook's repair,
    detached (3.15.3).  ``doctor --repair savings`` for the flagged session; a FAIL row
    stamps ``hook_repair``/``row`` into that session's flag for the router (design A B.5);
    the row is toasted either way.  Prints nothing; never raises."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        at = args.index("--repair-savings")
        sid, project = args[at + 1], args[at + 2]
    except (ValueError, IndexError):
        return 2
    try:
        from .. import config, fsutil
        from ..ledger_cli import repair_savings

        cfg = config.load()
        ok, row = repair_savings(sid, project)
        if not ok:
            hpath = os.path.join(project, ".run", "health.json")
            health = fsutil.read_json(hpath, None) or {}
            flag = health.get("savings_missing")
            if isinstance(flag, dict) and (flag.get("session") or sid) == sid:
                flag["hook_repair"] = time.time()
                flag["row"] = row
                fsutil.atomic_write_json(hpath, health, indent=1)
        name = os.path.basename(os.path.normpath(project)) or "session"
        toast(cfg, "PA3 · %s · savings display" % name, row, kind="waiting", session_id=sid,
              project_cfg=project_config(project), cause="savings" if ok else "crash")
    except Exception as exc:
        log.log("stop_repair_savings_failed", session=sid, err=repr(exc)[:200])
    return 0


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


if __name__ == "__main__":
    import sys

    sys.exit(repair_savings_main())
