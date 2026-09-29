#!/usr/bin/env python
"""curate.py -- generation-start curator: demote memories, cookbook, rules; trim HOW_WE_WORK.

Subcommands: memory, cookbook, rules, how-we-work, discussions, ops.
memory --route FILE=STORE: write each memory's fact to its store (developer and
how-we-work: a bullet in that HOW_WE_WORK.md section, within card.max_chars; rule:
rules_add.py; cookbook: cookbook_add.sh; ops: docs/ops/<stem>.md + its index row;
archive: nothing), then demote it to gen<G>.md. Every route is validated first;
any refusal writes nothing and exits 1.
ops --sunset --gen G: retire unreferenced ops topics; ops --reindex: refresh the
docs/ops/INDEX.md line counts and append rows for unindexed docs/ops/*.md.
Every subcommand takes --dry-run and prints a table of what it would do.
Stdlib only; no imports from `pa/`.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def die(msg):
    sys.stdout.write("refused: %s\n" % msg)
    raise SystemExit(1)


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


def stable_hash(line):
    """Short stable hash for a line (first 8 hex chars of sha256)."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# memory --demote <file>... --gen <G> [--dir DIR]
# --------------------------------------------------------------------------- #

def _memory_index_path(memdir):
    return os.path.join(memdir, "MEMORY.md")


def _gen_archive_path(memdir, gen):
    return os.path.join(memdir, "gen%s.md" % gen)


def _parse_index_line(line):
    """-> (filename, rest) or None."""
    m = re.match(r"^-\s+\[([^\]]+)\]\(([^)]+)\)", line)
    if m:
        return m.group(2), m.group(1)
    return None


def _count_gen_entries(text):
    """Count ## headings in a gen archive file."""
    return sum(1 for line in text.split("\n") if line.startswith("## "))


_ROUTE_STORES = ("developer", "how-we-work", "rule", "cookbook", "ops", "archive")
_CARD_SECTIONS = {"developer": r"^## Developer\b",
                  "how-we-work": r"^## Conventions & house style\b"}


def _card_cap(root):
    """card.max_chars from .claude/pa.json, default 7000 (mirrors card.py:_max_chars)."""
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            return json.load(fh).get("card", {}).get("max_chars", 7000)
    except (OSError, ValueError, AttributeError):
        return 7000


def _split_frontmatter(text):
    """-> (frontmatter dict, body) for an optional leading `---` YAML block."""
    t = text.replace("\r\n", "\n")
    fm = {}
    if t.startswith("---\n"):
        end = t.find("\n---", 3)
        if end != -1:
            for ln in t[4:end].split("\n"):
                key, sep, val = ln.partition(":")
                if sep and key.strip():
                    fm[key.strip()] = val.strip().strip("\"'")
            nl = t.find("\n", end + 4)
            t = t[nl + 1:] if nl != -1 else ""
    return fm, t.strip("\n")


def _one_line(s):
    return " ".join(s.split())


def _card_add_bullet(text, pattern, bullet):
    """Insert `bullet` as the last bullet of the section whose heading matches; None if absent."""
    lines = text.split("\n")
    cr = "\r" if "\r\n" in text else ""
    start = next((i for i, ln in enumerate(lines) if re.match(pattern, ln.rstrip("\r"))), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")),
               len(lines))
    at, in_bullet, last_text = None, False, start + 1
    for j in range(start + 1, end):
        s = lines[j].rstrip("\r")
        if s.startswith("- "):
            at, in_bullet = j + 1, True
        elif in_bullet and s.strip() and s[:1] in " \t":
            at = j + 1
        else:
            in_bullet = False
        if s.strip():
            last_text = j + 1
    lines.insert(at if at is not None else last_text, bullet + cr)
    return "\n".join(lines)


