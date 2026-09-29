"""``pa_install.py --project`` -- the per-repo install (design B J.2, P1..P10).

Public:
    install_project(opts)       run the steps; returns a process exit code
    detect_project(opts)        P1: layout, interpreter, inventory  (-> :class:`Proj`)
    bootstrap(env)              P2: .run/, phase-ends/, pa.json, .gitignore
    copy_files(env)             P3: agents, skill, commands, tools, templates,
                                    rules/, cookbook/, docs/project-architect.md
    copy_agents(env, up)        agents through ladder.render at the run's preset (3.10.6 T2)
    claude_and_settings(env)    P7: CLAUDE.md, .claude/settings.json, MEMORY.md, HOW_WE_WORK.md
    close(env)                  P10: manifest, explicit-path commit, the Next line
    Manifest                    what was created / skipped / left alone / retired

Only two of design B's five layouts run here: **fresh** (the whole tree) and
**pa3** (refresh the packaged files only -- agents, project-architect skill, commands,
tools, templates -- and never touch plans, rules, cookbook, HOW_WE_WORK or
CLAUDE.md).  The three migration layouts (``stock20``, ``onex``, ``overlay``)
are detected and refused: they need the P4..P9 moves, splits and memory sort
that M6 implements, and a half-install over a 2.0 tree is worse than none.

Discipline is ``--root``'s (SETUP.md section 0), carried over verbatim: every step is
``check -> SKIP | do -> verify``; every copy is byte-compared after it is written
and byte-compared before it is written (identical -> SKIP), so a second run is a
no-op; a packaged file the project has edited is listed and left alone unless
``--force``; nothing is ever deleted (a pre-PA3 ``CLAUDE.md`` is ``git mv``'d to
``docs/retired/``); the commit names its paths explicitly, carries no trailer and
is never pushed.  ``--dry-run`` prints the same lines and writes nothing at all.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

from .. import __version__ as PA_VERSION
from .. import fsutil
from .. import paths
from . import ask, is_tty, note, same_bytes, step
from . import detect as detect_mod
from . import settings_merge as sm
from . import ladder
from . import managed
from . import memory_sort
from .root import clone_warning, detect_interpreter, package_root

COMMIT_MSG = "chore: install Project Architect 3.0"
CLAUDE_MARKER = "Project Architect 3.0"
RETIRED_CLAUDE = "docs/retired/CLAUDE.pre-pa3.md"
GITIGNORE_MARKER = "# Project Architect 3.0"
GITIGNORE_BLOCK = GITIGNORE_MARKER + "\n.run/\n__pycache__/\n"
DEFAULT_TAGLINE = "<one line: what this project is -- edit me>"
FAILSAFES = ("never push", "the milestone gate is the arbiter")
NO_PROJECT_RULES = "(none yet -- add headlines with tools/rules_add.py promote)"
CURRENT_SUBDIRS = ("tasks", "logs", "research", "discussions")
REFRESHED = ("agents", "project-architect skill", "commands", "tools", "templates", "docs/project-architect.md")

DISCUSSIONS_INDEX = (
    "<!-- Discussion records for one phase. One line per record, written by "
    "tools/discussion.py off --new-record: D<n> | <topic> | <date> | discussions/D<n>.md -->\n"
)
TASK_INDEX = ("# Tasks -- cumulative\n"
              "# phase | id | status | title | tags | summary | log | research\n")
RESEARCH_INDEX = ("# Research -- cumulative\n"
                  "# phase | id | task | title | tags | agent | date | lines | path\n")
LEGACY_INDEX = ("# Legacy PhaseEnds (pre-PA3)\n"
                "# phase | title | milestone | date | path\n")

RETIRED_AGENTS = ("coder-opus46.md", "coder-sonnet.md", "expert-fable-high.md")  # was ("coder-opus46.md", "coder-sonnet.md") (3.9.5 T15); was ("coder-opus46.md",) (3.9.5 T9); coder-opus46 was also the hard-tier expert's agent file (3.9 T1: back as the hard tier)

# --------------------------------------------------------------------------- state

class Proj(object):
    """The P1 facts, passed to every later step."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):                                            # pragma: no cover
        return "Proj(%s)" % ", ".join("%s=%r" % kv for kv in sorted(self.__dict__.items()))

    def path(self, rel):
        return os.path.join(self.repo, rel.replace("/", os.sep))


class _Written(list):
    """A written-path bucket: a path already written this run is not marked again.

    ``created``, ``updated`` and ``forced`` share one ``seen`` set, so a path created
    and then appended to stays one "created", and a path updated twice is one "updated".
    """

    def __init__(self, seen):
        list.__init__(self)
        self._seen = seen

    def append(self, rel):
        if rel in self._seen:
            return
        self._seen.add(rel)
        list.append(self, rel)


class Manifest(object):
    """Every file the run touched, by what happened to it."""

    def __init__(self):
        seen = set()            # every path written this run (first bucket wins)
        self.created = _Written(seen)   # written for the first time
        self.updated = _Written(seen)   # merged/appended in place (settings.json, .gitignore)
        self.forced = _Written(seen)    # project-edited, overwritten because of --force
        self.skipped = []       # already byte-identical
        self.edited = []        # project-edited, left alone (no --force)
        self.retired = []       # (source, destination) moved out of the load order
        self.removed = []       # pruned by the refresh; ``git add`` stages the deletion

    def mark(self):
        return (len(self.created), len(self.updated), len(self.forced),
                len(self.skipped), len(self.edited), len(self.retired), len(self.removed))

    def since(self, mark):
        m = mark
        return {"created": self.created[m[0]:], "updated": self.updated[m[1]:],
                "forced": self.forced[m[2]:], "skipped": self.skipped[m[3]:],
                "edited": self.edited[m[4]:], "retired": self.retired[m[5]:],
                "removed": self.removed[m[6]:]}

    @staticmethod
    def changed(d):
        return (len(d["created"]) + len(d["updated"]) + len(d["forced"]) + len(d["retired"])
                + len(d["removed"]))

    @property
    def commit_paths(self):
        """Everything worth naming in ``git add`` (``.run/`` is gitignored)."""
        out = []
        for rel in self.created + self.updated + self.forced + self.removed:
            if rel.startswith(".run/") or rel in out:
                continue
            out.append(rel)
        for src, dst in self.retired:
            for rel in (src, dst):
                if rel not in out:
                    out.append(rel)
        return out


# --------------------------------------------------------------------------- files

def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _lf_only(rel):
    """A managed agent, command or skill markdown file: installed with LF line endings."""
    return rel.endswith(".md") and rel.startswith(
        (".claude/agents/", ".claude/commands/", ".claude/skills/"))


def _fill(text, mapping):
    """Replace every ``{{KEY}}`` present in ``mapping``; the rest stay as prompts."""
    for key, value in mapping.items():
        text = text.replace("{{%s}}" % key, value)
    return text


def _ensure_dir(env, path):
    if not env.dry_run:
        os.makedirs(path, exist_ok=True)


