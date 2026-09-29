"""One module per Claude Code hook event, plus the helpers they share.

Public (this module):
    EVENTS, BUDGET_MS
    now_iso(), sid_of(inp), agent_of(inp), role_of(inp, cfg), pinned_for(inp, cfg)
    find_project_root(cwd), project_config(root), governed(inp) -> root | None
    project_cutoff(root, cfg=None), session_pre_install(root, cfg, started)
    open_db(), close_db(conn)
    agent_transcript(inp, agent_id=None), main_transcript(inp)
    read_offset(sid), write_offset(sid, offset)
    status_patch(root, fields), status_read(root)
    spool_event(sid, kind, detail), drain_spool(conn, sid, cfg)
    toast(cfg, title, body, kind=None, session_id=None, project_cfg=None, tag=None,
          cause=None, run_id=None, skip=None, detail=None), WAITING_CAUSES
    disk_ok(cfg), waiting_write(sid, payload), waiting_clear(sid)

Import budget: this package and every hot hook import only ``json, os, re,
sys, time`` at module level.  ``pa.db`` (and therefore ``sqlite3``) is imported
inside :func:`open_db`, ``subprocess`` only inside ``pa.accounts`` /
``pa.notify``, so PostToolUse on the main thread costs one ``json.loads`` and a
dict lookup.

Two gates, deliberately different (master plan, "Hooks" row):
  * **ledger sampling** runs in every project -- the usage ledger must see all
    spend, including sessions in repos that never heard of PA3;
  * **governance** (handoff injection, discussion guard, ``.run/status.json``
    patches, PHASE_PLAN protection) runs only where :func:`governed` finds a
    ``.claude/pa.json`` marker walking up from ``cwd``.
"""

import json
import os
import time

EVENTS = ("session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use",
          "subagent_start", "subagent_stop", "stop", "stop_failure", "notification",
          "model_switch", "session_end")

# Watchdog budgets: comfortably under each hook's settings.json timeout (design
# A.3) so a wedged call exits 0 on its own instead of being killed by the harness.
BUDGET_MS = {
    "session_start": 12000, "user_prompt_submit": 4000, "pre_tool_use": 4000,
    "post_tool_use": 4000, "subagent_start": 4000, "subagent_stop": 18000,
    "stop": 8000, "stop_failure": 4000, "notification": 4000, "model_switch": 4000,
    "session_end": 18000,
}

_PROJECT_ROOTS = {}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# --------------------------------------------------------------------------- input shape

def sid_of(inp):
    return (inp or {}).get("session_id") or "unknown"


def agent_of(inp):
    """``agent_id`` when the hook fired inside a subagent, else None."""
    val = (inp or {}).get("agent_id")
    return str(val) if val else None


def role_of(inp, cfg=None):
    from .. import config

    return config.role_from_agent_type((inp or {}).get("agent_type"), cfg)


def pinned_for(inp, cfg=None):
    from .. import config

    return config.pinned_model_for((inp or {}).get("agent_type"), cfg)


def effort_of(inp):
    eff = (inp or {}).get("effort")
    if isinstance(eff, dict):
        return eff.get("level")
    return eff if isinstance(eff, str) else None


# --------------------------------------------------------------------------- project gate

def find_project_root(cwd):
    """Nearest ancestor of ``cwd`` holding ``.claude/pa.json`` (cached per process)."""
    start = os.path.abspath(cwd or os.getcwd())
    if start in _PROJECT_ROOTS:
        return _PROJECT_ROOTS[start]
    cur = start
    found = None
    for _ in range(24):
        try:
            if os.path.isfile(os.path.join(cur, ".claude", "pa.json")):
                found = cur
                break
        except OSError:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    _PROJECT_ROOTS[start] = found
    return found


def governed(inp):
    """Project root when this call is inside a PA3 project, else None."""
    return find_project_root((inp or {}).get("cwd"))