def _plan_routes(specs, root, memdir, index_lines, gen):
    """Validate every --route spec; -> (routes, refusals, card). Nothing is written."""
    routes, refusals, seen = [], [], set()
    card_path = os.path.join(root, "HOW_WE_WORK.md")
    card_text = read_text(card_path) if os.path.isfile(card_path) else None
    card_start = card_text
    cap = _card_cap(root)
    rules_dir = os.path.join(root, "rules")
    used = set()
    if os.path.isfile(os.path.join(rules_dir, "INDEX.md")):
        used |= {int(m) for m in re.findall(r"(?m)^L(\d+)\b",
                                            read_text(os.path.join(rules_dir, "INDEX.md")))}
    if os.path.isdir(rules_dir):
        used |= {int(m.group(1)) for m in (re.match(r"^L(\d+)\.md$", n)
                                           for n in os.listdir(rules_dir)) if m}
    next_rule = max(used) + 1 if used else 1
    cb_dir = os.path.join(root, "cookbook")
    cb_taken = set(os.listdir(cb_dir)) if os.path.isdir(cb_dir) else set()
    next_cb = 1
    bash = shutil.which("bash")
    for spec in specs:
        fname, sep, store = spec.rpartition("=")
        if not sep or not fname:
            refusals.append((spec, "expected FILE=STORE"))
            continue
        if store not in _ROUTE_STORES:
            refusals.append((fname, "unknown store %r (one of %s)"
                             % (store, ", ".join(_ROUTE_STORES))))
            continue
        if fname in seen:
            refusals.append((fname, "routed twice"))
            continue
        seen.add(fname)
        fpath = os.path.join(memdir, fname)
        if not os.path.isfile(fpath):
            refusals.append((fname, "no memory file at %s" % fpath))
            continue
        entry = None
        for line in index_lines:
            m = re.match(r"^-\s+\[([^\]]+)\]\(([^)]+)\)\s*(?:[—–-]\s*)?(.*)$", line)
            if m and m.group(2) == fname:
                entry = m
                break
        if entry is None:
            refusals.append((fname, "no index line in MEMORY.md"))
            continue
        fm, body = _split_frontmatter(read_text(fpath))
        title = _one_line(fm.get("name") or entry.group(1))
        summary = _one_line(fm.get("description") or entry.group(3))
        r = {"file": fname, "store": store, "title": title, "summary": summary, "body": body}
        stem = os.path.splitext(fname)[0]
        if store in _CARD_SECTIONS:
            if card_text is None:
                refusals.append((fname, "no HOW_WE_WORK.md"))
                continue
            new = _card_add_bullet(card_text, _CARD_SECTIONS[store],
                                   "- %s: %s" % (title, summary))
            if new is None:
                refusals.append((fname, "HOW_WE_WORK.md has no section matching %s"
                                 % _CARD_SECTIONS[store]))
                continue
            if len(new) > cap:
                refusals.append((fname, "HOW_WE_WORK.md would be %d/%d chars"
                                 % (len(new), cap)))
                continue
            card_text = new
            r["target"] = "HOW_WE_WORK.md"
        elif store == "rule":
            r["id"] = "L%d" % next_rule
            next_rule += 1
            r["target"] = "rules/%s.md" % r["id"]
        elif store == "cookbook":
            if bash is None:
                refusals.append((fname, "no bash on PATH for cookbook_add.sh"))
                continue
            if not os.path.isfile(os.path.join(root, ".claude", "pa.json")):
                refusals.append((fname, "no .claude/pa.json at the root "
                                 "(cookbook_add.sh would resolve another root)"))
                continue
            while "C%04d.md" % next_cb in cb_taken:
                next_cb += 1
            r["target"] = "cookbook/C%04d.md" % next_cb
            next_cb += 1
        elif store == "ops":
            if not os.path.isfile(os.path.join(root, "docs", "ops", "INDEX.md")):
                refusals.append((fname, "no docs/ops/INDEX.md"))
                continue
            r["target"] = "docs/ops/%s.md" % stem
            if os.path.exists(os.path.join(root, r["target"])):
                refusals.append((fname, "%s exists" % r["target"]))
                continue
        else:
            r["target"] = rel(root, _gen_archive_path(memdir, gen))
        routes.append(r)
    card = None
    if card_text is not None and card_text != card_start:
        card = (card_path, card_text, len(card_text) - len(card_start))
    return routes, refusals, card


def _apply_routes(routes, card, root):
    """Write each route's fact to its store (the demote follows in cmd_memory)."""
    if card:
        write_text(card[0], card[1])
        _cnote(card[0], script="curate.py", sub="memory", added=card[2], root=root)
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, PA_PROJECT_ROOT=root, PA_PYTHON=sys.executable)
    scratch = os.path.join(root, ".run")
    ops = False
    for r in routes:
        origin = "legacy memory %s" % r["file"]
        if r["store"] in ("rule", "cookbook"):
            body = os.path.join(scratch, "curate-route-%s" % r["file"])
            write_text(body, r["body"] + "\n")
            if r["store"] == "rule":
                cmd = [sys.executable, os.path.join(here, "rules_add.py"), "add",
                       "--id", r["id"], "--title", r["title"], "--file", body,
                       "--origin", origin, "--tags", "legacy,memory"]
            else:
                cmd = [shutil.which("bash"),
                       os.path.join(here, "cookbook_add.sh").replace("\\", "/"),
                       "--title", r["title"], "--tags", "legacy,memory",
                       "--origin", origin, "--file", body.replace("\\", "/")]
            res = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
            os.remove(body)
            if res.returncode != 0:
                die("%s: %s: %s" % (r["file"], os.path.basename(cmd[1]),
                                    (res.stdout + res.stderr).strip()))
        elif r["store"] == "ops":
            path = os.path.join(root, r["target"])
            write_text(path, "# %s\n\n%s\n" % (r["title"], r["body"]))
            _cnote(path, script="curate.py", sub="memory", whole=True, root=root)
            ops = True
    if ops:
        _ops_reindex(argparse.Namespace(dry_run=False), root)


