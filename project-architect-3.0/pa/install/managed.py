"""The managed-file record ``.claude/pa3-managed.json`` (3.9.7 T6).

One entry per packaged file the installer placed (agents, the project-architect skill,
commands, tools, templates and, since 3.11 T25, ``docs/project-architect.md``): the
``version:`` it was installed at and the sha256 of its
bytes (CRLF-normalised, cookbook C0046), so a later refresh can tell a user's edit
from an upstream change.

Public:
    sha(data)               sha256 hex of ``data`` with CRLF folded to LF
    base_of(text)           the ``version:`` value of a leading frontmatter block, or None
    record(env, results)    build the record from ``(rel, place result)`` pairs and write it
    add(env, rel)           record one file placed after P3 (the migration's doc, 3.11 T25)
    bump(version)           ``V`` -> ``V+u1``, ``V+uK`` -> ``V+u(K+1)`` (V = ``N`` or ``3.10.N``), else None
    drift(root)             ``[(rel, base, sha8, [task ids])]`` for files that differ (T7)
    stamp(root, rel)        bump the file's ``version:``, record the user's copy (T7)
    decline(root, rel)      record the user's copy as declined (T7)
    Refresh(env)            a ``--project`` refresh against the prior record (T8):
                            ``.classify(rel, cur, new)`` same|updated|user|merged|replaced|conflict
                            (3.10 T15: past package versions from the package's git history),
                            ``.stage()`` writes ``.claude/pa3-upgrade/`` for the conflicts,
                            ``.apply()`` applies the ``| resolve:`` verdicts first (3.10 T20)
    parse_resolutions(text) ``{rel: (keep|upstream|merged, path|None)}`` from UPGRADE.md lines
    history_repo(env)       ``(git work tree, path prefix)`` holding the package history, or None

3.10.6 T2: agents are hashed as rendered at the run's ladder preset; an agent the tier leaves
out is recorded ``{"absent": "tier", ...}`` (sha None once deleted, the prior values while kept).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time

RECORD = ".claude/pa3-managed.json"
FORMAT = 1
UPGRADE_DIR = ".claude/pa3-upgrade"
UPGRADE = UPGRADE_DIR + "/UPGRADE.md"
UPGRADE_HEADER = ("# PA3 upgrade: agents and skills you edited that the package also changed"
                  " (kept yours; the router's upgrade expert merges them)\n")
SKILL = ".claude/skills/project-architect/SKILL.md"
DOC = "docs/project-architect.md"       # 3.11 T25: the methodology, from the package root's file
PKG_MAP = ((".claude/agents/", "agents/"), (".claude/commands/", "commands/"),
           ("tools/", "tools/"), ("templates/", "templates/"))   # rel prefix -> package path
REPLACE = ("tools/", ".claude/commands/", "templates/", DOC)   # no base or overlap: upstream wins
RESTART = (".claude/agents/", ".claude/skills/", ".claude/commands/")
WROTE = ("upstream-changed", "merged", "replaced", "created", "forced")


def yours_rel(rel):
    """Where a replaced file's pre-refresh bytes are kept."""
    return "%s/%s.yours" % (UPGRADE_DIR, rel)


