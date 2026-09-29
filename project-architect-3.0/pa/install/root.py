"""``pa_install.py --root`` -- the per-machine install (design B J.1, R1..R5).

Public:
    install_root(opts)              run R1..R5; returns a process exit code
    detect(opts)                    R1: the machine facts (an :class:`Env`)
    copy_package(env, opts)         R2: <pkg> -> <pa3-dir>, VERSION, byte compare
    init_ledger(env, opts)          R3: usage-ledger tree, schema, config.json
    merge_settings(env, opts)       R4: <config-dir>/settings.json (backup first)
    manifest(env, opts, results)    R5: what changed + the two reminders
    detect_interpreter(explicit)    the ``py -3`` / ``sys.executable`` rule
    claude_version(), git_short_hash(path)

Discipline carried from ``project-architect-2.0/SETUP.md`` §0: every step is
``check -> SKIP | do -> verify`` and prints one ``SKIP``/``DONE``/``FAIL`` line
with a one-line reason, so a second ``--root`` is a no-op and the run needs no
resume pointer.  Nothing is ever deleted: the settings file is backed up before
it is rewritten and the retired PA2 guard scripts are renamed ``*.retired``.

Two directories are deliberately independent: the **config dir** (``~/.claude``
or ``~/.claude-vantage`` or ``$CLAUDE_CONFIG_DIR``) receives ``settings.json``,
while the **package** (``<home>/.claude/pa3``) and the **ledger**
(``<home>/.claude/usage-ledger``) are machine-global and shared by every config
dir.  ``--pa3-dir`` and ``$PA_LEDGER_DIR`` override the latter two for tests.
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

from .. import config as pa_config
from .. import fsutil
from .. import paths
from . import settings_merge as sm

MIN_CLAUDE = (2, 1, 270)
ENTRY_SCRIPTS = ("pa_hook.py", "pa_statusline.py", "pa_ledger.py", "pa_install.py")
COPY_SUFFIXES = (".py", ".ps1")
GUARD_SCRIPTS = ("ctx_guard.sh", "ctx90.sh")

REMINDERS = (
    "Remote Control: open /config and turn the push toggles on if you want "
    "questions on your phone.",
    "Turn \"recaps\" OFF in /config: away_summary requests are billed but never "
    "appear in a transcript (2.7-4.5 % of session cost).",
)

# 3.11 T6: the one thing to do after a machine install
NEXT_STEP = ("\nNext: open one of your repositories in Claude Code; PA3 shows the one line that sets it up\n"
             "there. Or double-click setup-project.cmd (Windows) beside install.cmd and pick the folder.")

_PROBE_CACHE = {}


# --------------------------------------------------------------------------- output

def _line(sid, title, status, reason):
    print("%-3s %-18s %-4s  %s" % (sid, title, status, reason))


def _note(text):
    print("      %s" % text)


class Env(object):
    """The R1 facts, passed to every later step."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):                                           # pragma: no cover
        return "Env(%s)" % ", ".join("%s=%r" % kv for kv in sorted(self.__dict__.items()))


# --------------------------------------------------------------------------- probes

def package_root():
    """The package this installer was started from (``project-architect-3.0/``)."""
    here = os.path.dirname(os.path.abspath(__file__))              # <pkg>/pa/install
    return os.path.dirname(os.path.dirname(here))


def _run(cmd, timeout=30):
    """Run a probe; returns stdout+stderr or ``None``.  Never raises."""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")


def _py_launcher():
    """``py -3`` resolved to the interpreter it would start (Windows only)."""
    if "py" in _PROBE_CACHE:
        return _PROBE_CACHE["py"]
    found = None
    exe = shutil.which("py")
    if exe:
        out = _run([exe, "-3", "-c", "import sys;print(sys.executable)"], timeout=30)
        if out:
            cand = out.strip().splitlines()[-1].strip() if out.strip() else ""
            if cand and os.path.isfile(cand):
                found = cand
    _PROBE_CACHE["py"] = found
    return found


def detect_interpreter(explicit=None):
    """``(absolute forward-slash path, how it was chosen)`` for the hook commands."""
    if explicit:
        return sm.abs_fwd(explicit), "--python"
    if os.name == "nt":
        cand = _py_launcher()
        if cand:
            return sm.abs_fwd(cand), "py -3 launcher"
    return sm.abs_fwd(sys.executable or "python"), "sys.executable"


