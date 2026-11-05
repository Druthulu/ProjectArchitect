#!/usr/bin/env python
"""history.py -- what a project's past holds: its inventory (3.32 T2) and its docs index (3.32 B2).

    PY tools/history.py [--root DIR] [--project-path PATH] inventory [--out DIR] [--until YYYY-MM-DD]
    PY tools/history.py [--root DIR] [--project-path PATH] index
    PY tools/history.py [--root DIR] mark intake [--incomplete]     pa.json: the PA3 intake is done, or gave up at its cap

index      writes `docs/PRE_PA3_INDEX.md`, no model: one line per pre-PA3 document (dates, path, title), oldest first, a
           big folder one line, then the lines naming the transcript folders and the memory archive. Out: `.claude/`,
           `.claude-state/`, `tools/`, `templates/`, build output, PA3's own files, a doc the install made and a doc
           first added after it (`docs/history/` is the project's own and always in). A rerun keeps any line a person added.

--root: the project (default: the cwd). --project-path: the path whose Claude Code project folders hold the
transcripts (default: --root), for dry runs on a clone whose own path has none; `.claude-state/` still comes from
--root. --out: where inventory.md and inventory.json live (default `<root>/.run/history/`). --until: the last day;
the inventory leaves out what is after it (commits, docs, transcripts that start later, conversation days; the branch
list stays).

inventory  git (commits per month, every branch with its last date and whether HEAD holds it: one `git log --all`,
           one `for-each-ref`), PhaseEnds (`phase-ends/LEGACY_INDEX.md` rows, `PhaseEnd*.md` and `GenerationEnd*.md`
           anywhere, `phase-ends/phase-*/RECAP.md`), every text-like document (DOC_EXTS, tracked or untracked and not
           in a `.gitignore` (`.git/info/exclude` hides a file from git, not from the history), never `.run/`, PRUNE,
           `.claude/worktrees/` or the journal (a PA3 project's log, thousands of files): path, first and last commit
           date from one `git log -M --name-status` pass, an untracked file's modified date for both, chars (bytes when
           not Markdown), title: a Markdown file's first heading, a .txt or .rst file's first line, a .docx's core
           title, a .pdf none; a kind guessed from the path). A folder with COLLAPSE or more non-Markdown documents
           left once the folders under it have collapsed is one `big_folders` row instead: folder, count, bytes, sample
           names. Transcripts (every `<config>/projects/<name>` and `<name>-*` folder, `<name>` = the project path's
           absolute path with each char outside [A-Za-z0-9] as `-`; a `<name>-*` folder whose rows carry a `cwd`
           outside the project path is a sibling project and skipped; `<config>` = $CLAUDE_CONFIG_DIR or ~/.claude),
           `.claude-state/transcripts/`, archives (listed, never extracted), memories, and the reading per month:
           chars / 4 of the phaseend, checkpoint, plan and notes Markdown docs dated by their last commit, plus the
           conversation: CONV_FRACTION of each main-session transcript's bytes / 4 spread evenly over the days it
           spans (tool output dominates a transcript; the conversation is a few percent of it) plus each claude.ai
           conversation file's messages' chars / 4 on their own dates. Those files (tracked or not) are kind
           `conversation`, first/last from their front matter, never their commit dates. A PhaseEnd in
           LEGACY_INDEX.md takes its row's date, everywhere (a `-` date: the first date in the file's first 30 lines,
           else the previous row's date). The install commit (the first to add `.claude/pa.json`) is not history: docs
           are dated ignoring it, a doc it moved inherits its old path's dates, and a doc with no other commit is kind
           `install` (never reading); a commit with the installer's own subject counts too, whichever is older (a project
           can keep pa.json out of git). A doc under `docs/history/` outside the conversations is kind `notes` (an older
           source saved as Markdown, such as a Trello board): it counts as reading, dated by its front matter's `first`
           and `last` when it has them, never by its commit.

Stdlib only; no imports from `pa/`.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import zipfile

_SCRIPT = "history.py"
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DATE_RE = re.compile(r"\b(20[0-9]{2}-[0-9]{2}-[0-9]{2})\b")
JOURNAL_DIR = ".claude-state/journal/"
CONV_FRACTION = 0.04  # share of a transcript's bytes that is conversation, for the estimate
READ_KINDS = ("phaseend", "checkpoint", "plan", "notes")   # the Markdown docs the reading estimate counts
DOC_EXTS = (".md", ".txt", ".rst", ".docx", ".pdf")
COLLAPSE = 40  # non-Markdown documents that make a folder one inventory line (Vantage: 3,113, mostly run output)
SAMPLES = 5  # sample names on that line
ARCHIVE_RE = re.compile(r"\.(7z|zip|tgz|tar(\.[a-z0-9]+)?)(\.[0-9]{3})?$", re.I)     # `.7z.001`: a split archive
PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__"}
CONV_DIR = "docs/history/claude-ai"
MSG_HEAD_RE = re.compile(r"^## ([0-9]{4}-[0-9]{2}-[0-9]{2}T\S+) (developer|claude)$")
INSTALL_SUBJECT = r"^chore: (install )?Project Architect 3\.0"     # pa/install's COMMIT_MSG, install and migration
INDEX_REL = "docs/PRE_PA3_INDEX.md"
INDEX_SKIP = (".claude/", ".claude-state/", "tools/", "templates/", "project-architect-2.0/")
INDEX_BUILD_RE = re.compile(r"(^|/)(bin|obj|node_modules|__pycache__|\.vs)/")
INDEX_PA3 = ("CLAUDE.md", "HOW_WE_WORK.md", "GENERATION_PLAN.md", INDEX_REL, "docs/PRE_PA3_STATE.md",
             "docs/PHASE_HISTORY.md", "docs/project-architect.md")
INDEX_LINE_RE = re.compile(r"^- \S+ `([^`]+)`")   # a document line this tool writes
INDEX_HAND = "## Added by hand"


def _cnote(path, **kw):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import _credit
        _credit.note(path, **kw)
    except Exception:
        pass


def _out(line=""):
    sys.stdout.write(line + "\n")


def _read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=root, capture_output=True)
    except OSError:
        return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


# -- git, docs, PhaseEnds ------------------------------------------------------

def install_commit(root):
    """-> (sha, date) of the oldest commit that added .claude/pa.json or carries the installer's subject (None: neither)."""
    fmt = "--format=%H %aI %at"
    rows = (_git(root, "log", "--diff-filter=A", fmt, "--", ".claude/pa.json")
            + _git(root, "log", fmt, "-E", "--grep=" + INSTALL_SUBJECT)).splitlines()
    rows = [r.split() for r in rows if r.strip()]
    if not rows:
        return None
    sha, date, _at = min(rows, key=lambda r: int(r[2]))
    return sha, date[:10]