def project_config(root):
    """``.claude/pa.json`` as a dict ({} when absent or unreadable)."""
    if not root:
        return {}
    from .. import fsutil

    return fsutil.read_json(os.path.join(root, ".claude", "pa.json"), {}) or {}


def project_cutoff(root, cfg=None):
    """A governed session's cutoff: the project's own ``.claude/pa.json``
    ``installed_at``, else the ledger's root ``installed_at`` (T10.1)."""
    ts = project_config(root).get("installed_at")
    if isinstance(ts, str) and ts:
        return ts
    ts = (cfg or {}).get("installed_at")
    return ts if isinstance(ts, str) and ts else None


def session_pre_install(root, cfg, started):
    """True when ``started`` (an ISO ts) predates :func:`project_cutoff` (T10.1)."""
    cutoff = project_cutoff(root, cfg)
    return bool(cutoff and started and str(started) < cutoff)


# --------------------------------------------------------------------------- database

def open_db(create=True):
    """Connect to the ledger, creating/migrating the schema when needed."""
    from .. import db, paths

    path = paths.db_path()
    fresh = not os.path.exists(path)
    if fresh and not create:
        return None
    if fresh:
        paths.ensure_ledger_tree()
    conn = db.connect(path)
    if fresh or db.schema_version(conn) != db.SCHEMA_VERSION:
        db.init_schema(conn)
    return conn


def close_db(conn):
    if conn is None:
        return
    from .. import db

    db.close(conn)


def disk_ok(cfg=None):
    """False when free space is under ``ledger.disk_min_free_mb`` (WSL guard)."""
    from .. import fsutil, paths

    floor = 50
    if isinstance(cfg, dict):
        try:
            floor = float((cfg.get("ledger") or {}).get("disk_min_free_mb", 50))
        except (TypeError, ValueError):
            floor = 50
    free = fsutil.disk_free_mb(paths.ledger_dir())
    return (free <= 0.0) or (free >= floor)


# --------------------------------------------------------------------------- transcripts

def main_transcript(inp):
    return (inp or {}).get("transcript_path") or ""


def agent_transcript(inp, agent_id=None):
    """``<dirname(transcript_path)>/<session_id>/subagents/agent-<agent_id>.jsonl``.

    The harness hands SubagentStop this path directly
    (``agent_transcript_path``); every other hook has to build it, and this is
    the shape verified in the probe fixtures.

    When the flat path does not exist, a one-time lookup through
    ``subagents/workflows/*/`` finds workflow agents (T1.1); the resolved path
    is cached in the agent's ``running.json`` entry so subsequent calls are O(1).
    """
    inp = inp or {}
    agent_id = agent_id or agent_of(inp)
    tp = main_transcript(inp)
    if not agent_id or not tp:
        return ""
    # check running.json cache first
    from .. import running as _running

    cached = (_running.agent(sid_of(inp), agent_id) or {}).get("transcript_path")
    if cached and os.path.exists(cached):
        return cached
    base = os.path.dirname(str(tp))
    sid = sid_of(inp)
    flat = os.path.join(base, sid, "subagents", "agent-%s.jsonl" % agent_id)
    if os.path.exists(flat):
        return flat
    # workflow agent: resolve through subagents/workflows/*/
    from .. import transcript as _transcript

    sid_dir = os.path.join(base, sid)
    resolved = _transcript.find_agent_transcript(sid_dir, agent_id)
    if resolved:
        _running.set_agent(sid, agent_id, {"transcript_path": resolved}, create=False)
        return resolved
    return flat       # fall back to the flat path (may not exist yet)


def read_offset(sid):
    from .. import fsutil, paths

    row = fsutil.read_json(paths.offsets_path(sid), {}) or {}
    try:
        return int(row.get("offset") or 0)
    except (TypeError, ValueError):
        return 0


