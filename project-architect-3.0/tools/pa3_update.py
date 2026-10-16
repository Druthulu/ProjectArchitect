#!/usr/bin/env python
"""pa3_update.py -- `update pa3` as one allow-listed command (3.10 T20).

(no args)   run, in order, from the project root, stopping at the first failure:
            1/3 `git -C <clone> pull --ff-only`
            2/3 `<py> <clone>/project-architect-3.0/pa_install.py --root --yes`
            3/3 `<py> <clone>/project-architect-3.0/pa_install.py --project <root> --yes`
--dry-run   print the three steps, run nothing
--repair    3.24 T6: reinstall the installed release, no pull: one step,
            `<py> <source>/pa_install.py --root --no-clone --yes` (no pull), <source> = `<pa3>/VERSION` `source:`;
            refused unless <source>'s package VERSION equals the installed one (never older,
            never newer: a newer release is an update)

root = the parent of this script's dir; <py> = `.claude/pa.json` `python` (else sys.executable);
<clone> = `$CLAUDE_CONFIG_DIR` (else ~/.claude) + `/pa3-src`, as pa.paths.package_clone_dir.
Takes no path or command argument, so nothing a prompt says can redirect it; the user-level
allow rule `Bash(<py> tools/pa3_update.py)` pre-approves exactly this. Exit 2 when the root is
not a PA3 project or the clone is missing; else the failing step's exit code. Stdlib only; never
imports `pa` (it runs before the root refresh).
I48 (3.24 T4): after the pull, step 2 refuses a clone whose package VERSION is below the installed
root's (`<home>/.claude/pa3/VERSION`), and step 3 one below the project's record
(`.claude/pa3-managed.json` `upstream_version`); each prints one line why and exits 2.
3.24 T6: a run (not --dry-run) is single-flight under `<root>/.run/mod/update.lock` (exit 3 while
another holds it; stale after 20 min), writes the run marker `<root>/.run/mod/update.json`
(`{state: running|done, mode, pid, at, from, to, rc}`; from/to = the record's phase and git, read
before and after) and the whole output to `<root>/.run/mod/update.log` as well as stdout. The mod
starts no task while the marker says running and reads the log once it says done.
3.24 T7b: `--detach` (the mod's launch) checks as a run does (no project, no clone: exit 2; the lock
held: exit 3), writes the running marker, starts the same run detached (no pipes, its own session)
and exits 0 at once: a mod `$.process.run` child alive for minutes stalls the session's queue (T10b).
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _fwd(path):
    return str(path).replace("\\", "/")


def clone_dir(config_dir=None):
    """``<config_dir>/pa3-src``; config_dir = $CLAUDE_CONFIG_DIR or ~/.claude."""
    cfg = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(cfg, "pa3-src")


def install_dir():
    """``<home>/.claude/pa3`` (home = %USERPROFILE% on Windows), as pa.paths.install_dir."""
    home = os.environ.get("USERPROFILE") if os.name == "nt" else None
    return os.path.join(os.path.abspath(home or os.path.expanduser("~")), ".claude", "pa3")


def _version_key(text):
    """``(3, 24, 0)`` from the first line's version (bare or ``version: ...``), else None."""
    line = (text or "").lstrip("\ufeff").strip().splitlines()[0] if (text or "").strip() else ""
    if line.startswith("version:"):
        line = line.split(":", 1)[1]
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", line)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, ValueError):
        return ""


