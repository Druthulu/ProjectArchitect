"""Overlay-kit migration mapping (design B J.3, bfm-decomp-shaped).

Public:
    mapping(env) -> [Row, ...]

The mapping drives :func:`migrate.run`: every row is a step with its own
check/execute/verify cycle.  The overlay layout has a ``*-architect`` or
``*-kit`` directory whose ``templates/`` carry domain-specific rules, ops
sections and CLAUDE constraints; rules also come from ``phase-ends/DIGEST.md``
bullets.

Stdlib only; nothing touches the filesystem at import time.
"""

import os
import re
import time

from .migrate import Row, readme_rows
from .howwework import _OPS_RE, _CLAUDE_RE


# ---------------------------------------------------------------------------  helpers

_DIGEST_RULE_RE = re.compile(
    r"^-\s+\*\*([A-Z]\d+)\s*(?:--|-|—)\s*(.+?)\.\*\*\s*(.*)")

_REGISTRY_HEADING_RE = re.compile(r"^###\s+([A-Z]\d+)\s*(?:--|-|—)\s*(.*)")


def _parse_digest_rules(path):
    """Parse ``## 3. Every rule, in full`` bullets from DIGEST.md.

    Returns ``[(id, headline, body), ...]``.
    """
    rules = []
    in_section = False
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\r\n")
            if stripped.startswith("## ") and "rule" in stripped.lower():
                in_section = True
                continue
            if stripped.startswith("## "):
                if in_section:
                    break
                continue
            if in_section:
                m = _DIGEST_RULE_RE.match(stripped)
                if m:
                    rules.append((m.group(1), m.group(2).strip(),
                                  m.group(3).strip()))
    return rules


def _parse_registry_rules(path):
    """Parse ``### G1 -- headline`` sections from a kit registry template.

    Returns ``[(id, headline, body), ...]``.
    """
    rules = []
    cur_id = None
    cur_head = None
    body_lines = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\r\n")
            m = _REGISTRY_HEADING_RE.match(stripped)
            if m:
                if cur_id is not None:
                    rules.append((cur_id, cur_head, "\n".join(body_lines).strip()))
                cur_id = m.group(1)
                cur_head = m.group(2).strip()
                body_lines = []
                continue
            if cur_id is not None and stripped and not stripped.startswith("## "):
                body_lines.append(stripped)
    if cur_id is not None:
        rules.append((cur_id, cur_head, "\n".join(body_lines).strip()))
    return rules


def _find_kit(env):
    """Return the relative path of the overlay-kit directory, or None."""
    kit_dirs = env.signals.get("kit_dirs", [])
    return kit_dirs[0] if kit_dirs else None


def _find_registry_templates(kit_dir_abs):
    """Return abs paths of ``registry-E*.md`` (or similar) kit templates."""
    tmpl = os.path.join(kit_dir_abs, "templates")
    if not os.path.isdir(tmpl):
        return []
    return [os.path.join(tmpl, n) for n in sorted(os.listdir(tmpl))
            if n.startswith("registry-") and n.endswith(".md")]


def _find_ops_templates(kit_dir_abs):
    """Return abs paths of ``ops-setup*.md`` kit templates."""
    tmpl = os.path.join(kit_dir_abs, "templates")
    if not os.path.isdir(tmpl):
        return []
    return [os.path.join(tmpl, n) for n in sorted(os.listdir(tmpl))
            if n.startswith("ops-setup") and n.endswith(".md")]


def _find_claude_overlays(kit_dir_abs):
    """Return abs paths of ``CLAUDE*overlay*.md`` kit templates."""
    tmpl = os.path.join(kit_dir_abs, "templates")
    if not os.path.isdir(tmpl):
        return []
    result = []
    for n in sorted(os.listdir(tmpl)):
        low = n.lower()
        if low.startswith("claude") and "overlay" in low and low.endswith(".md"):
            result.append(os.path.join(tmpl, n))
    return result


def _rule_file_text(rid, headline, body):
    """The content for ``rules/<id>.md``."""
    lines = ["# %s -- %s" % (rid, headline)]
    if body:
        lines.append("")
        lines.append(body)
    lines.append("")
    return "\n".join(lines)


def _rule_index_line(rid, headline, source=None):
    """One index line in the ``INDEX.rules.md`` grammar."""
    src_mark = " | source: %s" % source if source else ""
    return "<!-- %s | %s | | active | |%s -->" % (rid, headline, src_mark)


