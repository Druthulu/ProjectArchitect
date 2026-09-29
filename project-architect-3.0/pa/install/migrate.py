"""Migration engine for PA2 -> PA3 layouts (design B J.3, P4..P10).

Public:
    Row                 a mapping row: (kind, src, dst, note [, params])
    run(env, layout_mod)  execute the full migration; returns a process exit code

A ``layout_mod`` is a module with ``mapping(env) -> [Row, ...]``.  Today only
``stock20`` is implemented; ``onex`` and ``overlay`` plug in by providing the
same function.

Nothing touches the filesystem at import time; stdlib only.
"""

import json
import os
import shutil
import time
from collections import namedtuple

from .. import fsutil
from . import note, step
from . import memory_sort
from . import split_by_heading
from . import legacy_index
from . import settings_merge as sm
from . import same_bytes

Row = namedtuple("Row", ["kind", "src", "dst", "note", "params"], defaults=[None])

STATE_REL = ".run/install-state.json"


# --------------------------------------------------------------------------- state

def _load_state(env):
    return fsutil.read_json(env.path(STATE_REL), None)


def _save_state(env, state):
    if env.dry_run:
        return
    os.makedirs(os.path.dirname(env.path(STATE_REL)), exist_ok=True)
    fsutil.atomic_write_json(env.path(STATE_REL), state, indent=2)


# --------------------------------------------------------------------------- dry run

def _q(path):
    """Wrap *path* in double quotes when it contains a space."""
    return '"%s"' % path if " " in path else path


def _dry_line(row, env):
    """One dry-run note line for a mapping row."""
    if row.kind == "move":
        return "git mv %s %s" % (_q(row.src), _q(row.dst))
    if row.kind == "split":
        p = row.params or {}
        src_path = env.path(row.src)
        if os.path.isfile(src_path):
            plan = split_by_heading.plan(
                src_path, p.get("level", "##"), row.dst,
                p.get("scheme", "rules"), resplit=p.get("resplit", False))
            n = len(plan.rows)
        else:
            n = "?"
        return "split %s -> %s files in %s" % (_q(row.src), n, row.dst)
    if row.kind == "retire":
        return "retire %s -> %s" % (_q(row.src), _q(row.dst))
    if row.kind == "remove_hook":
        return "remove hook %s" % row.note
    if row.kind == "generate":
        return "generate %s" % row.dst
    return "%s %s -> %s" % (row.kind, row.src, row.dst)


def _line_key(row):
    """(dst, mode) for a text append/prepend row (one index line), else None.

    Consecutive rows with the same key print as one plan line and one step line.
    """
    p = row.params or {}
    if (row.kind == "generate" and p.get("generator") == "text"
            and p.get("mode", "write") != "write"):
        return (row.dst, p.get("mode"))
    return None


def _dry_lines(mapping, env):
    """The dry-run plan lines; a run of index-line rows for one dest is one line."""
    out = []
    i = 0
    while i < len(mapping):
        key = _line_key(mapping[i])
        j = i + 1
        while key is not None and j < len(mapping) and _line_key(mapping[j]) == key:
            j += 1
        line = _dry_line(mapping[i], env)
        if j - i > 1:
            line = "%s (%d lines)" % (line, j - i)
        out.append(line)
        i = j
    return out


def _unmapped_md(env, mapping):
    """Markdown files in top-level, docs/ and phase-ends/ not named by any row.

    Files kept by design are excluded: ``PROJECT_CONTEXT.md`` at the root
    (the constitution) and ``PhaseEnd_*.md`` in the phase-ends dir (indexed
    by LEGACY_INDEX.md).
    """
    named = set()
    for row in mapping:
        if row.src:
            named.add(row.src)
        if row.dst:
            named.add(row.dst)
    result = []
    ped = getattr(env, "phase_ends_dir", "phase-ends")
    scan_dirs = ["", "docs", "phase-ends"]
    if ped != "phase-ends" and ped not in scan_dirs:
        scan_dirs.append(ped)
        # D12: also scan <ped>/docs/ when ped is a custom folder
        ped_docs = "%s/docs" % ped
        if os.path.isdir(os.path.join(env.repo, ped_docs)):
            scan_dirs.append(ped_docs)
    for rel_dir in scan_dirs:
        full_dir = os.path.join(env.repo, rel_dir) if rel_dir else env.repo
        if not os.path.isdir(full_dir):
            continue
        for name in sorted(os.listdir(full_dir)):
            if not name.endswith(".md"):
                continue
            # kept by design: PROJECT_CONTEXT.md at root, PhaseEnd_*.md in ped
            if not rel_dir and name == "PROJECT_CONTEXT.md":
                continue
            if rel_dir in (ped, "phase-ends") and name.startswith("PhaseEnd_") and name.endswith(".md"):
                continue
            rel = ("%s/%s" % (rel_dir, name)) if rel_dir else name
            if rel not in named:
                result.append(rel)
    return result


