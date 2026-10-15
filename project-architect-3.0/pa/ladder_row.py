"""The spawn-time resolver: one agent's model and effort for the live account's tier (3.24 T17).

``pa_ledger.py ladder-row --agent <type> [--effort <level>] --project <dir> --json`` prints one line
``{"agent", "managed", "model", "effort", "tier", "source"}``; the mod (T17b) calls it at spawn.
Read-only and fast: ``pa_ledger.py`` imports this module directly, never ``ledger_cli``.

The tier comes from the active config dir's credentials (``source: credentials``), else the
project's installed ``pa.json`` ``preset`` (``preset``), else none; the row is the ladder's at that
tier with the ``pa.json`` ``ladder`` override on top.  An agent outside the ladder, one absent at
the tier, or no tier at all, falls back to the installed agent file's frontmatter (``default``),
the override still on top for a managed agent.  Never raises; unknown values are null.

For ``pa-planner`` (3.25 T5): the first ``status: open`` phase line of the project's
``GENERATION_PLAN.md`` may carry ``planner-effort: <level>``; the effort becomes the lower of that
level and the resolved row's.  No open phase, no field or a bad value leaves the row as it is.

``--effort <level>`` (3.25 T6a: a spawn's ``[effort <level>]`` hint; an effort or ``deep``): ``agent`` echoes
``<type>@<level>``; the row is the tier's variant ``<type>@<level>`` when it has one that is not None, else the
type's row with its effort lowered to the level (never raised; ``deep`` with no variant leaves it).

Public:
    resolve(agent, project, config_dir=None, effort=None) -> dict
    main(argv=None) -> int
"""

import argparse
import json
import os
import re
import sys

EFFORTS = ("low", "medium", "high", "xhigh", "max")
_PHASE_RE = re.compile(r"^\s*-\s+[0-9]+(?:\.[0-9]+)+\s+[^|]*\|")


def _planner_effort(project):
    """The first open phase line's ``planner-effort`` level, else None (a small copy of tools/_genplan.py)."""
    try:
        with open(os.path.join(project, "GENERATION_PLAN.md"), encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return None
    for line in text.splitlines():
        if not _PHASE_RE.match(line):
            continue
        got = {}
        for part in line.split(" | ")[1:]:
            if ":" in part:
                k, v = part.split(":", 1)
                got[k.strip().lower()] = v.strip().lower()
        if got.get("status") == "open":
            return got.get("planner-effort") if got.get("planner-effort") in EFFORTS else None
    return None


def _lowered(out, level):
    """``out`` with its effort lowered to ``level`` when that is a lower effort; never raised."""
    if level in EFFORTS and out["effort"] in EFFORTS and EFFORTS.index(level) < EFFORTS.index(out["effort"]):
        out["effort"] = level
    return out


def _capped(out, project, agent):
    """``out`` with a pa-planner's effort lowered to the open phase's ``planner-effort``; never raised."""
    if agent != "pa-planner" or not project:
        return out
    return _lowered(out, _planner_effort(project))


def _frontmatter(project, config_dir, agent):
    """``{"model", "effort"}`` from the agent file: the project's ``.claude/agents``, else the config dir's."""
    from .install import ladder
    for base in (os.path.join(project, ".claude") if project else None, config_dir):
        if not base:
            continue
        got = ladder._installed(os.path.join(base, "agents", agent + ".md"))
        if got is not None:
            return got
    return {"model": None, "effort": None}


def resolve(agent, project, config_dir=None, effort=None):
    """The agent's row for the live tier; ``effort``, a spawn hint's level; see the module docstring."""
    from . import accounts, fsutil, usage_api
    from .install import ladder
    rkey = "%s@%s" % (agent, effort) if effort else agent
    out = {"agent": rkey, "managed": agent in ladder.MANAGED, "model": None, "effort": None,
           "tier": None, "source": "default"}
    try:
        cdir = config_dir or accounts.config_dir()
    except Exception:
        cdir = None
    pa = {}
    if project:
        try:
            pa = fsutil.read_json(os.path.join(project, ".claude", "pa.json"), None)
        except Exception:
            pa = None
        pa = pa if isinstance(pa, dict) else {}
    override = pa.get("ladder") if isinstance(pa.get("ladder"), dict) else None
    tier, source = None, None
    try:
        cred = usage_api.read_credentials(cdir) if cdir else {}
        tier = accounts.tier_from(cred.get("subscription_type"), cred.get("rate_limit_tier"))
        source = "credentials" if tier in ladder.PRESETS else None
    except Exception:
        tier = None
    if source is None:
        tier = pa.get("preset") if pa.get("preset") in ladder.PRESETS else None
        source = "preset" if tier else None
    out["tier"] = tier
    row = ladder.row(rkey, tier, override) if tier and out["managed"] and effort else None
    if row is not None:                                     # the tier's variant row: as it is
        out.update(model=row["model"], effort=row["effort"], source=source)
        return _capped(out, project, agent)
    row = ladder.row(agent, tier, override) if tier and out["managed"] else None
    if row is not None:
        out.update(model=row["model"], effort=row["effort"], source=source)
        return _capped(_lowered(out, effort), project, agent)
    got = _frontmatter(project, cdir, agent)
    ov = override.get(agent) if out["managed"] and override else None
    for key in ("model", "effort"):
        val = got.get(key)
        if isinstance(ov, dict) and isinstance(ov.get(key), str) and ov[key].strip():
            val = ov[key].strip()
        out[key] = val
    return _capped(_lowered(out, effort), project, agent)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pa-ledger ladder-row",
                                description="one agent's model and effort for the live account's tier")
    p.add_argument("--agent", required=True, help="agent type, e.g. pa-planner")
    p.add_argument("--effort", default=None, choices=EFFORTS + ("deep",),
                   help="a spawn hint's level: the type's <type>@<level> row, else its row lowered to it")
    p.add_argument("--project", default=None, help="project directory (its .claude/pa.json and agents)")
    p.add_argument("--json", action="store_true", help="one JSON line")
    args = p.parse_args(argv)
    doc = resolve(args.agent, os.path.abspath(args.project) if args.project else None, effort=args.effort)
    if args.json:
        sys.stdout.write(json.dumps(doc, sort_keys=False) + "\n")
    else:
        sys.stdout.write("%s: %s · %s (tier %s, %s%s)\n" % (
            doc["agent"], doc["model"], doc["effort"], doc["tier"], doc["source"],
            "" if doc["managed"] else ", unmanaged"))
    return 0
