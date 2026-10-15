"""The mod's health and its healing (3.24 T6, decisions 5 and 6, section D).

The checks, shared by the session-start hook, the first-prompt check and doctor:

    root_files(pa3)                 the root's files against ``MANIFEST.json`` (VERSION written last)
    registration(settings, pa3)     ``extraKnownMarketplaces.pa3`` -> ``<pa3>/mod``, ``pa@pa3``/``pa-watch@pa3`` on
    floor(pa3, version)             Claude Code at the mod's floor (``VERSION_FLOOR`` in the root's activation.ts)
    heartbeat(root, sid)            ``.run/mod/heartbeat.json`` for ``sid`` and no ``.run/mod/error.json`` for it

The mod writes the heartbeat ``{sid, version, engine, at, plugin}`` when it activates and the error
record ``{sid, plugin, hook, error, at}`` when its activation hooks throw; an inert session writes
neither.  The hooks keep their own record in ``.run/mod/hook.json``: the previous session id seen
(``sid``), the session already healed (``healed``) and the session whose first prompt was checked
(``prompted``).  A session in which a run was started (``healed``) owes no heartbeat check, and
neither does a project whose mod never wrote one (a mod from before 3.24 writes none).

Healing (``heal``): once per session, logged, never a downgrade.  A damaged root, a missing
registration or a mod that did not load starts ``tools/pa3_update.py`` detached: the update when
the clone's ``@{u}`` holds a newer released VERSION and ``upgrade`` is ``auto``, else ``--repair``
(the installed release again, from VERSION ``source:``); under ``ask`` the repair runs at once and
the line also offers the newer release.  The session is marked healed after the run is started.
pa3_update.py is single-flight under ``.run/mod/update.lock`` and writes the run marker
``.run/mod/update.json`` the mod reads.  Below the floor nothing changes.  Nothing installs older
code: ``--repair`` refuses a source that is not the installed release.
"""

import json
import os
import subprocess
import sys
import time

from . import fsutil, log, paths

MOD_RUN = os.path.join(".run", "mod")
HEARTBEAT = "heartbeat.json"
ERROR = "error.json"
HOOK = "hook.json"
MARKER = "update.json"
RUN_STALE_S = 20 * 60          # a running marker older than this is a dead run (pa3_update.py LOCK_STALE_S)
RELOAD = "the mod loads at the next start or at /reload-plugins (a running session keeps its loaded code)"


def _fwd_norm(path):
    p = os.path.abspath(str(path)).replace("\\", "/").rstrip("/")
    return p.lower() if os.name == "nt" else p


def _version_fields(text):
    """``{key: value}`` of VERSION's ``key: value`` lines (first wins)."""
    out = {}
    for line in (text or "").splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            out.setdefault(key.strip(), val.strip())
    return out


# --------------------------------------------------------------------------- the checks

def root_files(pa3=None):
    """``{"state", "bad", "total"}``: state ``ok``, ``none`` (no manifest: a root from before 3.24),
    ``half`` (VERSION is not the one the manifest was written with) or ``bad`` (files differ)."""
    from .install import root as _root
    pa3 = pa3 or paths.install_dir()
    man = fsutil.read_json(os.path.join(pa3, _root.MANIFEST), None)
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict):
        return {"state": "none", "bad": [], "total": 0}
    bad = []
    for rel in sorted(man["files"]):
        try:
            same = _root._sha256(os.path.join(pa3, rel.replace("/", os.sep))) == man["files"][rel]
        except OSError:
            same = False
        if not same:
            bad.append(rel)
    total = len(man["files"])
    if fsutil.read_text(os.path.join(pa3, "VERSION"), "") != man.get("version"):
        return {"state": "half", "bad": bad, "total": total}
    return {"state": "bad" if bad else "ok", "bad": bad, "total": total}


def registration(settings_path, pa3=None):
    """``{"state", "have", "off"}`` for one user settings file: state ``ok``, ``nofile`` (unreadable),
    ``missing``, ``elsewhere`` (declared at another path) or ``off`` (a plugin not enabled)."""
    from .install import settings_merge as _sm
    data = fsutil.read_json(settings_path, None)
    if not isinstance(data, dict):
        return {"state": "nofile", "have": None, "off": []}
    want = _fwd_norm(os.path.join(pa3 or paths.install_dir(), "mod"))
    mks = data.get("extraKnownMarketplaces")
    mk = mks.get(_sm.MARKETPLACE) if isinstance(mks, dict) else None
    src = mk.get("source") if isinstance(mk, dict) else None
    have = src.get("path") if isinstance(src, dict) and src.get("source") == "directory" else None
    enabled = data.get("enabledPlugins") if isinstance(data.get("enabledPlugins"), dict) else {}
    off = [k for k in _sm.MOD_PLUGINS if enabled.get(k) is not True]
    if not have:
        state = "missing"
    elif _fwd_norm(have) != want:
        state = "elsewhere"
    elif off:
        state = "off"
    else:
        state = "ok"
    return {"state": state, "have": have, "off": off}