def cmd_memory(args, root):
    if not args.demote and not args.route:
        die("--demote or --route is required")
    memdir = args.dir or os.path.join(root, ".claude-state", "memory")
    gen = args.gen
    index_path = _memory_index_path(memdir)
    if not os.path.isfile(index_path):
        die("no MEMORY.md at %s" % index_path)

    actions = []
    index_lines = read_text(index_path).rstrip("\n").split("\n")

    demote = list(args.demote or [])
    routes, refusals, card = _plan_routes(args.route or [], root, memdir, index_lines, gen)
    demote += [r["file"] for r in routes if r["file"] not in demote]

    for fname in demote:
        fpath = os.path.join(memdir, fname)
        if not os.path.isfile(fpath):
            die("no memory file at %s" % fpath)
        # find the index line
        idx = None
        for i, line in enumerate(index_lines):
            parsed = _parse_index_line(line)
            if parsed and parsed[0] == fname:
                idx = i
                break
        if idx is None:
            die("no index line for %s in MEMORY.md" % fname)
        actions.append({"file": fname, "path": fpath, "index_line": idx})

    # print the table
    archive_path = _gen_archive_path(memdir, gen)
    existing_count = 0
    if os.path.isfile(archive_path):
        existing_count = _count_gen_entries(read_text(archive_path))
    new_count = existing_count + len(actions)

    if args.route:
        sys.stdout.write("memory --route --gen %s\n" % gen)
    else:
        sys.stdout.write("memory --demote --gen %s\n" % gen)
    for r in routes:
        sys.stdout.write("  route: %s -> %s (%s)\n" % (r["file"], r["store"], r["target"]))
    for a in actions:
        sys.stdout.write("  demote: %s -> gen%s.md\n" % (a["file"], gen))
    sys.stdout.write("  archive: %s (%d entries after)\n"
                     % (rel(root, archive_path), new_count))
    link_line = "- [Generation %s memories](gen%s.md) — %d entries" % (gen, gen, new_count)
    sys.stdout.write("  link: %s\n" % link_line)
    for fname, why in refusals:
        sys.stdout.write("refused: %s: %s\n" % (fname, why))
    if refusals:
        return 1

    if args.dry_run:
        sys.stdout.write("dry-run: no changes\n")
        return 0

    _apply_routes(routes, card, root)

    # append each memory to the archive
    archive_parts = []
    if os.path.isfile(archive_path):
        archive_parts.append(read_text(archive_path).rstrip("\n"))
    for a in actions:
        content = read_text(a["path"]).rstrip("\n")
        # use the filename stem as the heading name
        name = os.path.splitext(a["file"])[0]
        archive_parts.append("\n## %s\n\n%s" % (name, content))
    write_text(archive_path, "\n".join(archive_parts).lstrip("\n") + "\n")
    _cnote(archive_path, script="curate.py", sub="memory", whole=True, root=root)

    # remove demoted files and their index lines (reverse order to keep indices valid)
    _removed_chars = sum(len(index_lines[a["index_line"]]) for a in actions)
    for a in sorted(actions, key=lambda x: x["index_line"], reverse=True):
        index_lines.pop(a["index_line"])
        os.remove(a["path"])

    # update or add the link line for this generation
    gen_link_idx = None
    for i, line in enumerate(index_lines):
        if re.match(r"^-\s+\[Generation %s memories\]" % re.escape(str(gen)), line):
            gen_link_idx = i
            break
    if gen_link_idx is not None:
        _removed_chars += len(index_lines[gen_link_idx])
        index_lines[gen_link_idx] = link_line
    else:
        index_lines.append(link_line)

    write_text(index_path, "\n".join(index_lines) + "\n")
    _cnote(index_path, script="curate.py", sub="memory",
           removed=_removed_chars, added=len(link_line), root=root)
    return 0


# --------------------------------------------------------------------------- #
# cookbook --demote C0012 ... --gen <G>
# --------------------------------------------------------------------------- #