def claude_version():
    """``"2.1.270"`` or ``None`` when the CLI is not on PATH."""
    if "claude" in _PROBE_CACHE:
        return _PROBE_CACHE["claude"]
    ver = None
    exe = shutil.which("claude")
    if exe:
        out = _run([exe, "--version"], timeout=60)
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
        if m:
            ver = m.group(0)
    _PROBE_CACHE["claude"] = ver
    return ver


def _version_tuple(text):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def git_short_hash(path):
    """Short HEAD hash of the repo holding ``path`` (``None`` outside a repo)."""
    key = "git:" + os.path.abspath(path)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    out = None
    exe = shutil.which("git")
    if exe:
        got = _run([exe, "-C", path, "rev-parse", "--short", "HEAD"], timeout=20)
        if got:
            cand = got.strip().splitlines()[0].strip() if got.strip() else ""
            if re.match(r"^[0-9a-f]{6,40}$", cand):
                out = cand
    _PROBE_CACHE[key] = out
    return out


def _behind_count(clone):
    """How many commits the clone is behind its upstream (0 when unknown)."""
    exe = shutil.which("git")
    if not exe:
        return 0
    out = _run([exe, "-C", clone, "rev-list", "--count", "HEAD..@{u}"], timeout=10)
    if out:
        line = out.strip().splitlines()[0].strip() if out.strip() else ""
        if line.isdigit():
            return int(line)
    return 0


