"""Context-threshold handoff (D8; design A section C.1).

Public:
    DEFAULT_TEXT, DEFAULT_WINDOW, LONG_WINDOW
    ctx_next(req)                                   input+cache_write+cache_read+output
    context_window(model, project_cfg=None, cost_state=None)
    threshold(cfg, role="expert", project_cfg=None, model=None, window=None)
    text(cfg, role="expert", project_cfg=None)
    flag_path(agent_id), fired(agent_id), claim(agent_id), release(agent_id)
    payload(task_text)                              PostToolUse additionalContext
    check(req, role, cfg, project_cfg=None, model=None) -> dict

``ctx_next`` is what the *next* request of this agent will carry: the last
assistant line's ``input + cache_creation + cache_read + output`` (keep-LAST
gives the final usage of that message).  The threshold is
``min(config, 0.8 x context_window)`` -- the master plan pins Fable 5.1 and
Opus 4.6 in their ``[1m]`` form precisely so 350k is reachable; a bare
200k-window model must hand off at 160k instead.

Firing exactly once is an ``os.open(..., O_CREAT|O_EXCL)`` on
``state/handoff/<agent_id>``: two PostToolUse hooks racing on the same agent
cannot both win, and the flag survives the process.
"""

import json
import os
import time

DEFAULT_TEXT = (
    "HANDOFF — context threshold reached. Within the next 2 turns bring the current step "
    "to a stable point, write `phase-ends/current/TASK_PROGRESS.md` (done so far · in flight · "
    "hypotheses rejected with evidence · current hypothesis · next 5 steps · gotchas · state to "
    "carry verbatim), commit via `tools/commit_task.sh`, and return `STATUS: handoff` with "
    "LOG/CTX/COMMIT/RECOMMENDED. Do not start new sub-steps."
)

DEFAULT_WINDOW = 200_000
LONG_WINDOW = 1_000_000
WINDOW_FRACTION = 0.8


# --------------------------------------------------------------------------- numbers

def ctx_next(req):
    """``input + cache_write + cache_read + output`` of the last usage line."""
    if not isinstance(req, dict):
        return 0
    return (int(req.get("input") or 0) + int(req.get("cache_write") or 0)
            + int(req.get("cache_read") or 0) + int(req.get("output") or 0))


def context_window(model, project_cfg=None, cost_state=None):
    """Context window of ``model`` in tokens, or None when it is not known.

    Order: ``pa.json.context_window[<model>]`` -> this session's ``cost-state``
    (``modelUsage[<model>].contextWindow``, recorded by the M2 probe) -> the
    ``[1m]`` marker in the model id -> ``None`` (caller keeps the raw config).
    """
    name = str(model or "")
    if isinstance(project_cfg, dict):
        table = project_cfg.get("context_window")
        if isinstance(table, dict):
            for key, val in table.items():
                if str(key).lower() == name.lower():
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        pass
    if isinstance(cost_state, dict):
        usage = cost_state.get("modelUsage") or cost_state.get("model_usage")
        if isinstance(usage, dict):
            row = usage.get(name) or {}
            win = row.get("contextWindow") if isinstance(row, dict) else None
            try:
                if win:
                    return int(win)
            except (TypeError, ValueError):
                pass
    if not name:
        return None
    return LONG_WINDOW if "[1m]" in name.lower() else DEFAULT_WINDOW


def threshold(cfg, role="expert", project_cfg=None, model=None, window=None):
    """``min(config threshold, 0.8 x window)`` in tokens."""
    base = 350_000
    if isinstance(cfg, dict):
        try:
            base = int(((cfg.get("handoff") or {}).get("threshold_tokens")) or base)
        except (TypeError, ValueError):
            base = 350_000
    if isinstance(project_cfg, dict):                     # .claude/pa.json handoff_ctx
        table = project_cfg.get("handoff_ctx")
        if isinstance(table, dict) and role in table:
            try:
                base = int(table[role])
            except (TypeError, ValueError):
                pass
    win = window if window is not None else context_window(model, project_cfg)
    if win:
        try:
            base = min(base, int(WINDOW_FRACTION * int(win)))
        except (TypeError, ValueError):
            pass
    return int(base)


def roles(cfg):
    """Roles the handoff applies to (default ``["expert"]``)."""
    if isinstance(cfg, dict):
        vals = (cfg.get("handoff") or {}).get("roles")
        if isinstance(vals, list) and vals:
            return [str(v) for v in vals]
    return ["expert", "coder"]  # was ["expert"]


def text(cfg, role="expert", project_cfg=None):
    """The task text injected as ``additionalContext`` (config may override)."""
    override = None
    if isinstance(project_cfg, dict):
        override = project_cfg.get("handoff_text")
    if not override and isinstance(cfg, dict):
        override = (cfg.get("handoff") or {}).get("text")
    if isinstance(override, dict):
        override = override.get(role) or override.get("default")
    return str(override) if override else DEFAULT_TEXT


# --------------------------------------------------------------------------- once-flag

def flag_path(agent_id):
    from . import paths

    return paths.handoff_flag(agent_id)


def fired(agent_id):
    """True when this agent already received its handoff."""
    try:
        return os.path.exists(flag_path(agent_id))
    except OSError:
        return False


def claim(agent_id, detail=None):
    """Atomically claim the one handoff of ``agent_id`` (``O_EXCL``)."""
    path = flag_path(agent_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        blob = {"agent_id": agent_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if isinstance(detail, dict):
            blob.update(detail)
        os.write(fd, json.dumps(blob, ensure_ascii=False, default=str).encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return True


def release(agent_id):
    """Remove the once-flag (tests and ``pa-ledger recalc --pending``)."""
    try:
        os.remove(flag_path(agent_id))
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- decision

def payload(task_text):
    """The PostToolUse payload that injects the handoff task."""
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": str(task_text)}}


def check(req, role, cfg, project_cfg=None, model=None):
    """``{"ctx", "threshold", "crossed", "fire", "text"}`` for one usage line.

    ``crossed`` is the pure arithmetic; ``fire`` is True only for the process
    that wins the ``O_EXCL`` claim, so the caller can print the task once.
    """
    ctx = ctx_next(req)
    limit = threshold(cfg, role=role, project_cfg=project_cfg,
                      model=model or (req or {}).get("model"))
    out = {"ctx": ctx, "threshold": limit, "crossed": ctx >= limit, "fire": False, "text": None}
    if out["crossed"]:
        out["text"] = text(cfg, role=role, project_cfg=project_cfg)
    return out