def cmd_cookbook(args, root):
    gen = args.gen
    cb_dir = os.path.join(root, "cookbook")
    index_path = os.path.join(cb_dir, "INDEX.md")
    if not os.path.isfile(index_path):
        die("no cookbook/INDEX.md")

    index_lines = read_text(index_path).rstrip("\n").split("\n")
    gen_dir = os.path.join(cb_dir, "gen%s" % gen)
    gen_index = os.path.join(gen_dir, "INDEX.md")

    actions = []
    for cid in args.demote:
        # find the entry file
        entry_path = os.path.join(cb_dir, "%s.md" % cid)
        if not os.path.isfile(entry_path):
            die("no cookbook/%s.md" % cid)
        # find the index line
        idx = None
        for i, line in enumerate(index_lines):
            if line.strip().startswith(cid + " |"):
                idx = i
                break
        if idx is None:
            die("no index line for %s in cookbook/INDEX.md" % cid)
        actions.append({"id": cid, "path": entry_path, "index_line": idx,
                        "line_text": index_lines[idx]})

    sys.stdout.write("cookbook --demote --gen %s\n" % gen)
    for a in actions:
        sys.stdout.write("  demote: %s -> cookbook/gen%s/%s.md\n"
                         % (a["id"], gen, a["id"]))
    link_text = ("gen%s | Generation %s entries | see cookbook/gen%s/INDEX.md"
                 % (gen, gen, gen))
    sys.stdout.write("  link: %s\n" % link_text)

    if args.dry_run:
        sys.stdout.write("dry-run: no changes\n")
        return 0

    os.makedirs(gen_dir, exist_ok=True)

    # read or create the gen index with the template header
    if os.path.isfile(gen_index):
        gen_index_lines = read_text(gen_index).rstrip("\n").split("\n")
    else:
        gen_index_lines = [
            "<!-- Cookbook index. One line per entry, appended by tools/cookbook_add.sh. "
            "Grep this file by tag or title; never read it whole; never read an entry "
            "you did not find here. -->",
            "<!-- C<nnnn> | <title> | <tags> | <date> | <phase>/<task> | origin: "
            "<source section or report id> -->"
        ]

    for a in sorted(actions, key=lambda x: x["index_line"], reverse=True):
        # git mv the entry
        dest = os.path.join(gen_dir, "%s.md" % a["id"])
        subprocess.run(["git", "mv", a["path"], dest],
                       cwd=root, capture_output=True, text=True)
        # move the index line to the gen index
        gen_index_lines.append(a["line_text"])
        index_lines.pop(a["index_line"])

    # ensure the link line exists in the main index (once)
    _rm_cb = sum(len(a["line_text"]) for a in actions)
    has_link = any(line.strip().startswith("gen%s |" % gen) for line in index_lines)
    if not has_link:
        index_lines.append(link_text)

    write_text(index_path, "\n".join(index_lines) + "\n")
    _cnote(index_path, script="curate.py", sub="cookbook",
           removed=_rm_cb, added=len(link_text) if not has_link else 0, root=root)
    write_text(gen_index, "\n".join(gen_index_lines) + "\n")
    _cnote(gen_index, script="curate.py", sub="cookbook", whole=True, root=root)
    return 0


# --------------------------------------------------------------------------- #
# rules --demote <id>... --gen <G>
# --------------------------------------------------------------------------- #

def cmd_rules(args, root):
    gen = args.gen
    rules_dir = os.path.join(root, "rules")
    index_path = os.path.join(rules_dir, "INDEX.md")
    if not os.path.isfile(index_path):
        die("no rules/INDEX.md")

    index_lines = read_text(index_path).rstrip("\n").split("\n")
    gen_dir = os.path.join(rules_dir, "gen%s" % gen)
    gen_index = os.path.join(gen_dir, "INDEX.md")

    actions = []
    for rid in args.demote:
        entry_path = os.path.join(rules_dir, "%s.md" % rid)
        if not os.path.isfile(entry_path):
            die("no rules/%s.md" % rid)
        idx = None
        for i, line in enumerate(index_lines):
            if line.strip().startswith(rid + " |"):
                idx = i
                break
        if idx is None:
            die("no index line for %s in rules/INDEX.md" % rid)
        # check if the rule is active
        cells = [c.strip() for c in index_lines[idx].split("|")]
        is_active = len(cells) > 3 and cells[3] == "active"
        actions.append({"id": rid, "path": entry_path, "index_line": idx,
                        "line_text": index_lines[idx], "is_active": is_active})

    sys.stdout.write("rules --demote --gen %s\n" % gen)
    for a in actions:
        tag = " (active: kept in main index)" if a["is_active"] else ""
        sys.stdout.write("  demote: %s -> rules/gen%s/%s.md%s\n"
                         % (a["id"], gen, a["id"], tag))
    link_text = ("gen%s | Generation %s entries | see rules/gen%s/INDEX.md"
                 % (gen, gen, gen))
    sys.stdout.write("  link: %s\n" % link_text)

    if args.dry_run:
        sys.stdout.write("dry-run: no changes\n")
        return 0

    os.makedirs(gen_dir, exist_ok=True)

    if os.path.isfile(gen_index):
        gen_index_lines = read_text(gen_index).rstrip("\n").split("\n")
    else:
        gen_index_lines = [
            "<!-- Rules index. One line per rule, written by tools/rules_add.py. "
            "Grep by id or tag; the full text is rules/<id>.md. "
            "Sunset rules stay listed, marked. -->",
            "<!-- <id> | <headline> | <tags> | active | superseded-by:<id> "
            "| sunset | <origin: seed-2.0 or Phase <N>> -->"
        ]

    for a in sorted(actions, key=lambda x: x["index_line"], reverse=True):
        dest = os.path.join(gen_dir, "%s.md" % a["id"])
        subprocess.run(["git", "mv", a["path"], dest],
                       cwd=root, capture_output=True, text=True)
        gen_index_lines.append(a["line_text"])
        if a["is_active"]:
            # keep the line in the main index, marked demoted
            index_lines[a["index_line"]] = (
                index_lines[a["index_line"]].rstrip() + " | demoted:gen%s" % gen)
        else:
            index_lines.pop(a["index_line"])

    _rm_rl = sum(len(a["line_text"]) for a in actions if not a["is_active"])
    has_link = any(line.strip().startswith("gen%s |" % gen) for line in index_lines)
    if not has_link:
        index_lines.append(link_text)

    write_text(index_path, "\n".join(index_lines) + "\n")
    _cnote(index_path, script="curate.py", sub="rules",
           removed=_rm_rl, added=len(link_text) if not has_link else 0, root=root)
    write_text(gen_index, "\n".join(gen_index_lines) + "\n")
    _cnote(gen_index, script="curate.py", sub="rules", whole=True, root=root)
    return 0


