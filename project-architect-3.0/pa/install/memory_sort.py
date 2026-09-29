"""P6: memory lives in the repo, linked from the Claude projects dir.

Public:
    link(env) -> str            create ``.claude-state/memory/``, write the index,
                                junction/symlink from ``~/.claude/projects/<slug>/memory``;
                                returns ``"DONE"``, ``"SKIP"`` or ``"planned"``
    table(dir) -> list[dict]    inventory rows for every ``.md`` file in *dir*

The link is a junction on Windows (``cmd /c mklink /J``, no privilege) or a
symlink elsewhere.  On the first link a pre-existing real directory's files
are moved into the repo dir -- never deleted or overwritten.  A name collision
keeps both: the incoming file is renamed ``<stem>.<machine>.md`` and both
MEMORY.md index lines are merged.
"""

import os
import shutil
import stat
import sys

from .. import paths
from . import note, step


# --------------------------------------------------------------------------- constants

MEMORY_REL = os.path.join(".claude-state", "memory")

MEMORY_INDEX = """\
Memories are curated at the start of every generation and carried forward.
Agents in the governance loop do not write memories; standing facts go to
`HOW_WE_WORK.md`, rules to `rules/`, techniques to the cookbook.
"""

_GUESS_MAP = {
    "rule": ("rule", "constraint", "discipline", "protocol", "convention"),
    "standing fact": ("profile", "who-is", "environment", "tool", "path",
                      "preference", "setup", "config"),
    "phase state": ("phase", "milestone", "plan", "status", "progress",
                    "session-start", "session"),
    "obsolete": ("retired", "legacy", "deprecated", "old-", "archive"),
}


# --------------------------------------------------------------------------- junction / symlink

def _is_junction_or_symlink(path):
    """True when *path* is a symlink or a Windows junction (reparse point)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    if sys.platform.startswith("win") or os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _make_link(link_path, target):
    """Create a junction (Windows) or symlink (elsewhere) at *link_path* -> *target*."""
    os.makedirs(os.path.dirname(link_path), exist_ok=True)
    if sys.platform.startswith("win") or os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", link_path, target],
                       capture_output=True, check=True)
    else:
        os.symlink(target, link_path, target_is_directory=True)


def _move_files(src_dir, dst_dir, machine):
    """Move every file from *src_dir* into *dst_dir*, renaming on collision."""
    merged_index_lines = []
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            # collision: rename the incoming file
            stem, ext = os.path.splitext(name)
            new_name = "%s.%s%s" % (stem, machine, ext)
            dst = os.path.join(dst_dir, new_name)
            if name.upper() == "MEMORY.MD":
                # merge index lines from the incoming MEMORY.md
                try:
                    with open(src, "r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.rstrip("\r\n")
                            if line.startswith("- "):
                                merged_index_lines.append(line)
                except OSError:
                    pass
        shutil.move(src, dst)          # copies across drives; os.replace cannot
    # append merged index lines to the existing MEMORY.md
    if merged_index_lines:
        idx = os.path.join(dst_dir, "MEMORY.md")
        try:
            with open(idx, "r", encoding="utf-8") as fh:
                existing = fh.read()
        except OSError:
            existing = ""
        with open(idx, "a", encoding="utf-8", newline="\n") as fh:
            for line in merged_index_lines:
                if line not in existing:
                    fh.write(line + "\n")
    return merged_index_lines


# --------------------------------------------------------------------------- table / inventory

def _is_stub(path):
    """True for an index an installer wrote (this stub or the 3.1 'retired' one)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return False
    return text == MEMORY_INDEX.strip() or text.startswith("Memory is retired under PA3")


def _guess(name, first_line):
    """Guess a memory file's category from its name and first line."""
    key = (name + " " + first_line).lower()
    for label, tokens in _GUESS_MAP.items():
        for tok in tokens:
            if tok in key:
                return label
    return "keep"


def _first_line(path):
    """The first non-blank line of a file (stripped), or empty string."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return line
    except OSError:
        pass
    return ""


def _line_count(path):
    """Number of lines in *path*."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def table(directory):
    """Inventory rows for every ``.md`` file in *directory*.

    Returns ``[{"file": name, "type": ext, "lines": n,
                "first_line": str, "guess": str}, ...]``.
    """
    rows = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return rows
    for name in names:
        if not name.lower().endswith(".md"):
            continue
        if name == "INVENTORY.md":
            continue  # generated file, not a memory
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        fl = _first_line(full)
        rows.append({
            "file": name,
            "type": "md",
            "lines": _line_count(full),
            "first_line": fl,
            "guess": _guess(name, fl),
        })
    return rows


