"""1.x-shaped migration mapping (Vantage-shaped).

Public:
    mapping(env) -> [Row, ...]

The mapping drives :func:`migrate.run`: every row is a step with its own
check/execute/verify cycle.  The folder holding the registry, context and
PhaseEnd files is only the source: its PhaseEnds move to ``phase-ends/``, the
``phase_ends_dir`` in ``pa.json``, with ``current/``, ``misc/`` and ``logs/``
inside it; its other documents stay where they are (3.32 B10a).

Stdlib only; nothing touches the filesystem at import time.
"""

import fnmatch
import os
import re
import time

from . import natural_sort
from . import phaseend_normalize
from .migrate import Row, interphase_id, readme_rows, running_phase
from .howwework import _CLAUDE_RE

# glob patterns for research-archive documents (legacy; see _is_research_doc)
RESEARCH_GLOBS = (
    "*_Plan.md", "*_Verdict.md", "*Prereg*.md", "*Checkpoint*.md",
    "*Audit*.md", "*Brief.md", "*Report.md", "*_FixPlan.md", "*Guide.md",
)

# keywords that mark a file as research (case-insensitive token match)
_RESEARCH_KEYWORDS = frozenset({
    "plan", "verdict", "prereg", "checkpoint", "audit", "brief",
    "report", "fixplan", "guide", "decision", "spec", "notes",
    "handoff", "adjudication", "proposal", "protocol", "autopsy",
    "triage", "candidates", "ledger",
})

# stay-words: file stays in place unless generation-prefixed
_STAY_KEYWORDS = frozenset({
    "reference", "thesis", "description", "design", "ideas",
})

# generation/phase prefix patterns
_GEN_PREFIX_RE = re.compile(r"\w+_Gen\d", re.IGNORECASE)
_PHASE_PREFIX_RE = re.compile(r"\w+\s+Phase\s+\d", re.IGNORECASE)


# a doc edited this recently is live: it stays put (3.32 B3; `*_Plan.md` took the live Gen5 plan and Gen6 inbox)
RECENT_DAYS = 30


# --------------------------------------------------------------------------- helpers

def _phase_ends_dir(env):
    """Derive the project's PA2 folder from detect.signals.

    The folder holding ``registry_like``, ``context_like`` or
    ``loose_phaseends`` paths is the migration's source.
    """
    sig = env.signals
    candidates = []
    for key in ("registry_like", "context_like", "loose_phaseends"):
        for rel in sig.get(key, []):
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if os.path.isdir(env.path(rel)):
                # a directory entry (context_like can be a folder)
                candidates.append(rel)
            elif parent:
                candidates.append(parent)
    dirs = sorted(set(d for d in candidates if d))
    return dirs[0] if dirs else "phase-ends"


def _phaseend_files(env, phase_dir):
    """List PhaseEnd_*.md file names in *phase_dir*."""
    abs_dir = env.path(phase_dir)
    if not os.path.isdir(abs_dir):
        return []
    return sorted(name for name in os.listdir(abs_dir)
                  if name.startswith("PhaseEnd_") and name.endswith(".md"))


def _newest_family(normalized_names):
    """Return the family label of the newest PhaseEnd.

    The family is the first token of the label when split on ``[._-]+``.
    """
    labels = []
    for name in normalized_names:
        if not (name.startswith("PhaseEnd_Phase") and name.endswith(".md")):
            continue
        label = name[len("PhaseEnd_Phase"):-3]
        labels.append(label)
    if not labels:
        return "1"
    labels.sort(key=natural_sort.key)
    newest = labels[-1]
    tokens = re.split(r"[._\-]+", newest)
    return tokens[0] if tokens else newest