# --------------------------------------------------------------------------- #
# how-we-work --report [--before DATE] / --retire <hash>... --gen <G>
# --------------------------------------------------------------------------- #

# token targets
_STANDING_TARGET = 1500   # tokens
_FILE_TARGET = 3500       # tokens
_CHARS_PER_TOKEN = 4.0


def _hww_path(root):
    return os.path.join(root, "HOW_WE_WORK.md")


def _parse_sections(text):
    """-> [(heading, start_line, end_line, body)] where body is the lines."""
    lines = text.split("\n")
    sections = []
    cur_heading = None
    cur_start = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            if cur_heading is not None:
                sections.append((cur_heading, cur_start, i, lines[cur_start:i]))
            cur_heading = line[3:].strip()
            cur_start = i
    if cur_heading is not None:
        sections.append((cur_heading, cur_start, len(lines), lines[cur_start:len(lines)]))
    return sections


def _standing_candidates(text, before_date):
    """Yield (hash, block, heading, abs_start, abs_end) for standing-decision
    bullets dated before before_date.

    A bullet is its ``- `` line plus every following line that starts with
    whitespace, up to the next ``- `` line, a heading or a blank line. The
    hash is computed from the first line only, so existing hashes keep
    working. ``abs_start``/``abs_end`` are the block's line range (exclusive
    end) in the file's full line list.
    """
    sections = _parse_sections(text)
    for heading, start, _end, body in sections:
        if heading.lower().startswith("standing decision"):
            i = 0
            n = len(body)
            while i < n:
                line = body[i]
                m = re.match(r"^-\s+(\d{4}-\d{2}-\d{2})", line)
                if not m:
                    i += 1
                    continue
                j = i + 1
                while j < n and body[j] and body[j][0] in (" ", "\t"):
                    j += 1
                block = body[i:j]
                if before_date is None or m.group(1) < before_date:
                    yield stable_hash(line), block, heading, start + i, start + j
                i = j


def cmd_how_we_work(args, root):
    hww = _hww_path(root)
    if not os.path.isfile(hww):
        die("no HOW_WE_WORK.md")
    text = read_text(hww)
    total_chars = len(text)
    total_tokens = int(total_chars / _CHARS_PER_TOKEN)

    if args.report:
        sys.stdout.write("how-we-work --report\n")
        sections = _parse_sections(text)
        # check section sizes
        for heading, start, end, body in sections:
            body_text = "\n".join(body)
            sec_chars = len(body_text)
            sec_tokens = int(sec_chars / _CHARS_PER_TOKEN)
            if heading.lower().startswith("standing decision"):
                if sec_tokens > _STANDING_TARGET:
                    sys.stdout.write("  OVER: ## %s: %d tokens (target %d)\n"
                                     % (heading, sec_tokens, _STANDING_TARGET))
        if total_tokens > _FILE_TARGET:
            sys.stdout.write("  OVER: whole file: %d tokens (target %d)\n"
                             % (total_tokens, _FILE_TARGET))
        # list dated candidates
        count = 0
        for h, block, heading, _abs_start, _abs_end in _standing_candidates(text, args.before):
            suffix = " (%d lines)" % len(block) if len(block) > 1 else ""
            sys.stdout.write("  candidate %s: %s%s\n" % (h, block[0].strip()[:80], suffix))
            count += 1
        if count == 0:
            sys.stdout.write("  no candidates\n")
        return 0

    if args.retire:
        gen = args.gen
        if not gen:
            die("--gen is required with --retire")
        retire_path = os.path.join(root, "docs", "retired",
                                   "HOW_WE_WORK.gen%s.md" % gen)

        # build a map of hash -> (block, heading, abs_start, abs_end)
        all_candidates = {}
        for h, block, heading, abs_start, abs_end in _standing_candidates(text, None):
            all_candidates[h] = (block, heading, abs_start, abs_end)

        actions = []
        for h in args.retire:
            if h not in all_candidates:
                die("no standing-decision line with hash %s" % h)
            block, heading, abs_start, abs_end = all_candidates[h]
            actions.append((h, block, heading, abs_start, abs_end))

        sys.stdout.write("how-we-work --retire --gen %s\n" % gen)
        for h, block, heading, _abs_start, _abs_end in actions:
            sys.stdout.write("  retire %s: %s\n" % (h, block[0].strip()[:80]))
        sys.stdout.write("  -> %s\n" % rel(root, retire_path))

        if args.dry_run:
            sys.stdout.write("dry-run: no changes\n")
            return 0

        # append to the retired file, grouped by heading
        os.makedirs(os.path.dirname(retire_path), exist_ok=True)
        retired_parts = []
        if os.path.isfile(retire_path):
            retired_parts.append(read_text(retire_path).rstrip("\n"))

        by_heading = {}
        for h, block, heading, _abs_start, _abs_end in actions:
            by_heading.setdefault(heading, []).extend(block)

        for heading, lines in by_heading.items():
            retired_parts.append("\n## %s\n" % heading)
            for line in lines:
                retired_parts.append(line)

        write_text(retire_path, "\n".join(retired_parts).lstrip("\n") + "\n")
        _cnote(retire_path, script="curate.py", sub="how-we-work", whole=True, root=root)

        # remove the blocks from HOW_WE_WORK.md, by line range
        hww_lines = text.split("\n")
        remove_idx = set()
        _removed_hw = 0
        for _h, _block, _heading, abs_start, abs_end in actions:
            remove_idx.update(range(abs_start, abs_end))
            _removed_hw += sum(len(hww_lines[i]) for i in range(abs_start, abs_end))
        new_lines = [l for idx, l in enumerate(hww_lines) if idx not in remove_idx]
        write_text(hww, "\n".join(new_lines))
        _cnote(hww, script="curate.py", sub="how-we-work",
               removed=_removed_hw, root=root)
        return 0

    die("use --report or --retire")


