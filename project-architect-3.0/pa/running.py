"""``running.json`` -- what is alive right now (design A section B.2).

Public:
    load(), update(fn), snapshot()
    session(sid), agent(sid, agent_id)
    ensure_session(sid, **fields), patch_session(sid, fields), remove_session(sid)
    new_agent(**fields), set_agent(sid, agent_id, fields), remove_agent(sid, agent_id)
    set_expert(sid, agent_id), set_last_return(sid, payload)
    add_retrieval(sid, parent_id, ret_id, fields)
    bump_after(entry, ts_iso), live_saved(entry, price_read)
    prune(max_age_h=48, now=None)

One machine-global file, keyed by session id, written only under
``fsutil.locked_update`` so the statusline (reader) and several hooks (writers)
cannot tear it.  Everything is best-effort: a corrupt file is replaced by a
fresh skeleton rather than raising into a hook.

Shape::

    {"schema": 1, "updated": "...", "machine": "win",
     "sessions": {"<sid>": {"account", "kind", "project", "started", "expert",
                            "agents": {"<agent_id>": {...}},
                            "last_return": {...}}}}
"""

import time

SCHEMA = 1


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _skeleton():
    from . import paths

    return {"schema": SCHEMA, "updated": _now(), "machine": paths.machine_tag(),
            "sessions": {}}


def _normalize(data):
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("sessions"), dict):
        data["sessions"] = {}
    data.setdefault("schema", SCHEMA)
    if not data.get("machine"):
        from . import paths

        data["machine"] = paths.machine_tag()
    return data


def load():
    """The whole document (a fresh skeleton when missing or corrupt)."""
    from . import fsutil, paths

    data = fsutil.read_json(paths.running_path(), None)
    return _normalize(data) if isinstance(data, dict) else _skeleton()


snapshot = load


def update(fn, timeout_ms=2000):
    """Apply ``fn(data)`` to running.json under the file lock and write it back."""
    from . import fsutil, paths

    def _wrapped(data):
        data = _normalize(data)
        out = fn(data)
        out = data if out is None else out
        out["updated"] = _now()
        return out

    try:
        return fsutil.locked_update(paths.running_path(), _wrapped,
                                    timeout_ms=timeout_ms, default={})
    except Exception:
        from . import log

        log.log("running_update_failed")
        return None


# --------------------------------------------------------------------------- reads

def session(sid):
    return (load().get("sessions") or {}).get(str(sid or "")) or {}


def agent(sid, agent_id):
    return (session(sid).get("agents") or {}).get(str(agent_id or "")) or {}


# --------------------------------------------------------------------------- writes

def _sess(data, sid):
    sessions = data.setdefault("sessions", {})
    entry = sessions.get(str(sid))
    if not isinstance(entry, dict):
        entry = {"started": _now(), "agents": {}}
        sessions[str(sid)] = entry
    if not isinstance(entry.get("agents"), dict):
        entry["agents"] = {}
    return entry


def session_ref(data, sid):
    """The live ``sessions[sid]`` dict of a document inside :func:`update`."""
    return _sess(data, sid)


def agent_ref(data, sid, agent_id, **defaults):
    """The live ``sessions[sid].agents[agent_id]`` dict, created when absent."""
    agents = _sess(data, sid)["agents"]
    entry = agents.get(str(agent_id))
    if not isinstance(entry, dict):
        entry = new_agent(**defaults)
        agents[str(agent_id)] = entry
    return entry


def ensure_session(sid, **fields):
    """Create/merge the session entry (only non-None fields are written)."""
    def _patch(data):
        entry = _sess(data, sid)
        for key, val in fields.items():
            if val is not None:
                entry[key] = val
        return data

    return update(_patch)


def patch_session(sid, fields):
    def _patch(data):
        entry = _sess(data, sid)
        entry.update(fields or {})
        return data

    return update(_patch)


def remove_session(sid):
    def _patch(data):
        data.get("sessions", {}).pop(str(sid), None)
        return data

    return update(_patch)