def git_info(root, until=""):
    months, dates = {}, []
    for date in _git(root, "log", "--all", "--format=%aI").split():
        if until and date[:10] > until:
            continue
        dates.append(date)
        months[date[:7]] = months.get(date[:7], 0) + 1
    merged = set(_git(root, "for-each-ref", "--merged", "HEAD", "--format=%(refname)",
                      "refs/heads", "refs/remotes").split())
    branches = []
    for line in _git(root, "for-each-ref", "--format=%(refname)%09%(refname:short)%09%(committerdate:iso-strict)",
                     "refs/heads", "refs/remotes").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0].endswith("/HEAD"):
            continue
        branches.append({"name": parts[1], "remote": parts[0].startswith("refs/remotes/"),
                         "last": parts[2][:10], "merged": parts[0] in merged})
    dates.sort()
    return {"commits": len(dates), "first": dates[0][:10] if dates else "", "last": dates[-1][:10] if dates else "",
            "per_month": dict(sorted(months.items())), "branches": branches}


def guess_kind(path):
    p = path.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if p.startswith(CONV_DIR + "/"):
        return "conversation"
    if p.startswith("docs/history/"):
        return "notes"
    if base.startswith(("phaseend", "generationend")) or (p.startswith("phase-ends/phase-") and base == "recap.md"):
        return "phaseend"
    if base.startswith(("current_phase", "phaselog")):
        return "checkpoint"
    if re.search(r"(?:^|[_\-. ])plans?(?:[_\-. ]|$)", base):
        return "plan"
    if "/memory/" in "/" + p or base == "memory.md":
        return "memory"
    if "/retired/" in "/" + p:
        return "retired"
    if "cookbook" in p:
        return "cookbook"
    if "/rules/" in "/" + p or "registry" in base or base.startswith("rules"):
        return "rules"
    if re.search(r"(^|/)(research|reports?)/", p) or "report" in base or "audit" in base:
        return "report"
    if re.search(r"(^|/)designs?/", p) or re.search(r"design|spec|brief|architecture", base):
        return "design"
    return "other"