# --------------------------------------------------------------------------- #
# discussions --demote --gen G [--dry-run]
# --------------------------------------------------------------------------- #

def _phase_dir(root):
    try:
        import json as _json
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            c = _json.load(fh)
        return os.path.join(root, c.get("phase_ends_dir") or c.get("phase_dir") or "phase-ends")
    except Exception:
        return os.path.join(root, "phase-ends")


def cmd_discussions(args, root):
    if not args.demote:
        die("--demote is required")
    gen = args.gen
    if not gen:
        die("--gen is required")
    pdir = _phase_dir(root)
    di = os.path.join(pdir, "DISCUSSION_INDEX.md")
    if not os.path.isfile(di):
        die("no DISCUSSION_INDEX.md at %s" % rel(root, di))
    text = read_text(di)
    lines = text.rstrip("\n").split("\n")
    # identify lines to demote: executed, dropped, failed statuses
    demote_statuses = ("executed", "dropped", "failed")
    header_lines = []
    data_lines = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<!--") or s.startswith("-->"):
            header_lines.append(line)
        else:
            data_lines.append(line)
    actions = []
    keep = []
    for line in data_lines:
        parts = [c.strip() for c in line.split("|")]
        # grammar: phase | id | topic | date | status | path
        status = parts[4].strip() if len(parts) > 4 else ""
        if status in demote_statuses:
            actions.append(line)
        else:
            keep.append(line)

    sys.stdout.write("discussions --demote --gen %s\n" % gen)
    for line in actions:
        sys.stdout.write("  demote: %s\n" % line.strip()[:100])
    if not actions:
        sys.stdout.write("  (nothing to demote)\n")
        return 0

    gen_di = os.path.join(pdir, "gen%s" % gen, "DISCUSSION_INDEX.md")
    sys.stdout.write("  -> %s\n" % rel(root, gen_di))

    if args.dry_run:
        sys.stdout.write("dry-run: no changes\n")
        return 0

    # write the demoted lines to the gen archive
    gen_dir = os.path.dirname(gen_di)
    os.makedirs(gen_dir, exist_ok=True)
    gen_parts = []
    if os.path.isfile(gen_di):
        gen_parts.append(read_text(gen_di).rstrip("\n"))
    for line in actions:
        gen_parts.append(line)
    write_text(gen_di, "\n".join(gen_parts).lstrip("\n") + "\n")
    _cnote(gen_di, script="curate.py", sub="discussions", whole=True, root=root)

    # replace the main index: headers + kept lines + one link line
    link_line = "# see phase-ends/gen%s/DISCUSSION_INDEX.md for demoted records" % gen
    has_link = any("gen%s/DISCUSSION_INDEX.md" % gen in l for l in keep)
    remaining = header_lines[:]
    remaining += keep
    if not has_link:
        remaining.append(link_line)
    write_text(di, "\n".join(remaining) + "\n")
    _cnote(di, script="curate.py", sub="discussions",
           removed=sum(len(l) for l in actions), root=root)
    return 0


