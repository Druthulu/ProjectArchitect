"""Filesystem locations for the PA3 usage ledger (design doc A.1 / A.3).

Public:
    home(), claude_dir(), projects_dir()
    ledger_dir(), install_dir()
    db_path(), running_path(), summary_path(), config_path(), hooks_log_path()
    spool_dir(), spool_path(sid)
    state_dir(), state_path(*parts), offsets_path(sid), waiting_path(sid),
    handoff_flag(agent_id), auth_cache_path(), pending_locks_path(),
    session_state_path(sid), sessions_state_dir()
    ensure_ledger_tree()
    is_wsl(), machine_tag()
    project_slug(path), project_paths(cwd)
    session_dir(transcript_path), subagents_dir(transcript_path)

``ledger_dir()`` is deliberately anchored on the *user home* and not on
``CLAUDE_CONFIG_DIR``: this machine runs two config dirs (Windows and WSL) and
both must land on one ledger per machine.  ``PA_LEDGER_DIR`` overrides it for
tests and for ``pa install --root <dir>``.
"""

import os
import re
import sys

# --------------------------------------------------------------------------- home

def home():
    """User home directory (never CLAUDE_CONFIG_DIR)."""
    h = os.environ.get("USERPROFILE") if os.name == "nt" else None
    return os.path.abspath(h or os.path.expanduser("~"))


def claude_dir():
    """``<home>/.claude`` -- the *data* root, independent of CLAUDE_CONFIG_DIR."""
    return os.path.join(home(), ".claude")


def projects_dir():
    """``<home>/.claude/projects`` -- where transcripts live."""
    return os.path.join(claude_dir(), "projects")


def install_dir():
    """Install target of the package itself (``<home>/.claude/pa3``)."""
    return os.path.join(claude_dir(), "pa3")


def config_dir():
    """The Claude config dir: ``$CLAUDE_CONFIG_DIR`` or ``claude_dir()``."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or claude_dir()


def package_clone_dir(config_dir=None):
    """``<config_dir>/pa3-src`` -- the package source clone."""
    return os.path.join(config_dir or globals()["config_dir"](), "pa3-src")


# --------------------------------------------------------------------------- ledger

def ledger_dir():
    """``<home>/.claude/usage-ledger`` (or ``$PA_LEDGER_DIR``)."""
    override = os.environ.get("PA_LEDGER_DIR")
    if override:
        return os.path.abspath(override)
    return os.path.join(claude_dir(), "usage-ledger")


def db_path():
    return os.path.join(ledger_dir(), "ledger.sqlite")


def running_path():
    return os.path.join(ledger_dir(), "running.json")


def summary_path():
    return os.path.join(ledger_dir(), "summary.json")


def config_path():
    return os.path.join(ledger_dir(), "config.json")


def hooks_log_path():
    return os.path.join(ledger_dir(), "hooks.log")


def spool_dir():
    return os.path.join(ledger_dir(), "spool")


def spool_path(session_id):
    return os.path.join(spool_dir(), "%s.jsonl" % (session_id or "unknown"))


def state_dir():
    return os.path.join(ledger_dir(), "state")


def state_path(*parts):
    return os.path.join(state_dir(), *parts)


def offsets_path(session_id):
    return state_path("offsets", "%s.json" % (session_id or "unknown"))


def waiting_path(session_id):
    return state_path("waiting", "%s.json" % (session_id or "unknown"))


def follow_path(run_id, ext="json"):
    """``state/follow/<run_id>.json`` (an open focus tab) or ``.stop`` (its close marker)."""
    return state_path("follow", "%s.%s" % (run_id or "unknown", ext))


def handoff_flag(agent_id):
    return state_path("handoff", str(agent_id or "unknown"))


def auth_cache_path():
    return state_path("auth_cache.json")


def pending_locks_path():
    return state_path("pending_locks.jsonl")


def sessions_state_dir():
    return state_path("sessions")


def session_state_path(session_id):
    """Last statusline sample for one session (sampler diffing)."""
    return os.path.join(sessions_state_dir(), "%s.last.json" % (session_id or "unknown"))


def ensure_ledger_tree():
    """Create the ledger directory tree.  Only the installer/hooks call this."""
    for d in (ledger_dir(), spool_dir(), state_dir(),
              state_path("offsets"), state_path("waiting"), state_path("handoff"),
              sessions_state_dir()):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return ledger_dir()


# --------------------------------------------------------------------------- machine

_WSL = None


def is_wsl():
    """True when running inside WSL (cached)."""
    global _WSL
    if _WSL is None:
        found = False
        if sys.platform.startswith("linux"):
            if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
                found = True
            else:
                try:
                    with open("/proc/sys/kernel/osrelease", "rb") as fh:
                        rel = fh.read(256).decode("utf-8", "replace").lower()
                    found = ("microsoft" in rel) or ("wsl" in rel)
                except OSError:
                    found = False
        _WSL = found
    return _WSL


def machine_tag():
    """``win`` | ``wsl`` | ``linux`` | ``mac``."""
    if sys.platform.startswith("win") or os.name == "nt":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    if is_wsl():
        return "wsl"
    return "linux"


# --------------------------------------------------------------------------- projects

_SLUG_RE = re.compile(r"[\\/:. ]")


def project_slug(path):
    """Claude Code's transcript-directory slug for a project path.

    A Windows path 'Z:' + backslash + 'Storage/git/Vantage' becomes
    ``Z--Storage-git-Vantage``; ``/home/you/bfm-decomp`` becomes
    ``-home-you-bfm-decomp``.  Each separator character maps to one dash
    (runs are *not* collapsed: ``Z:`` + separator gives two dashes).
    """
    return _SLUG_RE.sub("-", str(path or ""))


def project_paths(cwd):
    """Project-local paths the hooks and statusline read (design doc B.4/D)."""
    cwd = os.path.abspath(cwd or os.getcwd())
    run = os.path.join(cwd, ".run")
    cur = os.path.join(cwd, "phase-ends", "current")
    return {
        "cwd": cwd,
        "name": os.path.basename(os.path.normpath(cwd)) or cwd,
        "slug": project_slug(cwd),
        "run_dir": run,
        "status_json": os.path.join(run, "status.json"),
        "discussion_flag": os.path.join(run, "DISCUSSION"),
        "logs_dir": os.path.join(run, "logs"),
        "phase_current": cur,
        "phase_plan": os.path.join(cur, "PHASE_PLAN.md"),
        "task_progress": os.path.join(cur, "TASK_PROGRESS.md"),
        "inbox": os.path.join(cur, "INBOX.md"),
        "replan": os.path.join(cur, "REPLAN.md"),
        "review": os.path.join(cur, "REVIEW.md"),
        "tasks_dir": os.path.join(cur, "tasks"),
    }


def session_dir(transcript_path):
    """``…/<slug>/<sid>.jsonl`` -> ``…/<slug>/<sid>`` (holds subagents/, tool-results/)."""
    if not transcript_path:
        return ""
    base = str(transcript_path)
    if base.lower().endswith(".jsonl"):
        base = base[:-6]
    return base


def subagents_dir(transcript_path):
    """``…/<slug>/<sid>/subagents`` for a main transcript path."""
    d = session_dir(transcript_path)
    return os.path.join(d, "subagents") if d else ""