def _title(text):
    for line in text.splitlines()[:200]:
        if line.startswith("#"):
            return line.lstrip("#").strip()[:120]
    return ""


def _is_md(path):
    return path.lower().endswith(".md")


def _is_doc(path):
    return path.lower().endswith(DOC_EXTS)


def _own_scratch(path):
    """True for what no one wrote as a document: `.run/`, PRUNE's folders, worktrees, the journal."""
    parts = path.split("/")
    return parts[0] == ".run" or bool(PRUNE.intersection(parts)) or path.startswith((".claude/worktrees/", JOURNAL_DIR))


def _doc_title(full):
    """-> a non-Markdown document's title: a .txt or .rst file's first line with a word in it, a .docx's core title."""
    try:
        if full.lower().endswith(".docx"):
            with zipfile.ZipFile(full) as z:
                m = re.search(r"<dc:title>([^<]+)", z.read("docProps/core.xml").decode("utf-8", "replace"))
            return m.group(1).strip()[:120] if m else ""
        if full.lower().endswith((".txt", ".rst")):
            with open(full, encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096).replace("\0", "")     # the NULs of a UTF-16 file
            return next((ln.strip("# \t\ufffd\ufeff")[:120] for ln in head.splitlines() if re.search(r"\w", ln)), "")
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    return ""


def docs_info(root, until=""):
    tracked = [p for p in _git(root, "ls-files", "-z").split("\0") if _is_doc(p) and not _own_scratch(p)]
    untracked = {p for p in _git(root, "ls-files", "--others", "--exclude-per-directory=.gitignore", "-z").split("\0")
                 if _is_doc(p) and not _own_scratch(p)}
    inst = install_commit(root)
    indexed = legacy_index(root)[1]
    first, last, moved, own, seen, sha, date = {}, {}, {}, set(), set(), "", ""
    for line in _git(root, "log", "-M", "--name-status", "--format=%x01%H %aI", "--",
                     *[":(icase)*" + e for e in DOC_EXTS]).splitlines():
        if line.startswith("\x01"):
            sha, date = line[1:].split(" ", 1)
            date = date[:10]
        elif line.strip():
            parts = line.split("\t")
            path = parts[-1]
            if inst and sha == inst[0]:
                own.add(path)
                if parts[0].startswith("R"):
                    moved[path] = parts[1]
                continue
            seen.add(path)
            if not until or date <= until:
                last.setdefault(path, date)   # newest first
                first[path] = date
    for new, old in moved.items():   # the install's moves: a moved doc's dates are its old path's
        if old in seen:
            seen.add(new)
        if old in first:
            first[new] = first[old]
            last.setdefault(new, last[old])
    cdir = os.path.join(root, CONV_DIR)
    if os.path.isdir(cdir):   # the conversation files count before the developer commits them
        tracked += [CONV_DIR + "/" + f for f in os.listdir(cdir) if f.endswith(".md")]
    docs = []
    for p in sorted(untracked.union(tracked)):
        full = os.path.join(root, p)
        isfile, md = os.path.isfile(full), _is_md(p)
        text = _read(full) if md and isfile else ""     # never a whole run-output file
        d = {"path": p, "first": first.get(p, ""), "last": last.get(p, ""),
             "chars": len(text) if md or not isfile else os.path.getsize(full),
             "title": _title(text) if md else _doc_title(full), "kind": guess_kind(p)}
        if d["kind"] == "conversation" and md:
            fm, msgs = parse_conv(text)
            a, b = _local(fm.get("first", "")), _local(fm.get("last", ""))
            d.update(first=a.date().isoformat() if a else "", last=b.date().isoformat() if b else "",
                     title=fm.get("title", ""))
        elif d["kind"] == "notes" and parse_conv(text)[0].get("last"):   # notes saved today from an older source
            fm = parse_conv(text)[0]
            d.update(first=fm.get("first", fm["last"])[:10], last=fm["last"][:10], title=fm.get("title") or d["title"])
        elif p in indexed:
            d.update(first=indexed[p], last=indexed[p])
        elif p in own and p not in seen:   # the install's own file
            d.update(first=inst[1], last=inst[1], kind="install")
        elif p in untracked and not d["first"] and isfile:   # no commit: the file's own date
            mtime = datetime.date.fromtimestamp(os.path.getmtime(full)).isoformat()
            d.update(first=mtime, last=mtime)
        if until and (d["first"] > until or (p in seen and not d["first"])):
            continue
        docs.append(d)
    return docs