# --------------------------------------------------------------------------- #
# ops --sunset --gen G [--dry-run] | ops --reindex [--dry-run]
# --------------------------------------------------------------------------- #

_OPS_ROW = re.compile(r"^(\S+\.md) \| (.*) \| (\d+)$")


def _ops_reindex(args, root):
    ops_dir = os.path.join(root, "docs", "ops")
    ops_index = os.path.join(ops_dir, "INDEX.md")
    if not os.path.isfile(ops_index):
        die("no docs/ops/INDEX.md")
    idx_lines = read_text(ops_index).rstrip("\n").split("\n")
    rows = {}
    last_row = -1
    for i, line in enumerate(idx_lines):
        m = _OPS_ROW.match(line)
        if m:
            rows[m.group(1)] = (i, m.group(2), int(m.group(3)))
            last_row = i
    changes = []
    added = []
    for name in sorted(os.listdir(ops_dir)):
        path = os.path.join(ops_dir, name)
        if name == "INDEX.md" or not name.endswith(".md") or not os.path.isfile(path):
            continue
        text = read_text(path)
        count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if name in rows:
            i, title, old = rows[name]
            if old != count:
                idx_lines[i] = "%s | %s | %d" % (name, title, count)
                changes.append("%s: %d -> %d lines" % (name, old, count))
        else:
            title = os.path.splitext(name)[0]
            for ln in text.split("\n"):
                if ln.startswith("# "):
                    title = ln[2:].strip()
                    break
            row = "%s | %s | %d" % (name, title, count)
            added.append(row)
            changes.append("add %s" % row)
    if added:
        at = last_row + 1 if last_row >= 0 else len(idx_lines)
        idx_lines[at:at] = added
    for c in changes:
        sys.stdout.write(c + "\n")
    if not changes:
        sys.stdout.write("ops --reindex: index current\n")
        return 0
    if args.dry_run:
        return 0
    write_text(ops_index, "\n".join(idx_lines) + "\n")
    _cnote(ops_index, script="curate.py", sub="ops", root=root)
    return 0