def write_offset(sid, offset):
    from .. import fsutil, paths

    try:
        fsutil.atomic_write_json(paths.offsets_path(sid),
                                 {"offset": int(offset), "ts": now_iso()}, indent=None)
    except Exception:
        pass
    return offset


# --------------------------------------------------------------------------- .run/status.json

def status_path(root):
    return os.path.join(root, ".run", "status.json") if root else ""


def status_read(root):
    from .. import fsutil

    return fsutil.read_json(status_path(root), {}) or {} if root else {}


def status_patch(root, fields):
    """Merge ``fields`` into ``.run/status.json`` (never creates the file).

    ``tools/status.py`` owns the document; hooks only patch ``run_id``,
    ``note`` and ``waiting``, and only when the router already wrote it.
    """
    if not root or not fields:
        return None
    path = status_path(root)
    if not os.path.exists(path):
        return None
    from .. import fsutil

    def _patch(data):
        if not isinstance(data, dict):
            return None
        data.update(fields)
        data["updated"] = now_iso()
        return data

    try:
        return fsutil.locked_update(path, _patch, timeout_ms=1000, default={})
    except Exception:
        return None


# --------------------------------------------------------------------------- waiting marker

def waiting_write(sid, payload):
    """``state/waiting/<sid>.json`` -- statusline line 4 ("waiting on you")."""
    from .. import fsutil, paths

    row = {"session_id": sid, "ts": now_iso()}
    row.update(payload or {})
    try:
        fsutil.atomic_write_json(paths.waiting_path(sid), row, indent=1)
    except Exception:
        pass
    return row


def waiting_clear(sid):
    from .. import paths

    try:
        os.remove(paths.waiting_path(sid))
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- spool

def spool_event(sid, kind, detail=None, run_id=None, account=None):
    """Queue an ``events`` row without opening sqlite (PreToolUse denials).

    The spool carries two line kinds: ``{"t": "sample", …}`` written by the
    statusline sampler (utilization) and ``{"t": "event", …}`` written here.
    Both are drained by :func:`drain_spool` on a cold path.
    """
    from .. import fsutil, paths

    row = {"t": "event", "ts": now_iso(), "session_id": sid, "run_id": run_id,
           "account": account, "kind": kind, "detail": detail}
    try:
        fsutil.append_line(paths.spool_path(sid),
                           json.dumps(row, ensure_ascii=False, default=str))
    except Exception:
        pass
    return row


def _sample_windows(row):
    """Tolerate every shape the sampler may use for ``windows``."""
    out = []
    wins = row.get("windows")
    if isinstance(wins, dict):
        for name, val in wins.items():
            if isinstance(val, dict):
                out.append((name, val.get("pct"), val.get("resets_at")))
            elif isinstance(val, (list, tuple)) and val:
                out.append((name, val[0], val[1] if len(val) > 1 else None))
            elif isinstance(val, (int, float)):
                out.append((name, val, None))
    elif isinstance(wins, list):
        for val in wins:
            if isinstance(val, dict):
                out.append((val.get("window") or val.get("name"), val.get("pct"),
                            val.get("resets_at")))
    return out