def collapse(docs):
    """-> (docs, big_folders): deepest folder first, a folder with COLLAPSE or more non-Markdown documents still
    uncollapsed under it goes to one row (the root counts only the files directly in it). Markdown stays listed."""
    under = {}
    for d in docs:
        if _is_md(d["path"]):
            continue
        if "/" not in d["path"]:
            under.setdefault("", []).append(d)
        folder = d["path"]
        while "/" in folder:
            folder = folder.rsplit("/", 1)[0]
            under.setdefault(folder, []).append(d)
    gone, big = set(), []
    for folder in sorted(under, key=lambda f: (-f.count("/"), f)):
        left = [d for d in under[folder] if d["path"] not in gone]
        if len(left) >= COLLAPSE:
            gone.update(d["path"] for d in left)
            cut_at = len(folder) + 1 if folder else 0
            big.append({"folder": folder or ".", "count": len(left), "bytes": sum(d["chars"] for d in left),
                        "sample": [d["path"][cut_at:] for d in left[:SAMPLES]]})
    return [d for d in docs if d["path"] not in gone], sorted(big, key=lambda b: b["folder"])


def legacy_index(root):
    """-> ([cells] per row, {path: date}) of LEGACY_INDEX.md: the installer's `phase | title | milestone | date | path`
    lines, or a Markdown table (its first row the header). A row's `-` date: the first date in the file's first 30
    lines, else the previous row's date."""
    legacy, dates, prev = [], {}, ""
    idx = os.path.join(root, "phase-ends", "LEGACY_INDEX.md")
    if not os.path.isfile(idx):
        return legacy, dates
    lines = [ln.strip() for ln in _read(idx).splitlines()]
    for ln in [ln for ln in lines if ln.startswith("|")][1:] \
            + [ln for ln in lines if "|" in ln and not ln.startswith(("|", "#"))]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(re.match(r"^:?-+:?$", c) for c in cells if c):
            continue
        legacy.append(cells)
        if len(cells) < 5:
            continue
        date, path = cells[-2], cells[-1].replace("\\", "/")
        if not DAY_RE.match(date):
            full = os.path.join(root, path)
            m = DATE_RE.search("\n".join(_read(full).splitlines()[:30])) if os.path.isfile(full) else None
            date = m.group(1) if m else prev
        if date:
            dates[path] = prev = date
    return legacy, dates


def phaseends_info(root, docs, until=""):
    legacy, dates = legacy_index(root)
    if until:
        legacy = [r for r in legacy if dates.get(r[-1].replace("\\", "/"), "") <= until]
    files = [d["path"] for d in docs if d["kind"] == "phaseend"]
    return {"legacy_index": legacy, "files": files}


# -- transcripts --------------------------------------------------------------

def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def project_name(root):
    return re.sub(r"[^A-Za-z0-9]", "-", root)


def _rows(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _first_cwd(path):
    for i, row in enumerate(_rows(path)):
        if isinstance(row.get("cwd"), str):
            return row["cwd"]
        if i > 200:
            break
    return None


def _under(path, root):
    norm = (lambda s: os.path.normcase(os.path.abspath(s)))
    p, r = norm(path), norm(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _ts_span(path):
    """-> (first, last) row timestamps of a transcript ('' when none)."""
    first = ""
    for i, row in enumerate(_rows(path)):
        if isinstance(row.get("timestamp"), str):
            first = row["timestamp"]
            break
        if i > 200:
            break
    last = ""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 65536))
        tail = fh.read().decode("utf-8", "replace").splitlines()
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("timestamp"), str):
            last = row["timestamp"]
            break
    return first, last or first


def _main_files(folder):
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = sorted(d for d in dns if d not in ("subagents", "memory"))
        out += [os.path.join(dp, f) for f in sorted(fns) if f.endswith(".jsonl")]
    return out