def _inventory_text(rows):
    """Return the text of ``INVENTORY.md`` from *rows*."""
    lines = ["# Memory inventory", ""]
    lines.append("| file | type | lines | first line | guess |")
    lines.append("|------|------|------:|------------|-------|")
    for r in rows:
        fl = r["first_line"][:60].replace("|", "/")
        lines.append("| %s | %s | %d | %s | %s |"
                     % (r["file"], r["type"], r["lines"], fl, r["guess"]))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- link (P6)

def _check_auto_memory_dir(env):
    """Return the autoMemoryDirectory from settings.local.json, or None."""
    sl = env.path(os.path.join(".claude", "settings.local.json"))
    if not os.path.isfile(sl):
        return None
    try:
        with open(sl, "r", encoding="utf-8") as fh:
            doc = __import__("json").loads(fh.read())
        return doc.get("autoMemoryDirectory")
    except (OSError, ValueError):
        return None


def link(env):
    """P6: ensure ``.claude-state/memory/`` and the projects-dir junction/symlink.

    Returns ``"DONE"``, ``"SKIP"`` or ``"planned"`` (dry run).
    """
    mark = env.man.mark()
    repo_mem = env.path(MEMORY_REL)
    slug = paths.project_slug(env.repo_fwd)
    proj_mem = os.path.join(paths.projects_dir(), slug, "memory")
    machine = paths.machine_tag()

    # -- report an autoMemoryDirectory override
    auto = _check_auto_memory_dir(env)
    if auto:
        note("settings.local.json autoMemoryDirectory: %s (not edited)" % auto)

    # -- ensure .claude-state/memory/; the index is written after the move (below): an index
    #    arriving from the projects dir is the index, and a stub written by an earlier install
    #    gives way to it; the stub only fills a directory that ends up without one
    idx_path = os.path.join(repo_mem, "MEMORY.md")
    if not os.path.isdir(repo_mem):
        if env.dry_run:
            note("would create %s" % MEMORY_REL)
        else:
            os.makedirs(repo_mem, exist_ok=True)
    arriving_idx = os.path.join(proj_mem, "MEMORY.md")
    if (not env.dry_run and os.path.isfile(idx_path) and _is_stub(idx_path)
            and os.path.isdir(proj_mem) and not _is_junction_or_symlink(proj_mem)
            and os.path.isfile(arriving_idx)):
        os.remove(idx_path)                 # an installer stub, never a memory

    # -- the link
    already_linked = _is_junction_or_symlink(proj_mem)
    link_status = "SKIP"

    if already_linked:
        note("link exists: %s" % proj_mem)
    elif env.dry_run:
        if os.path.isdir(proj_mem):
            note("would move %d file(s) from %s into the repo" %
                 (len([f for f in os.listdir(proj_mem) if os.path.isfile(os.path.join(proj_mem, f))]),
                  proj_mem))
        note("would link  %s -> %s" % (proj_mem, repo_mem))
        link_status = "planned"
    else:
        # move files from a pre-existing real directory
        leftover = False
        if os.path.isdir(proj_mem) and not _is_junction_or_symlink(proj_mem):
            _move_files(proj_mem, repo_mem, machine)
            try:
                os.rmdir(proj_mem)      # never rmtree: anything not moved stays where it is
            except OSError:
                leftover = True
        if leftover:
            note("not linked: %s still holds entries after the move (look, then re-run)" % proj_mem)
            link_status = "SKIP"
        else:
            _make_link(proj_mem, repo_mem)
            link_status = "DONE"
    if not env.dry_run and not os.path.isfile(idx_path):
        with open(idx_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(MEMORY_INDEX)
        env.man.created.append(MEMORY_REL + "/MEMORY.md")
    elif env.dry_run and not os.path.isfile(idx_path) and not os.path.isfile(arriving_idx):
        note("would write  MEMORY.md index")

    # -- INVENTORY.md (regenerated on every install)
    if not env.dry_run and os.path.isdir(repo_mem):
        rows = table(repo_mem)
        inv_text = _inventory_text(rows)
        inv_path = os.path.join(repo_mem, "INVENTORY.md")
        inv_rel = MEMORY_REL + "/INVENTORY.md"
        new_bytes = inv_text.encode("utf-8")
        from . import same_bytes
        if same_bytes(inv_path, new_bytes):
            env.man.skipped.append(inv_rel)
        else:
            with open(inv_path, "wb") as fh:
                fh.write(new_bytes)
            env.man.created.append(inv_rel)
    elif env.dry_run:
        note("would write  INVENTORY.md")

    done = env.man.since(mark)
    status = link_status if link_status != "SKIP" else ("SKIP" if not env.man.changed(done) else "DONE")
    step("P6", "memory link", status,
         ("inventory updated" if already_linked else "linked %s" % MEMORY_REL) if status == "DONE"
         else ("already linked" if already_linked
               else ("dry-run" if env.dry_run else "memory dir")))
    return status