def drain_spool(conn, sid, cfg=None):
    """Fold ``spool/<sid>.jsonl`` into ``utilization``/``events`` and remove it.

    The file is renamed aside first so a statusline refresh appending at the same
    moment cannot lose a line; the statusline's own offset drain notices the
    shrunken file and restarts at 0, and ``utilization`` is INSERT OR IGNORE on
    ``UNIQUE(session_id, ts, window)``, so both drains are safe together.
    """
    from .. import db, paths

    path = paths.spool_path(sid)
    if not os.path.exists(path):
        return 0
    n = 0
    work = "%s.drain-%d" % (path, os.getpid())
    try:
        os.replace(path, work)
    except OSError:
        work = path                                       # locked: read it in place
    try:
        with open(work, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        kind = row.get("t") or ("event" if row.get("kind") else "sample")
        try:
            if kind == "event":
                db.insert_event(conn, row.get("kind") or "spool", row.get("detail"),
                                session_id=row.get("session_id") or sid,
                                run_id=row.get("run_id"), account=row.get("account"),
                                ts=row.get("ts"))
                n += 1
            else:
                for window, pct, resets in _sample_windows(row):
                    if not window:
                        continue
                    db.insert_utilization(conn, {
                        "ts": row.get("ts"), "account": row.get("account"),
                        "machine": row.get("machine"), "session_id": row.get("session_id") or sid,
                        "window": window, "pct": pct, "resets_at": resets,
                        "session_cost": row.get("session_cost"), "model": row.get("model"),
                        "source": row.get("source") or "statusline",
                        "reason": row.get("reason") or "spool"})
                    n += 1
        except Exception:
            continue
    try:
        os.remove(work)
    except OSError:
        pass
    return n


# --------------------------------------------------------------------------- toast

# pa.json ``toast: waiting`` sends only these causes (3.9.5 T2, design A C.2)
WAITING_CAUSES = ("question", "review", "replan", "permission", "input", "discussion", "crash")


def toast(cfg, title, body, kind=None, session_id=None, project_cfg=None, tag=None,
          cause=None, run_id=None, skip=None, detail=None):
    """Fire a toast honouring ``config.notify`` and the ``pa.json.toast`` level.

    ``cause`` names why the call site wants to toast (every call site passes one).
    ``empty`` never sends; level ``off`` never; ``waiting`` only :data:`WAITING_CAUSES`;
    ``all`` keeps the pre-3.9.5 rules.  ``skip`` is a caller's own reason not to send
    (``background``, ``stop_hook_active``).  Every decision, sent or not, writes one
    ``events`` row kind ``toast`` (``detail`` adds fields to it).
    """
    from .. import config

    cfg = cfg if isinstance(cfg, dict) else {}
    cause = str(cause or "other")
    level = config.toast_level(project_cfg)
    if cause == "empty":
        result = {"sent": False, "reason": "empty message"}
    elif level == "off":
        result = {"sent": False, "reason": "level off"}
    elif level == "waiting" and cause not in WAITING_CAUSES:
        result = {"sent": False, "reason": "level waiting"}
    elif skip:
        result = {"sent": False, "reason": str(skip)}
    else:
        from .. import notify

        if isinstance(project_cfg, dict) and project_cfg.get("discord_webhook"):
            cfg = dict(cfg)
            merged = dict(cfg.get("notify") or {})
            if not merged.get("discord_webhook"):    # the user-level webhook wins when set;
                merged["discord_webhook"] = project_cfg.get("discord_webhook")   # defaults carry None
            cfg["notify"] = merged
        result = notify.toast(title, notify.plain(body), tag=tag or kind, kind=kind,
                              session_id=session_id, cfg=cfg)
    row = dict(detail) if isinstance(detail, dict) else {}
    row.update({"cause": cause, "level": level, "sent": 1 if result.get("sent") else 0,
                "reason": result.get("reason"), "title": title})
    try:
        from .. import db

        conn = open_db()
        try:
            db.insert_event(conn, "toast", row, session_id=session_id, run_id=run_id)
        finally:
            close_db(conn)
    except Exception:
        pass
    return result


# --------------------------------------------------------------------------- misc

def project_name(inp):
    cwd = (inp or {}).get("cwd") or ""
    root = find_project_root(cwd) or cwd
    return os.path.basename(os.path.normpath(root)) if root else ""


def session_kind(agent_type, cfg=None):
    """``sessions.kind`` for a main-thread ``agent_type`` (design B.1)."""
    name = str(agent_type or "").strip().lower()
    if not name:
        return "other"
    if name.startswith("planner-"):
        return name
    if name in ("router", "review"):
        return name
    from .. import config

    role = config.role_from_agent_type(name, cfg)
    return role if role != "other" else name
