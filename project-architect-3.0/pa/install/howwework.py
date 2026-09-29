"""P8: collect HOW_WE_WORK.md placeholder candidates from the source tree.

Public:
    inputs(env, sources) -> {PLACEHOLDER: [(pointer, text), ...]}
    write(env, found, interview=None, constitution=None)  -> text

``inputs`` searches ops-like files for ``## Version pins|Environment|Build*|Tool*``
and CLAUDE.md for ``## Environment|Project-Specific Constraints|Constraints``.
``write`` fills the template with candidates from the migration sources, then
from ``environment()`` (OS, shells, interpreter, build/test guesses), interview
answers (name, experience, domain, autonomy, notify, rhythm) and the
constitution (``PROJECT_CONTEXT.md``: tagline from the first heading, path lines
from a ``## Quick Reference Card`` section).  Migration inputs keep precedence;
placeholders left untouched when nothing fills them; never appends.

Stdlib only; nothing touches the filesystem at import time.
"""

import os
import re

from . import note
from .project import place, _fill, _template_text
from .. import fsutil


# section headings to search in ops-like files
_OPS_RE = re.compile(
    r"^##\s+(Version\s+pins|Environment|Build.*|Tool.*)\s*$", re.I)

# section headings to search in CLAUDE.md
_CLAUDE_RE = re.compile(
    r"^##\s+(Environment|Project-Specific\s+Constraints|Constraints)\s*$", re.I)

# placeholder reference for the heading -> placeholder map
_HEADING_MAP = {
    "version pins": ["PINS_SUMMARY"],
    "environment": ["OS_AND_SHELLS"],
    "build": ["BUILD_COMMAND"],
    "build / run / test": ["BUILD_COMMAND", "TEST_COMMAND", "RUN_COMMAND"],
    "tool": ["PROJECT_TOOL", "PROJECT_TOOL_COMMAND", "PROJECT_TOOL_PURPOSE"],
    "constraints": ["PROJECT_CONVENTION"],
    "project-specific constraints": ["PROJECT_CONVENTION"],
}


def _match_placeholders(heading):
    """Return placeholder names a heading can fill."""
    h = heading.lower().strip()
    for prefix, phs in _HEADING_MAP.items():
        if h == prefix or h.startswith(prefix):
            return phs
    return []


def _extract_sections(path, section_re):
    """Yield (lineno, heading, body_text) for matching ## sections in *path*."""
    if not os.path.isfile(path):
        return
    heading = None
    lineno = 0
    body = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            if line.startswith("## "):
                if heading is not None:
                    yield lineno, heading, "\n".join(body).strip()
                m = section_re.match(line)
                if m:
                    heading = m.group(1)
                    lineno = i
                    body = []
                else:
                    heading = None
                    body = []
            elif heading is not None:
                body.append(line)
    if heading is not None:
        yield lineno, heading, "\n".join(body).strip()


def inputs(env, sources):
    """Collect candidate fills for HOW_WE_WORK.template.md placeholders.

    *sources* is a list of ``(abs_path, compiled_re)`` pairs.
    Returns ``{PLACEHOLDER: [(pointer, text), ...]}``.
    """
    result = {}
    for abs_path, section_re in sources:
        rel = os.path.relpath(abs_path, env.repo).replace("\\", "/")
        for lineno, heading, text in _extract_sections(abs_path, section_re):
            if not text:
                continue
            pointer = "%s:%d" % (rel, lineno)
            for ph in _match_placeholders(heading):
                result.setdefault(ph, []).append((pointer, text))
    return result



# the skeleton's own heading suffix (templates/PROJECT_CONTEXT.skeleton.md): a title, never a tagline
_SKELETON_TITLE = "project context & roadmap"


def _after_sep(text):
    """The text after the first dash or colon separator, or ``""``."""
    for sep in (" — ", " - ", ": "):
        idx = text.find(sep)
        if idx >= 0:
            return text[idx + len(sep):].strip()
    return ""