def project_folders(root):
    """-> [folder] of this project's Claude Code sessions (root: the project path; its and its subfolders')."""
    pdir = os.path.join(config_dir(), "projects")
    if not os.path.isdir(pdir):
        return []
    name = project_name(root)
    fold = (lambda s: s.lower()) if os.name == "nt" else (lambda s: s)
    out = []
    for d in sorted(os.listdir(pdir)):
        full = os.path.join(pdir, d)
        if not os.path.isdir(full):
            continue
        if fold(d) == fold(name):
            out.append(full)
        elif fold(d).startswith(fold(name) + "-"):
            files = _main_files(full)
            cwd = _first_cwd(files[0]) if files else None
            if cwd is None or _under(cwd, root):
                out.append(full)
    return out


def archives(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in PRUNE)
        rel = os.path.relpath(dp, root).replace("\\", "/")
        in_state = rel == ".claude-state" or rel.startswith(".claude-state/")
        for f in sorted(fns):
            if ARCHIVE_RE.search(f) and (in_state or "transcript" in f.lower()):
                p = os.path.join(dp, f)
                out.append({"path": os.path.relpath(p, root).replace("\\", "/"), "bytes": os.path.getsize(p)})
    return out


def _local(ts):
    """ISO timestamp -> local datetime (None when unparseable)."""
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None


def _starts_after(item, until):
    dt = _local(item["first"])
    return bool(until and dt and dt.date().isoformat() > until)


def transcripts_info(root, ppath=None, until=""):
    folders = []
    for folder in project_folders(ppath or root):
        files = [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".jsonl")]
        items = []
        for f in files:
            a, b = _ts_span(f)
            items.append({"file": os.path.basename(f), "bytes": os.path.getsize(f), "first": a, "last": b})
        folders.append({"folder": os.path.basename(folder), "files": [i for i in items if not _starts_after(i, until)]})
    state = []
    tdir = os.path.join(root, ".claude-state", "transcripts")
    if os.path.isdir(tdir):
        for f in _main_files(tdir):
            a, b = _ts_span(f)
            state.append({"file": os.path.relpath(f, root).replace("\\", "/"), "bytes": os.path.getsize(f),
                          "first": a, "last": b})
    state = [i for i in state if not _starts_after(i, until)]
    return {"config": config_dir(), "name": project_name(ppath or root), "folders": folders, "state": state,
            "archives": archives(root)}


def memories_info(root, folders):
    out = {}
    for folder in folders:
        m = os.path.join(config_dir(), "projects", folder, "memory")
        if os.path.isdir(m):
            out["projects/%s/memory" % folder] = len([f for f in os.listdir(m) if f.endswith(".md")])
    m = os.path.join(root, ".claude-state", "memory")
    if os.path.isdir(m):
        out[".claude-state/memory"] = len([f for f in os.listdir(m) if f.endswith(".md")])
    return out


# -- reading estimate ---------------------------------------------------------

def estimate_days(transcripts):
    out = {}
    items = [it for fo in transcripts["folders"] for it in fo["files"]] + transcripts["state"]
    for it in items:
        a, b = _local(it["first"]), _local(it["last"])
        if a is None:
            continue
        b = b or a
        days = (b.date() - a.date()).days + 1
        share = it["bytes"] * CONV_FRACTION / 4 / max(days, 1)
        for i in range(max(days, 1)):
            d = (a.date() + datetime.timedelta(days=i)).isoformat()
            out[d] = out.get(d, 0) + share
    return {d: int(v) for d, v in out.items()}


def reading(docs, conv):
    """-> {date: {"docs": tokens, "paths": [...], "conversation": tokens}}."""
    days = {}
    for d in docs:
        if d["kind"] in READ_KINDS and d["last"] and _is_md(d["path"]):
            e = days.setdefault(d["last"], {"docs": 0, "paths": [], "conversation": 0})
            e["docs"] += d["chars"] // 4
            e["paths"].append(d["path"])
    for date, t in conv.items():
        days.setdefault(date, {"docs": 0, "paths": [], "conversation": 0})["conversation"] += t
    return dict(sorted(days.items()))


# -- inventory ----------------------------------------------------------------

def _until_days(days, until):
    return {d: t for d, t in days.items() if not until or d <= until}