def _kept_line(env):
    """Build the ``kept:`` line for the dry-run output.

    Names only classes actually present: ``PROJECT_CONTEXT.md`` at root and
    ``PhaseEnd_*.md`` files in the phase-ends directory.
    """
    parts = []
    ped = getattr(env, "phase_ends_dir", "phase-ends")
    if os.path.isfile(env.path("PROJECT_CONTEXT.md")):
        parts.append("PROJECT_CONTEXT.md (constitution)")
    ped_abs = env.path(ped)
    if os.path.isdir(ped_abs):
        pe_count = sum(1 for n in os.listdir(ped_abs)
                       if n.startswith("PhaseEnd_") and n.endswith(".md"))
        if pe_count:
            parts.append("%d PhaseEnd file(s) in %s/ (LEGACY_INDEX.md)" % (pe_count, ped))
    if parts:
        return "kept: %s" % ", ".join(parts)
    return None


def readme_rows(env):
    """Rows to retire a 2.0 phase-ends README and generate the PA3 one.

    Returns an empty list when the README is absent or already matches the
    template (idempotent on second run because ``_check_done`` covers the
    retire and the generate).
    """
    from .project import _template_text
    ped = getattr(env, "phase_ends_dir", "phase-ends")
    readme_rel = "%s/README.md" % ped
    readme_path = env.path(readme_rel)
    if not os.path.isfile(readme_path):
        return []
    template_text = _template_text(env, "phase-ends-README.md")
    if same_bytes(readme_path, template_text.encode("utf-8")):
        return []
    retire_dst = "docs/retired/phase-ends-README.pa2.md"
    rows = [
        Row("retire", readme_rel, retire_dst,
            "2.0 phase-ends README"),
        Row("generate", "", readme_rel,
            "PA3 phase-ends README",
            {"generator": "template", "template": "phase-ends-README.md"}),
    ]
    return rows


# --------------------------------------------------------------------------- step checks

def _check_done(env, row):
    """True when the step is already complete (idempotent skip)."""
    if row.kind == "move":
        return (os.path.isfile(env.path(row.dst))
                and not os.path.isfile(env.path(row.src)))
    if row.kind == "retire":
        # the destination alone proves it: a later step (T12.c2) may place a
        # new file of the same name as row.src (docs/project-architect.md),
        # so "src is gone" is not a valid re-check on the second run.
        return os.path.isfile(env.path(row.dst))
    if row.kind == "split":
        return not os.path.isfile(env.path(row.src))
    if row.kind == "remove_hook":
        path = env.path(".claude/settings.json")
        if not os.path.isfile(path):
            return True
        return row.note not in fsutil.read_text(path, "")
    if row.kind == "generate":
        gen = (row.params or {}).get("generator", "")
        if gen == "text" and (row.params or {}).get("mode", "write") != "write":
            # append/prepend: done once the text is in the file
            return (row.params or {}).get("text", "") in fsutil.read_text(env.path(row.dst), "")
        if gen == "gitignore_transcripts":
            # check if the marker line is already present
            text = fsutil.read_text(env.path(".gitignore"), "")
            return ".claude-state/transcripts/" in text
        dst_path = env.path(row.dst)
        if not os.path.isfile(dst_path):
            return False
        # D1: a bootstrap stub is not "done" — compare with stub text
        if gen == "legacy_index":
            from .project import LEGACY_INDEX
            content = fsutil.read_text(dst_path, "")
            if content.strip() == LEGACY_INDEX.strip():
                return False
        return True
    return False


# --------------------------------------------------------------------------- executors

