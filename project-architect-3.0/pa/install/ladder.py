"""The model ladder: which model and effort each managed agent runs at, per plan tier (3.10.6 T2).

The installer writes ``.claude/agents/*.md`` only through :func:`render`, so a max5 or pro
account never carries rows its plan cannot serve.  ``max20`` is the shipped frontmatter
byte for byte.  Hot path (T3's SessionStart reconcile calls :func:`mismatches`): module-level
imports stay json, os, sys, time.

Public:
    PRESETS                             ``{tier: {agent: {"model", "effort"} | None}}``; None = not installed
    MANAGED                             the agents the ladder owns; every other agent passes through
    row(agent, preset, override=None)   the agent's row (override merged), None when absent or unmanaged
    render(agent, pkg_text, preset, override=None)
                                        the packaged text with its frontmatter ``model:``/``effort:``
                                        lines rewritten; None = absent at this tier
    mismatches(agents_dir, preset, override=None)
                                        managed agents whose installed copy differs from the row
"""

import json     # noqa: F401  (hot-path import budget: json, os, sys, time)
import os
import sys      # noqa: F401
import time     # noqa: F401

MANAGED = ("expert-fable", "expert-fable-5m", "critic",  # expert-fable-5m: 3.15 T4 twin, rows as expert-fable
           "review", "auditor", "memory-curator", "plain")
_JUDGES = ("critic", "review", "auditor", "memory-curator")
_FABLE = "claude-fable-5-1[1m]"
_OPUS = "claude-opus-5-5[1m]"


def _r(model, effort):
    return {"model": model, "effort": effort}


PRESETS = {
    "max20": dict([("expert-fable", _r(_FABLE, "medium")), ("expert-fable-5m", _r(_FABLE, "medium"))]
                  + [(a, _r(_FABLE, "medium")) for a in _JUDGES]
                  + [("plain", _r(_FABLE, "high"))]),
    "max5": dict([("expert-fable", None), ("expert-fable-5m", None)]
                 + [(a, _r(_FABLE, "medium")) for a in _JUDGES]
                 + [("plain", _r(_FABLE, "medium"))]),
    "pro": dict([("expert-fable", None), ("expert-fable-5m", None)]
                + [(a, _r(_OPUS, "medium")) for a in _JUDGES]
                + [("plain", _r(_OPUS, "medium"))]),
}


def row(agent, preset, override=None):
    """``{"model", "effort"}`` for ``agent`` at ``preset``; None when absent or not managed.

    ``override`` is the raw pa.json ``ladder`` value ``{agent: {model?, effort?}}``: merged onto
    the preset row, or onto the max20 row when the preset row is None (an override installs it)."""
    if agent not in MANAGED:
        return None
    base = PRESETS.get(preset, PRESETS["max5"]).get(agent)
    ov = override.get(agent) if isinstance(override, dict) else None
    if not isinstance(ov, dict):
        return dict(base) if base else None
    out = dict(base or PRESETS["max20"][agent])
    for key in ("model", "effort"):
        if isinstance(ov.get(key), str) and ov[key].strip():
            out[key] = ov[key].strip()
    return out


def _front(lines):
    """Index of the closing ``---`` of a leading frontmatter block, else None."""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def render(agent, pkg_text, preset, override=None):
    """``pkg_text`` with only the frontmatter ``model:``/``effort:`` lines set from the row;
    unmanaged agents unchanged; None = the agent is absent at this tier."""
    if agent not in MANAGED:
        return pkg_text
    r = row(agent, preset, override)
    if r is None:
        return None
    lines = pkg_text.splitlines(True)
    end = _front(lines)
    if end is None:
        return pkg_text
    for i in range(1, end):
        for key in ("model", "effort"):
            if lines[i].startswith(key + ":"):
                body = lines[i].rstrip("\r\n")
                lines[i] = "%s: %s%s" % (key, r[key], lines[i][len(body):])
    return "".join(lines)


def _installed(path):
    """``{"model", "effort"}`` from an installed agent's frontmatter, else None when missing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return None
    out = {"model": None, "effort": None}
    end = _front(lines) or 0
    for line in lines[1:end]:
        for key in out:
            if line.startswith(key + ":"):
                out[key] = line.split(":", 1)[1].strip()
    return out


def mismatches(agents_dir, preset, override=None):
    """Managed agents whose installed ``model``/``effort`` differ from the row, or that are
    present at a tier where they are absent, or absent where they are present."""
    out = []
    for agent in MANAGED:
        want = row(agent, preset, override)
        have = _installed(os.path.join(agents_dir, agent + ".md"))
        if (want is None) != (have is None) or (want is not None and have != want):
            out.append(agent)
    return out
