"""The ``settings/*.snippet.json`` merge engine -- pure functions, no file I/O.

Public:
    PA_MARKERS, RETIRED_MARKERS, PROTECTED_KEYS, SPECIAL_KEYS
    snippet_path(name="user"), load_snippet(path=None, name="user")
    fill(obj, py_exe, pa3)              {{PY_EXE}} / {{PA3}} / {{PY}} substitution
    abs_fwd(path)                       absolute path with forward slashes
    is_pa_command(cmd), is_retired_command(cmd), pa_action(cmd)
    retired_script_token(cmd)
    merge(current, snippet, hooks=True, statusline=True)  -> (merged, report)
    render(settings)                    the exact text the installer writes

The rules implemented here are the ``_merge_rules`` key of
``settings/user.snippet.json``, verbatim:

* add-if-absent for every key (recursively for plain dicts), EXCEPT
* ``todoFeatureEnabled`` -- forced to ``false`` even when present (announced),
* ``cleanupPeriodDays`` -- raise only, never lower an existing larger value,
* ``hooks`` -- PA entries appended per event, PA entries already present
  replaced **in place** (so a second ``--root`` is byte-identical), entries whose
  command contains ``ctx_guard.sh``/``ctx90.sh`` removed (D46),
* ``statusLine`` -- replaced (the caller keeps the old command in
  ``statusline.sh.pa2``) unless ``statusline=False``,
* ``permissions.*`` -- list values unioned, order preserved, no duplicates,
* ``env`` -- add-if-absent per variable, except ``CLAUDE_CODE_ENABLE_TODO_TOOLS``,
  removed when truthy (it re-enables the Task tools; announced).

``model`` and ``modelSettings`` are never read and never written by the user-level
merge; :func:`merge` asserts that at the end and raises ``MergeError`` rather than
emit a document that touched them.  With ``scope="project"`` (``pa_install.py
--project``, 3.1 T9) they are add-if-absent instead: a product repo gets the
pa-session's pinned model and effort once, and a hand-edited value is kept.
"""

import json
import os
import re

PA_MARKERS = ("pa_hook.py", "pa_statusline.py")
RETIRED_MARKERS = ("ctx_guard.sh", "ctx90.sh")
PROTECTED_KEYS = ("model", "modelSettings")
SPECIAL_KEYS = ("todoFeatureEnabled", "cleanupPeriodDays", "hooks", "statusLine",
                "permissions", "env")

_ACTION_RE = re.compile(r"pa_hook\.py[\"']?\s+([A-Za-z_][A-Za-z0-9_]*)")
_RETIRED_RE = re.compile(r"(\S*(?:ctx_guard\.sh|ctx90\.sh))")
_WINDRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class MergeError(Exception):
    """Raised when the merge would produce something the rules forbid."""


# --------------------------------------------------------------------------- helpers

def _clone(obj):
    return json.loads(json.dumps(obj))


def abs_fwd(path):
    """Absolute path with forward slashes (``/usr/bin/python3`` survives on Windows)."""
    p = str(path).replace("\\", "/")
    if not (p.startswith("/") or p.startswith("//") or _WINDRIVE_RE.match(p)):
        p = os.path.abspath(p).replace("\\", "/")
    if len(p) > 1 and p.endswith("/") and not p.endswith(":/"):
        p = p.rstrip("/")
    return p


def fill(obj, py_exe, pa3):
    """Replace ``{{PY_EXE}}``/``{{PY}}``/``{{PA3}}`` everywhere in a JSON document."""
    if isinstance(obj, str):
        return (obj.replace("{{PY_EXE}}", py_exe)
                   .replace("{{PY}}", py_exe)
                   .replace("{{PA3}}", pa3))
    if isinstance(obj, list):
        return [fill(x, py_exe, pa3) for x in obj]
    if isinstance(obj, dict):
        return dict((k, fill(v, py_exe, pa3)) for k, v in obj.items())
    return obj


def snippet_path(name="user"):
    """Locate ``settings/<name>.snippet.json`` (package tree or installed copy)."""
    here = os.path.dirname(os.path.abspath(__file__))            # <root>/pa/install
    pkg = os.path.dirname(os.path.dirname(here))                 # <root>
    fname = "%s.snippet.json" % name
    for cand in (os.path.join(pkg, "settings", fname),
                 os.path.join(os.path.dirname(pkg), "settings", fname)):
        if os.path.isfile(cand):
            return cand
    return os.path.join(pkg, "settings", fname)