def _ensure_dir(env, path):
    if not env.dry_run:
        os.makedirs(path, exist_ok=True)


def _do_move(env, row):
    """Execute a move: git mv for tracked, shutil.move for untracked."""
    from .project import _git, git_tracked
    src_path = env.path(row.src)
    if not os.path.isfile(src_path):
        return "SKIP", "%s does not exist" % row.src
    _ensure_dir(env, os.path.dirname(env.path(row.dst)))
    if env.git_top and git_tracked(env.repo, row.src):
        rc, out = _git(env.repo, "mv", row.src, row.dst)
        if rc != 0:
            return "FAIL", "git mv failed: %s" % out.strip()[:80]
    else:
        shutil.move(src_path, env.path(row.dst))
        note("(untracked: moved with shutil.move)")
    env.man.retired.append((row.src, row.dst))
    return "DONE", "%s -> %s" % (row.src, row.dst)


def _do_split(env, row):
    """Execute a split + retire the original."""
    from .project import _git, git_tracked
    src_path = env.path(row.src)
    if not os.path.isfile(src_path):
        return "SKIP", "%s does not exist" % row.src
    p = row.params or {}
    plan = split_by_heading.plan(
        src_path, p.get("level", "##"), row.dst,
        p.get("scheme", "rules"), resplit=p.get("resplit", False))
    # D2: register the index file in the manifest only when the split changed it
    idx_before = ""
    idx_existed = False  # tracked apart from content: an empty index still existed
    if not env.dry_run and plan.index_rel:
        idx_path = env.path(plan.index_rel)
        if os.path.isfile(idx_path):
            idx_existed = True
            idx_before = fsutil.read_text(idx_path, "")
    split_by_heading.execute(plan, env)
    if not env.dry_run and plan.index_rel:
        idx = plan.index_rel
        idx_path = env.path(idx)
        idx_after = fsutil.read_text(idx_path, "") if os.path.isfile(idx_path) else ""
        if idx_after != idx_before and (idx not in env.man.created
                and idx not in env.man.updated and idx not in env.man.forced):
            (env.man.updated if idx_existed else env.man.created).append(idx)
    # retire the original to docs/retired/
    retire_name = os.path.basename(row.src)
    retire_dst = "docs/retired/%s" % retire_name
    _ensure_dir(env, env.path("docs/retired"))
    if env.git_top and git_tracked(env.repo, row.src):
        rc, out = _git(env.repo, "mv", row.src, retire_dst)
        if rc != 0:
            return "FAIL", "git mv of original failed: %s" % out.strip()[:80]
    else:
        shutil.move(src_path, env.path(retire_dst))
    env.man.retired.append((row.src, retire_dst))
    return "DONE", "%s -> %d files in %s; original -> %s" % (
        row.src, len(plan.rows), row.dst, retire_dst)


def _do_retire(env, row):
    """git mv to docs/retired/ (or shutil.move if untracked)."""
    from .project import _git, git_tracked
    src_path = env.path(row.src)
    if not os.path.isfile(src_path):
        return "SKIP", "%s does not exist" % row.src
    _ensure_dir(env, os.path.dirname(env.path(row.dst)))
    if env.git_top and git_tracked(env.repo, row.src):
        rc, out = _git(env.repo, "mv", row.src, row.dst)
        if rc != 0:
            return "FAIL", "git mv failed: %s" % out.strip()[:80]
    else:
        shutil.move(src_path, env.path(row.dst))
        note("(untracked: moved with shutil.move)")
    env.man.retired.append((row.src, row.dst))
    return "DONE", "%s -> %s" % (row.src, row.dst)


def _do_remove_hook(env, row):
    """Remove a hook whose command matches row.note from settings.json."""
    from .project import write_changed
    rel = ".claude/settings.json"
    path = env.path(rel)
    if not os.path.isfile(path):
        return "SKIP", "no settings.json"
    try:
        current = json.loads(fsutil.read_text(path, "{}"))
    except (json.JSONDecodeError, ValueError):
        return "FAIL", "cannot parse settings.json"
    hook_cmd = row.note
    hooks = current.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            continue
        new_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                new_groups.append(group)
                continue
            kept = [h for h in group["hooks"]
                    if hook_cmd not in sm._cmd_of(h)]
            if kept:
                g = dict(group)
                g["hooks"] = kept
                new_groups.append(g)
            else:
                changed = True
        if new_groups:
            hooks[event] = new_groups
        elif event in hooks:
            del hooks[event]
            changed = True
    if not changed:
        return "SKIP", "hook not found"
    if not hooks:
        current.pop("hooks", None)
    write_changed(env, rel, sm.render(current), backup=True)
    return "DONE", "removed hook containing %s" % hook_cmd


