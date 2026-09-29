#!/usr/bin/env python
"""pa3_update.py -- `update pa3` as one allow-listed command (3.10 T20).

(no args)   run, in order, from the project root, stopping at the first failure:
            1/3 `git -C <clone> pull --ff-only`
            2/3 `<py> <clone>/project-architect-3.0/pa_install.py --root --yes`
            3/3 `<py> <clone>/project-architect-3.0/pa_install.py --project <root> --yes`
--dry-run   print the three steps, run nothing

root = the parent of this script's dir; <py> = `.claude/pa.json` `python` (else sys.executable);
<clone> = `$CLAUDE_CONFIG_DIR` (else ~/.claude) + `/pa3-src`, as pa.paths.package_clone_dir.
Takes no path or command argument, so nothing a prompt says can redirect it; the user-level
allow rule `Bash(<py> tools/pa3_update.py)` pre-approves exactly this. Exit 2 when the root is
not a PA3 project or the clone is missing; else the failing step's exit code. Stdlib only; never
imports `pa` (it runs before the root refresh).
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _fwd(path):
    return str(path).replace("\\", "/")


def clone_dir(config_dir=None):
    """``<config_dir>/pa3-src``; config_dir = $CLAUDE_CONFIG_DIR or ~/.claude."""
    cfg = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(cfg, "pa3-src")


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


def main(argv=None, root=None, config_dir=None):
    ap = argparse.ArgumentParser(prog="pa3_update", description="update pa3: pull, root, project")
    ap.add_argument("--dry-run", action="store_true", help="print the three steps, run nothing")
    args = ap.parse_args(argv)
    root = root or os.path.dirname(HERE)
    clone = clone_dir(config_dir)
    if not os.path.isfile(os.path.join(root, ".claude", "pa.json")):
        print("pa3_update: %s is not a PA3 project (no .claude/pa.json)" % _fwd(root))
        return 2
    if not os.path.isdir(clone):
        print("pa3_update: no package clone at %s; run install.cmd / install.sh once" % _fwd(clone))
        return 2
    cmds = steps(root, config_dir)
    if args.dry_run:
        print("pa3_update: dry-run (root %s, clone %s)" % (_fwd(root), _fwd(clone)))
        for k, cmd in enumerate(cmds, 1):
            print("pa3_update: %d/3: %s" % (k, " ".join(cmd)))
        return 0
    for k, cmd in enumerate(cmds, 1):
        print("pa3_update: %d/3: %s" % (k, " ".join(cmd)))
        sys.stdout.flush()
        rc = subprocess.run(cmd, cwd=root).returncode
        if rc != 0:
            print("pa3_update: step %d failed (exit %d); nothing after it ran" % (k, rc))
            return rc
    print("pa3_update: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