def _tagline_from_constitution(text):
    """Extract the tagline from a PROJECT_CONTEXT.md.

    The skeleton's Quick Reference Card line ``- **Project:** <name> — <line>`` wins
    (T10, 3.11); else the text after the first dash or colon in the first heading,
    unless that is the skeleton's own title ("Project Context & Roadmap").
    Returns ``""`` when nothing is found (an unfilled ``{{…}}`` counts as nothing).
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- **Project:**"):
            found = _after_sep(line[len("- **Project:**"):].strip())
            if found and "{{" not in found:
                return found
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            found = _after_sep(line)
            if found.lower() != _SKELETON_TITLE and "{{" not in found:
                return found
            break
    return ""


def _paths_from_constitution(text):
    """Extract path lines from a ``## Quick Reference Card`` section.

    Only bullets that start with a path in backticks (``- `src/` — code``); the skeleton's
    ``- **Stack:** … `json` …`` bullets are no paths (T10, 3.11: was any bullet with a backtick).
    Returns a list of ``"- path: description"`` strings or ``[]``.
    """
    in_section = False
    paths = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = "quick reference card" in stripped.lower()
            continue
        if in_section and stripped.startswith("- `"):
            paths.append(stripped)
    return paths


def write(env, found, interview=None, constitution=None):
    """Fill HOW_WE_WORK.template.md with candidates and write the inputs record.

    *interview* is a dict with keys ``name, experience, domain, autonomy,
    notify, rhythm`` (all optional, ``None`` values are skipped).
    *constitution* is the text of ``PROJECT_CONTEXT.md`` (or ``None``).

    Returns the filled text (also written to ``HOW_WE_WORK.md`` via ``place``).
    """
    tmpl = _template_text(env, "HOW_WE_WORK.template.md")

    # env-level fills that project.py normally handles
    env_fills = {
        "PROJECT_NAME": env.name,
        "PROJECT_TAGLINE": getattr(env, "tagline", "") or "",
        "PY": env.py_exe,
        "RHYTHM": "autonomous",
    }
    import sys
    from . import settings_merge as sm
    if os.path.normcase(env.py_exe) == os.path.normcase(sm.abs_fwd(sys.executable or "")):
        env_fills["PY_VERSION"] = "%d.%d.%d" % sys.version_info[:3]

    # environment() fills for ## Environment and ## Build / run / test
    from .detect import environment as _environment
    rt = _environment(getattr(env, "repo", None))
    os_shells = "%s / %s" % (rt["os"], ", ".join(rt["shells"])) if rt["shells"] else rt["os"]
    env_fills.setdefault("OS_AND_SHELLS", os_shells)
    env_fills.setdefault("PY_VERSION", rt["py_version"])
    if rt["build_guess"]:
        env_fills.setdefault("BUILD_COMMAND", rt["build_guess"])
    if rt["test_guess"]:
        env_fills.setdefault("TEST_COMMAND", rt["test_guess"])
    if rt["run_guess"]:
        env_fills.setdefault("RUN_COMMAND", rt["run_guess"])

    # constitution fills for ## Project
    if constitution:
        tagline = _tagline_from_constitution(constitution)
        if tagline:
            env_fills.setdefault("PROJECT_TAGLINE", tagline)
        paths = _paths_from_constitution(constitution)
        if paths:
            _path_phs = ["ORACLES", "DATA_PATHS", "GENERATED_PATHS", "HAND_EDITED_PATHS"]
            for i, p in enumerate(paths[:len(_path_phs)]):
                env_fills.setdefault(_path_phs[i], p[2:] if p.startswith("- ") else p)

    # interview fills for ## Developer and ## Rhythm
    if interview:
        _iv_map = {
            "name": "DEVELOPER_NAME",
            "experience": "DEVELOPER_EXPERIENCE",
            "domain": "DEVELOPER_DOMAIN",
            "autonomy": "AUTONOMY_POSTURE",
            "notify": "NOTIFY_CHANNEL",
            "rhythm": "RHYTHM",
        }
        for key, ph in _iv_map.items():
            val = interview.get(key)
            if val:
                env_fills.setdefault(ph, val)

    text = _fill(tmpl, env_fills)

    # D6: omit the dash and tagline when it is empty
    tagline = getattr(env, "tagline", "") or env_fills.get("PROJECT_TAGLINE", "")
    if not tagline:
        text = text.replace(env.name + " — .", env.name + ".", 1)

    # replace remaining placeholders with found candidates or TODO
    ph_re = re.compile(r"\{\{(\w+)\}\}")
    all_phs = set(ph_re.findall(text))
    for ph in all_phs:
        if ph in found and found[ph]:
            # use the first line of the first candidate
            first = found[ph][0][1].split("\n")[0][:120]
            text = text.replace("{{%s}}" % ph, first)
        else:
            text = text.replace("{{%s}}" % ph,
                                "<!-- TODO %s: no source found -->" % ph)

    place(env, "HOW_WE_WORK.md", text=text)

    # write the inputs record
    rec = ["# HOW_WE_WORK.md source candidates", "",
           "Written by the migration engine.  Each placeholder lists",
           "every candidate found, with its source pointer.", ""]
    for ph in sorted(found):
        rec.append("## %s" % ph)
        rec.append("")
        for pointer, body in found[ph]:
            rec.append("- `%s`:" % pointer)
            for line in body.split("\n")[:5]:
                rec.append("  %s" % line)
            rec.append("")
    place(env, "docs/retired/HOW_WE_WORK.inputs.md", text="\n".join(rec) + "\n")
    return text
