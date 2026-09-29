"""PreToolUse: PHASE_PLAN protection and the discussion-mode read-only guard.

Matcher (settings.json): ``Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell``.
Budget 50 ms -- the developer waits on this one, so it does one ``os.path.exists``
and pure string work; nothing is imported beyond ``pa.guard`` and no database is
opened (a denial is *spooled* and folded into ``events`` by the next cold hook).

Both rules are governance, so both need ``<cwd>/.claude/pa.json``:

* ``PHASE_PLAN.md`` is frozen at approval -- Edit/Write/MultiEdit/NotebookEdit on
  it, and any Bash/PowerShell line that mutates it without going through
  ``tools/plan_edit.py``, are denied for every agent (D17).
* while ``.run/DISCUSSION`` exists (``/discuss``), edits are denied outright and
  shell commands must pass ``guard.is_read_only``.
* a leaf agent with a Bash allowlist (``guard.AGENT_BASH_ALLOW``: retriever-code ->
  ``tools/research_add.py``) is denied any other shell command (3.1 T15); the
  ``agent_type`` field of the hook input names the agent.
* a whole shell read (``cat``, ``Get-Content``, bare ``sed``/``head``/``tail``) of a
  spilled ``tool-results/<id>.txt`` or an over-threshold file is denied like the
  Read (``guard.bash_read_deny``, fix-11).
* a second whole Read of a file unchanged since this agent read it is denied,
  the fourth in a row allowed (``guard.reread_deny``, fix-17); an allowed edit
  drops the path from the agent's read set (``.run/guard/reads-<key>.json``).

The only hook besides PostToolUse that ever prints.
"""

import os

from .. import guard
from . import governed, sid_of, spool_event


def run(inp, cfg):
    inp = inp or {}
    tool = inp.get("tool_name")
    if not tool:
        return None
    root = governed(inp)
    if not root:
        return None

    discussion = False
    try:
        discussion = os.path.exists(os.path.join(root, ".run", "DISCUSSION"))
    except OSError:
        discussion = False

    reason = guard.decide(tool, inp.get("tool_input"), discussion=discussion, cfg=cfg,
                          agent_type=inp.get("agent_type"))

    # role-based guards (spilled read, whole-plan role, tool-source role)
    if not reason:
        from .. import config as _config
        role = _config.role_from_agent_type(inp.get("agent_type"))
        ti = inp.get("tool_input") or {}
        tname = str(tool or "").strip().lower()
        if tname == "read":
            reason = (guard.spilled_read_deny(ti, cfg)
                      or guard.whole_plan_role_deny(ti, role, cfg)
                      or guard.tool_source_deny(ti, role, cfg, root)
                      or guard.whole_read_deny(ti, role, cfg, root)
                      or guard.reread_deny(ti, sid_of(inp), inp.get("agent_id"), cfg, root))
        elif tname in ("bash", "powershell", "shell"):
            reason = (guard.whole_plan_role_deny(ti, role, cfg)
                      or guard.tool_source_deny(ti, role, cfg, root)
                      or guard.bash_read_deny(ti.get("command"), role, cfg, root))

    if not reason:
        if str(tool).strip().lower() in ("edit", "write", "multiedit", "notebookedit"):
            guard.reread_forget(inp.get("tool_input"), sid_of(inp), inp.get("agent_id"), root)
        return None

    detail = {"tool": tool, "reason": reason, "discussion": discussion,
              "target": _target(inp.get("tool_input"))}
    spool_event(sid_of(inp), "deny", detail, run_id=inp.get("agent_id"))
    return guard.deny(reason)


def _target(tool_input):
    ti = tool_input if isinstance(tool_input, dict) else {}
    for key in ("file_path", "notebook_path", "command"):
        val = ti.get(key)
        if isinstance(val, str) and val:
            return val[:300]
    return None