def cmd_ops(args, root):
    if args.reindex:
        return _ops_reindex(args, root)
    if not args.sunset:
        die("--sunset or --reindex is required")
    gen = args.gen
    if not gen:
        die("--gen is required")
    ops_dir = os.path.join(root, "docs", "ops")
    ops_index = os.path.join(ops_dir, "INDEX.md")
    if not os.path.isfile(ops_index):
        die("no docs/ops/INDEX.md")

    # collect the referenced file names from the reference sources
    reference_sources = []
    hww = os.path.join(root, "HOW_WE_WORK.md")
    if os.path.isfile(hww):
        reference_sources.append(hww)
    # rules/*.md
    rules_dir = os.path.join(root, "rules")
    if os.path.isdir(rules_dir):
        for name in os.listdir(rules_dir):
            if name.endswith(".md"):
                reference_sources.append(os.path.join(rules_dir, name))
    # cookbook/*.md
    cb_dir = os.path.join(root, "cookbook")
    if os.path.isdir(cb_dir):
        for name in os.listdir(cb_dir):
            if name.endswith(".md"):
                reference_sources.append(os.path.join(cb_dir, name))
    # .claude/skills/*/SKILL.md
    skills_dir = os.path.join(root, ".claude", "skills")
    if os.path.isdir(skills_dir):
        for skill in os.listdir(skills_dir):
            skill_file = os.path.join(skills_dir, skill, "SKILL.md")
            if os.path.isfile(skill_file):
                reference_sources.append(skill_file)
    # phase-ends/PhaseEnd_Phase<G>.*.md
    pdir = _phase_dir(root)
    if os.path.isdir(pdir):
        for name in os.listdir(pdir):
            if name.startswith("PhaseEnd_Phase%s" % gen) and name.endswith(".md"):
                reference_sources.append(os.path.join(pdir, name))
        # also look inside phase-<G.n>/ subdirs
        for sub in os.listdir(pdir):
            sub_path = os.path.join(pdir, sub)
            if os.path.isdir(sub_path):
                for name in os.listdir(sub_path):
                    if name.startswith("PhaseEnd_Phase%s" % gen) and name.endswith(".md"):
                        reference_sources.append(os.path.join(sub_path, name))

    # build set of all referenced ops filenames
    ref_text = ""
    for src in reference_sources:
        try:
            ref_text += read_text(src) + "\n"
        except OSError:
            pass

    # parse the ops index for topic files
    idx_text = read_text(ops_index)
    idx_lines = idx_text.rstrip("\n").split("\n")
    # find topics: lines that link to files  e.g. "- [topic](file.md)"
    ops_files = []
    for i, line in enumerate(idx_lines):
        m = re.match(r"^-\s+\[([^\]]+)\]\(([^)]+)\)", line)
        if m:
            ops_files.append({"name": m.group(2), "title": m.group(1),
                              "line_idx": i, "line": line})

    unreferenced = []
    referenced = []
    for item in ops_files:
        if item["name"] in ref_text:
            referenced.append(item)
        else:
            unreferenced.append(item)

    sys.stdout.write("ops --sunset --gen %s\n" % gen)
    for item in unreferenced:
        sys.stdout.write("  unreferenced: %s (%s)\n" % (item["name"], item["title"]))
    for item in referenced:
        sys.stdout.write("  referenced: %s (%s)\n" % (item["name"], item["title"]))
    if not unreferenced:
        sys.stdout.write("  (nothing to sunset)\n")
        return 0

    if args.dry_run:
        sys.stdout.write("dry-run: no changes\n")
        return 0

    # move unreferenced files to docs/retired/ops/
    retired_dir = os.path.join(root, "docs", "retired", "ops")
    os.makedirs(retired_dir, exist_ok=True)
    for item in unreferenced:
        src_path = os.path.join(ops_dir, item["name"])
        dst_path = os.path.join(retired_dir, item["name"])
        if os.path.isfile(src_path):
            res = subprocess.run(["git", "mv", src_path, dst_path],
                                 cwd=root, capture_output=True, text=True)
            if res.returncode != 0:
                import shutil
                shutil.move(src_path, dst_path)
        # replace the index line with a link line
        idx_lines[item["line_idx"]] = (
            "- [%s](../retired/ops/%s) — retired gen %s"
            % (item["title"], item["name"], gen))
    write_text(ops_index, "\n".join(idx_lines) + "\n")
    _cnote(ops_index, script="curate.py", sub="ops", root=root)
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def _add_dry_run(parser):
    parser.add_argument("--dry-run", action="store_true",
                        help="print the table of changes without writing anything")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="curate.py",
        description="Generation-start curator: demote memories, cookbook, rules; "
                    "trim HOW_WE_WORK.md.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("memory", help="route memory facts to their stores; demote memory "
                                      "files to a generation archive")
    _add_dry_run(s)
    s.add_argument("--demote", nargs="+", metavar="FILE",
                   help="memory filenames to demote (this or --route is required)")
    s.add_argument("--route", nargs="+", metavar="FILE=STORE",
                   help="write the memory's fact to STORE, then demote it; STORE: "
                        "developer | how-we-work (a bullet in that HOW_WE_WORK.md section, "
                        "refused past card.max_chars) | rule (rules_add.py, next free L<n>) | "
                        "cookbook (cookbook_add.sh) | ops (docs/ops/<stem>.md + index row) | "
                        "archive (demote only). All routes are checked first; any refusal "
                        "writes nothing and exits 1")
    s.add_argument("--gen", required=True, help="generation id (e.g. 3 or legacy)")
    s.add_argument("--dir", help="memory directory (default: <root>/.claude-state/memory)")

    s = sub.add_parser("cookbook", help="demote cookbook entries to a generation archive")
    _add_dry_run(s)
    s.add_argument("--demote", nargs="+", required=True, metavar="ID",
                   help="cookbook ids to demote (e.g. C0012)")
    s.add_argument("--gen", required=True, help="generation id")

    s = sub.add_parser("rules", help="demote rules to a generation archive")
    _add_dry_run(s)
    s.add_argument("--demote", nargs="+", required=True, metavar="ID",
                   help="rule ids to demote (e.g. M4)")
    s.add_argument("--gen", required=True, help="generation id")

    s = sub.add_parser("how-we-work", help="report or retire HOW_WE_WORK.md lines")
    _add_dry_run(s)
    s.add_argument("--report", action="store_true",
                   help="list trim candidates")
    s.add_argument("--before", metavar="DATE",
                   help="only standing decisions dated before DATE (YYYY-MM-DD)")
    s.add_argument("--retire", nargs="*", metavar="HASH",
                   help="retire lines by their stable hash")
    s.add_argument("--gen", help="generation id (required with --retire)")

    s = sub.add_parser("discussions", help="demote discussion index lines")
    _add_dry_run(s)
    s.add_argument("--demote", action="store_true",
                   help="demote executed/dropped/failed lines")
    s.add_argument("--gen", required=True, help="generation id")

    s = sub.add_parser("ops", help="sunset unreferenced ops topics; reindex the ops index")
    _add_dry_run(s)
    s.add_argument("--sunset", action="store_true",
                   help="move unreferenced ops topics to docs/retired/ops/")
    s.add_argument("--reindex", action="store_true",
                   help="refresh docs/ops/INDEX.md line counts; add rows for unindexed files")
    s.add_argument("--gen", help="generation id (required with --sunset)")

    a = p.parse_args(argv)
    root = find_root()

    # dispatch
    handlers = {
        "memory": cmd_memory,
        "cookbook": cmd_cookbook,
        "rules": cmd_rules,
        "how-we-work": cmd_how_we_work,
        "discussions": cmd_discussions,
        "ops": cmd_ops,
    }
    return handlers[a.cmd](a, root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("refused: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(1)
