"""Stock 2.0 migration mapping (design B J.3, TerrainDiffusion7DTD-shaped).

Public:
    mapping(env) -> [Row, ...]

The mapping drives :func:`migrate.run`: every row is a step with its own
check/execute/verify cycle.  The function reads the tree's real names and
computes ``N`` (highest closed PhaseEnd + 1) for the current-phase archive
and the interphase label.

Stdlib only; nothing touches the filesystem at import time.
"""

import os
import re

from . import natural_sort
from .migrate import Row, readme_rows
from .howwework import _OPS_RE, _CLAUDE_RE


def _highest_closed_phase(env):
    """Return the numeric label of the highest closed PhaseEnd (0 if none)."""
    phase_dir = env.path("phase-ends")
    best = 0
    if not os.path.isdir(phase_dir):
        return 0
    for name in os.listdir(phase_dir):
        if not (name.startswith("PhaseEnd_") and name.endswith(".md")):
            continue
        label = name[len("PhaseEnd_"):-3]
        m = re.match(r"Phase(\d+)", label)
        if m:
            n = int(m.group(1))
            if n > best:
                best = n
    return best


def _find_cookbook(env):
    """Return the relative path (docs/<name>) of the cookbook file, or None."""
    docs = env.path("docs")
    if not os.path.isdir(docs):
        return None
    for name in sorted(os.listdir(docs)):
        if "cookbook" in name.lower() and name.endswith(".md"):
            return "docs/%s" % name
    return None


def _find_templates(env):
    """Return relative paths of phase-ends/*.template.md files."""
    phase_dir = env.path("phase-ends")
    result = []
    if not os.path.isdir(phase_dir):
        return result
    for name in sorted(os.listdir(phase_dir)):
        if name.endswith(".template.md"):
            result.append("phase-ends/%s" % name)
    return result


