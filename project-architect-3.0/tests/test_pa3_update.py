"""tools/pa3_update.py: the allow-listed `update pa3` wrapper (3.10 T20).

Temp root and temp CLAUDE_CONFIG_DIR; nothing runs, nothing touches ``~/.claude``.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from pa import paths  # noqa: E402

_spec = importlib.util.spec_from_file_location("pa3_update", os.path.join(HERE, "tools", "pa3_update.py"))
upd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upd)


def fwd(p):
    return p.replace("\\", "/")


class Pa3UpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-update-")
        self.root = os.path.join(self.dir, "proj")
        self.cfg = os.path.join(self.dir, "cfg")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.cfg, "pa3-src"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"python": "/x/py"}, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = upd.main(list(argv), root=self.root, config_dir=self.cfg)
        return rc, buf.getvalue()

    def test_steps_exact(self):
        clone = fwd(os.path.join(self.cfg, "pa3-src"))
        inst = "%s/project-architect-3.0/pa_install.py" % clone
        self.assertEqual(upd.steps(self.root, self.cfg), [
            ["git", "-C", clone, "pull", "--ff-only"],
            ["/x/py", inst, "--root", "--yes"],
            ["/x/py", inst, "--project", fwd(self.root), "--yes"]])

    def test_dry_run_prints_three_steps(self):
        rc, out = self.run_main("--dry-run")
        self.assertEqual(rc, 0, out)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("pa3_update: dry-run (root %s, clone " % fwd(self.root)), out)
        self.assertEqual([ln.split(": ")[1] for ln in lines[1:]], ["1/3", "2/3", "3/3"], out)
        self.assertIn("pull --ff-only", lines[1])
        self.assertTrue(lines[3].endswith("--project %s --yes" % fwd(self.root)), out)

    def test_no_pa_json_exits_2(self):
        os.remove(os.path.join(self.root, ".claude", "pa.json"))
        rc, out = self.run_main("--dry-run")
        self.assertEqual(rc, 2)
        self.assertIn("is not a PA3 project (no .claude/pa.json)", out)

    def test_no_clone_exits_2(self):
        shutil.rmtree(os.path.join(self.cfg, "pa3-src"))
        rc, out = self.run_main("--dry-run")
        self.assertEqual(rc, 2)
        self.assertIn("no package clone at ", out)

    def _write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_refusals_i48(self):
        """3.24 T4: step 2 refuses a clone below the installed root; step 3 one below the record."""
        clone = os.path.join(self.cfg, "pa3-src")
        pa3 = os.path.join(self.dir, "pa3")
        self._write("cfg/pa3-src/project-architect-3.0/VERSION", "3.23\n")
        self._write("pa3/VERSION", "version: 3.24\ngit: abc\n")
        why = upd.refusal(2, self.root, clone, pa3)
        self.assertEqual(why, "pa3_update: refused: the clone holds PA3 3.23, below the installed PA3 (%s) at "
                              "3.24; PA3 never installs older code" % fwd(pa3))
        self._write("pa3/VERSION", "version: 3.23\ngit: abc\n")          # equal versions are fine
        self.assertIsNone(upd.refusal(2, self.root, clone, pa3))
        self._write("proj/.claude/pa3-managed.json", json.dumps({"upstream_version": "3.23.1"}))
        self.assertIn("below this project's record at 3.23.1", upd.refusal(3, self.root, clone, pa3))
        self._write("proj/.claude/pa3-managed.json", json.dumps({"upstream_version": "3.22"}))
        self.assertIsNone(upd.refusal(3, self.root, clone, pa3))

    def test_main_stops_before_the_root_step(self):
        """The refusal runs after the pull and before step 2; nothing after it runs."""
        from unittest import mock
        self._write("cfg/pa3-src/project-architect-3.0/VERSION", "3.23\n")
        self._write("pa3/VERSION", "version: 3.24\n")
        ran = []
        with mock.patch.object(upd.subprocess, "run",
                               lambda cmd, cwd=None, **kw: ran.append(cmd) or mock.Mock(returncode=0, stdout="",
                                                                                        stderr="")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = upd.main([], root=self.root, config_dir=self.cfg, pa3_dir=os.path.join(self.dir, "pa3"))
        self.assertEqual(rc, 2, buf.getvalue())
        self.assertEqual([c[3] for c in ran], ["pull"])
        self.assertIn("pa3_update: refused:", buf.getvalue())

    def test_detach(self):
        """3.24 T7b: --detach writes the running marker, starts the same run detached, exits 0; 3 while locked."""
        started = []
        upd._SPAWNER = lambda argv, cwd: started.append((argv, cwd))
        try:
            rc, out = self.run_main("--detach")
            self.assertEqual(rc, 0, out)
            self.assertEqual(started, [([sys.executable, os.path.abspath(upd.__file__)], self.root)])
            with open(os.path.join(self.root, ".run", "mod", "update.json"), encoding="utf-8") as fh:
                m = json.load(fh)
            self.assertEqual((m["state"], m["mode"], m["by"]), ("running", "update", "detach"))
            self._write("proj/.run/mod/update.lock", "1\n")
            rc, out = self.run_main("--detach")
            self.assertEqual(rc, 3, out)
            self.assertIn("another update is running", out)
            self.assertEqual(len(started), 1)
        finally:
            upd._SPAWNER = None

    def test_clone_matches_package_clone_dir(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        try:
            self.assertEqual(upd.clone_dir(), paths.package_clone_dir())
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old


if __name__ == "__main__":
    unittest.main()
