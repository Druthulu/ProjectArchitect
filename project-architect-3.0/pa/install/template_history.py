"""Past lines of the templates 3.2 rewrote (3.24 T7b): ``<pkg>/templates/HISTORY.json``.

``{template: [[old_lines, new_lines], ...]}``: for every version of ``templates/<template>`` in
the package's git history, each block it changed or dropped against the current file (difflib
``replace``/``delete``) and the current block that took its place (empty when dropped).
``curate.py claude-md --refresh`` and ``card --refresh`` replace a project block equal to an old
one (placeholders matching the project's fills) with the new one, and list the rest.  A block is
left out when one of its lines is still in the current template (a moved line) or carries fewer
than 10 literal characters (a bare placeholder, which would match any line).  Built the way
:mod:`seed_history` builds ``rules-seed/HISTORY.json``; the table ships with the templates.

Build (from the package's work tree, after one of these templates changes):
    cd project-architect-3.0 && python -m pa.install.template_history
"""

import difflib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = "templates/HISTORY.json"            # under the package root
FILES = ("CLAUDE.template.md", "HOW_WE_WORK.template.md")
_PH = re.compile(r"\{\{\w+\}\}")


def _lines(text):
    return text.replace("\r\n", "\n").rstrip("\n").split("\n")


def pairs(versions, current):
    """The ``[old, new]`` blocks each past version changed or dropped against ``current``."""
    cur = _lines(current)
    cur_set = {ln for ln in cur if ln.strip()}
    out = set()
    for text in versions:
        old = _lines(text)
        if old == cur:
            continue
        sm = difflib.SequenceMatcher(None, old, cur, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag not in ("replace", "delete"):
                continue
            a, b = old[i1:i2], cur[j1:j2] if tag == "replace" else []
            while a and not a[0].strip():              # trim blank edges; keep inner blanks
                a = a[1:]
            while a and not a[-1].strip():
                a = a[:-1]
            body = [ln for ln in a if ln.strip()]
            if not body or any(ln in cur_set for ln in body):
                continue
            if any(len(_PH.sub("", ln).strip()) < 10 for ln in body):
                continue
            out.add((tuple(a), tuple(b)))
    return [[list(a), list(b)] for a, b in sorted(out)]


def build(pkg):
    """``{template: pairs}`` from ``pkg``'s git history and its current templates."""
    top = subprocess.run(["git", "-C", pkg, "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=True).stdout.strip()
    prefix = os.path.relpath(pkg, top).replace(os.sep, "/")
    prefix = "" if prefix == "." else prefix + "/"
    out = {}
    for name in FILES:
        path = "%stemplates/%s" % (prefix, name)
        commits = subprocess.run(["git", "-C", top, "log", "--format=%H", "--", path],
                                 capture_output=True, text=True, check=True).stdout.split()
        versions = []
        for c in commits:
            got = subprocess.run(["git", "-C", top, "show", "%s:%s" % (c, path)], capture_output=True)
            if got.returncode == 0:
                versions.append(got.stdout.decode("utf-8"))
        with open(os.path.join(pkg, "templates", name), "r", encoding="utf-8") as fh:
            out[name] = pairs(versions, fh.read())
    return out


def main():
    pkg = os.path.dirname(os.path.dirname(HERE))
    table = build(pkg)
    path = os.path.join(pkg, TABLE)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(table, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print("%s: %s" % (path, ", ".join("%s %d" % (k, len(v)) for k, v in sorted(table.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