def floor(pa3=None, version=None):
    """``(ok, version, floor)``: Claude Code against the root's ``VERSION_FLOOR`` (else the package's);
    ``ok`` is False only for a readable version below a readable floor."""
    from .install import root as _root
    pa3 = pa3 or paths.install_dir()
    pkg = pa3 if os.path.isfile(os.path.join(pa3, _root.FLOOR_FILE)) else _root.package_root()
    ver = version if version is not None else _root.claude_version()
    ok, _why = _root.floor_check(pkg, ver)
    return ok, ver, _root.mod_floor(pkg)


def heartbeat(root, sid):
    """``ok``, ``missing`` (no heartbeat for ``sid``) or ``error`` (an error record for ``sid``)."""
    err = fsutil.read_json(os.path.join(root, MOD_RUN, ERROR), None)
    if isinstance(err, dict) and sid and err.get("sid") == sid:
        return "error"
    beat = fsutil.read_json(os.path.join(root, MOD_RUN, HEARTBEAT), None)
    return "ok" if isinstance(beat, dict) and sid and beat.get("sid") == sid else "missing"


def mod_on(root):
    """True in a project whose ``.claude/pa.json`` says ``mod: on`` (and PA_MOD_OFF is not set)."""
    if not root:
        return False
    off = os.environ.get("PA_MOD_OFF", "")
    if off not in ("", "0"):
        return False
    doc = fsutil.read_json(os.path.join(root, ".claude", "pa.json"), None)
    return isinstance(doc, dict) and doc.get("mod") == "on"


def upgrade_mode(root):
    """``.claude/pa.json`` ``upgrade``: ``ask`` or ``auto`` (the installer's default)."""
    doc = fsutil.read_json(os.path.join(root, ".claude", "pa.json"), None)
    return "ask" if isinstance(doc, dict) and doc.get("upgrade") == "ask" else "auto"


def run_running(root, now=None):
    """True while the run marker says ``running`` and is younger than RUN_STALE_S."""
    doc = fsutil.read_json(os.path.join(root, MOD_RUN, MARKER), None)
    if not isinstance(doc, dict) or doc.get("state") != "running":
        return False
    at = doc.get("at")
    return isinstance(at, (int, float)) and (now or time.time()) - at < RUN_STALE_S


def hook_record(root):
    doc = fsutil.read_json(os.path.join(root, MOD_RUN, HOOK), None)
    return doc if isinstance(doc, dict) else {}


def mark_ran(root, sid):
    """Record that a run started in ``sid``: no second heal this session, no heartbeat owed by it."""
    rec = hook_record(root)
    rec["healed"] = sid
    _hook_write(root, rec)


def _beat_seen(root):
    return os.path.isfile(os.path.join(root, MOD_RUN, HEARTBEAT))


def _hook_write(root, doc):
    try:
        fsutil.atomic_write_text(os.path.join(root, MOD_RUN, HOOK), json.dumps(doc, indent=1))
    except OSError:
        pass


# --------------------------------------------------------------------------- what broke

def problems(root, prev_sid, settings_path=None, pa3=None):
    """The plain-words problems found (``[]`` when healthy): the root's files, the registration in
    ``settings_path`` (default the config dir's user settings) and ``prev_sid``'s heartbeat."""
    pa3 = pa3 or paths.install_dir()
    out = []
    files = root_files(pa3)
    if files["state"] == "half":
        out.append("the PA3 root at %s is a half copy (its VERSION does not match its manifest)" % pa3)
    elif files["state"] == "bad":
        out.append("%d of the PA3 root's %d files are damaged or missing (%s%s)" % (
            len(files["bad"]), files["total"], ", ".join(files["bad"][:3]), ", ..." if len(files["bad"]) > 3 else ""))
    reg = registration(settings_path or os.path.join(paths.config_dir(), "settings.json"), pa3)
    if reg["state"] == "missing":
        out.append("the mod is not registered in the user settings")
    elif reg["state"] == "elsewhere":
        out.append("the mod is registered at %s, not at the root's mod/" % reg["have"])
    elif reg["state"] == "off":
        out.append("the mod's plugins are not enabled (%s)" % ", ".join(reg["off"]))
    if prev_sid:
        beat = heartbeat(root, prev_sid)
        if beat == "error":
            out.append("the mod failed with an error in the last session")
        elif beat == "missing":
            out.append("the mod did not load in the last session")
    return out


# --------------------------------------------------------------------------- healing