def load_snippet(path=None, name="user"):
    """Read a snippet and drop its ``_comment`` / ``_merge_rules`` documentation keys."""
    p = path or snippet_path(name)
    with open(p, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return dict((k, v) for k, v in doc.items() if not k.startswith("_"))


def render(settings):
    """The exact text written to ``settings.json`` (2-space JSON, LF, trailing NL)."""
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- commands

def _cmd_of(entry):
    if isinstance(entry, dict):
        c = entry.get("command")
        if isinstance(c, str):
            return c
    return ""


def is_pa_command(cmd):
    """True for a command that invokes a PA3 entry script."""
    return any(m in (cmd or "") for m in PA_MARKERS)


def is_retired_command(cmd):
    """True for the PA2 context-guard hooks that ``--root`` retires (D46)."""
    return any(m in (cmd or "") for m in RETIRED_MARKERS)


def pa_action(cmd):
    """``…/pa_hook.py session_start`` -> ``session_start``; statusline -> ``statusline``."""
    cmd = cmd or ""
    m = _ACTION_RE.search(cmd)
    if m:
        return m.group(1)
    if "pa_statusline.py" in cmd:
        return "statusline"
    return None


def retired_script_token(cmd):
    """The path token of a retired hook command (``"$HOME"/.claude/ctx_guard.sh``)."""
    m = _RETIRED_RE.search(cmd or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------- key merge

def _add_if_absent(cur, snip, report, prefix):
    """Recursive add-if-absent; returns the merged value."""
    if not isinstance(cur, dict) or not isinstance(snip, dict):
        return cur
    out = _clone(cur)
    for k, v in snip.items():
        key = "%s.%s" % (prefix, k) if prefix else k
        if k not in out:
            out[k] = _clone(v)
            report["added"].append(key)
        elif isinstance(v, dict) and isinstance(out[k], dict):
            out[k] = _add_if_absent(out[k], v, report, key)
    return out


def _union_list(cur, snip, report, key):
    out = [x for x in cur]
    for item in snip:
        if item not in out:
            out.append(_clone(item))
            report["added"].append("%s[%s]" % (key, json.dumps(item)))
    return out


def _merge_permissions(cur, snip, report):
    if not isinstance(cur, dict):
        report["added"].append("permissions")
        return _clone(snip)
    out = _clone(cur)
    for k, v in snip.items():
        key = "permissions.%s" % k
        if isinstance(v, list):
            if isinstance(out.get(k), list):
                out[k] = _union_list(out[k], v, report, key)
            else:
                out[k] = _clone(v)
                report["added"].append(key)
        elif k not in out:
            out[k] = _clone(v)
            report["added"].append(key)
    return out


def _merge_env(cur, snip, report):
    if not isinstance(cur, dict):
        report["added"].append("env")
        return _clone(snip)
    out = _clone(cur)
    for k, v in snip.items():
        if k not in out:
            out[k] = _clone(v)
            report["added"].append("env.%s" % k)
    return out


# --------------------------------------------------------------------------- hooks

def _merge_hooks(cur, snip, report):
    out = _clone(cur) if isinstance(cur, dict) else {}

    # (1) retire the PA2 context guards -- everywhere, not only in snippet events.
    for event in list(out.keys()):
        groups = out.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept = []
            for entry in group["hooks"]:
                cmd = _cmd_of(entry)
                if is_retired_command(cmd):
                    report["hooks_removed"].append((event, cmd))
                    tok = retired_script_token(cmd)
                    if tok:
                        report["retired_scripts"].append(tok)
                else:
                    kept.append(entry)
            if kept:
                g = _clone(group)
                g["hooks"] = kept
                kept_groups.append(g)
        if kept_groups:
            out[event] = kept_groups
        else:
            out.pop(event, None)

    # (2) add or replace-in-place the PA entries, keeping every other entry.
    for event in snip:
        sgroups = snip[event]
        if not isinstance(sgroups, list):
            continue
        groups = out.setdefault(event, [])
        for sgroup in sgroups:
            sentries = sgroup.get("hooks") or []
            if not sentries:
                continue
            action = pa_action(_cmd_of(sentries[0]))
            placed = False
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    continue
                for i, entry in enumerate(group["hooks"]):
                    if action and pa_action(_cmd_of(entry)) == action:
                        want_matcher = sgroup.get("matcher")
                        if entry != sentries[0] or group.get("matcher") != want_matcher:
                            report["hooks_replaced"].append(
                                (event, action, _cmd_of(sentries[0])))
                        group["hooks"][i] = _clone(sentries[0])
                        if want_matcher is None:
                            group.pop("matcher", None)
                        else:
                            group["matcher"] = want_matcher
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                groups.append(_clone(sgroup))
                report["hooks_added"].append((event, _cmd_of(sentries[0])))
    return out


# --------------------------------------------------------------------------- merge

def new_report():
    return {
        "added": [],                # dotted keys added
        "changed": [],              # (key, old, new)
        "notes": [],                # announced overrides / skips
        "hooks_added": [],          # (event, command)
        "hooks_replaced": [],       # (event, action, command)
        "hooks_removed": [],        # (event, command)
        "retired_scripts": [],      # path tokens found in removed commands
        "statusline_old": None,     # the command that statusline.sh.pa2 must keep
    }


def _kept(old, new):
    """True when everything in ``old`` is still in ``new`` with the same value."""
    if isinstance(old, dict) and isinstance(new, dict):
        return all(k in new and _kept(v, new[k]) for k, v in old.items())
    return old == new


def merge(current, snippet, hooks=True, statusline=True, scope="user"):
    """Merge ``snippet`` into ``current`` per ``_merge_rules``.

    Returns ``(merged, report)``.  ``current`` is never mutated; ``model`` and
    ``modelSettings`` are carried through untouched (``scope="user"``) or added
    only where absent (``scope="project"``).
    """
    cur = _clone(current) if isinstance(current, dict) else {}
    snip = dict((k, v) for k, v in (snippet or {}).items() if not k.startswith("_"))
    report = new_report()
    out = _clone(cur)

    for key in snip:
        val = snip[key]

        if key in PROTECTED_KEYS:
            if scope != "project":
                raise MergeError("snippet must never carry %r" % key)
            if key not in out:
                out[key] = _clone(val)
                report["added"].append(key)
            elif isinstance(val, dict) and isinstance(out[key], dict):
                out[key] = _add_if_absent(out[key], val, report, key)
            continue

        if key == "todoFeatureEnabled":
            if out.get(key) is not False:
                report["changed"].append((key, out.get(key, "<absent>"), False))
                report["notes"].append(
                    "todoFeatureEnabled forced to false (announced override; removes the Task tools)")
            out[key] = False

        elif key == "cleanupPeriodDays":
            old = out.get(key)
            if isinstance(old, bool) or not isinstance(old, int):
                if key not in out:
                    report["added"].append(key)
                else:
                    report["changed"].append((key, old, val))
                out[key] = val
            elif old < val:
                report["changed"].append((key, old, val))
                out[key] = val
            # raise only: a larger existing value is kept silently

        elif key == "hooks":
            if not hooks:
                report["notes"].append("--hooks off: no hook entries written, "
                                       "existing hooks left as they are")
                continue
            out[key] = _merge_hooks(out.get(key), val, report)

        elif key == "statusLine":
            if not statusline:
                report["notes"].append("--no-statusline: statusLine left as it is")
                continue
            old = out.get(key)
            old_cmd = _cmd_of(old) if isinstance(old, dict) else None
            if old == val:
                pass
            elif old is None:
                out[key] = _clone(val)
                report["added"].append(key)
            else:
                if old_cmd and not is_pa_command(old_cmd):
                    report["statusline_old"] = old_cmd
                report["changed"].append((key, old_cmd or old, _cmd_of(val)))
                out[key] = _clone(val)

        elif key == "permissions":
            out[key] = _merge_permissions(out.get(key), val, report)

        elif key == "env":
            out[key] = _merge_env(out.get(key), val, report)

        elif key not in out:
            out[key] = _clone(val)
            report["added"].append(key)

        elif isinstance(val, dict) and isinstance(out[key], dict):
            out[key] = _add_if_absent(out[key], val, report, key)

    for key in PROTECTED_KEYS:
        if scope == "project":
            if key in cur and not _kept(cur[key], out.get(key)):
                raise MergeError("%s must never be changed" % key)
        elif cur.get(key, KeyError) != out.get(key, KeyError):
            raise MergeError("%s must never be touched" % key)

    # One variable ever leaves env: a truthy CLAUDE_CODE_ENABLE_TODO_TOOLS fights
    # todoFeatureEnabled:false, so it is removed and announced (the Vantage config
    # dir has it -- master plan §5.2; fix-6).  A falsy value is left alone.
    todo_env = (out.get("env") or {}).get("CLAUDE_CODE_ENABLE_TODO_TOOLS")
    if todo_env not in (None, "", "0", "false", "False", False):
        del out["env"]["CLAUDE_CODE_ENABLE_TODO_TOOLS"]
        report["changed"].append(
            ("env.CLAUDE_CODE_ENABLE_TODO_TOOLS", todo_env, "<removed>"))
        report["notes"].append(
            "env.CLAUDE_CODE_ENABLE_TODO_TOOLS removed: it re-enabled the Task tools "
            "that todoFeatureEnabled:false removes (master plan §5.2)")

    try:
        json.loads(render(out))
    except ValueError as exc:                                    # pragma: no cover
        raise MergeError("merged settings do not parse: %s" % exc)
    return out, report
