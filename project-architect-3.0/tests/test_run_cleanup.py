"""Tests for tools/run_cleanup.py (3.29.8): the one-time .run/ cleanup deletes exactly its list, inside .run/ only."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG, "tools"))

import run_cleanup as RC  # noqa: E402


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class _Fixture:

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


class TestRunCleanup(_Fixture, unittest.TestCase):

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


PLAN_3_40 = """# Phase 3.40 — residue

## Tasks

- T2.1 | done | title: a split task's part | effort: medium
- T14 | done   | title: types table | effort: high
- T18 | done | title: dispositions | effort: high
- T19 | active | title: struct pass | effort: high
- T20 | done | title: retarget | effort: high
- T21 | pending | title: final | effort: high
"""


class TestOpenPhaseTaskFolders(_Fixture, unittest.TestCase):
    """3.33: inside the open phase's folder, a finished task's folder goes; the rest of it stays refused."""

    def setUp(self):
        super().setUp()
        _write(os.path.join(self.root, "phase-ends", "current", "PHASE_PLAN.md"), PLAN_3_40)
        for rel in ("3.40/t14/types.tsv", "3.40/t14b/x", "3.40/t14_prevc2/y", "3.40/T18/c1/notes.md",
                    "3.40/t20/c14/w/retarget.py", "3.40/t20/keep.md", "3.40/t19/a", "3.40/t21/a", "3.40/t22/a",
                    "3.40/t2/a", "3.40/tables/a", "3.40/notes.md"):
            _write(os.path.join(self.root, ".run", *rel.split("/")))
        self.loop({"task": {"id": "T21", "attempt": 1}, "state": "running"})

    def loop(self, state):
        _write(os.path.join(self.root, ".run", "mod", "loop.json"), json.dumps(state))

    def test_finished_task_folders_go(self):
        """bfm 3.45: t14, t14b, t14_prevc2, T18 and a folder deep in t20 go; the phase folder and its logs stay."""
        rc, out = self.run_tool(["3.40/t14", ".run/3.40/t14b", "3.40/t14_prevc2", "3.40/T18", "3.40/t20/c14",
                                 "P32/t3"])
        self.assertEqual(rc, 0, out)
        self.assertIn("deleted 6 of 6 items", out)
        for gone in ("t14", "t14b", "t14_prevc2", "T18", "t20/c14"):
            self.assertFalse(self.there(".run/3.40/" + gone), gone)
        for kept in ("t20/keep.md", "logs/t1.log", "notes.md", "t19/a", "t21/a"):
            self.assertTrue(self.there(".run/3.40/" + kept), kept)

    def test_refusals_inside_the_open_phase(self):
        phase_only = "the open phase's folder: only a finished task's folder inside it"
        for bad, why in (("3.40", phase_only), ("3.40/logs", phase_only), ("3.40/notes.md", phase_only),
                         ("3.40/tables", phase_only), ("3.40/t19", "T19 is not done"),
                         ("3.40/t22", "T22 is not done"), ("3.40/t2", "T2 is not done"),
                         ("3.40/t21", "T21 is running"), ("3.40/t21/a", "T21 is running")):
            rc, out = self.run_tool(["3.40/t14", "P32/t3", bad])
            self.assertEqual(rc, 1, bad)
            self.assertIn("refused: .run/%s: %s" % (bad, why), out)
            self.assertIn("nothing deleted: 1 of 3 items refused", out)
            self.assertTrue(self.there(".run/3.40/t14/types.tsv"), bad)
            self.assertTrue(self.there(".run/P32/t3/a.bin"), bad)

    def test_the_running_task_is_refused_even_when_done(self):
        self.loop({"task": {"id": "T20"}})
        rc, out = self.run_tool(["3.40/t20/c14"], check=True)
        self.assertEqual(rc, 1)
        self.assertIn("refused: .run/3.40/t20/c14: T20 is running", out)

    def test_no_running_task_without_a_readable_loop(self):
        """A done task's folder goes when loop.json names no task, is missing, or cannot be read."""
        _write(os.path.join(self.root, "phase-ends", "current", "PHASE_PLAN.md"),
               PLAN_3_40.replace("T21 | pending", "T21 | done"))
        for state in ({"task": None}, None, "{not json"):
            loop = os.path.join(self.root, ".run", "mod", "loop.json")
            if state is None:
                os.remove(loop)
            elif isinstance(state, str):
                _write(loop, state)
            else:
                self.loop(state)
            rc, out = self.run_tool(["3.40/t21"], check=True)
            self.assertEqual(rc, 0, (state, out))
            self.loop({"task": {"id": "T19"}})

    def test_a_done_task_folder_must_exist(self):
        rc, out = self.run_tool(["3.40/t20b"], check=True)
        self.assertEqual(rc, 1)
        self.assertIn("refused: .run/3.40/t20b: not found", out)