def _git_bytes(repo, args, data=None):
    """``(returncode, stdout bytes)`` of git in ``repo``; never raises."""
    exe = shutil.which("git")
    if not exe:
        return 127, b""
    try:
        proc = subprocess.run((exe, "-C", repo) + tuple(args), input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1, b""
    return proc.returncode, proc.stdout or b""


def _same_path(a, b):
    norm = lambda p: os.path.normcase(os.path.realpath(p))      # noqa: E731
    return norm(a) == norm(b)


def history_repo(env):
    """``(git work tree, path prefix)`` holding the package's history, else None (never raises).

    The package's own work tree when ``env.pkg`` is its top or its ``project-architect-3.0/``,
    else the VERSION ``source:`` clone."""
    from .project import _version_source, git_toplevel

    try:
        pkg = env.pkg
        top = git_toplevel(pkg)
        if top:
            if _same_path(pkg, os.path.join(top, "project-architect-3.0")):
                return top, "project-architect-3.0/"
            if _same_path(pkg, top):
                return top, ""
        src = _version_source(getattr(env, "pa3_dir", None) or pkg)
        top = git_toplevel(src) if src and os.path.isdir(src) else None
        if top:
            return top, "project-architect-3.0/"
    except Exception:
        pass
    return None


def _package_git(env):
    """The package's short HEAD, or None."""
    hist = history_repo(env)
    if not hist:
        return None
    rc, out = _git_bytes(hist[0], ("rev-parse", "--short", "HEAD"))
    return (out.decode("utf-8", "replace").strip() or None) if rc == 0 else None


def _package_phase(pkg):
    """The first ``## <id>`` token of ``<pkg>/CHANGES.md``, or None."""
    try:
        with open(os.path.join(pkg, "CHANGES.md"), "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("## ") and line[3:].split():
                    return line[3:].split()[0]
    except (OSError, ValueError):
        pass
    return None


def _package_version(pkg):
    """The release version in ``<pkg>/VERSION`` (I11), else this installer's ``__version__``."""
    from .. import __version__, parse_version
    try:
        with open(os.path.join(pkg, "VERSION"), "r", encoding="utf-8") as fh:
            return parse_version(fh.readline()) or __version__
    except (OSError, ValueError):
        return __version__


def sha(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def base_of(text):
    """The first ``version:`` line inside a leading ``---`` frontmatter block, else None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


def _lf(data):
    return data.replace(b"\r\n", b"\n")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record(env, results, upgrade=None):
    """Write the record for this run's placed managed files; untouched when unchanged.
    A ``merged`` file (T15) records the package bytes as its base and the merge as the user's."""
    from . import note
    from .project import _now_iso_utc, _pa3_version, write_changed

    old = _load(env.path(RECORD)) or {}
    prior = old.get("files") if isinstance(old.get("files"), dict) else {}
    files = {}
    for rel, result in results:
        if upgrade is not None and rel in upgrade.resolved:     # T20: an applied resolution
            verdict, new, out = upgrade.resolved[rel]
            files[rel] = {"base": base_of(new.decode("utf-8", "replace")), "sha": sha(new),
                          "user": None}
            if verdict != "upstream":
                user = (prior.get(rel) or {}).get("user")
                mark = user.get("mark") if isinstance(user, dict) and verdict == "keep" else None
                files[rel]["user"] = {"sha": sha(out), "mark": mark, "state": verdict,
                                      "at": _iso()}
        elif result == "merged" and upgrade is not None and rel in upgrade.merged:
            new, merged = upgrade.merged[rel]
            files[rel] = {"base": base_of(new.decode("utf-8", "replace")), "sha": sha(new),
                          "user": {"sha": sha(merged), "mark": None, "state": "merged",
                                   "at": _iso()}}
        elif result in ("created", "same", "forced", "updated", "replaced"):
            with open(env.path(rel), "rb") as fh:
                data = fh.read()
            files[rel] = {"base": base_of(data.decode("utf-8", "replace")),
                          "sha": sha(data), "user": None}
        elif result == "edited":
            if rel in prior:
                files[rel] = prior[rel]
            else:
                note("= %s (base unknown: differs from the package, not recorded)" % rel)
    if upgrade is not None:
        files.update(upgrade.absent)        # 3.10.6 T2: agents the plan tier leaves out
    package = _pa3_version(env.pkg)
    git, phase = _package_git(env), _package_phase(env.pkg)
    upstream_version = _package_version(env.pkg)
    if (old and old.get("files") == files and old.get("package") == package
            and old.get("git") == git and old.get("phase") == phase
            and old.get("upstream_version") == upstream_version):
        return "same"
    body = {"format": FORMAT, "package": package, "installed": _now_iso_utc(),
            "git": git, "phase": phase, "upstream_version": upstream_version, "files": files}
    text = json.dumps(body, sort_keys=True, indent=2) + "\n"
    return write_changed(env, RECORD, text, backup=False)[0]


def add(env, rel):
    """Record one placed file in this run's record: the doc a migration places after P3 wrote
    the record (3.11 T25), so a second run has nothing left to add."""
    from .project import write_changed

    if env.dry_run or not os.path.isfile(env.path(rel)):
        return None
    rec = _load(env.path(RECORD))
    if not isinstance(rec, dict):
        return None
    with open(env.path(rel), "rb") as fh:
        data = fh.read()
    files = rec.get("files") if isinstance(rec.get("files"), dict) else {}
    files[rel] = {"base": base_of(data.decode("utf-8", "replace")), "sha": sha(data), "user": None}
    rec["files"] = files
    return write_changed(env, RECORD, json.dumps(rec, sort_keys=True, indent=2) + "\n",
                         backup=False)[0]


# --------------------------------------------------------------------------- drift, stamp, decline (T7)

_BUMP_RE = re.compile(r"^(\d+|\d+(?:\.\d+){2,})(?:\+u(\d+))?$")   # 3.10: plain N or dotted a.b.N


def bump(version):
    """``V`` -> ``V+u1``, ``V+uK`` -> ``V+u(K+1)`` for V plain ``N`` or dotted ``a.b.N``; else None."""
    m = _BUMP_RE.match(str(version).strip()) if version is not None else None
    if not m:
        return None
    return "%s+u%d" % (m.group(1), int(m.group(2) or 0) + 1)


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _files(root):
    """``(record path, record, files)``; files is ``{}`` when the record is missing or bad."""
    path = os.path.join(root, RECORD)
    data = _load(path) or {}
    files = data.get("files")
    return path, data, files if isinstance(files, dict) else {}


def _write(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, sort_keys=True, indent=2) + "\n")


def _edit_tasks(rels):
    """``{rel: [task ids]}`` from the ledger's ``managed_edit`` events (distinct, first-seen order)."""
    out = {}
    try:
        from .. import db, paths

        p = paths.db_path()
        if not os.path.exists(p):
            return out
        conn = db.connect(p, readonly=True)
        try:
            rows = conn.execute("SELECT detail_json FROM events WHERE kind='managed_edit'"
                                " ORDER BY id").fetchall()
        finally:
            db.close(conn)
    except Exception:
        return out
    for (raw,) in rows:
        try:
            d = json.loads(raw or "null")
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("path") not in rels or not d.get("task"):
            continue
        tasks = out.setdefault(d["path"], [])
        if d["task"] not in tasks:
            tasks.append(d["task"])
    return out


def drift(root):
    """``[(rel, base, sha8, [task ids])]`` for listed files whose bytes differ from
    ``user.sha`` (when set) else ``sha``; missing files are skipped."""
    _path, _data, files = _files(root)
    found = []
    for rel in sorted(files):
        ent = files[rel]
        if not isinstance(ent, dict):
            continue
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                cur = sha(fh.read())
        except OSError:
            continue
        user = ent.get("user")
        ref = user.get("sha") if isinstance(user, dict) else ent.get("sha")
        if ref is None and ent.get("absent"):
            continue                    # 3.10.6 T2: absent at this tier, nothing recorded to differ from
        if cur != ref:
            found.append((rel, ent.get("base"), cur[:8]))
    tasks = _edit_tasks({f[0] for f in found}) if found else {}
    return [(rel, base, s8, tasks.get(rel, [])) for rel, base, s8 in found]


def _bump_text(text):
    """``(text with its frontmatter version: line bumped, new version)`` or ``(text, None)``."""
    lines = text.splitlines(True)
    if not lines or lines[0].strip() != "---":
        return text, None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            break
        if line.startswith("version:"):
            new = bump(line.split(":", 1)[1].strip())
            if not new:
                break
            body = line.rstrip("\r\n")
            lines[i] = "version: " + new + line[len(body):]
            return "".join(lines), new
    return text, None


def _entry(root, rel):
    path, data, files = _files(root)
    if rel not in files or not isinstance(files[rel], dict):
        raise KeyError("%s is not listed in %s" % (rel, RECORD))
    if files[rel].get("absent") and not os.path.isfile(os.path.join(root, rel)):
        raise KeyError("%s is absent at this plan tier" % rel)     # 3.10.6 T2
    return path, data, files[rel]


def stamp(root, rel):
    """Keep the user's copy: bump its ``version:`` (``+uN``) and record it. Returns the user entry."""
    path, data, ent = _entry(root, rel)
    fp = os.path.join(root, rel)
    with open(fp, "rb") as fh:
        raw = fh.read()
    text, new = _bump_text(raw.decode("utf-8"))
    if new:
        raw = text.encode("utf-8")
        with open(fp, "wb") as fh:
            fh.write(raw)
    ent["user"] = {"sha": sha(raw), "mark": new[new.index("+u"):] if new else None,
                   "state": "stamped", "at": _iso()}
    _write(path, data)
    return ent["user"]


def decline(root, rel):
    """Record the user's copy as declined: drift stays silent until its bytes change."""
    path, data, ent = _entry(root, rel)
    with open(os.path.join(root, rel), "rb") as fh:
        cur = sha(fh.read())
    ent["user"] = {"sha": cur, "mark": None, "state": "declined", "at": _iso()}
    _write(path, data)
    return ent["user"]


def parse_resolutions(text):
    """``{rel: (verdict, path|None)}`` from UPGRADE.md ``- <rel> | … | resolve: <verdict>`` lines
    (3.10 T20); verdict ``keep`` | ``upstream`` | ``merged`` (path relative to the root); file order."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("- ") or " | resolve: " not in line:
            continue
        rel = line[2:].split(" | ", 1)[0].strip()
        tail = line.rsplit(" | resolve: ", 1)[1].strip()
        if tail in ("keep", "upstream"):
            out[rel] = (tail, None)
        elif tail.startswith("merged:") and tail[7:].strip():
            out[rel] = ("merged", tail[7:].strip())
    return out


# --------------------------------------------------------------------------- refresh (T8)

class Refresh(object):
    """Classify a ``--project`` refresh of the managed groups against the prior record."""

    def __init__(self, env):
        self.env = env
        files = (_load(env.path(RECORD)) or {}).get("files")
        self.prior = files if isinstance(files, dict) else {}
        self.conflicts = []         # (rel, installed bytes, new bytes, prior entry or None)
        self.kinds = {}             # rel -> the kind classify returned (T10 class lines)
        self.merged = {}            # rel -> (new bytes LF, merged bytes LF)  (T15)
        self.replaced = {}          # rel -> the installed bytes LF, kept as ``.yours`` (T15)
        self.resolved = {}          # rel -> (verdict, new bytes LF, file bytes LF)  (T20)
        self.rules = None           # section 11 for past skill versions (copy_files sets it)
        self._repo = False          # history_repo(env), resolved on first lookup
        self._hist = {}             # rel -> {sha: transformed past package bytes}
        self.absent = {}            # rel -> record entry of an agent the tier leaves out (3.10.6 T2)
        self.dropped = []           # absent agents deleted this run (3.10.6 T2)

    def classify(self, rel, cur_bytes, data):
        """``same`` | ``updated`` (upstream-only) | ``user`` (user-only) | ``merged`` |
        ``replaced`` | ``conflict``."""
        self.kinds[rel] = kind = self._classify(rel, cur_bytes, data)
        return kind

    def _classify(self, rel, cur_bytes, data):
        cur, new = sha(cur_bytes), sha(data)
        if cur == new:
            return "same"
        ent = self.prior.get(rel)
        ent = ent if isinstance(ent, dict) else None
        if ent and cur == ent.get("sha"):
            return "updated"
        if ent and new == ent.get("sha"):
            return "user"
        hist = self._history(rel)
        if cur in hist:
            return "updated"            # an old package version the user never edited
        if ent and ent.get("sha") in hist:
            merged = self._merge(cur_bytes, hist[ent["sha"]], data)
            if merged is not None:
                self.merged[rel] = (_lf(data), merged)
                return "merged"
        if rel.startswith(REPLACE):
            self.replaced[rel] = _lf(cur_bytes)
            return "replaced"
        self.conflicts.append((rel, cur_bytes, data, ent))
        return "conflict"

    def save_yours(self, rel):
        """Write a replaced file's pre-refresh bytes to :func:`yours_rel`."""
        self._put(yours_rel(rel), self.replaced[rel])

    def _pkg_path(self, rel):
        if rel == SKILL:
            return "skills/project-architect/SKILL.template.md"
        if rel == DOC:
            return "project-architect-3.0.md"
        for pre, dst in PKG_MAP:
            if rel.startswith(pre):
                return dst + rel[len(pre):]
        return None

    def _transform(self, rel, blob):
        """A past package blob as the install would have placed it (then hashed)."""
        from .project import _fill, project_rules, skill_text

        if rel == SKILL:
            if self.rules is None:
                self.rules = project_rules(self.env)
            text = skill_text(self.env, self.rules, text=blob.decode("utf-8", "replace"))
            return text.encode("utf-8")
        if rel.startswith(".claude/commands/"):
            return _fill(blob.decode("utf-8", "replace"), {"PY": self.env.py_exe}).encode("utf-8")
        if rel.startswith(".claude/agents/") and rel.endswith(".md"):   # 3.10.6 T2: current preset
            from . import ladder
            text = ladder.render(rel.rsplit("/", 1)[1][:-3], blob.decode("utf-8", "replace"),
                                 getattr(self.env, "ladder_preset", None),
                                 getattr(self.env, "ladder_override", None))
            return blob if text is None else text.encode("utf-8")
        return blob

    def _history(self, rel):
        """``{sha: bytes}`` of every past package version of ``rel`` (cached; ``{}`` offline)."""
        if rel not in self._hist:
            self._hist[rel] = out = {}
            try:
                self._lookup(rel, out)
            except Exception:
                pass
        return self._hist[rel]

    def _lookup(self, rel, out):
        src = self._pkg_path(rel)
        if self._repo is False:
            self._repo = history_repo(self.env)
        if not src or not self._repo:
            return
        top, prefix = self._repo
        path = prefix + src
        rc, raw = _git_bytes(top, ("rev-list", "HEAD", "--", path))
        commits = raw.decode("ascii", "replace").split() if rc == 0 else []
        if not commits:
            return
        req = "".join("%s:%s\n" % (c, path) for c in commits).encode("utf-8")
        rc, raw = _git_bytes(top, ("cat-file", "--batch"), req)
        pos = 0
        while rc == 0 and pos < len(raw):
            nl = raw.find(b"\n", pos)
            if nl < 0:
                break
            head = raw[pos:nl].split()
            pos = nl + 1
            if len(head) == 3 and head[1] == b"blob":
                size = int(head[2])
                data = self._transform(rel, raw[pos:pos + size])
                out.setdefault(sha(data), _lf(data))
                pos += size + 1

    def _merge(self, cur, base, new):
        """``git merge-file -p`` of cur/base/new (LF); the merged bytes when clean, else None."""
        scratch = self.env.path(".run/pa3-merge")
        try:
            os.makedirs(scratch, exist_ok=True)
            names = []
            for name, data in (("cur", cur), ("base", base), ("new", new)):
                names.append(os.path.join(scratch, name))
                with open(names[-1], "wb") as fh:
                    fh.write(_lf(data))
            rc, out = _git_bytes(scratch, ("merge-file", "-p") + tuple(names))
            return _lf(out) if rc == 0 else None
        except OSError:
            return None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def class_of(self, rel, result, exists):
        """The class line label of one placed file: from :meth:`classify` when it ran, else
        from ``place``'s result (``exists``: the file was there before a dry-run plan)."""
        kind = self.kinds.get(rel)
        if kind == "conflict":
            return ("conflict" if isinstance(self.prior.get(rel), dict)
                    else "conflict (base unknown)")
        if kind is not None:
            return {"same": "unchanged", "updated": "upstream-changed", "user": "user-changed",
                    "merged": "merged", "replaced": "replaced"}[kind]
        if result == "same":
            return "unchanged"
        if result == "planned":
            return "forced" if exists else "created"
        return result                   # created | forced

    def report(self, pairs):
        """One ``<class> <rel>`` line per managed file that is not unchanged, the counts,
        then ``restart: needed`` when an agent, skill or command file changed this run."""
        from . import note

        counts = dict.fromkeys(("unchanged", "upstream-changed", "user-changed", "merged",
                                "replaced", "conflict", "created", "forced"), 0)
        restart = False
        for rel, result in pairs:
            label = self.class_of(rel, result, os.path.isfile(self.env.path(rel)))
            counts[label.split(" ")[0]] += 1
            if label == "replaced":
                note("  replaced %s (yours: %s)" % (rel, yours_rel(rel)))
            elif label != "unchanged":
                note("  %s %s" % (label, rel))
            restart = restart or (label in WROTE and rel.startswith(RESTART))
        for rel in self.dropped:
            note("  removed %s (not in this plan tier)" % rel)
            restart = True
        keys = [k for k in counts if k != "forced" or counts[k]]
        note("classes: " + ", ".join("%s %d" % (k, counts[k]) for k in keys))
        note("restart: needed" if restart else "restart: not needed")

    def _compare(self, rel):
        """The bench verdict of an installed agent, else ``no series``."""
        if not (rel.startswith(".claude/agents/") and rel.endswith(".md")):
            return "no series"
        try:
            import importlib.util
            import sqlite3
            from pathlib import Path
            from .. import db, paths

            p = paths.db_path()
            if not os.path.exists(p):
                return "no series"
            spec = importlib.util.spec_from_file_location(
                "_pa3_bench", os.path.join(self.env.pkg, "tools", "bench.py"))
            bench = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bench)
            path = Path(self.env.path(rel))
            name = bench._frontmatter(path)[0].get("name") or path.stem
            conn = db.connect(p, readonly=True)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM bench WHERE agent=? ORDER BY rowid",
                                    (name,)).fetchall()
            finally:
                db.close(conn)
            if not rows:
                return "no series"
            return bench._verdict(path, rows, Path(self.env.pkg) / "agents")[0]
        except Exception:
            return "no series"

    def _put(self, rel, data):
        """Write bytes ``data`` to ``rel`` through the Manifest (created/updated/skipped)."""
        from . import same_bytes

        dst = self.env.path(rel)
        exists = os.path.isfile(dst)
        if exists and same_bytes(dst, data):
            self.env.man.skipped.append(rel)
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        (self.env.man.updated if exists else self.env.man.created).append(rel)

    def _drop(self, rel):
        """Delete ``rel``; a tracked file goes to ``Manifest.removed`` so the commit stages it."""
        from .project import _pruned_files

        tracked = _pruned_files(self.env, rel)
        os.remove(self.env.path(rel))
        for r in tracked:
            if r not in self.env.man.removed:
                self.env.man.removed.append(r)

    def apply(self):
        """Apply the ``| resolve:`` verdicts on UPGRADE.md lines to this run's conflicts (T20),
        in file order, before :func:`record` and :meth:`stage`; a verdict applies only while the
        staged ``.upstream`` still equals this run's upstream."""
        from . import note

        env = self.env
        try:
            with open(env.path(UPGRADE), "r", encoding="utf-8") as fh:
                res = parse_resolutions(fh.read())
        except OSError:
            return
        conflicts = {c[0]: c for c in self.conflicts}
        for rel, (verdict, path) in res.items():
            if rel not in conflicts:
                continue
            new, cur = _lf(conflicts[rel][2]), _lf(conflicts[rel][1])
            try:
                with open(env.path("%s/%s.upstream" % (UPGRADE_DIR, rel)), "rb") as fh:
                    if fh.read() != new:
                        continue            # upstream moved since staging: decide again
            except OSError:
                continue
            if verdict == "merged":
                src = path.replace("\\", "/")
                if src.startswith(".claude/") or not os.path.isfile(env.path(src)):
                    note("upgrade: %s: merged path missing: %s" % (rel, path))
                    continue
                with open(env.path(src), "rb") as fh:
                    out = _lf(fh.read())
            else:
                out = new if verdict == "upstream" else cur
            if verdict == "upstream" and rel in self.absent:     # 3.10.6 T2: upstream = no file
                self._drop(rel)
                self.absent[rel] = {"absent": "tier", "base": None, "sha": None, "user": None}
                self.dropped.append(rel)
            elif verdict != "keep":
                self._put(rel, out)
            if rel in self.absent and verdict != "upstream":
                self.absent[rel]["user"] = {"sha": sha(out), "mark": None, "state": verdict,
                                            "at": _iso()}
                if rel in env.man.edited:
                    env.man.edited.remove(rel)
            self.resolved[rel] = (verdict, new, out)
            self.kinds[rel] = {"keep": "user", "upstream": "updated", "merged": "merged"}[verdict]
            note("upgrade: %s: %s" % (rel, "merged from " + path if verdict == "merged"
                                      else verdict))

    def stage(self):
        """Stage this run's conflicts under ``.claude/pa3-upgrade/``; rebuild ``UPGRADE.md``."""
        from . import note
        from .project import write_changed

        env = self.env
        up_path = env.path(UPGRADE)
        listed = set()
        try:
            with open(up_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("- ") and " | " in line:
                        listed.add(line[2:].split(" | ", 1)[0].strip())
        except OSError:
            pass
        listed -= set(self.resolved)        # T20: an applied line counts as resolved
        tasks = _edit_tasks({c[0] for c in self.conflicts}) if self.conflicts else {}
        pkg_version = _package_version(env.pkg)
        keep, lines = set(), []
        for rel, cur_bytes, data, ent in self.conflicts:
            data = data.replace(b"\r\n", b"\n")    # LF-only, like every installed managed file
            urel = "%s/%s.upstream" % (UPGRADE_DIR, rel)
            keep.add(urel)
            upath = env.path(urel)
            if rel not in listed and os.path.isfile(upath):
                with open(upath, "rb") as fh:
                    if fh.read() == data:
                        continue            # resolved: the planner's task deleted its line
            self._put(urel, data)
            user = (ent or {}).get("user")
            yours = (user.get("mark") if isinstance(user, dict) else None) or sha(cur_bytes)[:8]
            lines.append("- %s | base %s | yours %s | upstream %s (version %s) | compare: %s"
                         " | edited in: %s | default: keep yours\n"
                         % (rel, (ent or {}).get("base") or "unknown", yours,
                            "absent (tier)" if rel in self.absent
                            else base_of(data.decode("utf-8", "replace")) or "unknown", pkg_version,
                            self._compare(rel), ", ".join(tasks.get(rel, [])) or "—"))
            note("! %s kept yours; upstream staged" % rel)
        root = env.path(UPGRADE_DIR)
        for dirpath, _dirs, names in os.walk(root, topdown=False):
            for name in sorted(names):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, env.repo).replace(os.sep, "/")
                if name.endswith(".upstream") and rel not in keep:
                    self._drop(rel)
            if dirpath != root and not os.listdir(dirpath):
                os.rmdir(dirpath)
        if lines:
            write_changed(env, UPGRADE, UPGRADE_HEADER + "".join(lines), backup=False)
        elif os.path.isfile(up_path):
            self._drop(UPGRADE)
        if os.path.isdir(root) and not os.listdir(root):
            os.rmdir(root)