def _do_generate(env, row):
    """Generate a file; dispatch on params['generator']."""
    from .project import place, _fill, _template_text, claude_md_text
    gen = (row.params or {}).get("generator", "")
    dst = row.dst

    if gen == "claude_md":
        text = claude_md_text(env)
        place(env, dst, text=text)
        return "DONE", dst

    if gen == "how_we_work":
        from . import howwework
        found = (row.params or {}).get("found", {})
        text = howwework.write(env, found)
        return "DONE", dst

    if gen == "legacy_index":
        phase_dir = env.path((row.params or {}).get("phase_dir") or "phase-ends")
        idx_rows = legacy_index.rows(phase_dir, rel_root=env.repo)
        text = legacy_index.text(idx_rows)
        place(env, dst, text=text)
        return "DONE", "%s (%d entries)" % (dst, len(idx_rows))

    if gen == "generation_plan":
        p = row.params or {}
        text = _generation_plan_text(env, p.get("gen_number", 1),
                                     p.get("gen_name", ""))
        place(env, dst, text=text)
        return "DONE", dst

    if gen == "interphase_phaseend":
        p = row.params or {}
        text = _interphase_text(env, row.params)
        place(env, dst, text=text)
        return "DONE", dst

    if gen == "gitignore_transcripts":
        _gitignore_transcripts(env)
        return "DONE", dst

    if gen == "template":
        tmpl_name = (row.params or {}).get("template", "")
        text = _template_text(env, tmpl_name)
        place(env, dst, text=text)
        return "DONE", dst

    if gen == "text":
        # a layout module supplies the text: write a new file, or append / prepend once
        p = row.params or {}
        text, mode = p.get("text", ""), p.get("mode", "write")
        if mode == "write":
            place(env, dst, text=text)
            return "DONE", dst
        path = env.path(dst)
        current = fsutil.read_text(path, "")
        if text in current:
            return "DONE", "%s (already present)" % dst
        new = (current + text) if mode == "append" else (text + current)
        if not env.dry_run:
            existed = os.path.isfile(path)
            _ensure_dir(env, os.path.dirname(path))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
            (env.man.updated if existed else env.man.created).append(dst)
        return "DONE", "%s (%s)" % (dst, mode)

    return "FAIL", "unknown generator %r" % gen


# --------------------------------------------------------------------------- generators

def _generation_plan_text(env, gen_number, gen_name):
    """GENERATION_PLAN.md from the template, with legacy index rows as closed phases."""
    from .project import _template_text, _fill
    gen_str = str(gen_number)

    # Read the legacy index (already generated by a prior row) so the plan
    # lists the pre-PA3 phases as closed and avoids template placeholders.
    ped = getattr(env, "phase_ends_dir", "phase-ends")
    idx_path = env.path(ped + "/LEGACY_INDEX.md")
    entries = []
    if os.path.isfile(idx_path):
        for raw in fsutil.read_text(idx_path, "").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 5:
                entries.append(parts)

    if not entries:
        # No legacy entries: use the bare template (fresh-install path)
        tmpl = _template_text(env, "GENERATION_PLAN.template.md")
        return _fill(tmpl, {"GENERATION": gen_str, "GENERATION_NAME": gen_name or gen_str})

    # D1: build a filled generation plan from the legacy index entries
    highest = 0
    for parts in entries:
        try:
            highest = max(highest, int(float(parts[0])))
        except (ValueError, IndexError):
            pass
    interphase = "%d.5" % highest
    interphase_pe = "%s/PhaseEnd_Phase%s.md" % (ped, interphase)

    out = []
    out.append("# Generation %s — %s" % (gen_str, gen_name or gen_str))
    out.append("")
    out.append("Goal: legacy project layout, migrated to PA3.")
    out.append("Done-criteria: PA3 migration commit present; LEGACY_INDEX.md has entries.")
    out.append("")
    out.append("## Phases")
    out.append("")
    out.append("<!-- Line grammar (parsed by launch.py, genend_index.py, plan_edit.py gen-close):")
    out.append("     - <G>.<n> <name> | milestone: … | scope: … "
               "| depends: — | status: open|closed "
               "| phase-end: phase-ends/PhaseEnd_Phase<N>.md")
    out.append("     Order = build order. `depends:` names phase ids or "
               "`—`. Only plan_edit.py gen-close|gen-open flips `status:`. -->")
    out.append("")
    for parts in entries:
        label, title, milestone, _date, pe_path = parts[:5]
        ms = milestone if milestone != "-" else "—"
        out.append("- %s %s | milestone: %s | scope: — | depends: — "
                   "| status: closed | phase-end: %s"
                   % (label, title[:60], ms[:60], pe_path))
    out.append("- %s PA3 migration | milestone: migration complete "
               "| scope: — | depends: — | status: closed "
               "| phase-end: %s" % (interphase, interphase_pe))
    out.append("")
    out.append("## Ordering rationale")
    out.append("")
    out.append("- Legacy phases in their original order; the migration phase closes the generation.")
    out.append("")
    out.append("## Standing constraints")
    out.append("")
    out.append("- Stdlib only.")
    out.append("")
    out.append("## Changes")
    out.append("")
    out.append("<!-- - <date> planner|developer: <what> — <why> -->")
    out.append("")
    return "\n".join(out)