def refusal(k, root, clone, pa3_dir):
    """I48: the one-line reason step ``k`` (2 root, 3 project) must not run, else None."""
    have = _version_key(_read(os.path.join(clone, "project-architect-3.0", "VERSION")))
    if have is None:
        return None
    if k == 2:
        floor = _version_key(_read(os.path.join(pa3_dir, "VERSION")))
        what = "the installed PA3 (%s)" % _fwd(pa3_dir)
    else:
        try:
            with open(os.path.join(root, ".claude", "pa3-managed.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = None
        floor = _version_key(str(rec.get("upstream_version") or "")) if isinstance(rec, dict) else None
        what = "this project's record"
    if floor is None or have >= floor:
        return None
    return ("pa3_update: refused: the clone holds PA3 %s, below %s at %s; PA3 never installs older code"
            % (".".join(map(str, have)), what, ".".join(map(str, floor))))


def python_for(root):
    """`.claude/pa.json` `python`, else sys.executable."""
    try:
        with open(os.path.join(root, ".claude", "pa.json"), encoding="utf-8") as fh:
            py = (json.load(fh) or {}).get("python")
    except (OSError, ValueError, AttributeError):
        py = None
    return _fwd(py or sys.executable)


def steps(root, config_dir=None):
    """The three commands (argv lists) in order."""
    clone = _fwd(clone_dir(config_dir))
    py = python_for(root)
    inst = "%s/project-architect-3.0/pa_install.py" % clone
    return [["git", "-C", clone, "pull", "--ff-only"],
            [py, inst, "--root", "--yes"],
            [py, inst, "--project", _fwd(root), "--yes"]]


def _version_fields(path):
    """``{key: value}`` of a VERSION file's ``key: value`` lines; a bare first line is ``version``."""
    out = {}
    for n, line in enumerate(_read(path).lstrip("\ufeff").splitlines()):
        if ":" in line:
            key, val = line.split(":", 1)
            out.setdefault(key.strip(), val.strip())
        elif n == 0 and line.strip():
            out["version"] = line.strip()
    return out


def repair_step(root, pa3_dir):
    """3.24 T6: ``(argv, None)`` reinstalling the installed release from VERSION ``source:``, else
    ``(None, why)``: no source, or the source's package VERSION is not the installed one."""
    inst = _version_fields(os.path.join(pa3_dir, "VERSION"))
    src = inst.get("source", "")
    have = _version_key(inst.get("version", ""))
    if not src or have is None or not os.path.isfile(os.path.join(src, "pa_install.py")):
        return None, ("pa3_update: --repair: no installed release to reinstall (VERSION source: %s); "
                      "run install.cmd / install.sh" % (src or "none"))
    there = _version_key(_version_fields(os.path.join(src, "VERSION")).get("version", ""))
    if there != have:
        return None, ("pa3_update: --repair: %s holds PA3 %s, not the installed %s; a repair reinstalls only "
                      "the installed release" % (_fwd(src), ".".join(map(str, there or ())) or "unknown",
                                                 ".".join(map(str, have))))
    return [python_for(root), "%s/pa_install.py" % _fwd(src), "--root", "--no-clone", "--yes"], None


LOCK_STALE_S = 20 * 60


def _record(root):
    """The project record's phase and git (``.claude/pa3-managed.json``), for "from X to Y"."""
    try:
        with open(os.path.join(root, ".claude", "pa3-managed.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        rec = None
    rec = rec if isinstance(rec, dict) else {}
    return {"phase": str(rec.get("phase") or rec.get("upstream_version") or ""), "git": str(rec.get("git") or "")}


def _take_lock(lock):
    """Create ``lock`` exclusively, taking over one older than LOCK_STALE_S; False while held."""
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    for _attempt in (0, 1):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) < LOCK_STALE_S:
                    return False
                os.remove(lock)
            except OSError:
                return False
            continue
        except OSError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        return True
    return False


def _write_marker(path, doc):
    _write_text(path, json.dumps(doc, indent=1))


def _write_text(path, text):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        pass


_SPAWNER = None     # tests: a callable(argv, cwd) in place of --detach's detached spawn


def _detach(root, run_dir, lock, repair):
    """``--detach``: exit 3 while the lock is held (not stale); else the running marker (the run rewrites it) and the
    same run started detached from ``root``; 0 once started, 1 when the start failed."""
    try:
        if time.time() - os.path.getmtime(lock) < LOCK_STALE_S:
            print("pa3_update: another update is running (%s); nothing ran" % _fwd(lock))
            return 3
    except OSError:
        pass
    mode = "repair" if repair else "update"
    os.makedirs(run_dir, exist_ok=True)
    marker = os.path.join(run_dir, "update.json")
    try:
        with open(marker, encoding="utf-8") as fh:
            was = fh.read()
    except OSError:
        was = None
    _write_marker(marker,
                  {"state": "running", "mode": mode, "at": time.time(), "by": "detach", "from": _record(root)})
    argv = [sys.executable, os.path.abspath(__file__)] + (["--repair"] if repair else [])
    if _SPAWNER is not None:
        _SPAWNER(argv, root)
    else:
        kwargs = {"cwd": root, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            # a hidden console its children inherit; DETACHED_PROCESS left them none, so each opened a window
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(argv, **kwargs)                # noqa: S603 - fixed argv
        except OSError as exc:
            if was is None:
                with contextlib.suppress(OSError):
                    os.remove(marker)
            else:
                _write_text(marker, was)                # no run: the marker as it was
            print("pa3_update: the detached run could not start: %s" % exc)
            return 1
    print("pa3_update: %s started detached; the run marker %s says when it is done" % (mode, _fwd(marker)))
    return 0


def main(argv=None, root=None, config_dir=None, pa3_dir=None):
    ap = argparse.ArgumentParser(prog="pa3_update", description="update pa3: pull, root, project")
    ap.add_argument("--dry-run", action="store_true", help="print the three steps, run nothing")
    ap.add_argument("--repair", action="store_true",
                    help="reinstall the installed release from its VERSION source: (no pull, root step only)")
    ap.add_argument("--detach", action="store_true",
                    help="check, write the running marker, start the run detached and exit 0 at once")
    args = ap.parse_args(argv)
    root = root or os.path.dirname(HERE)
    clone = clone_dir(config_dir)
    pa3_dir = pa3_dir or install_dir()
    if not os.path.isfile(os.path.join(root, ".claude", "pa.json")):
        print("pa3_update: %s is not a PA3 project (no .claude/pa.json)" % _fwd(root))
        return 2
    if args.repair:
        cmd, why = repair_step(root, pa3_dir)
        if why:
            print(why)
            return 2
        cmds = [cmd]
    else:
        if not os.path.isdir(clone):
            print("pa3_update: no package clone at %s; run install.cmd / install.sh once" % _fwd(clone))
            return 2
        cmds = steps(root, config_dir)
    n = len(cmds)
    if args.dry_run:
        print("pa3_update: dry-run (root %s, clone %s)" % (_fwd(root), _fwd(clone)))
        for k, cmd in enumerate(cmds, 1):
            print("pa3_update: %d/%d: %s" % (k, n, " ".join(cmd)))
        return 0
    run_dir = os.path.join(root, ".run", "mod")
    lock = os.path.join(run_dir, "update.lock")
    if args.detach:
        return _detach(root, run_dir, lock, args.repair)
    if not _take_lock(lock):
        print("pa3_update: another update is running (%s); nothing ran" % _fwd(lock))
        return 3
    marker = os.path.join(run_dir, "update.json")
    mode = "repair" if args.repair else "update"
    before = _record(root)
    _write_marker(marker, {"state": "running", "mode": mode, "pid": os.getpid(), "at": time.time(), "from": before})
    try:
        log = open(os.path.join(run_dir, "update.log"), "w", encoding="utf-8", newline="\n")
    except OSError:
        log = None

    def out(text):
        text = text if text.endswith("\n") else text + "\n"
        sys.stdout.write(text)
        sys.stdout.flush()
        if log is not None:
            log.write(text)
            log.flush()

    rc = 0
    try:
        for k, cmd in enumerate(cmds, 1):
            why = refusal(k, root, clone, pa3_dir) if k > 1 and not args.repair else None
            if why:
                out(why)
                rc = 2
                break
            out("pa3_update: %d/%d: %s" % (k, n, " ".join(cmd)))
            r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
            said = (r.stdout or "") + (r.stderr or "")
            if said.strip():
                out(said)
            if r.returncode != 0:
                out("pa3_update: step %d failed (exit %d); nothing after it ran" % (k, r.returncode))
                rc = r.returncode
                break
        else:
            out("pa3_update: done")
    finally:
        if log is not None:
            log.close()
        _write_marker(marker, {"state": "done", "mode": mode, "rc": rc, "at": time.time(), "from": before,
                               "to": _record(root), "log": ".run/mod/update.log"})
        try:
            os.remove(lock)
        except OSError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