def _git(root, *args):
    subprocess.run(["git", "-C", root] + list(args), check=True, capture_output=True)


class TestCites(unittest.TestCase):
    """3.33: run_cleanup.py --cites lists the tracked files citing paths under .run/."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-cites-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        files = {
            ".claude/pa.json": "{}\n",
            ".gitignore": ".run/\n",
            "README.md": "No cites here; foo.run/bar is another folder.\n",
            "tools/restruct.py": 'FOLD_FLOWS = ".run/3.40/t14/types.tsv"\nBACKUP = ".run/3.40/t14/types.tsv"\n',
            "config/struct_dispositions.tsv": "a\t.run/3.40/t18/c1/notes.md\nb\t.run/3.40/t18/c1/notes.md\n"
                                              "c\tsee .run/3.40/t18/c1/notes.md.\n",
            "cookbook/retarget.md": "Run `.run/3.40/t20/c14/w/retarget.py`; data in .run/keep/*.jsonl.\n"
                                    "Template: .run/<phase>/t3/ and .run/3.40/<task>/x; own: .run/mod/loop.json,\n"
                                    ".run/logs/t1.log, .run/status.json, .run/PHASE_PLAN.draft.md, .run/* and .run/.\n",
            "phase-ends/current/tasks/T14.md": "Left .run/3.40/t14b/x for T15.\n",
            "phase-ends/current/logs/T1.md": "wrote .run/3.40/t14/types.tsv\n",
            "phase-ends/phase-3.39/tasks/T1.md": "used .run/old/x\n",
            "phase-ends/PhaseEnd_3.39.md": "cleaned .run/old/x\n",
            ".claude-state/journal/2026-11-23.md": "c12 read .run/P32/t3/a.bin\n",
            "src/main.c": "int main(void) { return 0; }\n",
        }
        for rel, text in files.items():
            _write(os.path.join(self.root, *rel.split("/")), text)
        for rel, data in (("assets/blob.bin", b"\0\1.run/blob/y"), ("legacy.txt", b"\xff\xfe .run/bad/z"),
                          ("big.txt", b".run/big/z " + b"x" * 2000)):
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "wb") as fh:
                fh.write(data)
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        for rel in ("3.40/t14/types.tsv", "3.40/t14b/x", "keep/dup.jsonl", "P32/t3/a.bin", "mod/loop.json"):
            _write(os.path.join(self.root, ".run", *rel.split("/")))
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)

    def cites(self, *extra):
        buf = io.StringIO()
        with mock.patch.object(RC, "CITE_MAX_BYTES", 1000), redirect_stdout(buf):
            rc = RC.main(["--cites"] + list(extra))
        return rc, buf.getvalue()

    def test_report_skips_history_by_default(self):
        rc, out = self.cites()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "config/struct_dispositions.tsv (3 cites)\n"
                              "  .run/3.40/t18/c1/notes.md (missing)\n"
                              "cookbook/retarget.md (2 cites)\n"
                              "  .run/3.40/t20/c14/w/retarget.py (missing)\n"
                              "  .run/keep/*.jsonl\n"
                              "phase-ends/current/tasks/T14.md (1 cites)\n"
                              "  .run/3.40/t14b/x\n"
                              "tools/restruct.py (2 cites)\n"
                              "  .run/3.40/t14/types.tsv\n"
                              "4 files cite 5 distinct paths under .run/ (3 exist)\n")

    def test_all_includes_history(self):
        rc, out = self.cites("--all")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        heads = [ln for ln in lines if not ln.startswith("  ")]
        self.assertEqual(heads, [".claude-state/journal/2026-11-23.md (1 cites)",
                                 "config/struct_dispositions.tsv (3 cites)", "cookbook/retarget.md (2 cites)", "phase-ends/PhaseEnd_3.39.md (1 cites)",
                                 "phase-ends/current/logs/T1.md (1 cites)", "phase-ends/current/tasks/T14.md (1 cites)",
                                 "phase-ends/phase-3.39/tasks/T1.md (1 cites)", "tools/restruct.py (2 cites)",
                                 "8 files cite 7 distinct paths under .run/ (4 exist)"])
        self.assertIn("  .run/P32/t3/a.bin", lines)
        self.assertIn("  .run/old/x (missing)", lines)
        for skipped in ("blob", "bad", "big", "mod", "logs/t1", "status.json", "<"):
            self.assertNotIn(".run/" + skipped, out)

    def test_no_git_is_still_a_report(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(self.root, "no-such-git")}):
            rc, out = self.cites()
        self.assertEqual(rc, 0)
        self.assertIn("git ls-files failed", out)
        self.assertTrue(out.endswith("0 files cite 0 distinct paths under .run/ (0 exist)\n"), out)


if __name__ == "__main__":
    unittest.main()
