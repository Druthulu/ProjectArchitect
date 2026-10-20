"""Tests for tools/run_cleanup.py (3.29.8): the one-time .run/ cleanup deletes exactly its list, inside .run/ only."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG, "tools"))

import run_cleanup as RC  # noqa: E402


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class TestRunCleanup(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-cleanup-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _write(os.path.join(self.root, ".claude", "pa.json"), "{}\n")
        _write(os.path.join(self.root, "phase-ends", "current", "PHASE_PLAN.md"), "# Phase 3.40 — residue\n")
        for rel in ("P32/t3/a.bin", "keep/dup.jsonl", "keep/ledger.jsonl", "keep/p39-census/sites.jsonl",
                    "wave_ab/shard0.log", "mod/state.json", "3.40/logs/t1.log", "loose.json"):
            _write(os.path.join(self.root, ".run", *rel.split("/")))
        _write(os.path.join(self.root, "src", "main.c"))
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)

    def run_tool(self, paths, check=False):
        _write(os.path.join(self.root, ".run", "mod", "cleanup.json"), json.dumps({"paths": paths}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = RC.main((["--check"] if check else []))
        return rc, buf.getvalue()

    def there(self, rel):
        return os.path.lexists(os.path.join(self.root, *rel.split("/")))

    def test_deletes_exactly_the_list(self):
        """bfm 2026-10-19: one listed file inside keep/ must not take keep/'s other files."""
        rc, out = self.run_tool(["P32/t3", ".run/keep/dup.jsonl", "wave_ab", "loose.json"])
        self.assertEqual(rc, 0, out)
        self.assertIn("deleted 4 of 4 items", out)
        for gone in (".run/P32/t3", ".run/keep/dup.jsonl", ".run/wave_ab", ".run/loose.json"):
            self.assertFalse(self.there(gone), gone)
        for kept in (".run/keep/ledger.jsonl", ".run/keep/p39-census/sites.jsonl", ".run/P32", ".run/mod/state.json",
                     ".run/3.40/logs/t1.log", "src/main.c"):
            self.assertTrue(self.there(kept), kept)

    def test_check_deletes_nothing(self):
        rc, out = self.run_tool(["P32/t3"], check=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("checked 1 items, all inside .run/", out)
        self.assertTrue(self.there(".run/P32/t3/a.bin"))

    def test_refuses_the_whole_list_on_one_bad_item(self):
        for bad, why in (("../src", "not a plain path"), ("mod", "PA3's own"), ("3.40", "open phase"),
                         ("nope", "not found"), ("/etc", "not a plain path")):
            rc, out = self.run_tool(["P32/t3", bad])
            self.assertEqual(rc, 1, bad)
            self.assertIn(why, out)
            self.assertIn("nothing deleted", out)
            self.assertTrue(self.there(".run/P32/t3/a.bin"), bad)

    def test_refuses_a_link_out_of_run(self):
        link = os.path.join(self.root, ".run", "out")
        try:
            os.symlink(os.path.join(self.root, "src"), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        rc, out = self.run_tool(["out"])
        self.assertEqual(rc, 1)
        self.assertIn("leads outside .run/", out)
        self.assertTrue(self.there("src/main.c"))


if __name__ == "__main__":
    unittest.main()