def _is_research_doc(name):
    """True when *name* should be archived as a research document.

    Case-insensitive.  Excludes PhaseEnd, registry, context,
    CURRENT_PHASE*, README, templates and index files.  Research when the
    name carries a generation/phase prefix or any research keyword; names
    with stay-words (reference, thesis, description, design, ideas) are
    kept in place unless generation-prefixed.
    """
    low = name.lower()
    if not low.endswith(".md"):
        return False
    # exclusions
    if low.startswith("phaseend_"):
        return False
    if "rules_registry" in low or "project_context" in low:
        return False
    if low.startswith("current_phase"):
        return False
    if low.startswith("readme"):
        return False
    if low.endswith(".template.md"):
        return False
    if low in ("research_index.md", "legacy_index.md"):
        return False

    stem = name[:-3]  # strip .md
    has_gen = bool(_GEN_PREFIX_RE.search(stem)) or bool(_PHASE_PREFIX_RE.search(stem))

    # generation-prefixed -> always research
    if has_gen:
        return True

    # tokenize for keyword matching
    tokens = set(re.split(r"[_\s\-]+", stem.lower()))

    # stay-words without gen prefix -> not research
    if tokens & _STAY_KEYWORDS:
        return False

    # research keywords -> research
    if tokens & _RESEARCH_KEYWORDS:
        return True

    return False


def _recently_edited(env, ped):
    """The file names directly in *ped* edited in the last RECENT_DAYS days: by commit date, since a clone's
    files all carry the checkout's mtime; by mtime for what git has not seen yet (or with no git)."""
    from .project import _git
    cut = time.time() - RECENT_DAYS * 86400

    def recent(name):
        path = env.path("%s/%s" % (ped, name))
        return os.path.isfile(path) and os.path.getmtime(path) >= cut

    if not env.git_top:
        abs_ped = env.path(ped)
        return {n for n in os.listdir(abs_ped) if recent(n)} if os.path.isdir(abs_ped) else set()
    prefix = ped + "/"
    _rc, log = _git(env.repo, "-c", "core.quotepath=off", "log", "--since=%d.days.ago" % RECENT_DAYS,
                    "--name-only", "--format=", "--", ped, timeout=300)
    names = {p[len(prefix):] for p in log.splitlines() if p.startswith(prefix) and "/" not in p[len(prefix):]}
    _rc, status = _git(env.repo, "status", "--porcelain", "-z", "--", ped, timeout=300)
    for entry in status.split("\0"):
        rel = entry[3:]
        if rel.startswith(prefix) and "/" not in rel[len(prefix):] and recent(rel[len(prefix):]):
            names.add(rel[len(prefix):])
    return names


def _research_index_text(files, env, phase_dir):
    """Build ``docs/research-archive/INDEX.md`` content."""
    lines = ["# Research Archive (pre-PA3)", ""]
    lines.append("| file | size | first heading | date |")
    lines.append("|---|---|---|---|")
    for name in sorted(files):
        path = env.path(os.path.join(phase_dir, name).replace("\\", "/"))
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        heading = ""
        date = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("# ") and not heading:
                        heading = line[2:].strip()[:80]
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                    if m and not date:
                        date = m.group(1)
        lines.append("| %s | %d | %s | %s |" % (name, size, heading, date))
    lines.append("")
    return "\n".join(lines) + "\n"


def _extract_harness_facts(env, phase_dir):
    """Extract the HARNESS FACTS paragraph from CURRENT_PHASE.md."""
    path = env.path(phase_dir + "/CURRENT_PHASE.md")
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "HARNESS FACTS" in line:
                # strip bold markers and the label itself
                text = re.sub(r"^\*\*HARNESS FACTS[^.]*\.\*\*\s*", "", line.strip())
                return text.strip()
    return ""


def _find_by_glob(env, phase_dir, pattern):
    """Return the first file name matching *pattern* in *phase_dir*, or None."""
    abs_dir = env.path(phase_dir)
    if not os.path.isdir(abs_dir):
        return None
    for name in sorted(os.listdir(abs_dir)):
        if fnmatch.fnmatch(name, pattern):
            return name
    return None