def _git(clone, *args):
    try:
        r = subprocess.run(["git", "-C", clone] + list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _vkey(text):
    import re
    m = re.match(r"^\s*(?:version:)?\s*(\d+(?:\.\d+)*)", (text or "").lstrip("﻿"))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def newer_release(pa3=None, clone=None):
    """The clone's ``@{u}`` package version when it is above the installed one (I48), else None."""
    clone = clone or paths.package_clone_dir()
    if not os.path.isdir(clone):
        return None
    remote = _git(clone, "show", "@{u}:project-architect-3.0/VERSION")
    have = _vkey(_version_fields(fsutil.read_text(os.path.join(pa3 or paths.install_dir(), "VERSION"), "")).get("version"))
    want = _vkey(remote)
    if want is None or (have is not None and want <= have):
        return None
    return ".".join(map(str, want))


def installed_version(pa3=None):
    return _version_fields(fsutil.read_text(os.path.join(pa3 or paths.install_dir(), "VERSION"), "")).get("version") or "?"


def python_for(root):
    doc = fsutil.read_json(os.path.join(root, ".claude", "pa.json"), None)
    py = doc.get("python") if isinstance(doc, dict) else None
    return str(py or sys.executable).replace("\\", "/")


_SPAWNER = None     # tests: a callable(argv, cwd) in place of the detached spawn


def start_run(root, repair, why):
    """Start ``tools/pa3_update.py`` (``--repair``) detached from ``root``; the hook writes the running
    marker first so a second start this instant sees it (pa3_update.py rewrites it). True when started."""
    if not os.path.isfile(os.path.join(root, "tools", "pa3_update.py")):
        return False
    argv = [python_for(root), "tools/pa3_update.py"] + (["--repair"] if repair else [])
    mode = "repair" if repair else "update"
    try:
        fsutil.atomic_write_text(os.path.join(root, MOD_RUN, MARKER), json.dumps(
            {"state": "running", "mode": mode, "at": time.time(), "by": "hook", "why": why}, indent=1))
    except OSError:
        pass
    log.log("health_run", root=root, mode=mode, why=why[:200])
    if _SPAWNER is not None:
        _SPAWNER(argv, root)
        return True
    kwargs = {"cwd": root, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)                # noqa: S603 - fixed argv
        return True
    except OSError:
        return False


def heal(root, sid, found, pa3=None, clone=None, version=None):
    """Heal ``found`` (problems' list) once for ``sid``; returns the plain-words line, or None when this
    session already healed.  Below the floor nothing changes and the line says to update Claude Code."""
    rec = hook_record(root)
    if rec.get("healed") == sid:
        return None
    what = "; ".join(found)
    ok, ver, need = floor(pa3, version)
    if not ok:
        log.log("health_below_floor", root=root, version=ver, floor=need)
        return ("PA3 health: %s. Claude Code %s is older than %s, the oldest release the PA3 mod runs on, so "
                "nothing was changed: tell the developer to update Claude Code (claude update) and start "
                "again." % (what, ver, need))
    if run_running(root):
        did = "an update run is already going (.run/mod/update.log); it restores the root"
    else:
        # decision 6: the repair never waits on ``upgrade``; only the newer release follows it
        newer = newer_release(pa3, clone)
        ask = bool(newer) and upgrade_mode(root) == "ask"
        if newer and not ask and start_run(root, False, what):
            did = "the session-start hook started the update to PA3 %s in the background (.run/mod/update.log)" % newer
        elif start_run(root, True, what):
            did = ("the session-start hook started a reinstall of the installed release (PA3 %s) in the background "
                   "(.run/mod/update.log)" % installed_version(pa3))
        else:
            did = ("nothing could be started (this project has no tools/pa3_update.py): run the root installer "
                   "(pa_install.py --root --yes) from the package clone")
        if ask:
            did += ("; PA3 %s, a newer release, is on offer (upgrade: ask): the update line below installs it once "
                    "the developer says yes" % newer)
    rec = hook_record(root)                             # healed only once the run was started
    rec["healed"] = sid
    _hook_write(root, rec)
    log.log("health_heal", root=root, session_id=sid, found=what[:300], did=did[:200])
    return "PA3 health: %s. Done: %s. Once it finishes, %s. Tell the developer in plain words." % (what, did, RELOAD)


# --------------------------------------------------------------------------- the hook entries

def session_start(root, sid, source):
    """SessionStart in a ``mod: on`` project: check, heal, record ``sid``; the line or None."""
    if not mod_on(root) or source == "compact":
        return None
    rec = hook_record(root)
    prev = rec.get("sid") if rec.get("sid") != sid else None
    if prev and (rec.get("healed") == prev or not _beat_seen(root)):
        prev = None
    rec["sid"] = sid
    _hook_write(root, rec)
    found = problems(root, prev)
    return heal(root, sid, found) if found else None


def first_prompt(root, sid):
    """The first UserPromptSubmit of ``sid`` in a ``mod: on`` project: this session's heartbeat."""
    if not mod_on(root):
        return None
    rec = hook_record(root)
    if rec.get("prompted") == sid or rec.get("healed") == sid or not _beat_seen(root):
        return None
    rec["prompted"] = sid
    _hook_write(root, rec)
    beat = heartbeat(root, sid)
    if beat == "ok":
        return None
    found = ["the mod failed with an error in this session" if beat == "error" else "the mod did not load in this session"]
    found += problems(root, None)
    return heal(root, sid, found)