def mapping(env):
    """Build the migration mapping for a stock 2.0 repository.

    Returns a list of :class:`~migrate.Row` objects.
    """
    rows = []
    highest = _highest_closed_phase(env)
    n = highest + 1                                   # current phase number
    interphase_label = getattr(env, "interphase", None) or "PA3 migration"
    phase_n = "%d.5" % highest                        # e.g. "23.5"

    # -- P4: move CLAUDE.md -> docs/retired/ --------------------------------
    rows.append(Row("move", "CLAUDE.md", "docs/retired/CLAUDE.pa2.md",
                     "retire pre-PA3 CLAUDE.md"))

    # -- P5: split RULES_REGISTRY.md ---------------------------------------
    if os.path.isfile(env.path("RULES_REGISTRY.md")):
        rows.append(Row("split", "RULES_REGISTRY.md", "rules",
                         "### scheme -> rules/",
                         {"level": "###", "scheme": "rules"}))

    # -- P5: split cookbook -------------------------------------------------
    cb_rel = _find_cookbook(env)
    if cb_rel:
        rows.append(Row("split", cb_rel, "cookbook",
                         "## scheme -> cookbook/",
                         {"level": "##", "scheme": "cookbook"}))

    # -- P5: split ops-setup.md --------------------------------------------
    if os.path.isfile(env.path("docs/ops-setup.md")):
        rows.append(Row("split", "docs/ops-setup.md", "docs/ops",
                         "## scheme -> docs/ops/ (resplit)",
                         {"level": "##", "scheme": "ops", "resplit": True}))

    # -- P4: retire effort-map, templates, project-architect ----------------
    if os.path.isfile(env.path("docs/effort-map.md")):
        rows.append(Row("retire", "docs/effort-map.md",
                         "docs/retired/effort-map.md", ""))
    for tmpl in _find_templates(env):
        name = os.path.basename(tmpl)
        rows.append(Row("retire", tmpl,
                         "docs/retired/%s" % name, ""))
    if os.path.isfile(env.path("docs/project-architect.md")):
        rows.append(Row("retire", "docs/project-architect.md",
                         "docs/retired/project-architect-2.0.md", ""))

    # -- P4: archive CURRENT_PHASE.md --------------------------------------
    if os.path.isfile(env.path("phase-ends/CURRENT_PHASE.md")):
        rows.append(Row("move", "phase-ends/CURRENT_PHASE.md",
                         "phase-ends/logs/PhaseLog_%d.partial.md" % n,
                         "archive current phase"))

    # -- P7: remove backup hook -------------------------------------------
    rows.append(Row("remove_hook", ".claude/settings.json", "",
                     "backup-claude-state.sh"))

    # -- P7: gitignore transcripts ----------------------------------------
    rows.append(Row("generate", "", ".gitignore",
                     ".claude-state/transcripts/ line",
                     {"generator": "gitignore_transcripts"}))

    # -- P7: generate new CLAUDE.md ----------------------------------------
    rows.append(Row("generate", "", "CLAUDE.md",
                     "PA3 CLAUDE.md",
                     {"generator": "claude_md"}))

    # -- P8: generate HOW_WE_WORK.md ---------------------------------------
    # collect inputs NOW (before sources are moved/retired)
    from . import howwework
    hw_sources = []
    if os.path.isfile(env.path("docs/ops-setup.md")):
        hw_sources.append((env.path("docs/ops-setup.md"), _OPS_RE))
    if os.path.isfile(env.path("CLAUDE.md")):
        hw_sources.append((env.path("CLAUDE.md"), _CLAUDE_RE))
    hw_found = howwework.inputs(env, hw_sources)
    rows.append(Row("generate", "", "HOW_WE_WORK.md",
                     "from template + ops/CLAUDE.md candidates",
                     {"generator": "how_we_work", "found": hw_found}))

    # -- P9: generate LEGACY_INDEX.md --------------------------------------
    rows.append(Row("generate", "", "phase-ends/LEGACY_INDEX.md",
                     "from PhaseEnd files",
                     {"generator": "legacy_index"}))

    # -- P9: generate GENERATION_PLAN.md -----------------------------------
    rows.append(Row("generate", "", "GENERATION_PLAN.md",
                     "from template + legacy index",
                     {"generator": "generation_plan",
                      "gen_number": 1, "gen_name": ""}))

    # -- phase-ends README: retire 2.0, generate PA3 -----------------------
    rows.extend(readme_rows(env))

    # -- P9: interphase PhaseEnd -------------------------------------------
    interphase_path = "phase-ends/PhaseEnd_Phase%s.md" % phase_n
    mapping_table = _mapping_table(rows)
    counts = _counts_line(rows, env)
    rows.append(Row("generate", "", interphase_path,
                     "interphase PhaseEnd",
                     {"generator": "interphase_phaseend",
                      "label": interphase_label,
                      "phase_n": phase_n,
                      "mapping_table": mapping_table,
                      "counts": counts}))

    return rows


def _mapping_table(rows):
    """Build a task-table string from the mapping rows (one line per row)."""
    lines = []
    for i, row in enumerate(rows):
        if row.kind == "generate" and (row.params or {}).get("generator") == "interphase_phaseend":
            continue
        lines.append("- %s: %s -> %s  (%s)" % (row.kind, row.src or "-", row.dst or "-",
                                                  row.note or "-"))
    return "\n".join(lines)


def _counts_line(rows, env):
    """A plain-English line naming the counts."""
    n_rules = 0
    n_cookbook = 0
    n_ops = 0
    n_moves = 0
    n_retired = 0

    for row in rows:
        if row.kind == "split":
            p = row.params or {}
            scheme = p.get("scheme", "")
            src_path = env.path(row.src)
            if os.path.isfile(src_path):
                from . import split_by_heading
                plan = split_by_heading.plan(
                    src_path, p.get("level", "##"), row.dst,
                    scheme, resplit=p.get("resplit", False))
                if scheme == "rules":
                    n_rules = len(plan.rows)
                elif scheme == "cookbook":
                    n_cookbook = len(plan.rows)
                elif scheme == "ops":
                    n_ops = len(plan.rows)
        elif row.kind == "move":
            n_moves += 1
        elif row.kind == "retire":
            n_retired += 1

    parts = []
    if n_rules:
        parts.append("%d rules" % n_rules)
    if n_cookbook:
        parts.append("%d cookbook entries" % n_cookbook)
    if n_ops:
        parts.append("%d ops topics" % n_ops)
    if n_moves:
        parts.append("%d moves" % n_moves)
    if n_retired:
        parts.append("%d retired" % n_retired)
    return "Migration produced %s." % ", ".join(parts) if parts else ""