def place(env, rel, text=None, src=None, upgrade=None):
    """Create ``<repo>/<rel>``; byte-compare first and after.

    Returns ``"created"``, ``"same"`` (identical already), ``"edited"`` (differs and
    kept), ``"forced"`` (differs and overwritten) or ``"planned"`` (``--dry-run``).
    With ``upgrade`` (a :class:`managed.Refresh`, T8) a differing file is classified
    against the prior record: upstream-only is overwritten (``"updated"``), a
    CRLF-only difference is ``"same"``, the rest stay ``"edited"``. Agent, command and
    skill ``.md`` files are written LF-only; a CRLF copy with the same content is rewritten.
    T15: a clean three-way merge is written (``"merged"``); a tool, command or template
    without base or with overlap gets the upstream bytes, the user's kept as ``.yours``
    (``"replaced"``).
    """
    data = text.encode("utf-8") if text is not None else _read(src)
    lf_only = _lf_only(rel)
    if lf_only:     # fix-1: CRLF in agent frontmatter makes Claude Code drop the agent
        data = data.replace(b"\r\n", b"\n")
    dst = env.path(rel)
    exists = os.path.isfile(dst)

    if exists and same_bytes(dst, data):
        env.man.skipped.append(rel)
        return "same"
    upstream = False
    if exists and not env.force:
        cur = _read(dst)
        kind = upgrade.classify(rel, cur, data) if upgrade is not None else "conflict"
        if kind == "same" and lf_only and b"\r\n" in cur and not env.dry_run:
            with open(dst, "wb") as fh:             # heal: same content, CRLF bytes -> LF-only
                fh.write(data)
            env.man.updated.append(rel)
            note("~ %s rewritten LF-only (CRLF line endings)" % rel)
            return "same"
        if kind == "same":
            env.man.skipped.append(rel)
            return "same"
        if kind not in ("updated", "merged", "replaced"):
            env.man.edited.append(rel)
            return "edited"
        upstream = kind
        if kind == "merged":
            data = upgrade.merged[rel][1]
        elif kind == "replaced":
            data = data.replace(b"\r\n", b"\n")

    bucket = env.man.updated if upstream else env.man.forced if exists else env.man.created
    # D4: a later write supersedes an earlier "edited" mark
    if rel in env.man.edited:
        env.man.edited.remove(rel)
    bucket.append(rel)
    if env.dry_run:
        return "planned"

    _ensure_dir(env, os.path.dirname(dst))
    tmp = dst + ".pa3tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)
    if not same_bytes(dst, data):                                  # pragma: no cover
        raise OSError("byte compare failed for %s" % rel)
    if upstream == "replaced":
        upgrade.save_yours(rel)
    return upstream or ("forced" if exists else "created")


def write_changed(env, rel, text, backup=True):
    """Write a file we own the content of (settings.json): back it up, then replace."""
    data = text.encode("utf-8")
    dst = env.path(rel)
    exists = os.path.isfile(dst)
    if exists and same_bytes(dst, data):
        env.man.skipped.append(rel)
        return "same", None

    if env.dry_run:
        (env.man.updated if exists else env.man.created).append(rel)
        return "planned", None

    saved = None
    if exists and backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # D3: backups go under .run/backups/ (gitignored) with a flattened name
        bak_dir = env.path(".run/backups")
        os.makedirs(bak_dir, exist_ok=True)
        flat = rel.replace("/", "--").replace("\\", "--")
        saved = os.path.join(bak_dir, "%s.bak-%s" % (flat, stamp))
        n = 1
        while os.path.exists(saved):
            saved = os.path.join(bak_dir, "%s.bak-%s.%d" % (flat, stamp, n))
            n += 1
        shutil.copyfile(dst, saved)
    _ensure_dir(env, os.path.dirname(dst))
    fsutil.atomic_write_text(dst, text)
    # D4: a later write supersedes an earlier "edited" mark
    if rel in env.man.edited:
        env.man.edited.remove(rel)
    (env.man.updated if exists else env.man.created).append(rel)
    return ("updated" if exists else "created"), saved


def copy_dir(env, src_dir, dst_rel, transform=None, recurse=False, only=None, upgrade=None):
    """Copy a packaged directory into the project; returns the per-file results."""
    out = []
    if not os.path.isdir(src_dir):
        return out
    for name in sorted(os.listdir(src_dir)):
        if name == "__pycache__" or name.endswith(".pyc"):
            continue
        src = os.path.join(src_dir, name)
        rel = "%s/%s" % (dst_rel, name)
        if os.path.isdir(src):
            if recurse:
                out.extend(copy_dir(env, src, rel, transform=transform, recurse=True,
                                    only=only, upgrade=upgrade))
            continue
        if only and not name.endswith(only):
            continue
        if transform is None:
            out.append((rel, place(env, rel, src=src, upgrade=upgrade)))
        else:
            with open(src, "r", encoding="utf-8", newline="") as fh:
                text = transform(fh.read())
            out.append((rel, place(env, rel, text=text, upgrade=upgrade)))
    return out