def cmd_inventory(root, out, ppath=None, until=""):
    if until and not DAY_RE.match(until):
        _out("refused: %s is not YYYY-MM-DD" % until)
        return 1
    git = git_info(root, until)
    docs, big = collapse(docs_info(root, until))
    pes = phaseends_info(root, docs, until)
    tr = transcripts_info(root, ppath, until)
    mem = memories_info(root, [f["folder"] for f in tr["folders"]])
    conv = estimate_days(tr)
    for d, t in conv_doc_days(root, docs).items():
        conv[d] = conv.get(d, 0) + t
    days = reading(docs, _until_days(conv, until))
    months = {}
    for date, e in days.items():
        m = months.setdefault(date[:7], {"docs": 0, "conversation": 0})
        m["docs"] += e["docs"]
        m["conversation"] += e["conversation"]
    inv = {"root": root, "until": until, "git": git, "phaseends": pes, "docs": docs, "big_folders": big,
           "transcripts": tr, "memories": mem, "days": days, "months": dict(sorted(months.items()))}
    _write(os.path.join(out, "inventory.json"), json.dumps(inv, indent=1, sort_keys=True) + "\n")
    md = render_inventory(inv)
    _write(os.path.join(out, "inventory.md"), md)
    _cnote(root, script=_SCRIPT, sub="inventory", whole=True, file_chars=sum(d["chars"] for d in docs if _is_md(d["path"])),
           root=root)
    _cnote(os.path.join(out, "inventory.md"), script=_SCRIPT, sub="inventory", whole=True, added=len(md), root=root)
    files = [it for fo in tr["folders"] for it in fo["files"]]
    total = sum(m["docs"] + m["conversation"] for m in months.values())
    _out("git: %d commits (%s .. %s), %d branches" % (git["commits"], git["first"], git["last"], len(git["branches"])))
    _out("phaseends: %d files, %d legacy index rows" % (len(pes["files"]), len(pes["legacy_index"])))
    _out("docs: %d listed, %d of them Markdown (%d the install's own); %d big folders, %d files"
         % (len(docs), sum(1 for d in docs if _is_md(d["path"])), sum(1 for d in docs if d["kind"] == "install"),
            len(big), sum(b["count"] for b in big)))
    _out("claude.ai: %d conversation files" % sum(1 for d in docs if d["kind"] == "conversation"))
    _out("transcripts: %d folders, %d sessions, %d bytes; %d in .claude-state/transcripts; %d archives"
         % (len(tr["folders"]), len(files), sum(i["bytes"] for i in files), len(tr["state"]), len(tr["archives"])))
    _out("memories: %d" % sum(mem.values()))
    _out("reading: ~%d tokens over %d months" % (total, len(months)))
    _out("wrote: %s, %s" % (os.path.join(out, "inventory.md"), os.path.join(out, "inventory.json")))
    return 0


def render_inventory(inv):
    g, tr = inv["git"], inv["transcripts"]
    L = ["# History inventory", "", "Root: `%s`%s" % (inv["root"], ", until %s" % inv["until"] if inv["until"] else ""),
         "", "## git", "",
         "- commits: %d (%s .. %s)" % (g["commits"], g["first"], g["last"])]
    L += ["- %s: %d" % (m, n) for m, n in g["per_month"].items()]
    L += ["", "Branches:"]
    L += ["- `%s`%s last %s, %s" % (b["name"], " (remote)" if b["remote"] else "", b["last"],
                                     "merged" if b["merged"] else "not merged") for b in g["branches"]]
    L += ["", "## PhaseEnds", ""]
    L += ["- legacy index: %s" % " | ".join(r) for r in inv["phaseends"]["legacy_index"]]
    L += ["- `%s`" % p for p in inv["phaseends"]["files"]]
    L += ["", "## docs", ""]
    L += ["- `%s` %s..%s %d %s, %s: %s" % (d["path"], d["first"], d["last"], d["chars"],
                                            "chars" if _is_md(d["path"]) else "bytes", d["kind"], d["title"])
          for d in inv["docs"]]
    L += ["", "## big folders", ""]
    L += ["- `%s/`: %d files, %d bytes, such as %s" % (b["folder"], b["count"], b["bytes"],
                                                       ", ".join("`%s`" % s for s in b["sample"]))
          for b in inv["big_folders"]]
    L += ["", "## transcripts", "", "Config: `%s`; project folder name `%s`" % (tr["config"], tr["name"])]
    for fo in tr["folders"]:
        fs = fo["files"]
        firsts = sorted(f["first"] for f in fs if f["first"])
        lasts = sorted(f["last"] for f in fs if f["last"])
        L.append("- `%s`: %d sessions, %d bytes, %s .. %s" % (fo["folder"], len(fs), sum(f["bytes"] for f in fs),
                                                             firsts[0] if firsts else "", lasts[-1] if lasts else ""))
    L += ["- `%s`: %d bytes, %s .. %s" % (s["file"], s["bytes"], s["first"], s["last"]) for s in tr["state"]]
    L += ["- archive `%s`: %d bytes (not extracted)" % (a["path"], a["bytes"]) for a in tr["archives"]]
    L += ["", "## memories", ""]
    L += ["- `%s`: %d" % (k, n) for k, n in sorted(inv["memories"].items())]
    L += ["", "## reading estimate (tokens)", ""]
    L += ["- %s: docs %d, conversation %d" % (m, e["docs"], e["conversation"]) for m, e in inv["months"].items()]
    return "\n".join(L) + "\n"