def new_agent(agent_type=None, role=None, parent=None, task=None, model_pinned=None,
              model_seen=None, started=None, **extra):
    """A fresh ``agents[<id>]`` entry (design A B.2)."""
    entry = {"agent_type": agent_type, "role": role, "parent": parent, "task": task,
             "started": started or _now(), "ctx": 0, "n_requests": 0, "last_msg_id": None,
             "model_pinned": model_pinned, "model_seen": model_seen,
             "mismatch_alerted": False, "handoff_fired": False, "handoff_at": None,
             "cost_live_usd": 0.0, "retrievals": {}, "saved_live_usd": 0.0}
    entry.update({k: v for k, v in extra.items() if v is not None})
    return entry


def set_agent(sid, agent_id, fields, create=True):
    """Merge ``fields`` into ``sessions[sid].agents[agent_id]``."""
    def _patch(data):
        entry = _sess(data, sid)
        agents = entry["agents"]
        cur = agents.get(str(agent_id))
        if not isinstance(cur, dict):
            if not create:
                return None
            cur = new_agent()
            agents[str(agent_id)] = cur
        cur.update(fields or {})
        return data

    return update(_patch)


def remove_agent(sid, agent_id):
    def _patch(data):
        entry = _sess(data, sid)
        entry["agents"].pop(str(agent_id), None)
        if entry.get("expert") == str(agent_id):
            entry["expert"] = None
        return data

    return update(_patch)


def set_expert(sid, agent_id):
    def _patch(data):
        _sess(data, sid)["expert"] = str(agent_id) if agent_id else None
        return data

    return update(_patch)


def set_last_return(sid, payload):
    def _patch(data):
        _sess(data, sid)["last_return"] = payload
        return data

    return update(_patch)


def add_retrieval(sid, parent_id, ret_id, fields):
    """Attach a returned retriever to its parent's live entry (R, returned_at)."""
    def _patch(data):
        entry = _sess(data, sid)
        agents = entry["agents"]
        parent = agents.get(str(parent_id))
        if not isinstance(parent, dict):
            parent = new_agent()
            agents[str(parent_id)] = parent
        rets = parent.setdefault("retrievals", {})
        cur = rets.get(str(ret_id)) or {"R": 0, "returned_at": None, "n_after": 0, "void": False}
        cur.update(fields or {})
        rets[str(ret_id)] = cur
        return data

    return update(_patch)


# --------------------------------------------------------------------------- live math

def bump_after(entry, ts_iso):
    """Count one new expert request against every retrieval that returned before it.

    Mutates ``entry`` in place (it is a plain dict inside the locked update) and
    returns the number of retrievals credited.
    """
    n = 0
    rets = entry.get("retrievals")
    if not isinstance(rets, dict):
        return 0
    for ret in rets.values():
        if not isinstance(ret, dict) or ret.get("void"):
            continue
        returned = ret.get("returned_at")
        if returned and ts_iso and str(returned) > str(ts_iso):
            continue
        ret["n_after"] = int(ret.get("n_after") or 0) + 1
        n += 1
    return n


def live_saved(entry, price_read, price_write=0.0, price_input=None):
    """``sum(R_i * (n_after_i * P_read + P_write)) / 1e6`` from a live agent entry (design E.1,
    3.1: the avoided cache write counts once per retrieval).

    Delegates to :func:`pa.savings.live_saved` so the live number and the locked
    one can never drift apart (``pa.savings`` imports only ``pa.prices``, so this
    stays off sqlite and cheap enough for the PostToolUse hot path).
    """
    from . import savings

    try:
        return float(savings.live_saved(entry, price_read, price_write, price_input))
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- maintenance

def prune(max_age_h=48, now=None):
    """Drop sessions whose ``updated``/``started`` stamp is older than ``max_age_h``."""
    cutoff = (now if now is not None else time.time()) - float(max_age_h) * 3600.0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))

    def _patch(data):
        sessions = data.get("sessions", {})
        dead = [sid for sid, e in sessions.items()
                if isinstance(e, dict) and str(e.get("touched") or e.get("started") or "") < stamp]
        for sid in dead:
            sessions.pop(sid, None)
        return data if dead else None

    return update(_patch)


def touch(sid):
    """Stamp a session as live (prune keeps the last 48 h of touched sessions)."""
    def _patch(data):
        _sess(data, sid)["touched"] = _now()
        return data

    return update(_patch)