def _registry_level(path):
    """Choose split level: ``###`` when the file has any, else ``##``."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("### "):
                return "###"
    return "##"


def _find_cookbook_in_ped(env, ped):
    """Return the relative path of a cookbook file in *ped*/docs/ or *ped*/, or None."""
    for subdir in ("%s/docs" % ped, ped):
        abs_dir = env.path(subdir)
        if not os.path.isdir(abs_dir):
            continue
        for name in sorted(os.listdir(abs_dir)):
            low = name.lower()
            if "cookbook" in low and low.endswith(".md") and "index" not in low:
                return "%s/%s" % (subdir, name)
    return None


def _find_ops_setup_in_ped(env, ped):
    """Return the relative path of an ops-setup file in *ped*/docs/ or *ped*/, or None."""
    for subdir in ("%s/docs" % ped, ped):
        abs_dir = env.path(subdir)
        if not os.path.isdir(abs_dir):
            continue
        for name in sorted(os.listdir(abs_dir)):
            low = name.lower()
            if (fnmatch.fnmatch(low, "*ops_setup*.md") or
                    fnmatch.fnmatch(low, "*ops-setup*.md")):
                return "%s/%s" % (subdir, name)
    return None


def _sort_check_line(normalized_names):
    """A note line showing the sorted PhaseEnd labels for verification."""
    labels = []
    for name in sorted(normalized_names):
        if not (name.startswith("PhaseEnd_Phase") and name.endswith(".md")):
            continue
        label = name[len("PhaseEnd_Phase"):-3]
        labels.append(label)
    labels.sort(key=natural_sort.key)
    return "sort: %s" % ", ".join(labels)


# --------------------------------------------------------------------------- mapping

def mapping(env):
    """Build the migration mapping for a 1.x-shaped repository.

    Returns a list of :class:`~migrate.Row` objects.
    """
    from . import howwework, note

    rows = []

    # -- the PA2 folder (ped) is the source; PA3's home is always phase-ends --
    # 3.32 B10a: the mod and the agents name `phase-ends`; a PhaseEnd or a PA3 working file goes there, the rest stays
    ped = _phase_ends_dir(env)
    home = env.phase_ends_dir = "phase-ends"
    env.phase_ends_src = ped

    # -- PhaseEnd renames per phaseend_normalize.plan -----------------------
    pe_names = _phaseend_files(env, ped)
    renames = phaseend_normalize.plan(pe_names)

    # build the set of normalized names for family detection and sort check
    normalized = set(pe_names)
    for old, new, _kind in renames:
        normalized.discard(old)
        basename = new.split("/")[-1] if "/" in new else new
        normalized.add(basename)

    newest_family = _newest_family(sorted(normalized))
    interphase_label = getattr(env, "interphase", None) or "PA3 migration"
    phase_n = interphase_id(newest_family)

    for old, new, kind in renames:
        src = "%s/%s" % (ped, old)
        dst = "%s/%s" % (home, new)
        rows.append(Row("move", src, dst, "normalize PhaseEnd name"))

    # -- PhaseEnds with no rename and GenerationEnds move to phase-ends ------
    abs_ped = env.path(ped)
    gen_ends = []
    if ped != home:
        if os.path.isdir(abs_ped):
            gen_ends = sorted(n for n in os.listdir(abs_ped) if fnmatch.fnmatch(n, "GenerationEnd*.md"))
        renamed = {old for old, _new, _kind in renames}
        for name in [n for n in pe_names if n not in renamed] + gen_ends:
            rows.append(Row("move", "%s/%s" % (ped, name), "%s/%s" % (home, name), "move to phase-ends"))

    # -- research docs -> docs/research-archive/ ----------------------------
    research_docs = []
    if os.path.isdir(abs_ped):
        live = _recently_edited(env, ped)
        for name in sorted(os.listdir(abs_ped)):
            if name.endswith(".md") and _is_research_doc(name) and name not in live and name not in gen_ends:
                research_docs.append(name)
        kept = [n for n in sorted(live) if _is_research_doc(n)]
        if kept:
            note("kept in place (edited in the last %d days): %s" % (RECENT_DAYS, ", ".join(kept)))

    for name in research_docs:
        rows.append(Row("move", "%s/%s" % (ped, name),
                         "docs/research-archive/%s" % name,
                         "archive research doc"))

    if research_docs:
        index_text = _research_index_text(research_docs, env, ped)
        rows.append(Row("generate", None, "docs/research-archive/INDEX.md",
                         "research archive index",
                         {"generator": "text", "text": index_text,
                          "mode": "write"}))
        legacy_line = ("\n## Legacy\n\n"
                       "Pre-PA3 research documents: "
                       "see `docs/research-archive/INDEX.md`.\n")
        rows.append(Row("generate", None, "%s/RESEARCH_INDEX.md" % home,
                         "legacy pointer in RESEARCH_INDEX",
                         {"generator": "text", "text": legacy_line,
                          "mode": "append"}))
        note("archive: %d research documents" % len(research_docs))

    # -- split rules registry -----------------------------------------------
    reg_name = _find_by_glob(env, ped, "*Rules_Registry*.md")
    if reg_name:
        reg_path = env.path("%s/%s" % (ped, reg_name))
        level = _registry_level(reg_path)
        rows.append(Row("split", "%s/%s" % (ped, reg_name), "rules",
                         "%s scheme -> rules/" % level,
                         {"level": level, "scheme": "rules"}))

    # -- move project context (verbatim) ------------------------------------
    ctx_name = _find_by_glob(env, ped, "*Project_Context*.md")
    if ctx_name:
        rows.append(Row("move", "%s/%s" % (ped, ctx_name),
                         "PROJECT_CONTEXT.md", "project context (verbatim)"))

    # -- archive CURRENT_PHASE.md -------------------------------------------
    cp_rel = "%s/CURRENT_PHASE.md" % ped
    if os.path.isfile(env.path(cp_rel)):
        rows.append(Row("move", cp_rel,
                         "%s/logs/PhaseLog_%s.partial.md" % (home, newest_family),
                         "archive current phase"))

    # -- move phase-logs/* -> logs/ (D10: collision check first) -------------
    logs_src = env.path("%s/phase-logs" % ped)
    if os.path.isdir(logs_src):
        logs_dst_dir = env.path("%s/logs" % home)
        phase_log_names = sorted(n for n in os.listdir(logs_src)
                                 if n.endswith(".md"))
        if os.path.isdir(logs_dst_dir):
            for name in phase_log_names:
                src_p = os.path.join(logs_src, name)
                dst_p = os.path.join(logs_dst_dir, name)
                if os.path.isfile(dst_p) and os.path.isfile(src_p):
                    raise ValueError(
                        "phase-log collision: %s exists in both "
                        "phase-logs/ and logs/" % name)
        for name in phase_log_names:
            rows.append(Row("move",
                             "%s/phase-logs/%s" % (ped, name),
                             "%s/logs/%s" % (home, name),
                             "move phase log"))

    # -- retire effort map ---------------------------------------------------
    docs_sub = env.path("%s/docs" % ped)
    if os.path.isdir(docs_sub):
        for name in sorted(os.listdir(docs_sub)):
            if fnmatch.fnmatch(name, "*Effort_Map*.md"):
                rows.append(Row("retire",
                                 "%s/docs/%s" % (ped, name),
                                 "docs/retired/%s" % name, ""))

    # -- D7: retire 2.0 templates in ped ------------------------------------
    if os.path.isdir(abs_ped):
        for name in sorted(os.listdir(abs_ped)):
            if name.endswith(".template.md"):
                rows.append(Row("retire",
                                 "%s/%s" % (ped, name),
                                 "docs/retired/%s" % name,
                                 "retire 2.0 template"))

    # -- D11: split cookbook in ped ------------------------------------------
    cb_ped = _find_cookbook_in_ped(env, ped)
    if cb_ped:
        rows.append(Row("split", cb_ped, "cookbook",
                         "## scheme -> cookbook/",
                         {"level": "##", "scheme": "cookbook"}))

    # -- D11: split ops_setup in ped ----------------------------------------
    ops_ped = _find_ops_setup_in_ped(env, ped)
    if ops_ped:
        rows.append(Row("split", ops_ped, "docs/ops",
                         "## scheme -> docs/ops/ (resplit)",
                         {"level": "##", "scheme": "ops", "resplit": True}))

    # -- retire old CLAUDE.md ------------------------------------------------
    rows.append(Row("move", "CLAUDE.md", "docs/retired/CLAUDE.pa2.md",
                     "retire pre-PA3 CLAUDE.md"))

    # -- P7: generate new CLAUDE.md ------------------------------------------
    rows.append(Row("generate", "", "CLAUDE.md", "PA3 CLAUDE.md",
                     {"generator": "claude_md"}))

    # -- P8: generate HOW_WE_WORK.md -----------------------------------------
    hw_sources = []
    if os.path.isfile(env.path("CLAUDE.md")):
        hw_sources.append((env.path("CLAUDE.md"), _CLAUDE_RE))
    hw_found = howwework.inputs(env, hw_sources)

    # extract HARNESS FACTS paragraph from CURRENT_PHASE.md
    harness = _extract_harness_facts(env, ped)
    if harness:
        pointer = "%s/CURRENT_PHASE.md:harness" % ped
        hw_found.setdefault("PROJECT_CONVENTION", []).append(
            (pointer, harness))

    rows.append(Row("generate", "", "HOW_WE_WORK.md",
                     "from template + CLAUDE.md/CURRENT_PHASE.md candidates",
                     {"generator": "how_we_work", "found": hw_found}))

    # -- P9: generate LEGACY_INDEX.md ----------------------------------------
    rows.append(Row("generate", "", "%s/LEGACY_INDEX.md" % home,
                     "from PhaseEnd files",
                     {"generator": "legacy_index", "phase_dir": home}))

    # -- P9: generate GENERATION_PLAN.md -------------------------------------
    rows.append(Row("generate", "", "GENERATION_PLAN.md",
                     "from template + legacy index",
                     {"generator": "generation_plan",
                      "gen_number": 1, "gen_name": "",
                      "interphase": phase_n,
                      "running": running_phase(env, "%s/CURRENT_PHASE.md" % ped)}))

    # -- phase-ends README: retire 2.0, generate PA3 -------------------------
    rows.extend(readme_rows(env))

    # -- P9: interphase PhaseEnd ---------------------------------------------
    interphase_path = "%s/PhaseEnd_Phase%s.md" % (home, phase_n)
    mapping_table = _mapping_table(rows)
    counts = _counts_line(rows, env)
    rows.append(Row("generate", "", interphase_path,
                     "interphase PhaseEnd",
                     {"generator": "interphase_phaseend",
                      "label": interphase_label,
                      "phase_n": phase_n,
                      "mapping_table": mapping_table,
                      "counts": counts}))

    # -- gitignore transcripts (only when the directory exists) --------------
    if os.path.isdir(env.path(".claude-state/transcripts")):
        rows.append(Row("generate", "", ".gitignore",
                         ".claude-state/transcripts/ line",
                         {"generator": "gitignore_transcripts"}))

    # -- sort-check note (printed during mapping construction) ---------------
    if normalized:
        note(_sort_check_line(sorted(normalized)))

    return rows


def _mapping_table(rows):
    """Build a task-table string from the mapping rows (one line per row)."""
    lines = []
    for row in rows:
        if (row.kind == "generate"
                and (row.params or {}).get("generator") == "interphase_phaseend"):
            continue
        lines.append("- %s: %s -> %s  (%s)" % (
            row.kind, row.src or "-", row.dst or "-", row.note or "-"))
    return "\n".join(lines)


def _counts_line(rows, env):
    """A plain-English line naming the counts."""
    n_rules = 0
    n_moves = 0
    n_retired = 0

    for row in rows:
        if row.kind == "split":
            p = row.params or {}
            src_path = env.path(row.src)
            if os.path.isfile(src_path):
                from . import split_by_heading
                plan = split_by_heading.plan(
                    src_path, p.get("level", "##"), row.dst,
                    p.get("scheme", "rules"), resplit=p.get("resplit", False))
                if p.get("scheme") == "rules":
                    n_rules = len(plan.rows)
        elif row.kind == "move":
            n_moves += 1
        elif row.kind == "retire":
            n_retired += 1

    parts = []
    if n_rules:
        parts.append("%d rules" % n_rules)
    if n_moves:
        parts.append("%d moves" % n_moves)
    if n_retired:
        parts.append("%d retired" % n_retired)
    return "Migration produced %s." % ", ".join(parts) if parts else ""