def _interphase_text(env, params):
    """The interphase PhaseEnd from the template."""
    p = params or {}
    label = p.get("label", "PA3 migration")
    phase_n = p.get("phase_n", "")
    mapping_table = p.get("mapping_table", "")
    counts = p.get("counts", "")

    lines = []
    lines.append("# PhaseEnd -- Phase %s: %s" % (phase_n, label))
    lines.append("")
    lines.append("Closed: %s" % time.strftime("%Y-%m-%d"))
    lines.append("")
    lines.append("## Milestone")
    lines.append("Project Architect 3.0 migration complete.")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    if mapping_table:
        lines.append(mapping_table)
    lines.append("")
    lines.append("## Plain-English Recap")
    lines.append("")
    if counts:
        lines.append(counts)
    else:
        lines.append("Automated migration from PA2 to PA3.")
    lines.append("")
    return "\n".join(lines)


def _gitignore_transcripts(env):
    """Add .claude-state/transcripts/ to .gitignore when that directory exists."""
    from .project import write_changed
    transcripts = env.path(".claude-state/transcripts")
    if not os.path.isdir(transcripts):
        return
    gi_path = env.path(".gitignore")
    current = fsutil.read_text(gi_path, "")
    marker = ".claude-state/transcripts/"
    if marker in current:
        env.man.skipped.append(".gitignore")
        return
    sep = "" if current.endswith("\n") else "\n"
    write_changed(env, ".gitignore", current + sep + marker + "\n", backup=False)


# --------------------------------------------------------------------------- run

def _step_label(row):
    """Step id for the step() printer."""
    if row.kind in ("move", "retire"):
        return "P4"
    if row.kind == "split":
        return "P5"
    if row.kind == "remove_hook":
        return "P7"
    if row.kind == "generate":
        p = row.params or {}
        gen = p.get("generator", "")
        if gen == "how_we_work":
            return "P8"
        if gen in ("claude_md", "gitignore_transcripts"):
            return "P7"
        return "P9"
    return "P4"


_EXECUTORS = {
    "move": _do_move,
    "split": _do_split,
    "retire": _do_retire,
    "remove_hook": _do_remove_hook,
    "generate": _do_generate,
}


