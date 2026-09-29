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
