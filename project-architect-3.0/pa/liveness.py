"""Liveness sweep: close running entries whose agents have actually finished.

Public:
    sweep(sid, cfg, now=None, min_age_s=300) -> [swept agent ids]

Two detection paths:
  1. **parent_tool_result** -- the child's meta names a ``toolUseId``; when the
     parent's transcript holds a ``tool_result`` with that id, the child has
     returned and its hook was missed.
  2. **stale_tool_result** -- the child's own transcript ends on a ``user``
     ``tool_result`` older than ``liveness.dead_min`` minutes; the model answers
     within seconds, so silence after 20 minutes means the turn limit was hit.
     An entry whose last record is an assistant ``tool_use`` is never swept by
     age (a tool call may be a long gate or wait; the warmer, ``pa/warmer.py``, pings idle runs).

Swept agents are closed through :func:`pa.hooks.subagent_stop.close_run` so
they are locked and booked like a normal SubagentStop.
"""

import json
import os
import sys
import time

from . import config as _config
from . import log
from . import running


# --------------------------------------------------------------------------- config helpers

def _dead_min(cfg):
    try:
        return float(_config.get(cfg, "liveness.dead_min", 20) or 20)
    except (TypeError, ValueError):
        return 20.0


def _scan_bytes(cfg):
    try:
        return int(_config.get(cfg, "liveness.scan_bytes", 4194304) or 4194304)
    except (TypeError, ValueError):
        return 4194304


# --------------------------------------------------------------------------- transcript helpers

def _read_meta(transcript_path):
    """``agent-<id>.meta.json`` beside the transcript."""
    p = str(transcript_path or "")
    if p.lower().endswith(".jsonl"):
        p = p[:-6] + ".meta.json"
    else:
        p = p + ".meta.json"
    try:
        with open(p, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _last_record(path):
    """The last non-empty JSON line of a JSONL transcript, or None."""
    if not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    read_size = min(size, 131072)  # 128 KB tail is enough for one record
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - read_size))
            chunk = fh.read()
    except OSError:
        return None
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw:
            continue
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
    return None


def _scan_parent_for_tool_use_id(parent_path, tool_use_id, scan_bytes):
    """True when the last *scan_bytes* of *parent_path* contain a tool_result
    record whose ``tool_use_id`` matches."""
    if not parent_path or not tool_use_id:
        return False
    try:
        size = os.path.getsize(parent_path)
    except OSError:
        return False
    read_size = min(size, scan_bytes)
    try:
        with open(parent_path, "rb") as fh:
            fh.seek(max(0, size - read_size))
            chunk = fh.read()
    except OSError:
        return False
    # fast pre-check: the tool_use_id string must appear in the chunk
    needle = tool_use_id.encode("utf-8", "replace")
    if needle not in chunk:
        return False
    for raw in chunk.split(b"\n"):
        raw = raw.strip()
        if not raw or needle not in raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        # check message.content for tool_result blocks
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if (isinstance(block, dict) and
                        block.get("type") == "tool_result" and
                        block.get("tool_use_id") == tool_use_id):
                    return True
    return False


def _is_tool_result(rec):
    """True when *rec* is a user message containing a tool_result."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return False
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
    return False


def _is_tool_use(rec):
    """True when *rec* is an assistant message containing a tool_use."""
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return False
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
    return False


def _record_epoch(rec):
    """Epoch seconds from a record's timestamp field."""
    ts = (rec or {}).get("timestamp") or ""
    if not ts:
        return None
    try:
        from .statusline import parse_epoch
        return parse_epoch(ts)
    except Exception:
        return None


# --------------------------------------------------------------------------- parent resolution

