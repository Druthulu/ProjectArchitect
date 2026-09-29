"""PostModelSwitch: the session changed model -- record it, warn when unpinned.

Input (taken from the binary, not a probe -- design A H.12 says to stay
tolerant): ``from_model, to_model, requested_model, source``.  Every field may
be missing.

A switch away from the model pinned for this session's agent is exactly the D48
symptom the developer wants to see immediately (a Fable run silently continuing
on Opus), so it toasts; a switch *to* the pinned model is only an ``events`` row.
"""

from .. import log
from . import (close_db, governed, open_db, pinned_for, project_config, sid_of, toast)


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    to_model = inp.get("to_model") or inp.get("toModel")
    from_model = inp.get("from_model") or inp.get("fromModel")
    requested = inp.get("requested_model") or inp.get("requestedModel")
    source = inp.get("source")
    detail = {"from": from_model, "to": to_model, "requested": requested, "source": source,
              "agent": inp.get("agent_type")}

    conn = open_db()
    try:
        from .. import db

        db.insert_event(conn, "model_switch", detail, session_id=sid,
                        run_id=inp.get("agent_id") or sid)
    finally:
        close_db(conn)
    log.log("model_switch", session=sid, to=to_model, frm=from_model, source=source)

    pinned = pinned_for(inp, cfg)
    from .post_tool_use import mismatch

    if to_model and mismatch(pinned, to_model):
        toast(cfg, "PA3 · model switched",
              "%s now runs on %s (pinned %s)" % (inp.get("agent_type") or "session",
                                                 to_model, pinned),
              kind="model_mismatch", session_id=sid,
              project_cfg=project_config(governed(inp)), cause="model",
              run_id=inp.get("agent_id") or sid)
    return None