def run(env, layout_mod):
    """Execute the full migration from P2 through P10.

    Returns a process exit code (0 = success).
    """
    from .project import (bootstrap, copy_files, commit, close, place,
                          merge_project_settings, Manifest, _ensure_dir,
                          _import_ledger_slices, COMMIT_MSG)

    try:
        mapping = layout_mod.mapping(env)
    except (OSError, ValueError) as exc:
        step("P4", "mapping", "FAIL", "%s: %s" % (type(exc).__name__, exc))
        return 1

    # -- dry run: print the table and quit --------------------------------
    if env.dry_run:
        for line in _dry_lines(mapping, env):
            note(line)
        kept = _kept_line(env)
        if kept:
            note(kept)
        unmapped = _unmapped_md(env, mapping)
        if unmapped:
            note("unmapped:")
            for rel in unmapped:
                note("  %s" % rel)
        return 0

    # -- state / resume ----------------------------------------------------
    state = _load_state(env) or {
        "layout": env.layout,
        "last_step": -1,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_state(env, state)
    last_done = state.get("last_step", -1)

    # -- P2 bootstrap + P3 files (idempotent) ------------------------------
    # D1: collect generate destinations so bootstrap skips pre-placing stubs
    generate_dsts = set()
    for row in mapping:
        if row.kind == "generate" and row.dst:
            generate_dsts.add(row.dst)
    try:
        bootstrap(env, skip=generate_dsts)
        copy_files(env)
    except (OSError, ValueError) as exc:
        step("P3", "files", "FAIL", "%s: %s" % (type(exc).__name__, exc))
        return 1

    # -- mapping rows (P4..P9) ---------------------------------------------
    results = {}
    group = {"n": 0, "done": 0}         # a run of index-line rows for one dest

    def emit(i, row, sid, status, reason):
        """step(), but a run of same-dest index-line rows prints one line."""
        key = _line_key(row)
        if key is None or status == "FAIL":
            step(sid, row.kind, status, reason)
            return
        group["n"] += 1
        group["done"] += status == "DONE"
        if i + 1 < len(mapping) and _line_key(mapping[i + 1]) == key:
            return
        if group["n"] > 1:
            status = "DONE" if group["done"] else "SKIP"
            reason = "%s (%s, %d lines)" % (row.dst, key[1], group["n"])
        step(sid, row.kind, status, reason)
        group["n"] = group["done"] = 0

    for i, row in enumerate(mapping):
        sid = _step_label(row)
        if i <= last_done:
            emit(i, row, sid, "SKIP", "resume: already completed")
            continue
        if _check_done(env, row):
            emit(i, row, sid, "SKIP", "%s (already done)" % (row.note or row.dst))
            state["last_step"] = i
            _save_state(env, state)
            continue
        executor = _EXECUTORS.get(row.kind)
        if executor is None:
            step(sid, row.kind, "FAIL", "unknown kind %r" % row.kind)
            return 1
        try:
            status, reason = executor(env, row)
        except (OSError, ValueError) as exc:
            step(sid, row.kind, "FAIL", "%s: %s" % (type(exc).__name__, exc))
            note("partial migration: re-run to resume after step %d" % i)
            return 1
        emit(i, row, sid, status, reason)
        if status == "FAIL":
            return 1
        state["last_step"] = i
        _save_state(env, state)
        results["%s_%d" % (sid, i)] = status

    # -- P9: the methodology document (mapping rows retired the 2.0 copy) --
    master = os.path.join(env.pkg, "project-architect-3.0.md")
    if os.path.isfile(master):
        from . import managed

        place(env, managed.DOC, src=master)
        managed.add(env, managed.DOC)       # 3.11 T25: recorded in this run, as a refresh records it

    # -- P6 memory link ----------------------------------------------------
    try:
        mem_status = memory_sort.link(env)
    except (OSError, ValueError) as exc:
        step("P6", "memory link", "FAIL", str(exc))
        return 1

    # -- P9: ledger slices --------------------------------------------------
    _import_ledger_slices(env)

    # -- P7 settings merge + gitignore ------------------------------------
    try:
        merge_project_settings(env)
        _gitignore_transcripts(env)
    except (OSError, ValueError, sm.MergeError) as exc:
        step("P7", "settings", "FAIL", str(exc))
        return 1

    # -- P10 close ---------------------------------------------------------
    # Use migration commit message; find phase_n from the interphase row
    p = ""
    for row in mapping:
        pn = (row.params or {}).get("phase_n")
        if pn:
            p = pn
    old_msg = COMMIT_MSG
    import pa.install.project as _pj
    _pj.COMMIT_MSG = "chore: Project Architect 3.0 migration (Phase %s)" % p
    try:
        result = close(env)
    finally:
        _pj.COMMIT_MSG = old_msg

    if result == "FAIL":
        return 1
    return 0