def _raw_pa_json(env):
    """The raw ``.claude/pa.json`` dict (C0084: the file, not the merged config), else ``{}``."""
    try:
        with open(env.path(".claude/pa.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _preset(env):
    """The ladder preset, once per run (3.10.6 T2): ``env.preset`` when set > the ledger's
    ``override`` row for the cached email > the credentials tier > ``accounts.tier_for``."""
    forced = getattr(env, "preset", None)
    if forced in ladder.PRESETS:
        return forced
    from .. import accounts
    email, conn = None, None
    try:
        email = accounts._cache_read().get("email")
    except Exception:
        pass
    try:
        from .. import db
        if os.path.exists(paths.db_path()):
            conn = db.connect(paths.db_path(), readonly=True)
    except Exception:
        conn = None
    try:
        if conn is not None and email:
            try:
                got = conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                                   (email,)).fetchone()
                if got is not None and got[1] == "override" and got[0] in ladder.PRESETS:
                    return got[0]
            except Exception:
                pass
        try:
            from .. import usage_api
            cred = usage_api.read_credentials(accounts.config_dir())
            tier = accounts.tier_from(cred.get("subscription_type"), cred.get("rate_limit_tier"))
            if tier in ladder.PRESETS:
                return tier
        except Exception:
            pass
        try:
            tier = accounts.tier_for(conn, email, _raw_pa_json(env))
            return tier if tier in ladder.PRESETS else "max5"
        except Exception:
            return "max5"
    finally:
        if conn is not None:
            try:
                from .. import db
                db.close(conn)
            except Exception:
                pass


def _absent_agent(env, up, rel):
    """An agent the preset leaves out (3.10.6 T2): never written; an unedited copy is deleted,
    an edited one kept and staged as a conflict (C0053). Records ``absent: "tier"``."""
    ent = up.prior.get(rel) if isinstance(up.prior.get(rel), dict) else None
    keep = {k: (ent or {}).get(k) for k in ("base", "sha", "user")}
    gone = {"absent": "tier", "base": None, "sha": None, "user": None}
    dst = env.path(rel)
    if not os.path.isfile(dst):
        up.absent[rel] = gone
        return
    cur = _read(dst)
    s = managed.sha(cur)
    user = keep["user"] if isinstance(keep["user"], dict) else None
    if user and user.get("sha") == s:           # the user's recorded copy: keep it quietly
        up.absent[rel] = dict(keep, absent="tier")
        env.man.edited.append(rel)
        return
    with open(os.path.join(env.pkg, "agents", rel.rsplit("/", 1)[1]), "rb") as fh:
        pkg = fh.read()
    if env.force or s in (keep["sha"], managed.sha(pkg)) or s in up._history(rel):
        up.absent[rel] = gone
        if env.dry_run:
            step("P3", "files", "SKIP", "would remove %s (not in this plan tier)" % rel)
            return
        up._drop(rel)
        up.dropped.append(rel)
        return
    up.absent[rel] = dict(keep, absent="tier")
    up.kinds[rel] = "conflict"
    up.conflicts.append((rel, cur, b"", ent))
    env.man.edited.append(rel)


def copy_agents(env, up):
    """``.claude/agents/*.md`` through :func:`ladder.render` at the run's preset (3.10.6 T2):
    the only route that writes agents. Returns the per-file results (absent agents omitted)."""
    out = []
    src_dir = os.path.join(env.pkg, "agents")
    if not os.path.isdir(src_dir):
        return out
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        if not name.endswith(".md") or not os.path.isfile(src):
            continue
        rel = ".claude/agents/" + name
        with open(src, "r", encoding="utf-8", newline="") as fh:
            text = ladder.render(name[:-3], fh.read(), env.ladder_preset, env.ladder_override)
        if text is None:
            _absent_agent(env, up, rel)
            continue
        out.append((rel, place(env, rel, text=text, upgrade=up)))
    return out


# --------------------------------------------------------------------------- git

def _git(repo, *args, **kw):
    """Run git in ``repo``; returns ``(returncode, output)``.  Never raises."""
    exe = shutil.which("git")
    if not exe:
        return 127, "git not found on PATH"
    try:
        proc = subprocess.run((exe, "-C", repo) + tuple(args), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=kw.get("timeout", 60))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")


def git_toplevel(repo):
    """The work tree holding ``repo``, or ``None`` when it is not a git repository."""
    rc, out = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    line = out.strip().splitlines()[0].strip() if out.strip() else ""
    return sm.abs_fwd(line) if line else None


def git_tracked(repo, rel):
    rc, _out = _git(repo, "ls-files", "--error-unmatch", "--", rel)
    return rc == 0


# --------------------------------------------------------------------------- P1

def _pa3_version(pa3_dir):
    text = fsutil.read_text(os.path.join(pa3_dir, "VERSION"), "")
    for line in text.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


_REFUSE_PATTERNS = None


def _refuse_set():
    """Paths that should never be governed (expanded at call time)."""
    global _REFUSE_PATTERNS
    if _REFUSE_PATTERNS is None:
        h = os.path.expanduser("~")
        _REFUSE_PATTERNS = {os.path.normcase(os.path.abspath(p)) for p in [
            h, os.path.join(h, "Downloads")]}
    return _REFUSE_PATTERNS


def _is_drive_root(path):
    """True for ``/``, ``C:/``, ``D:\\``, ``/mnt/<x>``."""
    norm = os.path.normcase(os.path.abspath(path)).replace("\\", "/").rstrip("/")
    if not norm or norm == "/":
        return True
    # Windows drive root: "c:" (after rstrip /)
    if len(norm) == 2 and norm[1] == ":":
        return True
    # /mnt/<letter>
    import re as _re
    if _re.match(r"^/mnt/[a-z]$", norm):
        return True
    return False


def git_checks(repo, opts):
    """Pre-flight git checks before detect_project.

    Returns an error message string when the repo is not suitable, or ``None``
    when everything is fine (the repo may have been initialised as a side effect).
    """
    # git on PATH
    exe = shutil.which("git")
    if not exe:
        return "git is not on PATH: install git first"

    # refuse dangerous targets
    norm = os.path.normcase(os.path.abspath(repo))
    if norm in _refuse_set() or _is_drive_root(repo):
        return "refusing to govern %s: pick the project's folder" % sm.abs_fwd(repo)

    # empty folder without .git: offer to initialise
    if not os.path.isdir(os.path.join(repo, ".git")) and git_toplevel(repo) is None:
        if getattr(opts, "dry_run", False):
            note("would initialise a git repository in %s" % sm.abs_fwd(repo))
            return None
        if getattr(opts, "yes", False):
            do_init = True
        else:
            from . import is_tty as _is_tty
            if _is_tty():
                answer = ask("initialise a git repository here? [y/N]", "n", opts)
                do_init = answer.strip().lower().startswith("y")
            else:
                do_init = False
        if do_init:
            try:
                subprocess.run([exe, "init", "-q", repo],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30, check=True)
                note("initialised git repository in %s" % sm.abs_fwd(repo))
            except (OSError, subprocess.SubprocessError) as exc:
                return "git init failed: %s" % exc

    # core.safecrlf: set to false when unset (never on a dry run: the session-start hook probes with dry_run)
    if getattr(opts, "dry_run", False):
        return None
    top = git_toplevel(repo)
    if top:
        rc, out = _git(top, "config", "core.safecrlf")
        if rc != 0 or not out.strip():
            _git(top, "config", "core.safecrlf", "false")
            note("set core.safecrlf false in %s" % sm.abs_fwd(top))

    return None


def _complete_package(path):
    """True when ``path`` holds the skill template, ``templates/`` and ``agents/``."""
    return (os.path.isfile(os.path.join(path, "skills", "project-architect",
                                        "SKILL.template.md"))
            and os.path.isdir(os.path.join(path, "templates"))
            and os.path.isdir(os.path.join(path, "agents")))


def _version_source(pa3_dir):
    """The ``source:`` line of ``<pa3_dir>/VERSION`` (the machine clone), or None."""
    text = fsutil.read_text(os.path.join(pa3_dir, "VERSION"), "")
    for line in text.splitlines():
        if line.startswith("source:"):
            return line.split(":", 1)[1].strip() or None
    return None


def resolve_package(pa3_dir, script_root=None):
    """The package dir: the script's own when complete, else the VERSION source clone.

    The installed root (``~/.claude/pa3/``) holds only ``pa/`` and the entry scripts, so
    ``--project`` run from there reads skills, templates and agents from the clone it was
    installed from (``<source>/project-architect-3.0`` or ``<source>``). None when neither.
    """
    here = script_root or package_root()
    if _complete_package(here):
        return here
    source = _version_source(pa3_dir)
    if source:
        for cand in (os.path.join(source, "project-architect-3.0"), source):
            if _complete_package(cand):
                return cand
    return None


def detect_project(opts):
    """P1: git check, layout, interpreter, the root install, the inventory table."""
    raw = opts.project if opts.project not in (None, "") else "."
    repo = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(repo):
        step("P1", "detect", "FAIL", "no such directory: %s" % repo)
        return None

    # Pre-flight git checks
    err = git_checks(repo, opts)
    if err:
        step("P1", "detect", "FAIL", err)
        return None

    # When running from a clone source, ensure_clone first
    source = getattr(opts, "source", None)
    no_clone = getattr(opts, "no_clone", False)
    clone_behind = 0
    if getattr(opts, "dry_run", False):
        no_clone = True                                           # a dry run writes nothing
    if not no_clone and source:
        from .root import ensure_clone, Env as _Env
        cfg_dir = getattr(opts, "config_dir", None) or paths.config_dir()
        _env = _Env(config_dir=sm.abs_fwd(cfg_dir))
        from .. import config as pa_config
        timeout_s = pa_config.DEFAULTS.get("install", {}).get("fetch_timeout_s", 8)
        clone_info = ensure_clone(_env, source, timeout_s)
        clone_behind = clone_info.get("behind", 0)
        warning = clone_warning(clone_info)
        if warning:
            note(warning)
        elif clone_behind:
            note("WARN clone behind by %d" % clone_behind)
    elif not no_clone:
        # Check whether we're running from a clone (VERSION source: names it)
        pa3_dir = getattr(opts, "pa3_dir", None) or paths.install_dir()
        ver_path = os.path.join(pa3_dir, "VERSION")
        ver_text = fsutil.read_text(ver_path, "")
        clone_source = None
        for line in ver_text.splitlines():
            if line.startswith("source:"):
                clone_source = line.split(":", 1)[1].strip()
                break
        if clone_source and os.path.isdir(os.path.join(clone_source, ".git")):
            from .root import ensure_clone, Env as _Env
            # maintain the clone VERSION names, not <config-dir>/pa3-src (a clone of the clone)
            named = os.path.normpath(os.path.abspath(clone_source))
            _env = _Env(config_dir=sm.abs_fwd(os.path.dirname(named)),
                        _clone_dir_name=os.path.basename(named))
            from .. import config as pa_config
            timeout_s = pa_config.DEFAULTS.get("install", {}).get("fetch_timeout_s", 8)
            clone_info = ensure_clone(_env, clone_source, timeout_s)
            clone_behind = clone_info.get("behind", 0)
            warning = clone_warning(clone_info)
            if warning:
                note(warning)
            elif clone_behind:
                note("WARN clone behind by %d" % clone_behind)

    layout, sig, rows = detect_mod.inventory(repo)
    top = git_toplevel(repo)
    if top is None and not opts.force:
        step("P1", "detect", "FAIL",
             "not a git repository: %s (run `git init` first, or --force)" % sm.abs_fwd(repo))
        return None

    py_exe, py_how = detect_interpreter(getattr(opts, "python", None))
    pa3_dir = sm.abs_fwd(getattr(opts, "pa3_dir", None) or paths.install_dir())
    pa3_version = _pa3_version(pa3_dir)
    script_root = package_root()
    pkg = resolve_package(pa3_dir, script_root)
    if pkg is None:
        step("P1", "detect", "FAIL",
             "package not found beside %s or under VERSION source (%s): reinstall the root "
             "with `pa_install.py --root --source <repo>`"
             % (sm.abs_fwd(script_root), _version_source(pa3_dir) or "none"))
        return None
    pkg = sm.abs_fwd(pkg)
    name = (opts.name or os.path.basename(repo.rstrip(os.sep)) or "project").strip()
    env = Proj(
        repo=repo,
        repo_fwd=sm.abs_fwd(repo),
        name=name,
        tagline=(opts.tagline or "").strip(),
        layout=layout,
        signals=sig,
        py_exe=py_exe,
        py_how=py_how,
        pkg=pkg,
        pa3_dir=pa3_dir,
        pa3_version=pa3_version,
        git_top=top,
        dry_run=bool(opts.dry_run),
        force=bool(opts.force),
        no_commit=bool(getattr(opts, "no_commit", False)),
        interphase=getattr(opts, "interphase", None),
        yes=bool(getattr(opts, "yes", False)),
        man=Manifest(),
        warnings=[],
    )

    if env.layout == "fresh" and not env.tagline:
        # T10 (3.11): the constitution's own line is the default, at a prompt and under --yes
        # (the ! line), so CLAUDE.md and the card never keep "edit me" when it names one
        from . import howwework
        known = howwework._tagline_from_constitution(
            fsutil.read_text(os.path.join(repo, "PROJECT_CONTEXT.md"), "") or "")
        env.tagline = ask("one line: what this project is", known or DEFAULT_TAGLINE, opts)

    if top is None:
        env.warnings.append("not a git repository -- --force: nothing will be committed")
    elif os.path.normcase(top) != os.path.normcase(env.repo_fwd):
        env.warnings.append("installing into a subdirectory of the repository at %s" % top)
    if pa3_version is None:
        env.warnings.append("no %s/VERSION -- run `pa_install.py --root` first "
                            "(the hooks, the statusline and the ledger are per machine)"
                            % pa3_dir)
    step("P1", "detect", "DONE", "%s -- %s"
         % (detect_mod.LABEL[layout], env.repo_fwd))
    note("project     %s" % env.name)
    note("python      %s (%s)" % (env.py_exe, env.py_how))
    note("package     %s" % env.pkg)
    note("pa3 root    %s (%s)" % (env.pa3_dir, "VERSION %s" % pa3_version
                                  if pa3_version else "not installed"))
    for label, value in rows:
        note("%-11s %s" % (label, value))
    note("plan        %s" % _plan_line(env))
    for warn in env.warnings:
        note("warn: %s" % warn)
    return env


def _plan_line(env):
    if env.layout == "pa3":
        return "refresh %s (byte-for-byte); plans, rules, cookbook, " \
               "HOW_WE_WORK.md and CLAUDE.md are left alone" % ", ".join(REFRESHED)
    return ("bootstrap .run/ + phase-ends/current/, .claude/{agents,skills,commands,"
            "pa.json,settings.json}, tools/, templates/, rules/, cookbook/, docs/, "
            "CLAUDE.md, HOW_WE_WORK.md")


def confirm(env, opts):
    """One confirmation for the whole install; ``--yes``/no TTY/dry run skip it."""
    if opts.dry_run or opts.yes or not is_tty():
        return True
    answer = ask("install Project Architect 3.0 into %s? (y/n)" % env.repo_fwd, "y", opts)
    if answer.strip().lower().startswith("y"):
        return True
    print("aborted -- nothing was written")
    return False


# --------------------------------------------------------------------------- P2

def _template(env, name):
    return os.path.join(env.pkg, "templates", name)


def _template_text(env, name):
    with open(_template(env, name), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _now_iso_utc():
    """UTC ISO-8601 with a trailing ``Z`` (matches ``pa.transcript.iso()``, T10.1)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def pa_json_text(env):
    """``pa.json.template`` with the project name, the interpreter, the version and
    ``installed_at`` (T10.1): stamped once, carried forward unchanged from an existing
    ``.claude/pa.json`` on every later run so a re-install never moves the cutoff."""
    text = _fill(_template_text(env, "pa.json.template"),
                 {"PROJECT_NAME": env.name, "PY": env.py_exe})
    text = re.sub(r'("pa_version"\s*:\s*)"[^"]*"',
                  lambda m: m.group(1) + json.dumps(PA_VERSION), text, count=1)
    existing = fsutil.read_json(env.path(".claude/pa.json"), {}) or {}
    stamp = existing.get("installed_at") or _now_iso_utc()
    text = re.sub(r'("installed_at"\s*:\s*)null',
                  lambda m: m.group(1) + json.dumps(stamp), text, count=1)
    # the plan tier: the existing pa.json value > the interview answer > "max5"
    tier = existing.get("tier")
    if tier not in _TIERS:
        tier = (_interview(env) or {}).get("tier")
    if tier not in _TIERS:
        tier = "max5"
    text = re.sub(r'("tier"\s*:\s*)"[^"]*"',
                  lambda m: m.group(1) + json.dumps(tier), text, count=1)
    # 3.10.6 T3: the reconciled ``preset`` and the user's ``ladder`` survive a forced rewrite
    # (place keeps a differing pa.json unless --force)
    for key in ("ladder", "preset"):
        if key in existing and ('"%s"' % key) not in text:
            text = re.sub(r'("tier"\s*:\s*"[^"]*")',
                          lambda m, k=key: m.group(1) + ',\n  "%s": %s' % (k, json.dumps(existing[k])),
                          text, count=1)
    ped =getattr(env, "phase_ends_dir", "phase-ends")
    if ped != "phase-ends":
        text = re.sub(r'("phase_ends_dir"\s*:\s*)"[^"]*"',
                      lambda m: m.group(1) + json.dumps(ped), text, count=1)
    json.loads(text)                                     # refuse to write broken JSON
    return text


def gitignore_text(current):
    """``current`` plus the PA3 block, appended once (returns ``None`` when present)."""
    if GITIGNORE_MARKER in current:
        return None
    if not current:
        return GITIGNORE_BLOCK
    sep = "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
    return current + sep + GITIGNORE_BLOCK


def bootstrap(env, skip=None):
    """P2: ``.run/``, ``phase-ends/`` (+ the open phase), ``pa.json``, ``.gitignore``.

    *skip* is a set of relative paths that a later mapping row will generate;
    bootstrap must not pre-place them (D1: the stub would make ``_check_done``
    skip the real generation).
    """
    if env.layout == "pa3":
        step("P2", "bootstrap", "SKIP", "pa3 upgrade: the project tree is already bootstrapped")
        return "SKIP"
    mark = env.man.mark()
    ped = getattr(env, "phase_ends_dir", "phase-ends")
    skip = skip or set()

    for rel in (".run", ".run/logs", ped, ped + "/current",
                "docs", "cookbook", "rules"):
        _ensure_dir(env, env.path(rel))
    for sub in CURRENT_SUBDIRS:
        _ensure_dir(env, env.path(ped + "/current/" + sub))

    if ped + "/README.md" not in skip:
        place(env, ped + "/README.md", text=_template_text(env, "phase-ends-README.md"))
    if ped + "/TASK_INDEX.md" not in skip:
        place(env, ped + "/TASK_INDEX.md", text=TASK_INDEX)
    if ped + "/RESEARCH_INDEX.md" not in skip:
        place(env, ped + "/RESEARCH_INDEX.md", text=RESEARCH_INDEX)
    if ped + "/LEGACY_INDEX.md" not in skip:
        place(env, ped + "/LEGACY_INDEX.md", text=LEGACY_INDEX)
    place(env, ped + "/current/tasks/INDEX.md",
          text=_template_text(env, "INDEX.tasks.md"))
    place(env, ped + "/current/research/INDEX.md",
          text=_template_text(env, "INDEX.research.md"))
    place(env, ped + "/current/discussions/INDEX.md", text=DISCUSSIONS_INDEX)
    place(env, ".claude/pa.json", text=pa_json_text(env))

    want = gitignore_text(fsutil.read_text(env.path(".gitignore"), ""))
    if want is None:
        env.man.skipped.append(".gitignore")
    else:
        write_changed(env, ".gitignore", want, backup=False)

    done = env.man.since(mark)
    status = "SKIP" if (env.dry_run or not Manifest.changed(done)) else "DONE"
    step("P2", "bootstrap", status, _reason(env, done,
         "%s/current/{%s}, .claude/pa.json, .gitignore"
         % (ped, ",".join(CURRENT_SUBDIRS))))
    _detail(env, done)
    return status


# --------------------------------------------------------------------------- P3

# the skill name in project-owned files (templates/CLAUDE.template.md, rules-seed/P10.md)
SKILL_REFS = (("CLAUDE.md", re.compile(r"(Rules:\s+the\s+`)([a-z0-9-]+)(`\s+skill)")),
              ("rules/P10.md", re.compile(r"(live\s+in\s+the\s+`)([a-z0-9-]+)(`\s+skill)")))


def _rules_section(text):
    """The body of the ``## 11.`` section (to the next heading of its level or EOF), or ``None``."""
    lines = text.splitlines()
    start = level = None
    for i, raw in enumerate(lines):
        m = re.match(r"(#+)\s*11\.\s", raw)
        if m:
            start, level = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"(#+)\s", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    body = lines[start + 1:end]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    body = "\n".join(body)
    if not body.strip() or body.strip() == NO_PROJECT_RULES:
        return None
    return body


def project_rules(env):
    """Section 11 of the installed skill (else of a legacy PA3 skill), or ``NO_PROJECT_RULES``."""
    for name in [detect_mod.SKILL_NAME] + detect_mod.legacy_skills(env.repo):
        text = fsutil.read_text(env.path(".claude/skills/%s/SKILL.md" % name), None)
        body = _rules_section(text) if text else None
        if body:
            return body
    return NO_PROJECT_RULES


def skill_text(env, rules=None, text=None):
    """``skills/project-architect/SKILL.template.md`` (or template ``text``, a past version)
    with the interpreter and section 11."""
    if text is None:
        src = os.path.join(env.pkg, "skills", "project-architect", "SKILL.template.md")
        with open(src, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    if rules is None:
        rules = project_rules(env)
    return _fill(text, {"PY": env.py_exe, "PROJECT_RULES": rules})


def _pruned_files(env, rel):
    """Repo-relative files at/under ``rel`` to name in ``git add`` once pruned."""
    if env.git_top:
        rc, out = _git(env.repo, "ls-files", "-z", "--", rel)
        return [f for f in out.split("\0") if f] if rc == 0 else []
    full = env.path(rel)
    if os.path.isfile(full):
        return [rel]
    out = []
    for dirpath, _dirs, files in os.walk(full):
        for name in sorted(files):
            out.append(os.path.relpath(os.path.join(dirpath, name), env.repo).replace(os.sep, "/"))
    return out


def _stage_earlier_prunes(env):
    """Deletions an earlier refresh left unstaged: retired agents, vanished skill dirs."""
    if not env.git_top:
        return
    rc, out = _git(env.repo, "ls-files", "--deleted", "-z", "--",
                   ".claude/agents", ".claude/skills")
    if rc != 0:
        return
    for rel in [f for f in out.split("\0") if f]:
        parts = rel.split("/")
        if rel.startswith(".claude/agents/"):
            hit = len(parts) == 3 and parts[2] in RETIRED_AGENTS
        else:
            hit = (len(parts) > 3 and parts[2] != detect_mod.SKILL_NAME
                   and not os.path.isdir(env.path("/".join(parts[:3]))))
        if not hit or rel in env.man.removed:
            continue
        env.man.removed.append(rel)
        if env.dry_run:
            step("P3", "files", "SKIP", "would stage earlier prune of %s" % rel)
        else:
            step("P3", "files", "DONE", "staged earlier prune of %s" % rel)


def rename_skill_refs(env):
    """``--force`` pa3 refresh: the skill named in CLAUDE.md and rules/P10.md follows SKILL_NAME."""
    if not (env.layout == "pa3" and env.force):
        return
    for rel, pat in SKILL_REFS:
        path = env.path(rel)
        if not os.path.isfile(path):
            continue
        text = _read(path).decode("utf-8")
        new = pat.sub(lambda m: m.group(1) + detect_mod.SKILL_NAME + m.group(3), text)
        if new == text:
            continue
        write_changed(env, rel, new, backup=False)
        if env.dry_run:
            step("P3", "files", "SKIP", "would rename the skill in %s" % rel)
        else:
            step("P3", "files", "DONE", "renamed the skill in %s" % rel)


def copy_files(env):
    """P3: agents, skill, commands, tools, templates, rules, cookbook, docs."""
    mark = env.man.mark()
    pkg = env.pkg
    fill_py = lambda text: _fill(text, {"PY": env.py_exe})               # noqa: E731
    rules = project_rules(env)              # section 11, read before the legacy prune below
    up = managed.Refresh(env)               # the prior record, loaded once (T8)
    up.rules = rules                        # past skill versions are filled with it (T15)
    env.ladder_preset = _preset(env)        # the model ladder, resolved once (3.10.6 T2)
    env.ladder_override = _raw_pa_json(env).get("ladder")

    pairs = copy_agents(env, up)
    # prune retired agents that a previous install may have placed
    for stale in RETIRED_AGENTS:
        old = env.path(os.path.join(".claude", "agents", stale))
        if os.path.isfile(old):
            env.man.removed.extend(_pruned_files(env, ".claude/agents/" + stale))
            if env.dry_run:
                step("P3", "files", "SKIP", "would remove retired agent %s" % stale)
                continue
            os.remove(old)
            step("P3", "files", "DONE", "removed retired agent %s" % stale)
    # prune a PA3 skill a previous install placed under an older name (found by description)
    for stale in detect_mod.legacy_skills(env.repo):
        env.man.removed.extend(_pruned_files(env, ".claude/skills/" + stale))
        if env.dry_run:
            step("P3", "files", "SKIP", "would remove legacy PA3 skill %s" % stale)
            continue
        shutil.rmtree(env.path(os.path.join(".claude", "skills", stale)))
        step("P3", "files", "DONE", "removed legacy PA3 skill %s" % stale)
    _stage_earlier_prunes(env)
    skill = ".claude/skills/project-architect/SKILL.md"
    pairs.append((skill, place(env, skill, text=skill_text(env, rules), upgrade=up)))
    pairs += copy_dir(env, os.path.join(pkg, "commands"), ".claude/commands",
                      transform=fill_py, only=".md", upgrade=up)
    pairs += copy_dir(env, os.path.join(pkg, "tools"), "tools", recurse=True, upgrade=up)
    pairs += copy_dir(env, os.path.join(pkg, "templates"), "templates", upgrade=up)

    if env.layout != "pa3":
        migration = env.layout in detect_mod.MIGRATION
        if not migration:
            # fresh install: seed rules from the packaged rules-seed directory
            seed = os.path.join(pkg, "rules-seed")
            for name in sorted(os.listdir(seed)):
                if not name.endswith(".md"):
                    continue
                rel = "rules/INDEX.md" if name == "INDEX.seed.md" else "rules/" + name
                place(env, rel, src=os.path.join(seed, name))
        place(env, "cookbook/INDEX.md", text=_template_text(env, "INDEX.cookbook.md"))
    if env.layout not in detect_mod.MIGRATION:
        # the methodology is a packaged file: fresh installs and pa3 upgrades refresh it
        # (a migration places it after its mapping rows retire the 2.0 one); 3.11 T25: a
        # managed file, so an untouched old copy updates and an edited one is kept or replaced
        master = os.path.join(pkg, "project-architect-3.0.md")
        if not os.path.isfile(master):
            step("P3", "files", "FAIL", "package incomplete: project-architect-3.0.md missing")
            return "FAIL"
        pairs.append((managed.DOC, place(env, managed.DOC, src=master, upgrade=up)))
    rename_skill_refs(env)
    if not env.dry_run:
        up.apply()                      # UPGRADE.md `| resolve:` verdicts, before record (T20)
        managed.record(env, pairs, up)  # .claude/pa3-managed.json: the five groups and the doc (T25)
        up.stage()                      # .claude/pa3-upgrade/: conflicts kept, upstream staged (T8)
    if env.layout == "pa3":
        up.report(pairs)                # one class line per changed managed file, then counts (T10)

    done = env.man.since(mark)
    status = "SKIP" if (env.dry_run or not Manifest.changed(done)) else "DONE"
    what = ("agents, project-architect skill, commands, tools/, templates/, docs/project-architect.md"
            if env.layout == "pa3" else
            "agents, project-architect skill, commands, tools/, templates/, rules/, "
            "cookbook/")
    step("P3", "files", status, _reason(env, done, what))
    _detail(env, done)
    return status


# --------------------------------------------------------------------------- P7

def claude_md_text(env):
    return _fill(_template_text(env, "CLAUDE.template.md"), {
        "PROJECT_NAME": env.name,
        "PROJECT_TAGLINE": env.tagline or DEFAULT_TAGLINE,
        "FAILSAFE_1": FAILSAFES[0],
        "FAILSAFE_2": FAILSAFES[1],
    })


_INTERVIEW_PROMPTS = (
    ("name", "Developer name"),
    ("experience", "Experience level (e.g. advanced, intermediate)"),
    ("domain", "Primary domain"),
    ("autonomy", "Autonomy posture"),
    ("notify", "Notification channel (e.g. toast, discord)"),
    ("rhythm", "Rhythm (autonomous or review)"),
    ("tier", "Plan tier (max20, max5 or pro)"),
)

_TIERS = ("max20", "max5", "pro")

def _interview_default(field, env):
    """The ``tools/card.py interview --defaults`` default for one field (T3.c1)."""
    if field == "name":
        rc, out = _git(env.repo, "config", "user.name")
        return out.strip() if rc == 0 and out.strip() else ""
    if field == "domain":
        return os.path.basename(os.path.abspath(env.repo))
    if field == "experience":
        return "advanced"
    if field == "autonomy":
        return "full inside an approved plan; stop only at the two gates"
    if field == "notify":
        return "toast"
    if field == "rhythm":
        return "autonomous"
    if field == "tier":
        return "max5"
    return ""


def _interview(env):
    """One ``input()`` prompt per field, TTY only; ``None`` when skipped.

    Skipped entirely on ``--yes`` or a non-TTY stdin (``sys.stdin.isatty()``):
    :func:`how_we_work_text` then writes the defaults (T10, 3.11).

    The answers are cached on ``env`` (``_interview_answers``) so one interview
    feeds both :func:`how_we_work_text` and :func:`pa_json_text`, whichever runs first.
    """
    if hasattr(env, "_interview_answers"):
        return env._interview_answers
    answers = None
    if not env.yes and sys.stdin.isatty():
        answers = {}
        for field, prompt_text in _INTERVIEW_PROMPTS:
            default = _interview_default(field, env)
            try:
                answer = input("%s [%s]: " % (prompt_text, default))
            except (EOFError, KeyboardInterrupt):
                answer = ""
            answers[field] = answer.strip() or default
        if answers.get("tier") not in _TIERS:
            answers["tier"] = _interview_default("tier", env)
    env._interview_answers = answers
    return answers


def how_we_work_text(env):
    """Delegates to ``howwework.write()`` (T3.c1): environment fills, the
    constitution's tagline and paths when ``PROJECT_CONTEXT.md`` is present,
    and a TTY interview for the ``## Developer`` fields.

    ``write()`` turns every placeholder it cannot fill into a TODO comment
    (T3.c1). When the interview is skipped (``--yes``, the ``!`` line, no terminal)
    the card takes the interview's defaults (T10, 3.11: was T3.c2's put-back of the
    ``{{DEVELOPER_*}}`` placeholders for the first planner session, which no agent
    filled, so ``tools/card.py check`` failed and the first archive refused).
    """
    from . import howwework
    constitution = fsutil.read_text(env.path("PROJECT_CONTEXT.md"), "") or None
    interview = _interview(env)
    if interview is None:
        interview = {field: _interview_default(field, env) for field, _p in _INTERVIEW_PROMPTS}
    text = howwework.write(env, {}, interview=interview, constitution=constitution)
    fixed = text

    # write()'s own PROJECT_TAGLINE fill is always present in its fill map (even
    # empty), so its setdefault() for the constitution's tagline never fires;
    # apply it here when the CLI never asked the developer for a real one.
    if constitution and env.tagline == DEFAULT_TAGLINE:
        found_tagline = howwework._tagline_from_constitution(constitution)
        if found_tagline:
            fixed = fixed.replace(DEFAULT_TAGLINE, found_tagline, 1)

    # write() already placed ``text`` via ``place()``; a second ``place()`` call
    # here would see the file it just created and treat it as developer-edited
    # (left alone). Rewrite the bytes directly instead (T3.c2).
    if fixed != text and not env.dry_run:
        fsutil.atomic_write_text(env.path("HOW_WE_WORK.md"), fixed)
    return fixed


def retire_claude_md(env):
    """Move a pre-PA3 ``CLAUDE.md`` to ``docs/retired/`` (``git mv`` when tracked)."""
    src = env.path("CLAUDE.md")
    if not os.path.isfile(src):
        return None
    first = fsutil.read_text(src, "").split("\n", 1)[0]
    if CLAUDE_MARKER in first:
        return None
    if env.dry_run:
        env.man.retired.append(("CLAUDE.md", RETIRED_CLAUDE))
        return RETIRED_CLAUDE

    _ensure_dir(env, os.path.dirname(env.path(RETIRED_CLAUDE)))
    dst = env.path(RETIRED_CLAUDE)
    if os.path.exists(dst):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        rel = RETIRED_CLAUDE.replace(".md", "-%s.md" % stamp)
    else:
        rel = RETIRED_CLAUDE
    moved = False
    if env.git_top and git_tracked(env.repo, "CLAUDE.md"):
        rc, _out = _git(env.repo, "mv", "CLAUDE.md", rel)
        moved = rc == 0
    if not moved:
        os.replace(src, env.path(rel))
    env.man.retired.append(("CLAUDE.md", rel))
    return rel


def merge_project_settings(env):
    """Merge ``settings/project.snippet.json`` into ``.claude/settings.json``."""
    rel = ".claude/settings.json"
    path = env.path(rel)
    # empty or 1-byte file counts as absent (P7 writes it fresh)
    if os.path.isfile(path) and os.path.getsize(path) <= 1:
        current = {}
    else:
        current = fsutil.read_json(path, {}) or {}
    snippet = sm.fill(sm.load_snippet(name="project"), env.py_exe, env.pa3_dir)
    merged, report = sm.merge(current, snippet, scope="project")
    if merged == current:
        env.man.skipped.append(rel)
        return "same", report, None
    result, backup = write_changed(env, rel, sm.render(merged))
    return result, report, backup


def _absent(env, rel, make):
    """Write ``rel`` from ``make(env)`` only when it does not exist (developer-owned)."""
    if os.path.isfile(env.path(rel)):
        env.man.skipped.append(rel)
        return "same"
    return place(env, rel, text=make(env))


def claude_and_settings(env):
    """P7: CLAUDE.md (+ retire), project settings, HOW_WE_WORK.md."""
    if env.layout == "pa3":
        step("P7", "claude + settings", "SKIP",
             "pa3 upgrade: CLAUDE.md, settings.json, HOW_WE_WORK.md left alone")
        return "SKIP"
    mark = env.man.mark()

    retired = retire_claude_md(env)
    _absent(env, "CLAUDE.md", claude_md_text)

    _settings, report, backup = merge_project_settings(env)

    # memory stub moved to P6 (memory_sort.link)

    _absent(env, "HOW_WE_WORK.md", how_we_work_text)

    done = env.man.since(mark)
    status = "SKIP" if (env.dry_run or not Manifest.changed(done)) else "DONE"
    step("P7", "claude + settings", status,
         _reason(env, done, "CLAUDE.md, .claude/settings.json, HOW_WE_WORK.md"))
    if retired:
        note("retired     CLAUDE.md -> %s" % retired)
    for key in report["added"]:
        note("+ settings  %s" % key)
    for key, old, new in report["changed"]:
        note("~ settings  %s: %s -> %s"
             % (key, json.dumps(old, default=str), json.dumps(new, default=str)))
    for text in report["notes"]:
        note("note: %s" % text)
    if backup:
        note("backup      %s" % sm.abs_fwd(backup))
    _detail(env, done)
    return status


# --------------------------------------------------------------------------- P9

def _import_ledger_slices(env):
    """P9: import ``<repo>/.claude-state/ledger/*.jsonl.gz`` into the local ledger
    (T8.c2).  The ledger is per-machine, not per-project: any failure here is a
    note, never a reason to fail the install."""
    files = sorted(glob.glob(env.path(".claude-state/ledger/*.jsonl.gz")))
    if not files:
        step("P9", "ledger slices", "SKIP", "no slices")
        return "SKIP"
    if env.dry_run:
        note("would import %d slice(s)" % len(files))
        step("P9", "ledger slices", "SKIP", "dry-run")
        return "SKIP"
    try:
        from .. import ledger_cli, config
        conn = ledger_cli.open_db()
        try:
            res = ledger_cli.import_slices(conn, env.repo, config.load())
        finally:
            conn.close()
    except Exception as exc:                                     # noqa: BLE001
        note("note: ledger import skipped: %s: %s" % (type(exc).__name__, exc))
        step("P9", "ledger slices", "SKIP", "ledger import failed")
        return "SKIP"

    total = sum(sum(info["inserted"].values()) for info in res.values())
    if not total:
        step("P9", "ledger slices", "SKIP", "nothing new")
        return "SKIP"
    parts = []
    for path in sorted(res):
        ins = res[path]["inserted"]
        n = sum(ins.values())
        if n:
            detail = ", ".join("%s %d" % (t, c) for t, c in sorted(ins.items()) if c)
            parts.append("%s: %s" % (os.path.basename(path), detail))
    step("P9", "ledger slices", "DONE", "; ".join(parts))
    return "DONE"


# --------------------------------------------------------------------------- P10

def commit(env):
    """P10: ``git add`` the manifest by explicit path, one commit, no trailer."""
    paths_rel = env.man.commit_paths
    if not paths_rel:
        return "SKIP", "nothing to commit"
    if env.dry_run:
        return "SKIP", "dry-run: would commit %d path(s) as %r" % (len(paths_rel), COMMIT_MSG)
    if env.no_commit:
        return "SKIP", "--no-commit: %d path(s) left unstaged" % len(paths_rel)
    if not env.git_top:
        return "SKIP", "not a git repository: nothing committed"

    failed = []
    for i in range(0, len(paths_rel), 60):
        chunk = paths_rel[i:i + 60]
        rc, _out = _git(env.repo, "add", "--", *chunk)
        if rc != 0:
            for rel in chunk:
                if _git(env.repo, "add", "--", rel)[0] != 0:
                    failed.append(rel)
    rc, out = _git(env.repo, "diff", "--cached", "--name-only")
    if rc != 0 or not out.strip():
        return "SKIP", "nothing staged (already committed?)"
    rc, out = _git(env.repo, "commit", "-m", COMMIT_MSG)
    if rc != 0:
        last = (out.strip().splitlines() or ["git commit failed"])[-1]
        return "FAIL", "git commit refused: %s" % last.strip()
    _rc, head = _git(env.repo, "log", "-1", "--format=%h %s")
    if failed:
        note("warn: not staged (ignored by .gitignore?): %s" % ", ".join(failed[:6]))
    return "DONE", head.strip() or COMMIT_MSG


def close(env):
    """P10: the manifest, the commit and the relaunch line."""
    man = env.man
    counts = "%d created, %d updated, %d already current, %d left alone" % (
        len(man.created), len(man.updated) + len(man.forced), len(man.skipped),
        len(man.edited))
    status, reason = commit(env)
    step("P10", "close", "SKIP" if env.dry_run else status,
         "dry-run: %s" % counts if env.dry_run else "%s; %s" % (counts, reason))

    if not env.dry_run:                       # the steps already printed the plan
        for rel in man.created[:12]:
            note("+ %s" % rel)
        if len(man.created) > 12:
            note("... %d more created" % (len(man.created) - 12))
        for rel in man.updated + man.forced:
            note("~ %s" % rel)
        for rel in man.removed:
            note("- %s" % rel)
    for src, dst in man.retired:
        note("> %s -> %s" % (src, dst))
    if man.edited:
        note("kept (project-edited; --force overwrites): %s"
             % ", ".join(man.edited[:8]) + (" ..." if len(man.edited) > 8 else ""))
    for warn in env.warnings:
        note("warn: %s" % warn)
    note("Next: open `claude` in the repo (the pa-session agent takes it from there)")
    return status if status != "SKIP" else "SKIP"


# --------------------------------------------------------------------------- driver

def _reason(env, done, what):
    if env.dry_run:
        n = Manifest.changed(done)
        return "dry-run: would write %d file(s) -- %s" % (n, what)
    n = Manifest.changed(done)
    if not n:
        return "%d file(s) already current -- %s" % (len(done["skipped"]), what)
    return "%d file(s) written, %d already current -- %s" % (n, len(done["skipped"]), what)


def _detail(env, done):
    for rel in done["edited"]:
        note("= %s (project-edited, kept; --force overwrites)" % rel)
    if env.dry_run:
        for rel in (done["created"] + done["forced"])[:12]:
            note("+ %s" % rel)
        extra = len(done["created"]) + len(done["forced"]) - 12
        if extra > 0:
            note("... %d more" % extra)


def _memory_link(env):
    """P6: ensure .claude-state/memory/ and the projects-dir link."""
    return memory_sort.link(env)


def install_project(opts):
    """Run P1..P3, P6 (memory link), P7, P10 for a fresh or already-PA3 repository."""
    print("Project Architect 3.0 -- per-repo install (--project)%s"
          % ("  [dry run]" if opts.dry_run else ""))
    env = detect_project(opts)
    if env is None:
        print("FAIL (P1)")
        return 1

    if env.layout in detect_mod.MIGRATION:
        if not confirm(env, opts):
            return 1
        from . import migrate
        import importlib
        try:                                    # pa/install/<layout>.py with mapping(env)
            layout_mod = importlib.import_module("." + env.layout, __package__)
        except ImportError:
            print("detected %s: migration not implemented for this layout yet" % env.layout)
            note("nothing was written. %s" % detect_mod.evidence(env.signals, env.layout))
            return 1
        return migrate.run(env, layout_mod)
    if not confirm(env, opts):
        return 1

    results = {}
    try:
        results["P2"] = bootstrap(env)
        results["P3"] = copy_files(env)
        results["P6"] = _memory_link(env)
        results["P7"] = claude_and_settings(env)
        results["P9"] = _import_ledger_slices(env)
    except (OSError, ValueError, sm.MergeError) as exc:
        step("P10", "close", "FAIL", "%s: %s" % (type(exc).__name__, exc))
        note("partial install: re-run the same command once the cause is fixed "
             "(every step is idempotent)")
        return 1
    results["P10"] = close(env)

    if "FAIL" in results.values():
        print("FAIL (%s)" % ", ".join(k for k, v in sorted(results.items()) if v == "FAIL"))
        return 1
    print("OK%s" % (" (dry run -- nothing was written)" if env.dry_run else ""))
    return 0