def _kit_agents(kit_dir_abs):
    """Return a list of agent .md file names inside the kit."""
    d = os.path.join(kit_dir_abs, "agents")
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if n.endswith(".md"))


def _kit_commands(kit_dir_abs):
    """Return a list of command .md file names inside the kit."""
    d = os.path.join(kit_dir_abs, "commands")
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if n.endswith(".md"))


def _pa3_file_names(pkg, subdir):
    """Return the set of .md file names under pkg/<subdir>."""
    d = os.path.join(pkg, subdir)
    if not os.path.isdir(d):
        return set()
    return {n for n in os.listdir(d) if n.endswith(".md")}


def _project_agents(env):
    """Return the set of .md file names under the project's .claude/agents/."""
    d = env.path(".claude/agents")
    if not os.path.isdir(d):
        return set()
    return {n for n in os.listdir(d) if n.endswith(".md")}


def _project_commands(env):
    """Return the set of .md file names under the project's .claude/commands/."""
    d = env.path(".claude/commands")
    if not os.path.isdir(d):
        return set()
    return {n for n in os.listdir(d) if n.endswith(".md")}


def _check_agent_collisions(env):
    """Raise ValueError if a project agent or command collides with a PA3 name."""
    pa3_agents = _pa3_file_names(env.pkg, "agents")
    pa3_commands = _pa3_file_names(env.pkg, "commands")

    for name in _project_agents(env):
        if name in pa3_agents:
            raise ValueError(
                "kit agent %s collides with PA3 agent %s" % (name, name))

    for name in _project_commands(env):
        if name in pa3_commands:
            raise ValueError(
                "kit command %s collides with PA3 command %s" % (name, name))


def _highest_closed_phase(env):
    """Return the numeric label of the highest closed PhaseEnd (0 if none)."""
    phase_dir = env.path("phase-ends")
    best = 0
    if not os.path.isdir(phase_dir):
        return 0
    for name in os.listdir(phase_dir):
        if not (name.startswith("PhaseEnd_") and name.endswith(".md")):
            continue
        m = re.match(r"Phase(\d+)", name[len("PhaseEnd_"):-3])
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
        if "cookbook" in name.lower() and name.endswith(".md") and "index" not in name.lower():
            return "docs/%s" % name
    return None


def _find_cookbook_index(env):
    """Return the relative path of a cookbook-index.md, or None."""
    docs = env.path("docs")
    if not os.path.isdir(docs):
        return None
    for name in sorted(os.listdir(docs)):
        if "cookbook" in name.lower() and "index" in name.lower() and name.endswith(".md"):
            return "docs/%s" % name
    return None


def _read_cookbook_index_lines(path):
    """Parse a cookbook-index.md for index lines matching section headings.

    Returns ``{heading_text: index_line, ...}`` where heading_text is the raw
    ``## `` heading stripped of ``##``, and index_line is the relevant bullet.
    """
    result = {}
    # collect bullets that contain a section reference like ``-> section_id``
    # format: ``- **symptom** -> §N (text)`` or similar
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\r\n")
            if stripped.startswith("- ") or stripped.startswith("* "):
                result.setdefault("_raw", []).append(stripped)
    return result


def _cookbook_heading_to_index(heading, raw_bullets, section_num, cb_id):
    """Try to find a matching bullet for a cookbook heading from the cookbook-index.

    Falls back to the standard index line.
    """
    # look for the section id (§N) in the bullets
    section_tag = "%s%d" % ("§", section_num)  # §N
    for bullet in raw_bullets:
        if section_tag in bullet:
            return bullet
    # fallback: standard index line grammar
    return "<!-- %s | %s | | | | origin: %s%d %s -->" % (
        cb_id, heading, "§", section_num, heading)


# ---------------------------------------------------------------------------  mapping