def _find_parent_transcript(agent_path, parent_id, sid):
    """Locate the parent's transcript file.

    If *parent_id* is an agent, look for ``agent-<parent_id>.jsonl`` in the same
    subagents directory.  Otherwise (parent is the session) look for the main
    session transcript ``<sid>.jsonl`` one level up from the ``subagents/`` dir.
    """
    if not agent_path:
        return None
    agent_dir = os.path.dirname(str(agent_path))
    if parent_id and parent_id != sid:
        # parent is another agent in the same subagents dir
        candidate = os.path.join(agent_dir, "agent-%s.jsonl" % parent_id)
        if os.path.exists(candidate):
            return candidate
    # parent is the session: transcript is <projects>/<sid>.jsonl
    # agent_path is <projects>/<sid>/subagents/agent-<id>.jsonl
    # so go up two levels from subagents dir
    parts = agent_dir.replace("\\", "/").rstrip("/").split("/")
    # the subagents dir structure: .../projects/<sid>/subagents/
    if len(parts) >= 2 and parts[-1] == "subagents":
        session_dir = "/".join(parts[:-1])
        # main transcript: ../<sid>.jsonl  (one level above the <sid> dir)
        parent_dir = os.path.dirname(session_dir)
        candidate = os.path.join(parent_dir, "%s.jsonl" % sid)
        if os.path.exists(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------- throttle

_SWEEP_STAMP_FILE = "liveness_sweep.json"


def _should_sweep(now, interval_s=60):
    """True when at least *interval_s* have passed since the last sweep."""
    from . import paths

    stamp_path = os.path.join(paths.state_dir(), _SWEEP_STAMP_FILE)
    try:
        with open(stamp_path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read())
        last = float(data.get("ts", 0))
    except (OSError, ValueError):
        last = 0.0
    return (now - last) >= interval_s


def _write_stamp(now):
    """Record the sweep timestamp."""
    from . import paths

    stamp_path = os.path.join(paths.state_dir(), _SWEEP_STAMP_FILE)
    try:
        os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
        with open(stamp_path, "w", encoding="utf-8") as fh:
            json.dump({"ts": now}, fh)
    except OSError:
        pass


# --------------------------------------------------------------------------- sweep

def sweep(sid, cfg, now=None, min_age_s=300):
    """Sweep finished agents from the session's running entries.

    Returns a list of swept agent ids.
    """
    now = now if now is not None else time.time()
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(now - min_age_s))
    dead_s = _dead_min(cfg) * 60.0
    scan_limit = _scan_bytes(cfg)

    live = running.session(sid)
    agents = live.get("agents")
    if not isinstance(agents, dict):
        return []

    swept = []
    for agent_id, entry in list(agents.items()):
        if not isinstance(entry, dict):
            continue
        if str(agent_id) == str(sid):
            continue  # the main thread's own live entry: no subagent transcript to find
        started = entry.get("started") or ""
        if not started or str(started) > cutoff_iso:
            continue  # too young

        # transcript path from the running entry
        path = entry.get("transcript_path") or ""
        agent_type = entry.get("agent_type") or ""
        parent_in_entry = entry.get("parent") or ""

        # read meta for toolUseId and parentAgentId
        meta = _read_meta(path) if path else {}
        tool_use_id = meta.get("toolUseId")
        meta_parent = meta.get("parentAgentId")

        # repair parent: entry says session but meta names an agent
        if meta_parent and str(parent_in_entry) == sid and str(meta_parent) != sid:
            running.set_agent(sid, agent_id, {"parent": str(meta_parent),
                                              "parent_source": "meta_repair"})
            parent_in_entry = str(meta_parent)

        # determine effective parent for transcript lookup
        effective_parent = str(meta_parent) if meta_parent else parent_in_entry

        # find project cwd from the session entry for close_run
        cwd = live.get("project") or ""

        # Path 1: parent_tool_result
        if tool_use_id:
            parent_path = _find_parent_transcript(path, effective_parent, sid)
            if parent_path and _scan_parent_for_tool_use_id(parent_path, tool_use_id, scan_limit):
                _do_close(cfg, sid, agent_id, agent_type, path, cwd, "parent_tool_result")
                swept.append(agent_id)
                continue
            # parent transcript not found or no match yet -> fall through to path 2

        # Path 2: stale_tool_result (fallback)
        if not path or not os.path.exists(path):
            # no transcript: just drop the entry
            if not tool_use_id:
                _do_close(cfg, sid, agent_id, agent_type, None, cwd, "no_transcript")
                swept.append(agent_id)
            continue

        last = _last_record(path)
        if last is None:
            continue

        if _is_tool_use(last):
            continue  # assistant tool_use: never sweep by age (could be a long gate; idle runs are the warmer's, pa/warmer.py)

        if _is_tool_result(last):
            rec_epoch = _record_epoch(last)
            if rec_epoch is not None and (now - rec_epoch) >= dead_s:
                _do_close(cfg, sid, agent_id, agent_type, path, cwd, "stale_tool_result")
                swept.append(agent_id)

    return swept


def _do_close(cfg, sid, agent_id, agent_type, path, cwd, reason):
    """Close one swept agent through the shared SubagentStop close path."""
    try:
        from .hooks.subagent_stop import close_run

        live = running.agent(sid, agent_id)
        close_run(cfg, sid, agent_id, agent_type, path, live=live, cwd=cwd)
    except Exception:
        # fallback: at least remove from running.json
        running.remove_agent(sid, agent_id)
    log.log("subagent_swept", agent=agent_id, session=sid, reason=reason)


def sweep_if_due(sid, cfg, now=None):
    """Run :func:`sweep` at most once per 60 seconds."""
    now = now if now is not None else time.time()
    if not _should_sweep(now):
        return []
    result = sweep(sid, cfg, now=now)
    _write_stamp(now)
    return result
