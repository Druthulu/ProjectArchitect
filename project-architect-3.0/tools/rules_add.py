#!/usr/bin/env python
"""rules_add.py -- rules/<id>.md + rules/INDEX.md, and promotion to project-architect.

add | supersede | promote | retire.  Index line grammar (design B G.10/D):
    M4 | headline | tags | active|superseded-by:R31|sunset | origin
Stdlib only; no imports from `pa/`.
"""

import argparse
import datetime
import json
import os
import re
import sys

try:                          # UTF-8 output even when not started with -X utf8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INDEX_HEADER = ("# Rules -- full texts in rules/<id>.md\n"
                "# id | headline | tags | active|superseded-by:<id>|sunset | origin\n")
HEADLINE_BUDGET_TOKENS = 400
CHARS_PER_TOKEN = 3.2
RULE = """# {rid} — {title}
id: {rid} · group: {group} · status: {status} · tags: {tags} · origin: {origin} · added: {date}

{body}
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


def rules_dir(root):
    return os.path.join(root, "rules")


def index_path(root):
    return os.path.join(rules_dir(root), "INDEX.md")


def load_index(root):
    p = index_path(root)
    if not os.path.isfile(p):
        write_text(p, INDEX_HEADER)
    return read_text(p).split("\n")


def save_index(root, lines):
    out = [x for x in lines]
    while out and not out[-1].strip():
        out.pop()
    write_text(index_path(root), "\n".join(out) + "\n")


def index_row(lines, rid):
    for i, raw in enumerate(lines):
        if raw.strip().startswith(rid + " |"):
            return i
    return None


def split_row(raw):
    return [c.strip() for c in raw.split("|")]


def cmd_add(a, root):
    rid = a.id
    if not re.match(r"^[A-Za-z]+[0-9]+(?:\.[0-9]+)?$", rid):
        die("rule id must look like R28 / M4 / H7 (got %r)" % rid)
    path = os.path.join(rules_dir(root), "%s.md" % rid)
    if os.path.isfile(path) and not a.force:
        die("%s already exists (--force to overwrite)" % rel(root, path))
    body = ""
    if a.file:
        if not os.path.isfile(a.file):
            die("no body file at %s" % a.file)
        body = read_text(a.file).rstrip("\n")
    write_text(path, RULE.format(rid=rid, title=a.title, group=a.group or "-",
                                 status="active", tags=a.tags or "-",
                                 origin=a.origin or "-",
                                 date=datetime.date.today().isoformat(), body=body))
    _cnote(path, script="rules_add.py", sub="add", whole=True, root=root)
    lines = load_index(root)
    _old_row = ""
    row = "%s | %s | %s | active | %s" % (rid, a.title, a.tags or "-", a.origin or "-")
    i = index_row(lines, rid)
    if i is None:
        lines.append(row)
    else:
        _old_row = lines[i]
        lines[i] = row
    save_index(root, lines)
    _cnote(index_path(root), script="rules_add.py", sub="add",
           removed=len(_old_row), added=len(row), root=root)
    sys.stdout.write("%s\n%s\n" % (rel(root, path), row))
    return 0


def _set_status(root, rid, status):
    lines = load_index(root)
    i = index_row(lines, rid)
    if i is None:
        die("no index line for %s" % rid)
    _old_row = lines[i]
    cells = split_row(lines[i])
    while len(cells) < 5:
        cells.append("-")
    cells[3] = status
    lines[i] = " | ".join(cells)
    save_index(root, lines)
    _cnote(index_path(root), script="rules_add.py", sub=status.split(":")[0],
           removed=len(_old_row), added=len(lines[i]), root=root)
    path = os.path.join(rules_dir(root), "%s.md" % rid)
    if os.path.isfile(path):
        text = read_text(path)
        _old_text = text
        if re.search(r"status:\s*\S+", text):
            text = re.sub(r"status:\s*\S+", "status: " + status, text, count=1)
        else:
            text = text.rstrip("\n") + "\nstatus: %s\n" % status
        write_text(path, text)
        _cnote(path, script="rules_add.py", sub=status.split(":")[0],
               removed=len(_old_text), added=len(text), root=root)
    return lines[i]


def cmd_supersede(a, root):
    if index_row(load_index(root), a.by) is None:
        die("the superseding rule %s is not in rules/INDEX.md" % a.by)
    row = _set_status(root, a.rule, "superseded-by:%s" % a.by)
    sys.stdout.write(row + "\n")
    return 0


def cmd_retire(a, root):
    row = _set_status(root, a.rule, "sunset")
    sys.stdout.write(row + "\n")
    return 0


def skill_path(root):
    return os.path.join(root, ".claude", "skills", "project-architect", "SKILL.md")


def find_headline_section(lines):
    """-> (heading index, end index) of the project-headlines section."""
    start = None
    for i, raw in enumerate(lines):
        if not raw.startswith("#"):
            continue
        low = raw.lower()
        if "headline" in low or re.search(r"[§#]\s?11\b", low):
            start = i
            break
    if start is None:
        return None, None
    end = len(lines)
    level = len(raw) - len(raw.lstrip("#"))
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("#"):
            lvl = len(lines[j]) - len(lines[j].lstrip("#"))
            if lvl <= level:
                end = j
                break
    return start, end


def cmd_promote(a, root):
    sp = skill_path(root)
    if not os.path.isfile(sp):
        die("no project-architect skill at %s" % rel(root, sp))
    lines = load_index(root)
    i = index_row(lines, a.rule)
    if i is None:
        die("no index line for %s" % a.rule)
    cells = split_row(lines[i])
    headline = cells[1] if len(cells) > 1 else a.rule
    text = read_text(sp)
    slines = text.split("\n")
    start, end = find_headline_section(slines)
    if start is None:
        die("project-architect has no project-headlines section (expected a heading with '11')")
    row = "- %s %s %s" % (a.rule, "—", headline)
    if any(x.strip().startswith("- %s " % a.rule) for x in slines[start:end]):
        sys.stdout.write("%s already promoted\n" % a.rule)
        return 0
    body = "\n".join(slines[start:end] + [row])
    tokens = int(len(body) / CHARS_PER_TOKEN)
    if tokens > HEADLINE_BUDGET_TOKENS:
        die("promoting %s would put the headlines section at ~%d tokens (max %d)"
            % (a.rule, tokens, HEADLINE_BUDGET_TOKENS))
    at = end
    while at > start + 1 and not slines[at - 1].strip():
        at -= 1
    slines.insert(at, row)
    write_text(sp, "\n".join(slines))
    _cnote(sp, script="rules_add.py", sub="promote", added=len(row), root=root)
    sys.stdout.write("promoted %s -> project-architect (~%d tokens in the section)\n"
                     % (a.rule, tokens))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="rules_add.py", description="Write rules/<id>.md and rules/INDEX.md.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="create rules/<id>.md and index it")
    s.add_argument("--id", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--group")
    s.add_argument("--file", help="markdown body")
    s.add_argument("--origin")
    s.add_argument("--tags")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("supersede", help="mark a rule superseded-by another")
    s.add_argument("rule")
    s.add_argument("--by", required=True)
    s = sub.add_parser("promote", help="add the headline to the project-architect skill")
    s.add_argument("rule")
    s = sub.add_parser("retire", help="mark a rule sunset")
    s.add_argument("rule")
    a = p.parse_args(argv)
    root = find_root()
    return {"add": cmd_add, "supersede": cmd_supersede, "promote": cmd_promote,
            "retire": cmd_retire}[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
