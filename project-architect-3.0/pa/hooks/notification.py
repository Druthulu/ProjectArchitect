"""Notification: forward the harness's own notifications to the desktop.

Matcher (settings.json): ``idle_prompt|permission_prompt|agent_needs_input|
elicitation_dialog``.  Input (recon V5): ``notification_type, title, message``.

``agent_needs_input`` and ``permission_prompt`` mean something is blocked on the
developer, so they are treated as waiting (toast exempt from the rate limit and
a ``state/waiting/<sid>.json`` marker for statusline line 4); ``idle_prompt`` is
informational.

3.9.5 T2: the toast's cause comes from ``notification_type`` (:data:`CAUSES`, else
``other``; ``idle_prompt`` while ``.run/DISCUSSION`` exists is ``discussion``), and
:func:`pa.hooks.toast` writes the one ``events`` row kind ``toast`` for this site.
"""

import os

from . import governed, project_config, project_name, sid_of, toast, waiting_write

WAITING_TYPES = ("permission_prompt", "agent_needs_input", "elicitation_dialog")
CAUSES = {"idle_prompt": "idle", "permission_prompt": "permission",
          "agent_needs_input": "input", "elicitation_dialog": "input"}


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    ntype = str(inp.get("notification_type") or "notification")
    title = inp.get("title") or "Claude Code"
    message = inp.get("message") or ""

    root = governed(inp)
    cause = CAUSES.get(ntype, "other")
    if cause == "idle" and root and os.path.exists(os.path.join(root, ".run", "DISCUSSION")):
        cause = "discussion"

    waiting = ntype in WAITING_TYPES
    if waiting:
        waiting_write(sid, {"reason": ntype, "agent": inp.get("agent_type") or "main",
                            "message": str(message)[:300]})
    toast(cfg, "PA3 · %s · %s" % (project_name(inp) or "session", title),
          message, kind="waiting" if waiting or cause == "discussion" else "notification",
          session_id=sid, project_cfg=project_config(root), cause=cause,
          run_id=inp.get("agent_id"),
          detail={"notification_type": ntype, "message": str(message)[:400]})
    return None