# -- the docs index -------------------------------------------------------------

def _phase_ends_dir(root):
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("phase_ends_dir") or "phase-ends"
    except (OSError, ValueError, AttributeError):
        return "phase-ends"


def index_docs(root):
    """-> (docs, big_folders) of the index: docs_info and collapse, less everything that is not a pre-PA3 document."""
    ped = _phase_ends_dir(root).rstrip("/")
    own = set(INDEX_PA3) | {"%s/%s" % (ped, n) for n in ("README.md", "LEGACY_INDEX.md", "TASK_INDEX.md", "RESEARCH_INDEX.md")}
    inst = install_commit(root)
    made = set()     # first added by the install, which also retires files; git shows a retired CLAUDE.md as an add
    if inst:
        made.update(p for p in _git(root, "show", "-M", "--diff-filter=A", "--name-only", "--format=", inst[0]).splitlines() if p)
    added = set()    # first added after it; the curator's memories moved into rules/ or docs/ops/ count
    for line in _git(root, "log", "--reverse", "-M", "--diff-filter=AR", "--name-status", "--format=",
                     inst[0] + "..HEAD").splitlines() if inst else ():
        parts = line.split("\t")
        if parts[0] == "A":
            added.add(parts[-1])
        elif len(parts) == 3 and (parts[1] in added or parts[1] in made or parts[1].startswith(INDEX_SKIP)):
            added.add(parts[2])

    def keep(d):
        p = d["path"]
        if p in own or p.startswith(INDEX_SKIP + (ped + "/current/", ped + "/phase-")) or INDEX_BUILD_RE.search(p):
            return False
        if p.startswith("docs/history/"):
            return True
        if p in added or (inst and d["first"] > inst[1]):
            return False
        return p.startswith("docs/retired/") or not (d["kind"] == "install" or p in made)

    return collapse([d for d in docs_info(root) if keep(d)])


def _elsewhere(root, ppath):
    """-> the index's last lines: where the transcripts and the memory archive are."""
    pdir = os.path.join(config_dir(), "projects").replace("\\", "/")
    names = sorted({project_name(ppath)} | {os.path.basename(f) for f in project_folders(ppath)})
    out = ["- Claude Code transcripts: `%s/%s`" % (pdir, n) for n in names]
    if os.path.isdir(os.path.join(root, ".claude-state", "transcripts")):
        out.append("- Claude Code transcripts kept with the project: `.claude-state/transcripts/`")
    out += ["- Transcript archive (not extracted): `%s`" % a["path"] for a in archives(root)]
    out.append("- Memory archive: `.claude-state/memory/gen*.md`")
    return out


def _hand_lines(root, text, listed):
    """-> the lines of an index already on disk a person added: anything this tool does not write. A document line
    goes when its path is listed anew or the file is gone."""
    fixed = {INDEX_HAND, "## Folders of many files, one line each", "## Elsewhere"}
    out = []
    for ln in text.splitlines():
        m = INDEX_LINE_RE.match(ln)
        if (not ln.strip() or ln in fixed or ln.startswith(("# Documents from before PA3", "One line per document:"))
                or ln.startswith(("- Claude Code transcripts", "- Transcript archive", "- Memory archive"))
                or re.match(r"^- `[^`]+`: [0-9]+ files$", ln)
                or (m and (m.group(1) in listed or not os.path.exists(os.path.join(root, m.group(1)))))):
            continue
        out.append(ln)
    return out


