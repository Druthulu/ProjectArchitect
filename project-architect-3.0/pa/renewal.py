"""The renewal-day prompt (3.11 T9, developer 2026-09-28).

PA3 never guesses an account's billing renewal day. While the active account has none, the month figures on the
statusline show a dash, and PA3 asks once per session and account: the user sees a line naming the Claude app's
Settings > Billing page with a copy-ready command, and the model is told to ask one plain-text question and run the
command with the answer. SessionStart asks for a new session; UserPromptSubmit asks after a mid-session switch.

Hot path (both hooks): only json, os, sys and time at module level.
"""

import os
import sys
import time


def command(email):
    """The command that stores ``email``'s renewal day; ``<day>`` is left for the user to fill in."""
    from . import paths

    py = (sys.executable or "python").replace("\\", "/")
    ledger = os.path.join(paths.install_dir(), "pa_ledger.py").replace("\\", "/")
    return '%s "%s" accounts renewal %s <day>' % (py, ledger, email)


def _asked_path(sid):
    from . import paths

    return paths.state_path("renewal_asked", "%s.json" % sid)


def due(cfg, sid, email):
    """True when ``email`` has no renewal day and this session has not asked about it yet."""
    if not email or not sid:
        return False
    from . import config, fsutil

    if config.renewal_day_for(cfg, email) is not None:
        return False
    asked = fsutil.read_json(_asked_path(sid), {}) or {}
    return email not in (asked.get("emails") or [])


def mark(sid, email):
    """Remember that this session asked about ``email``."""
    from . import fsutil

    path = _asked_path(sid)
    asked = fsutil.read_json(path, {}) or {}
    emails = list(asked.get("emails") or [])
    if email not in emails:
        emails.append(email)
    fsutil.atomic_write_json(path, {"emails": emails, "ts": time.time()})


def prompt(email, label=None):
    """``(user_line, model_line)`` for an account whose renewal day is unknown."""
    cmd = command(email)
    user = ("PA3 does not know when %s's Claude plan renews, so the month figures show a dash. The Claude app "
            "shows the renewal date under Settings > Billing; set the day with:\n  ! %s"
            % (label or email, cmd))
    model = ("PA3: the plan renewal day of %s is unknown, so the statusline's month figures show a dash. Ask the "
             "developer once, as a plain-text question at the end of your turn: \"Which day of the month does "
             "your Claude plan renew? The Claude app shows it under Settings > Billing.\" When they answer, run: "
             "%s (with that day in place of <day>)." % (email, cmd))
    return user, model
