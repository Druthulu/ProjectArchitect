"""Notification: forward the harness's own notifications to the desktop.

Matcher (settings.json): ``idle_prompt|permission_prompt|agent_needs_input|
elicitation_dialog``.  Input (recon V5): ``notification_type, title, message``.

``agent_needs_input`` and ``permission_prompt`` mean something is blocked on the
developer, so they are treated as waiting (toast exempt from the rate limit and
a ``state/waiting/<sid>.json`` marker for statusline line 4); ``idle_prompt`` is
informational.

3.9.5 T2: the toast's cause comes from ``notification_type`` (:data:`CAUSES`, else
``other``; 3.24 T3.2: the ``discussion`` cause left with the discussion guard), and
:func:`pa.hooks.toast` writes the one ``events`` row kind ``toast`` for this site.

3.24 T13h: in a ``mod: on`` project a main-thread ``permission_prompt`` whose message is
exactly :data:`MOD_PICKER_MESSAGE` is the mod's picker (cookbook C0101), not a tool
permission: recorded ``sent 0`` reason ``mod picker``, no toast (the mod raises its own),
as ``stop._maybe_toast`` skips mod stops.

3.26 T3: a waiting prompt whose marker already holds the same ``reason`` and ``message``
under :data:`REPEAT_S` old is a repeat (one plan view toasted twice, 22 s apart): recorded
``sent 0`` reason ``repeat``, no toast. A marker the developer's reply cleared is a new wait.
"""

import calendar
import json
import time

from . import governed, project_config, project_name, sid_of, toast, waiting_write

WAITING_TYPES = ("permission_prompt", "agent_needs_input", "elicitation_dialog")
CAUSES = {"idle_prompt": "idle", "permission_prompt": "permission",
          "agent_needs_input": "input", "elicitation_dialog": "input"}
MOD_PICKER_MESSAGE = "Claude needs your permission"
REPEAT_S = 60


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    ntype = str(inp.get("notification_type") or "notification")
    title = inp.get("title") or "Claude Code"
    message = inp.get("message") or ""

    root = governed(inp)
    cause = CAUSES.get(ntype, "other")
    skip = "mod picker" if _mod_picker(inp, root, ntype, message) else None

    waiting = ntype in WAITING_TYPES
    if waiting:
        if skip is None and _repeat(sid, ntype, str(message)[:300]):
            skip = "repeat"
        waiting_write(sid, {"reason": ntype, "agent": inp.get("agent_type") or "main",
                            "message": str(message)[:300]})
    toast(cfg, "PA3 · %s · %s" % (project_name(inp) or "session", title),
          message, kind="waiting" if waiting else "notification",
          session_id=sid, project_cfg=project_config(root), cause=cause,
          run_id=inp.get("agent_id"), skip=skip,
          detail={"notification_type": ntype, "message": str(message)[:400]})
    return None


def _repeat(sid, reason, message):
    """True when the session's waiting marker is this same wait, under :data:`REPEAT_S` old."""
    from .. import paths

    try:
        with open(paths.waiting_path(sid), encoding="utf-8") as fh:
            row = json.load(fh)
        ts = calendar.timegm(time.strptime(str(row.get("ts")), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return False
    return (row.get("reason") == reason and row.get("message") == message
            and time.time() - ts < REPEAT_S)


def _mod_picker(inp, root, ntype, message):
    """A main-thread picker in a ``mod: on`` project (its main thread calls no tools)."""
    if ntype != "permission_prompt" or inp.get("agent_id") or message != MOD_PICKER_MESSAGE:
        return False
    from .. import health

    return health.mod_on(root)