def index_text(root, ppath, old=""):
    """-> (text, documents, big folders) of the index; `old`: the file already on disk."""
    docs, big = index_docs(root)
    docs.sort(key=lambda d: (d.get("first") or "", d["path"]))
    out = ["# Documents from before PA3", "",
           "One line per document: dates, path, title. Nobody has read these for you. This file is long: grep it.", ""]
    for d in docs:
        dates = d.get("first") or "?"
        if d.get("last") and d["last"] != d.get("first"):
            dates += ".." + d["last"]
        out.append(("- %s `%s` %s" % (dates, d["path"], (d.get("title") or "").strip()[:80])).rstrip())
    if big:
        out += ["", "## Folders of many files, one line each", ""]
        out += ["- `%s`: %d files" % (b["folder"], b["count"]) for b in big]
    hand = _hand_lines(root, old, {d["path"] for d in docs})
    if hand:
        out += ["", INDEX_HAND, ""] + hand
    out += ["", "## Elsewhere", ""] + _elsewhere(root, ppath)
    return "\n".join(out) + "\n", len(docs), len(big)


def cmd_index(root, ppath):
    path = os.path.join(root, *INDEX_REL.split("/"))
    old = _read(path) if os.path.isfile(path) else ""
    text, n, big = index_text(root, ppath, old)
    _write(path, text)
    _cnote(path, script=_SCRIPT, sub="index", whole=True, added=len(text), root=root)
    _out("index: %d documents, %d big folders, %d chars: %s" % (n, big, len(text), INDEX_REL))
    return 0


# -- conversation files ---------------------------------------------------------

def parse_conv(text):
    """-> (front matter {key: str}, [(ts, who, text)]) of a conversation file."""
    fm, msgs = {}, []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    if lines and lines[0] == "---":
        for i in range(1, len(lines)):
            if lines[i] == "---":
                break
            k, sep, v = lines[i].partition(": ")
            if sep:
                v = v.strip()
                if v.startswith('"'):
                    try:
                        v = json.loads(v)
                    except ValueError:
                        pass
                fm[k.strip()] = v
    cur = None
    for ln in lines[i + 1:]:
        m = MSG_HEAD_RE.match(ln)
        if m:
            cur = [m.group(1), m.group(2), []]
            msgs.append(cur)
        elif cur is not None:
            cur[2].append(ln)
    return fm, [(ts, who, "\n".join(body).strip("\n")) for ts, who, body in msgs]


def conv_doc_days(root, docs):
    """-> {date: tokens} of the conversation files' messages, each on its own local date."""
    days = {}
    for d in docs:
        if d["kind"] != "conversation":
            continue
        for ts, _who, text in parse_conv(_read(os.path.join(root, d["path"])))[1]:
            dt = _local(ts)
            if dt:
                k = dt.date().isoformat()
                days[k] = days.get(k, 0) + len(text) // 4
    return days


def cmd_mark(root, key, incomplete=False):
    """3.32 B9: the mod's one-time intake (`"pending"` from the install) ends once: `intake: "done"`, or `"incomplete"`
    when it reached its cap with work owed."""
    path = os.path.join(root, ".claude", "pa.json")
    conf = json.loads(_read(path))
    conf[key] = "incomplete" if incomplete else "done"
    _write(path, json.dumps(conf, indent=2) + "\n")
    _out("%s: %s" % (key, conf[key]))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="history.py", description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--project-path", default=None,
                    help="the path whose Claude Code projects hold the transcripts (default: --root)")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("inventory", help="inventory.md and inventory.json")
    s.add_argument("--out", default=None)
    s.add_argument("--until", default="", help="last day")
    sub.add_parser("index", help="docs/PRE_PA3_INDEX.md")
    s = sub.add_parser("mark", help="pa.json: the PA3 intake has ended")
    s.add_argument("key", choices=("intake",))
    s.add_argument("--incomplete", action="store_true", help="it reached its cap with work owed")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    ppath = os.path.abspath(a.project_path) if a.project_path else root
    if a.command == "index":
        return cmd_index(root, ppath)
    if a.command == "mark":
        return cmd_mark(root, a.key, a.incomplete)
    out = os.path.abspath(a.out) if a.out else os.path.join(root, ".run", "history")
    return cmd_inventory(root, out, ppath, a.until)


if __name__ == "__main__":
    sys.exit(main())
