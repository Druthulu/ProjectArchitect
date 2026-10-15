"""The model ladder: which model and effort each managed agent runs at, per plan tier (3.10.6 T2).

The installer writes ``.claude/agents/*.md`` only through :func:`render`, so a max5 or pro
account never carries rows its plan cannot serve.  ``max20`` is the shipped frontmatter
byte for byte, except ``pa-planner`` (3.24 T17), whose file carries the safe default (Opus 5.5 at
high) that every resolver fallback lands on.  Hot path (T3's SessionStart reconcile calls :func:`mismatches`): module-level
imports stay json, os, sys, time.

Public:
    PRESETS                             ``{tier: {agent: {"model", "effort"} | None}}``; None = not installed;
                                        3.25 T6a: a ``<type>@<level>`` key is a variant row (a hinted spawn)
    MANAGED                             the agents the ladder owns; every other agent passes through
                                        (3.24 T3.1: MOD_MANAGED folded in, MOD_AGENTS and ``mod=`` gone)
    row(agent, preset, override=None)
                                        the agent's row (override merged), None when absent or unmanaged;
                                        ``agent`` may be ``<type>@<level>`` (managed when its type is)
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

# 3.24 T3.1: was ("expert-fable", "expert-fable-5m", "critic", "review", "auditor", "memory-curator", "plain")
# plus MOD_MANAGED ("deep-thinker",) under mod on; the Gen 3 agents left
# 3.25 T6a: one agent per role (pa-expert, pa-coder, pa-thinker); was ("expert-fable", "auditor",
# "memory-curator", "deep-thinker", "pa-planner") (3.24 T17: + pa-planner)
MANAGED = ("pa-expert", "pa-coder", "pa-thinker", "auditor", "memory-curator", "pa-planner")
_JUDGES = ("auditor",)  # was ("auditor", "memory-curator"); before 3.24, + "critic", "review"
# 3.24 T17e: the memory curator runs Opus 5.5 at medium on every tier (developer 2026-10-12: "curator is
# opus55medium for all tiers"); it left the judges' Fable row
_FABLE = "claude-fable-5-1[1m]"
_OPUS = "claude-opus-5-5[1m]"
_SONNET = "claude-sonnet-5-5[1m]"  # 3.27 T11: Pro's coder


def _r(model, effort):
    return {"model": model, "effort": effort}


# 3.24 T17f: the planner's effort per tier (developer 2026-10-12): Max 20x max, Max 5x high, Pro medium
# 3.25 T6a: the role rows carry the old agents' values: pa-expert@high was expert-fable (Fable medium on max20,
# None on max5/pro, now Opus high there), pa-thinker was thinker, pa-thinker@deep was deep-thinker
def _roles(expert_high, thinker_deep):
    return [("pa-expert", _r(_OPUS, "medium")), ("pa-expert@high", expert_high),
            ("pa-coder", _r(_OPUS, "medium")),
            ("pa-thinker", _r(_OPUS, "max")), ("pa-thinker@deep", thinker_deep)]


PRESETS = {
    "max20": dict(_roles(_r(_FABLE, "medium"), _r(_FABLE, "max"))
                  + [(a, _r(_FABLE, "medium")) for a in _JUDGES]
                  + [("memory-curator", _r(_OPUS, "medium"))]
                  + [("pa-planner", _r(_OPUS, "max"))]),
    "max5": dict(_roles(_r(_OPUS, "high"), _r(_FABLE, "max"))
                 + [(a, _r(_FABLE, "medium")) for a in _JUDGES]
                 + [("memory-curator", _r(_OPUS, "medium"))]
                 + [("pa-planner", _r(_OPUS, "high"))]),
    # 3.27 T11 (developer 2026-10-13, the 3.27 benches): Pro runs pa-expert Opus low, pa-coder Sonnet medium,
    # pa-thinker Opus medium; were Opus medium, Opus medium, Opus max (the base rows)
    "pro": dict(_roles(_r(_OPUS, "high"), None)
                + [("pa-expert", _r(_OPUS, "low")), ("pa-coder", _r(_SONNET, "medium")),
                   ("pa-thinker", _r(_OPUS, "medium"))]
                + [(a, _r(_OPUS, "medium")) for a in _JUDGES]
                + [("memory-curator", _r(_OPUS, "medium"))]
                + [("pa-planner", _r(_OPUS, "medium"))]),
}


def row(agent, preset, override=None):
    """``{"model", "effort"}`` for ``agent`` at ``preset``; None when absent or not managed.

    ``override`` is the raw pa.json ``ladder`` value ``{agent: {model?, effort?}}``: merged onto
    the preset row, or onto the max20 row when the preset row is None (an override installs it).
    3.25 T6a: ``agent`` may be a variant ``<type>@<level>``, managed when ``<type>`` is; a variant the
    table lacks is None unless the override names it (then merged onto the type's row)."""
    if agent.split("@", 1)[0] not in MANAGED:
        return None
    base = PRESETS.get(preset, PRESETS["max5"]).get(agent)
    ov = override.get(agent) if isinstance(override, dict) else None
    if not isinstance(ov, dict):
        return dict(base) if base else None
    out = dict(base or PRESETS["max20"].get(agent) or row(agent.split("@", 1)[0], preset) or {})
    if not out:
        return None
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
