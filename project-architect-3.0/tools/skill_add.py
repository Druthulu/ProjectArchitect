#!/usr/bin/env python
"""skill_add.py -- turn a captured workflow into .claude/skills/<name>/SKILL.md.

    skill_add.py <name> --from logs/T3.md#Workflow --description "<=120 chars"

Adds one line to HOW_WE_WORK.md `## Skills`. Descriptions are injected into every
agent's context, so >120 chars is refused. Stdlib only; no imports from `pa/`.
"""

import argparse
import datetime
import os
import re
import sys

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_DESC = 120
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL = """---
name: {name}
description: {description}
---

# {title}

Captured {date} from {source}.

## When to use
{when}

## Steps
{steps}
"""


def find_root(start=None):
    env = os.environ.get("PA_PROJECT_ROOT")
    if env:
        return os.path.abspath(env)
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(cur, ".claude", "pa.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=start or os.getcwd(), capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except Exception:
        pass
    return os.path.abspath(start or os.getcwd())


def read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _cnote(path, **kw):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def rel(root, path):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


def capture(root, spec):
    """'logs/T3.md#Workflow' -> the lines of that section (or the whole file)."""
    if not spec:
        return []
    path, _, anchor = spec.partition("#")
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.isfile(full):
        die("no source file at %s" % path)
    lines = read_text(full).split("\n")
    if not anchor:
        return [x for x in lines if x.strip()][:30]
    want = anchor.strip().lower().replace("-", " ")
    out, inside, level = [], False, 0
    for raw in lines:
        if raw.startswith("#"):
            head = raw.lstrip("#").strip().lower().replace("-", " ")
            lvl = len(raw) - len(raw.lstrip("#"))
            if inside and lvl <= level:
                break
            if want in head:
                inside, level = True, lvl
                continue
        if inside:
            out.append(raw)
    if not inside:
        die("no section '%s' in %s" % (anchor, path))
    while out and not out[-1].strip():
        out.pop()
    return out[:30]


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="skill_add.py",
        description="Scaffold .claude/skills/<name>/SKILL.md from a captured workflow.")
    p.add_argument("name")
    p.add_argument("--from", dest="source", help="path[#Section] to capture")
    p.add_argument("--description", required=True, help="<=120 chars")
    p.add_argument("--title")
    p.add_argument("--when", default="(fill in: the trigger for this workflow)")
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    root = find_root()

    if not NAME_RE.match(a.name):
        die("skill name must be lowercase-with-dashes (got %r)" % a.name)
    desc = a.description.strip()
    if len(desc) > MAX_DESC:
        die("description is %d chars (max %d); it is injected into every agent's context"
            % (len(desc), MAX_DESC))
    if "\n" in a.description:
        die("description must be one line")
    target = os.path.join(root, ".claude", "skills", a.name, "SKILL.md")
    if os.path.isfile(target) and not a.force:
        die("%s already exists (--force to overwrite)" % rel(root, target))
    steps = capture(root, a.source)
    body = "\n".join(steps) if steps else "- (fill in the steps)"
    write_text(target, SKILL.format(
        name=a.name, description=desc,
        title=a.title or a.name.replace("-", " ").title(),
        date=datetime.date.today().isoformat(),
        source=a.source or "this session", when=a.when, steps=body))
    _cnote(target, script="skill_add.py", sub="add", whole=True, root=root)

    hww = os.path.join(root, "HOW_WE_WORK.md")
    line = "- %s — %s" % (a.name, desc)
    noted = "not found"
    if os.path.isfile(hww):
        lines = read_text(hww).split("\n")
        at = None
        for i, raw in enumerate(lines):
            if raw.strip().lower().startswith("## skills"):
                at = i
                break
        if at is None:
            lines += ["", "## Skills", line]
        else:
            end = len(lines)
            for j in range(at + 1, len(lines)):
                if lines[j].startswith("## "):
                    end = j
                    break
            while end > at + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, line)
        write_text(hww, "\n".join(lines))
        _cnote(hww, script="skill_add.py", sub="add", added=len(line), root=root)
        noted = "HOW_WE_WORK.md ## Skills"
    sys.stdout.write("%s\n%s (%s)\n" % (rel(root, target), line, noted))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
