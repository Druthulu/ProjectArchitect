"""StopFailure: the session died mid-turn -- record it and tell the developer.

Input (recon V5): ``error, error_details, last_assistant_message`` (plus the
usual session fields).  Budget 80 ms: one ``events`` row and a detached toast.
``stop_failure`` is exempt from the toast rate limit -- a crash always reaches
the developer.
"""

from .. import log
from . import (close_db, governed, open_db, project_config, project_name, sid_of, toast)


def run(inp, cfg):
    inp = inp or {}
    sid = sid_of(inp)
    error = str(inp.get("error") or "unknown error")
    details = inp.get("error_details")
    detail = {"error": error, "details": details,
              "last_assistant_message": str(inp.get("last_assistant_message") or "")[:400],
              "agent": inp.get("agent_type")}

    conn = open_db()
    try:
        from .. import db

        db.insert_event(conn, "error", detail, session_id=sid, run_id=inp.get("agent_id") or sid)
    finally:
        close_db(conn)

    log.log("stop_failure", session=sid, error=error[:200])
    toast(cfg, "PA3 · %s · session stopped" % (project_name(inp) or "session"),
          "session stopped: %s" % error, kind="stop_failure", session_id=sid,
          project_cfg=project_config(governed(inp)), cause="crash",
          run_id=inp.get("agent_id") or sid)
    return None