def ensure_clone(env, source, timeout_s):
    """Clone or update the package source repository under the config dir.

    Returns ``{status, path, head, behind, cause}``; status is one of
    ``cloned|pulled|current|offline|absent``.  Never raises.

    ``cause`` is ``None`` unless status is ``offline``; then it is the last
    non-empty stderr line of the failed fetch/clone, ``timeout after <n>s`` or the
    exception text, or ``pull --ff-only failed``.  ``behind`` is ``None`` (unknown)
    after a failed fetch or fresh clone: the stale ``@{u}`` count is not real.  A
    pull that fails after a good fetch keeps the real count.
    """
    clone_dir_name = getattr(env, "_clone_dir_name", None) or "pa3-src"
    clone = os.path.join(env.config_dir, clone_dir_name)
    result = {"status": "absent", "path": sm.abs_fwd(clone), "head": None, "behind": 0,
              "cause": None}
    exe = shutil.which("git")
    if not exe:
        return result

    def _offline(cause, head=None):
        result.update(status="offline", head=head, behind=None, cause=cause)
        return result

    def _exc_cause(exc):
        if isinstance(exc, subprocess.TimeoutExpired):
            return "timeout after %ss" % timeout_s
        return str(exc) or exc.__class__.__name__

    def _stderr_cause(proc, fallback):
        text = (proc.stderr or b"").decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else fallback

    if os.path.isdir(os.path.join(clone, ".git")):
        # existing clone: fetch + pull
        try:
            proc = subprocess.run(
                [exe, "-C", clone, "fetch", "--quiet"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return _offline(_exc_cause(exc), git_short_hash(clone))
        if proc.returncode != 0:
            return _offline(_stderr_cause(proc, "git fetch exited %d" % proc.returncode),
                            git_short_hash(clone))
        behind = _behind_count(clone)
        result["behind"] = behind
        if behind == 0:
            result["status"] = "current"
            result["head"] = git_short_hash(clone)
            return result
        # pull --ff-only
        try:
            pull = subprocess.run([exe, "-C", clone, "pull", "--ff-only"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout_s)
            pulled = pull.returncode == 0
        except (OSError, ValueError, subprocess.SubprocessError):
            pulled = False
        if pulled:
            result["status"] = "pulled"
            result["behind"] = 0
        else:
            result["status"] = "offline"                          # behind keeps the real n
            result["cause"] = "pull --ff-only failed"
        result["head"] = git_short_hash(clone)
        return result

    # no clone yet: git clone
    try:
        os.makedirs(os.path.dirname(clone), exist_ok=True)
        proc = subprocess.run(
            [exe, "clone", "-c", "core.autocrlf=false", source, clone],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return _offline(_exc_cause(exc))
    if proc.returncode != 0:
        return _offline(_stderr_cause(proc, "git clone exited %d" % proc.returncode))
    result["status"] = "cloned"
    result["head"] = git_short_hash(clone)
    return result


def clone_warning(info):
    """The one-line ``WARN clone offline: ...`` for an offline :func:`ensure_clone` result, else ``None``."""
    if not info or info.get("status") != "offline":
        return None
    behind = info.get("behind")
    return "WARN clone offline: %s at %s, behind %s: %s" % (
        info.get("path"), info.get("head") or "none",
        "unknown" if behind is None else behind, info.get("cause") or "unknown")


def _git_bash():
    exe = shutil.which("bash")
    if exe:
        return sm.abs_fwd(exe)
    if os.name == "nt":
        for cand in (r"C:\Program Files\Git\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(cand):
                return sm.abs_fwd(cand)
    return None


# --------------------------------------------------------------------------- R1

def detect(opts):
    """R1: OS, WSL, interpreter, Claude Code version, config dir, Git Bash."""
    py_exe, py_how = detect_interpreter(getattr(opts, "python", None))
    cfg_dir = getattr(opts, "config_dir", None) or paths.config_dir()
    pa3_dir = getattr(opts, "pa3_dir", None) or paths.install_dir()
    env = Env(
        machine=paths.machine_tag(),
        platform=sys.platform,
        is_wsl=paths.is_wsl(),
        py_exe=py_exe,
        py_how=py_how,
        py_exists=os.path.isfile(py_exe),
        claude=claude_version(),
        config_dir=sm.abs_fwd(cfg_dir),
        pa3_dir=sm.abs_fwd(pa3_dir),
        ledger_dir=sm.abs_fwd(paths.ledger_dir()),
        pkg=sm.abs_fwd(package_root()),
        bash=_git_bash(),
        settings_path=sm.abs_fwd(os.path.join(cfg_dir, "settings.json")),
        warnings=[],
    )

    ver_t = _version_tuple(env.claude)
    if env.claude is None:
        env.warnings.append("claude CLI not found on PATH -- version not checked")
    elif ver_t and ver_t < MIN_CLAUDE:
        env.warnings.append("claude %s < %s: SubagentStart/Stop and rate_limits may be missing"
                            % (env.claude, ".".join(str(x) for x in MIN_CLAUDE)))
    if not env.py_exists:
        env.warnings.append("interpreter %s does not exist on this machine "
                            "(fine for a cross-machine install)" % env.py_exe)
    if env.machine == "win" and not env.bash:
        env.warnings.append("Git Bash not found (only needed for tools/*.sh)")

    _line("R1", "detect", "DONE", "%s%s - %s (%s)"
          % (env.machine, " (wsl)" if env.is_wsl else "",
             env.claude and ("claude " + env.claude) or "claude not found", env.py_how))
    _note("python      %s" % env.py_exe)
    _note("config dir  %s" % env.config_dir)
    _note("pa3 dir     %s" % env.pa3_dir)
    _note("ledger dir  %s" % env.ledger_dir)
    _note("package     %s" % env.pkg)
    _note("git bash    %s" % (env.bash or "not found"))
    for warn in env.warnings:
        _note("warn: %s" % warn)
    return env


# --------------------------------------------------------------------------- R2

def package_files(pkg):
    """``[(relative posix path, absolute source)]`` of everything ``--root`` installs."""
    out = []
    pa_dir = os.path.join(pkg, "pa")
    for dirpath, dirnames, filenames in os.walk(pa_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(COPY_SUFFIXES):
                src = os.path.join(dirpath, name)
                out.append((os.path.relpath(src, pkg).replace("\\", "/"), src))
    for name in ENTRY_SCRIPTS:
        src = os.path.join(pkg, name)
        if os.path.isfile(src):
            out.append((name, src))
    snip = os.path.join(pkg, "settings")
    if os.path.isdir(snip):
        for name in sorted(os.listdir(snip)):
            if name.endswith(".json"):
                out.append(("settings/" + name, os.path.join(snip, name)))
    fixture = os.path.join(pkg, "tests", "fixtures", "hooks", "probe1.log")
    if os.path.isfile(fixture):
        out.append(("tests/fixtures/hooks/probe1.log", fixture))
    return out


def _git_tree(path, spec):
    """Full tree id of ``spec`` (``HEAD:<dir>``) in the repo at ``path`` (``None`` on failure)."""
    exe = shutil.which("git")
    if not exe:
        return None
    got = _run([exe, "-C", path, "rev-parse", spec], timeout=20)
    cand = got.strip().splitlines()[0].strip() if got and got.strip() else ""
    return cand if re.match(r"^[0-9a-f]{40,64}$", cand) else None


def version_text(pkg, clone_path=None, clone_head=None):
    """Contents of ``<pa3-dir>/VERSION`` (deterministic: no timestamp).

    When installed from a clone, ``source:`` names the clone path (``/`` separators)
    and ``git:`` carries the clone's HEAD hash; ``tree:`` carries the full tree id of
    ``project-architect-3.0`` at the clone's HEAD (the package dir in a pkg repo).
    """
    from .. import __version__

    lines = ["version: %s" % __version__]
    if clone_head:
        lines.append("git: %s" % clone_head)
    elif clone_path:
        h = git_short_hash(clone_path)
        if h:
            lines.append("git: %s" % h)
    else:
        short = git_short_hash(pkg)
        if short:
            lines.append("git: %s" % short)
    tree = _git_tree(clone_path, "HEAD:project-architect-3.0") if clone_path \
        else _git_tree(pkg, "HEAD:./")
    if tree:
        lines.append("tree: %s" % tree)
    source = clone_path if clone_path else pkg
    lines.append("source: %s" % sm.abs_fwd(source))
    return "\n".join(lines) + "\n"


def _same_bytes(src, dst):
    try:
        with open(src, "rb") as a, open(dst, "rb") as b:
            return a.read() == b.read()
    except OSError:
        return False


def copy_package(env, opts):
    """R2: copy ``pa/`` + the entry scripts into ``<pa3-dir>``, byte-compare, VERSION."""
    # When a clone exists and --no-clone is not set, read the package from the clone.
    clone_path = getattr(env, "clone_path", None)
    clone_head = getattr(env, "clone_head", None)
    no_clone = getattr(opts, "no_clone", False)
    pkg = env.pkg
    if clone_path and not no_clone:
        candidate = os.path.join(clone_path, "project-architect-3.0")
        if os.path.isdir(candidate):
            pkg = candidate

    files = package_files(pkg)
    missing = [n for n in ENTRY_SCRIPTS if not os.path.isfile(os.path.join(pkg, n))]
    todo = [(rel, src) for rel, src in files
            if not _same_bytes(src, os.path.join(env.pa3_dir, rel))]

    ver = version_text(pkg,
                       clone_path=clone_path if (clone_path and not no_clone) else None,
                       clone_head=clone_head if (clone_path and not no_clone) else None)
    ver_path = os.path.join(env.pa3_dir, "VERSION")
    ver_stale = fsutil.read_text(ver_path, "") != ver

    result = {"files": len(files), "copied": 0, "missing": missing, "status": "SKIP"}

    if opts.dry_run:
        result["status"] = "SKIP"
        _line("R2", "package copy", "SKIP",
              "dry-run: would write %d of %d files to %s"
              % (len(todo) + (1 if ver_stale else 0), len(files) + 1, env.pa3_dir))
        for rel, _src in todo[:8]:
            _note("+ %s" % rel)
        if len(todo) > 8:
            _note("... %d more" % (len(todo) - 8))
    elif not todo and not ver_stale:
        _line("R2", "package copy", "SKIP", "%d files already byte-identical in %s"
              % (len(files), env.pa3_dir))
    else:
        try:
            for rel, src in todo:
                dst = os.path.join(env.pa3_dir, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                if not _same_bytes(src, dst):
                    raise OSError("byte compare failed for %s" % rel)
                result["copied"] += 1
            if ver_stale:
                fsutil.atomic_write_text(ver_path, ver)
        except OSError as exc:
            result["status"] = "FAIL"
            _line("R2", "package copy", "FAIL", str(exc))
            return result
        result["status"] = "DONE"
        _line("R2", "package copy", "DONE", "%d of %d files written to %s (byte-compared)"
              % (result["copied"], len(files), env.pa3_dir))

    if missing:
        _note("warn: not in the package yet: %s (their hooks no-op until installed)"
              % ", ".join(missing))
    return result


# --------------------------------------------------------------------------- R3

def _renewal_flags(opts):
    """``--renewal-day EMAIL=N`` (one account), repeatable; a bare ``N`` is returned but names no account
    and is ignored (3.11 T9: no global renewal day)."""
    raw = getattr(opts, "renewal_day", None)
    items = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
    fallback, per_account = None, {}
    for item in items:
        text = str(item).strip()
        if "=" in text:
            email, _, n = text.partition("=")
            per_account[email.strip()] = n.strip()
        elif text:
            fallback = text
    return fallback, per_account


def _apply_renewal_flags(cfg, opts):
    """Write every ``EMAIL=N`` flag into ``accounts.<email>.renewal_day``; True when anything changed."""
    changed = False
    for email, n in _renewal_flags(opts)[1].items():
        try:
            day = min(28, max(1, int(n)))
        except (TypeError, ValueError):
            continue
        accounts = cfg.setdefault("accounts", {})
        entry = accounts.get(email)
        if not isinstance(entry, dict):
            entry = accounts[email] = {}
        if entry.get("renewal_day") != day:
            entry["renewal_day"] = day
            changed = True
    return changed


def _wsl_ledger_glob():
    """Candidate Windows ledger dirs seen from WSL (T8: its own function so a
    test can monkeypatch it and pin the suggestion instead of depending on
    whatever ``/mnt/c/Users/*/.claude/usage-ledger`` happens to exist on the
    machine running the suite)."""
    import glob

    return sorted(glob.glob("/mnt/c/Users/*/.claude/usage-ledger"))


def _suggest_extra_roots(env):
    """The other machine's ledger, when it is reachable and obvious."""
    if env.machine == "wsl":
        for cand in _wsl_ledger_glob():
            return [cand.replace("\\", "/")]
    return []


def _labels_from_opts(opts):
    out = {}
    for item in (opts.label or []):
        if "=" in item:
            email, label = item.split("=", 1)
            email, label = email.strip(), label.strip()
            if email and label:
                out[email] = {"label": label}
    return out


def _now_iso_utc():
    """UTC ISO-8601 with a trailing ``Z`` (matches ``transcript.iso()``)."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _db_ready(db, path):
    if not os.path.isfile(path):
        return False
    conn = None
    try:
        conn = db.connect(path, create=False)
        if db.schema_version(conn) != db.SCHEMA_VERSION:
            return False
        have = set(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        return all(t in have for t in db.TABLES)
    except Exception:
        return False
    finally:
        if conn is not None:
            db.close(conn)


def _dropped_keys(existing):
    """Dotted keys of the raw config.json that ``pa_config.slim`` would drop."""
    dropped = []
    pa_config.slim(existing, dropped)
    return dropped


def init_ledger(env, opts):
    """R3: ``usage-ledger/`` tree, ``ledger.sqlite`` schema, ``config.json``."""
    ledger = env.ledger_dir
    db_path = sm.abs_fwd(os.path.join(ledger, "ledger.sqlite"))
    cfg_path = sm.abs_fwd(os.path.join(ledger, "config.json"))
    result = {"status": "SKIP", "db": "SKIP", "config": "SKIP", "config_path": cfg_path}

    from .. import db as pa_db

    have_db = _db_ready(pa_db, db_path)
    have_cfg = os.path.isfile(cfg_path)

    if opts.dry_run:
        if have_cfg:
            existing = fsutil.read_json(cfg_path, {}) or {}
            for dotted in _dropped_keys(existing):
                _note("would drop: %s (a current or past default)" % dotted)
        _line("R3", "ledger", "SKIP", "dry-run: would %s %s and %s config.json"
              % ("keep" if have_db else "create", db_path,
                 "keep" if have_cfg else "write"))
        return result

    try:
        paths.ensure_ledger_tree()
        if not have_db:
            conn = pa_db.connect(db_path)
            try:
                pa_db.init_schema(conn, machine=env.machine)
            finally:
                pa_db.close(conn)
            result["db"] = "DONE"
    except Exception as exc:                                       # pragma: no cover
        result["status"] = "FAIL"
        _line("R3", "ledger", "FAIL", "%s: %s" % (type(exc).__name__, exc))
        return result

    if have_cfg:
        existing = fsutil.read_json(cfg_path, {}) or {}
        merged = pa_config.merge(pa_config.defaults(), existing)
        _apply_renewal_flags(merged, opts)
        if merged.get("installed_at") is None:              # T10: set once, never moved
            merged["installed_at"] = _now_iso_utc()
        # T12: config.json keeps only user-set keys and overrides; current and past
        # defaults (T5's superseded values) are dropped, the package supplies them
        dropped = _dropped_keys(existing)
        for dotted in dropped:
            _note("note: dropped %s (a current or past default)" % dotted)
        if pa_config.slim(merged) != existing:
            added = sorted(set(merged) - set(existing))
            pa_config.save(merged, cfg_path)
            result["config"] = "DONE"
            result["added_keys"] = added
        if dropped:
            result["migrated"] = len(dropped)
    else:
        cfg = pa_config.defaults()
        cfg["machine"] = env.machine
        cfg["python"] = env.py_exe
        cfg["installed_at"] = _now_iso_utc()

        # 3.11 T6: the machine install asks nothing (developer 2026-09-28); every answer stays a flag.
        # 3.11 T9: no global renewal day; each account's own is asked when first needed, never guessed
        if _renewal_flags(opts)[0] is not None:
            _note("note: a bare --renewal-day N names no account and is ignored; use --renewal-day EMAIL=N")

        roots = list(opts.extra_root or []) or _suggest_extra_roots(env)   # was: asked, suggestions the default
        cfg["extra_roots"] = [sm.abs_fwd(r) for r in roots]

        accounts = _labels_from_opts(opts)                                  # was: asked at a terminal
        cfg["accounts"] = accounts
        _apply_renewal_flags(cfg, opts)                 # after the labels, which set the block
        if accounts and not cfg.get("recalc_default_account"):
            cfg["recalc_default_account"] = sorted(accounts)[0]

        problems = pa_config.validate(cfg)
        if problems:
            result["status"] = "FAIL"
            _line("R3", "ledger", "FAIL", "config.json invalid: %s" % "; ".join(problems))
            return result
        pa_config.save(cfg, cfg_path)
        result["config"] = "DONE"

    for flag, name in ((opts.renewal_day, "--renewal-day"), (opts.extra_root, "--extra-root"),
                       (opts.label, "--label")):
        if have_cfg and flag:
            _note("note: %s ignored -- config.json already exists (edit it to change)" % name)

    if result["db"] == "SKIP" and result["config"] == "SKIP":
        _line("R3", "ledger", "SKIP", "%s: schema and config.json already present" % ledger)
    else:
        result["status"] = "DONE"
        bits = []
        bits.append("schema created" if result["db"] == "DONE" else "schema present")
        if result.get("migrated"):
            bits.append("config.json migrated (%d key(s))" % result["migrated"])
        elif result["config"] == "DONE":
            bits.append("config.json written")
        else:
            bits.append("config.json present")
        _line("R3", "ledger", "DONE", "%s: %s" % (ledger, ", ".join(bits)))
    return result


# --------------------------------------------------------------------------- R4

def _under(path, directory):
    a = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(directory))
    return a.startswith(b + os.sep)


def _resolve_guard(token):
    t = (token or "").replace('"', "").replace("'", "").strip()
    if not t:
        return None
    t = t.replace("$HOME", "~").replace("${HOME}", "~").replace("%USERPROFILE%", "~")
    return os.path.abspath(os.path.expanduser(t))


def retire_guard_scripts(config_dir, tokens, dry_run=False):
    """Rename ``ctx_guard.sh``/``ctx90.sh`` under the config dir to ``*.retired``."""
    cands = [_resolve_guard(t) for t in (tokens or [])]
    cands += [os.path.join(config_dir, name) for name in GUARD_SCRIPTS]
    done, seen = [], set()
    for cand in cands:
        if not cand:
            continue
        key = os.path.normcase(os.path.abspath(cand))
        if key in seen:
            continue
        seen.add(key)
        if not _under(cand, config_dir) or not os.path.isfile(cand):
            continue
        target = cand + ".retired"
        if os.path.exists(target):
            continue
        if not dry_run:
            try:
                os.replace(cand, target)
            except OSError:
                continue
        done.append(sm.abs_fwd(target))
    return done


def _backup(settings_path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = "%s.bak-%s" % (settings_path, stamp)
    path, n = base, 1
    while os.path.exists(path):
        path = "%s.%d" % (base, n)
        n += 1
    with open(settings_path, "rb") as src:
        raw = src.read()
    with open(path, "wb") as dst:
        dst.write(raw)
    return path, raw


def merge_settings(env, opts):
    """R4: merge ``settings/user.snippet.json`` into ``<config-dir>/settings.json``."""
    result = {"status": "SKIP", "report": None, "backup": None, "retired": [],
              "statusline_pa2": None}
    path = env.settings_path
    current = fsutil.read_json(path, {}) or {}

    # T20: the snippet's `Bash({{PY}} tools/pa3_update.py)` rule pre-approves `update pa3`
    # (replaces T16's per-clone pull / --root allow entries).
    snippet = sm.fill(sm.load_snippet(), env.py_exe, env.pa3_dir)
    try:
        merged, report = sm.merge(current, snippet,
                                  hooks=(opts.hooks != "off"),
                                  statusline=not opts.no_statusline)
    except sm.MergeError as exc:
        _line("R4", "settings merge", "FAIL", str(exc))
        result["status"] = "FAIL"
        return result
    result["report"] = report

    changed = (merged != current)
    n_changes = (len(report["added"]) + len(report["changed"]) + len(report["hooks_added"])
                 + len(report["hooks_replaced"]) + len(report["hooks_removed"]))

    if opts.dry_run:
        _line("R4", "settings merge", "SKIP", "dry-run: %d change(s) for %s"
              % (n_changes, path))
        _print_diff(report, snippet, opts)
        return result

    if not changed:
        _line("R4", "settings merge", "SKIP", "%s already carries every PA3 key" % path)
        return result

    text = sm.render(merged)
    try:
        if json.loads(text) != merged:                              # pragma: no cover
            raise ValueError("round-trip mismatch")
    except ValueError as exc:                                       # pragma: no cover
        _line("R4", "settings merge", "FAIL", "merged JSON does not parse: %s" % exc)
        result["status"] = "FAIL"
        return result

    backup, raw = (None, None)
    try:
        os.makedirs(env.config_dir, exist_ok=True)
        if os.path.isfile(path):
            backup, raw = _backup(path)
            result["backup"] = sm.abs_fwd(backup)
        fsutil.atomic_write_text(path, text)
        with open(path, "r", encoding="utf-8") as fh:
            json.load(fh)
    except (OSError, ValueError) as exc:
        if backup and raw is not None:
            try:
                with open(path, "wb") as fh:
                    fh.write(raw)
            except OSError:
                pass
        _line("R4", "settings merge", "FAIL", "%s (restored %s)" % (exc, backup))
        result["status"] = "FAIL"
        return result

    if report["statusline_old"]:
        pa2 = os.path.join(env.config_dir, "statusline.sh.pa2")
        fsutil.atomic_write_text(pa2, _statusline_pa2_text(report["statusline_old"]))
        result["statusline_pa2"] = sm.abs_fwd(pa2)

    if opts.hooks != "off":
        result["retired"] = retire_guard_scripts(env.config_dir, report["retired_scripts"])

    result["status"] = "DONE"
    _line("R4", "settings merge", "DONE", "%d change(s) in %s (backup %s)"
          % (n_changes, path, os.path.basename(backup) if backup else "not needed"))
    _print_diff(report, snippet, opts)
    if result["statusline_pa2"]:
        _note("kept old statusLine command in %s" % result["statusline_pa2"])
    for retired in result["retired"]:
        _note("retired %s" % retired)
    return result


def _statusline_pa2_text(old_command):
    return (
        "#!/usr/bin/env bash\n"
        "# Project Architect 3.0 (pa_install.py --root) replaced settings.json\n"
        "# statusLine with the PA3 renderer.  The previous command was:\n"
        "#   %s\n"
        "# To go back, put that line into statusLine.command again.\n"
        % old_command
    )


def _print_diff(report, snippet, opts):
    for key in report["added"]:
        _note("+ %s" % key)
    for key, old, new in report["changed"]:
        _note("~ %s: %s -> %s" % (key, json.dumps(old, default=str), json.dumps(new, default=str)))
    for event, cmd in report["hooks_removed"]:
        _note("- hook %s: %s" % (event, cmd))
    for event, cmd in report["hooks_added"]:
        _note("+ hook %s: %s" % (event, cmd))
    for item in report["hooks_replaced"]:
        _note("~ hook %s %s: %s" % item)
    for note in report["notes"]:
        _note("note: %s" % note)
    if opts.dry_run and opts.hooks != "off":
        added = set(c for _e, c in report["hooks_added"])
        for event, groups in (snippet.get("hooks") or {}).items():
            for group in groups:
                for entry in group.get("hooks") or []:
                    cmd = entry.get("command", "")
                    if cmd not in added:
                        _note("= hook %s: %s" % (event, cmd))


# --------------------------------------------------------------------------- R5

def manifest(env, opts, r2, r3, r4):
    """R5: the added/changed keys, the three directories and the two reminders."""
    report = r4.get("report") or sm.new_report()
    keys = list(report["added"]) + ["%s (was %s)" % (k, json.dumps(o, default=str))
                                    for k, o, _n in report["changed"]]
    hooks_n = len(report["hooks_added"]) + len(report["hooks_replaced"])
    touched = bool(keys or hooks_n or report["hooks_removed"]
                   or r2["status"] == "DONE" or r3["status"] == "DONE")

    status = "SKIP" if (opts.dry_run or not touched) else "DONE"
    reason = ("dry-run: nothing written" if opts.dry_run else
              ("%d settings key(s), %d hook(s)" % (len(keys), hooks_n) if touched
               else "nothing to add -- this machine was already installed"))
    _line("R5", "manifest", status, reason)
    _note("config dir  %s" % env.config_dir)
    _note("pa3 dir     %s" % env.pa3_dir)
    _note("ledger dir  %s" % env.ledger_dir)
    if keys:
        _note("keys        %s" % ", ".join(keys))
    if hooks_n:
        _note("hooks       %d PA entries across %d events"
              % (hooks_n, len(set(e for e, _ in report["hooks_added"])
                              | set(i[0] for i in report["hooks_replaced"]))))
    if report["hooks_removed"]:
        _note("removed     %s" % ", ".join("%s (%s)" % (e, os.path.basename(c.split()[-1]))
                                           for e, c in report["hooks_removed"]))
    if r4.get("backup"):
        _note("backup      %s" % r4["backup"])
    if r2.get("missing"):
        _note("warn        missing entry scripts: %s" % ", ".join(r2["missing"]))
    for reminder in REMINDERS:
        _note("reminder    %s" % reminder)
    return {"status": status}


# --------------------------------------------------------------------------- driver

def install_root(opts):
    """Run R1..R5 in order; returns 0 when every step succeeded."""
    print("Project Architect 3.0 -- per-machine install (--root)%s"
          % ("  [dry run]" if opts.dry_run else ""))
    env = detect(opts)

    # Clone or update the package source repository when --no-clone is absent.
    no_clone = getattr(opts, "no_clone", False)
    env.clone_path = None
    env.clone_head = None
    if not no_clone and not opts.dry_run:
        source = getattr(opts, "source", None)
        if not source:
            try:
                source = pa_config.DEFAULTS["install"]["source"]
            except (KeyError, TypeError):
                source = None
        if source:
            timeout_s = pa_config.DEFAULTS.get("install", {}).get("fetch_timeout_s", 8)
            clone_info = ensure_clone(env, source, timeout_s)
            env.clone_path = clone_info["path"]
            env.clone_head = clone_info["head"]
            _note("clone       %s (%s, head %s%s)"
                  % (clone_info["path"], clone_info["status"],
                     clone_info["head"] or "n/a",
                     ", behind %s" % clone_info["behind"] if clone_info["behind"] else ""))
            warning = clone_warning(clone_info)
            if warning:
                print(warning)
                if not getattr(opts, "allow_stale", False):
                    print("pass --allow-stale to install from it anyway")
                    return 2                                      # before R2: nothing written

    r2 = copy_package(env, opts)
    r3 = init_ledger(env, opts)
    r4 = merge_settings(env, opts)
    r5 = manifest(env, opts, r2, r3, r4)

    failed = [name for name, res in (("R2", r2), ("R3", r3), ("R4", r4), ("R5", r5))
              if res.get("status") == "FAIL"]
    if failed:
        print("FAIL (%s)" % ", ".join(failed))
        return 1
    print("OK%s" % (" (dry run -- nothing was written)" if opts.dry_run else ""))
    if not opts.dry_run:
        print(NEXT_STEP)
    return 0