def mapping(env):
    """Build the migration mapping for an overlay-kit repository.

    Returns a list of :class:`~migrate.Row` objects.
    """
    from . import note as _note
    kit_rel = _find_kit(env)
    if kit_rel is None:
        raise ValueError("no kit directory found in overlay layout")
    kit_abs = env.path(kit_rel)

    # -- collision check (raises ValueError -> engine prints FAIL) -----------
    _check_agent_collisions(env)

    # announce the kit and its registry templates
    reg_names = [os.path.basename(p) for p in _find_registry_templates(kit_abs)]
    _note("kit %s (registry: %s)" % (kit_rel, ", ".join(reg_names) or "none"))

    rows = []
    highest = _highest_closed_phase(env)
    n = highest + 1                                   # current phase number
    interphase_label = getattr(env, "interphase", None) or "PA3 migration"
    phase_n = "%d.5" % highest                        # e.g. "37.5"

    # -- rules from DIGEST.md bullets ----------------------------------------
    digest_path = env.path("phase-ends/DIGEST.md")
    digest_rules = []
    if os.path.isfile(digest_path):
        digest_rules = _parse_digest_rules(digest_path)

    for rid, headline, body in digest_rules:
        text = _rule_file_text(rid, headline, body)
        rows.append(Row("generate", None, "rules/%s.md" % rid,
                         "rule %s from DIGEST" % rid,
                         {"generator": "text", "text": text, "mode": "write"}))

    # -- rules from kit registry templates -----------------------------------
    registry_templates = _find_registry_templates(kit_abs)
    kit_rules = []
    for reg_path in registry_templates:
        kit_rules.extend(_parse_registry_rules(reg_path))

    for rid, headline, body in kit_rules:
        text = _rule_file_text(rid, headline, body)
        reg_name = os.path.basename(registry_templates[0]) if registry_templates else kit_rel
        rows.append(Row("generate", None, "rules/%s.md" % rid,
                         "rule %s from %s" % (rid, reg_name),
                         {"generator": "text", "text": text, "mode": "write"}))

    # -- rules INDEX.md lines (digest rules, then kit rules with source) -----
    all_rules = digest_rules + kit_rules
    for rid, headline, _body in digest_rules:
        idx_line = _rule_index_line(rid, headline)
        rows.append(Row("generate", None, "rules/INDEX.md",
                         "index line %s" % rid,
                         {"generator": "text", "text": idx_line + "\n",
                          "mode": "append"}))

    for rid, headline, _body in kit_rules:
        idx_line = _rule_index_line(rid, headline, source=kit_rel)
        rows.append(Row("generate", None, "rules/INDEX.md",
                         "index line %s (source: %s)" % (rid, kit_rel),
                         {"generator": "text", "text": idx_line + "\n",
                          "mode": "append"}))

    # -- frozen header on DIGEST.md ------------------------------------------
    frozen_text = "<!-- frozen at the PA3 migration %s; not maintained -->\n" % (
        time.strftime("%Y-%m-%d"))
    rows.append(Row("generate", None, "phase-ends/DIGEST.md",
                     "frozen header on DIGEST",
                     {"generator": "text", "text": frozen_text,
                      "mode": "prepend"}))

    # -- P4: retire CLAUDE.md ------------------------------------------------
    rows.append(Row("move", "CLAUDE.md", "docs/retired/CLAUDE.pa2.md",
                     "retire pre-PA3 CLAUDE.md"))

    # -- P5: split docs/SETUP.md (ops) then append kit ops sections ----------
    if os.path.isfile(env.path("docs/SETUP.md")):
        rows.append(Row("split", "docs/SETUP.md", "docs/ops",
                         "## scheme -> docs/ops/ (resplit)",
                         {"level": "##", "scheme": "ops", "resplit": True}))

    # kit ops sections appended as text generates into docs/ops/
    for ops_path in _find_ops_templates(kit_abs):
        ops_name = os.path.basename(ops_path)
        slug = ops_name.replace(".md", "").replace(".", "-")
        with open(ops_path, "r", encoding="utf-8") as fh:
            ops_text = fh.read()
        rows.append(Row("generate", None, "docs/ops/%s.md" % slug,
                         "kit ops %s" % ops_name,
                         {"generator": "text", "text": ops_text, "mode": "write"}))

    # -- P5: split cookbook ---------------------------------------------------
    cb_rel = _find_cookbook(env)
    cb_index_rel = _find_cookbook_index(env)
    if cb_rel:
        rows.append(Row("split", cb_rel, "cookbook",
                         "## scheme -> cookbook/",
                         {"level": "##", "scheme": "cookbook"}))

        # build cookbook index lines from the existing cookbook-index.md when possible
        if cb_index_rel and os.path.isfile(env.path(cb_index_rel)):
            cb_idx_data = _read_cookbook_index_lines(env.path(cb_index_rel))
            raw_bullets = cb_idx_data.get("_raw", [])
            # read headings from the cookbook to match
            headings = []
            with open(env.path(cb_rel), "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("## "):
                        headings.append(line[3:].rstrip("\r\n").strip())
            for i, heading in enumerate(headings, 1):
                cb_id = "C%04d" % i
                idx_line = _cookbook_heading_to_index(
                    heading, raw_bullets, i, cb_id)
                rows.append(Row("generate", None, "cookbook/INDEX.md",
                                 "cookbook index %s" % cb_id,
                                 {"generator": "text",
                                  "text": idx_line + "\n",
                                  "mode": "append"}))

    # -- D8: retire the stale 2.0 cookbook-index after split ------------------
    if cb_rel and cb_index_rel:
        rows.append(Row("retire", cb_index_rel,
                         "docs/retired/%s" % os.path.basename(cb_index_rel),
                         "stale 2.0 cookbook index"))

    # -- P4: retire effort-map -----------------------------------------------
    if os.path.isfile(env.path("docs/effort-map.md")):
        rows.append(Row("retire", "docs/effort-map.md",
                         "docs/retired/effort-map.md", ""))

    # -- P4: archive CURRENT_PHASE.md ----------------------------------------
    if os.path.isfile(env.path("phase-ends/CURRENT_PHASE.md")):
        rows.append(Row("move", "phase-ends/CURRENT_PHASE.md",
                         "phase-ends/logs/PhaseLog_%d.partial.md" % n,
                         "archive current phase"))

    # -- P9: generate LEGACY_INDEX.md ----------------------------------------
    rows.append(Row("generate", "", "phase-ends/LEGACY_INDEX.md",
                     "from PhaseEnd files",
                     {"generator": "legacy_index"}))

    # -- P9: generate GENERATION_PLAN.md -------------------------------------
    rows.append(Row("generate", "", "GENERATION_PLAN.md",
                     "from template + legacy index",
                     {"generator": "generation_plan",
                      "gen_number": 1, "gen_name": ""}))

    # -- P7: generate new CLAUDE.md ------------------------------------------
    rows.append(Row("generate", "", "CLAUDE.md",
                     "PA3 CLAUDE.md",
                     {"generator": "claude_md"}))

    # -- P7: gitignore transcripts -------------------------------------------
    rows.append(Row("generate", "", ".gitignore",
                     ".claude-state/transcripts/ line",
                     {"generator": "gitignore_transcripts"}))

    # -- P8: generate HOW_WE_WORK.md -----------------------------------------
    from . import howwework
    hw_sources = []
    if os.path.isfile(env.path("docs/SETUP.md")):
        hw_sources.append((env.path("docs/SETUP.md"), _OPS_RE))
    for ops_path in _find_ops_templates(kit_abs):
        hw_sources.append((ops_path, _OPS_RE))
    if os.path.isfile(env.path("CLAUDE.md")):
        hw_sources.append((env.path("CLAUDE.md"), _CLAUDE_RE))
    for claude_path in _find_claude_overlays(kit_abs):
        hw_sources.append((claude_path, _CLAUDE_RE))
    hw_found = howwework.inputs(env, hw_sources)

    # docs that are left in place: decision-log, tool-index, memory-map
    docs_in_place = []
    for doc_name in ("decision-log.md", "tool-index.md"):
        doc_rel = "docs/%s" % doc_name
        if os.path.isfile(env.path(doc_rel)):
            docs_in_place.append(doc_rel)

    # existing project agents and commands (kit-installed) as extra agents
    pa3_agents = _pa3_file_names(env.pkg, "agents")
    pa3_commands = _pa3_file_names(env.pkg, "commands")
    extra_agents = []
    for name in sorted(_project_agents(env)):
        if name not in pa3_agents:
            extra_agents.append(".claude/agents/%s" % name)
    for name in sorted(_project_commands(env)):
        if name not in pa3_commands:
            extra_agents.append(".claude/commands/%s" % name)

    # add docs-in-place and extra agents to hw_found as DATA_PATHS / DOCS
    for doc_rel in docs_in_place:
        hw_found.setdefault("DATA_PATHS", []).append(
            ("%s:1" % doc_rel, doc_rel))
    for agent_rel in extra_agents:
        hw_found.setdefault("DOCS", []).append(
            ("%s:1" % agent_rel, agent_rel))

    rows.append(Row("generate", "", "HOW_WE_WORK.md",
                     "from template + ops/CLAUDE.md candidates",
                     {"generator": "how_we_work", "found": hw_found}))

    # -- phase-ends README: retire 2.0, generate PA3 -------------------------
    rows.extend(readme_rows(env))

    # -- P9: interphase PhaseEnd ---------------------------------------------
    interphase_path = "phase-ends/PhaseEnd_Phase%s.md" % phase_n
    rows.append(Row("generate", "", interphase_path,
                     "interphase PhaseEnd",
                     {"generator": "interphase_phaseend",
                      "label": interphase_label,
                      "phase_n": phase_n}))

    return rows
