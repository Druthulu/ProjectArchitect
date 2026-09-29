"""Tests for discussion.py status subcommand and curate.py discussions/ops."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")
FIXTURES = os.path.join(HERE, "fixtures")


def write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


class DiscussionStatusTest(unittest.TestCase):
    """discussion.py status rewrites the record and the index line."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-disc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        self.disc = os.path.join(self.cur, "discussions")
        os.makedirs(self.disc, exist_ok=True)
        # create a record with Status: open
        write(os.path.join(self.disc, "D1.md"),
              "# D1 — card design (2026-09-20, session router)\n"
              "Status: open\n\n## Decisions\n- keep it short\n\n"
              "## Plan edits\n- (none)\n\n## Open\n- (none)\n\n"
              "## Deferred\n- deferred: slice format → 3.8\n")
        write(os.path.join(self.disc, "INDEX.md"),
              "# Discussions -- one line per record\n"
              "# id | topic | date | status | path\n"
              "D1 | card design | 2026-09-20 | open | phase-ends/current/discussions/D1.md\n")

    def _run(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "discussion.py")]
        cmd += list(args)
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        return subprocess.run(cmd, cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def test_status_sets_executed(self):
        p = self._run("status", "D1", "executed")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("D1 | executed", p.stdout)
        # record file updated
        rec = read(os.path.join(self.disc, "D1.md"))
        self.assertIn("Status: executed", rec)
        self.assertNotIn("Status: open", rec)
        # index line updated
        idx = read(os.path.join(self.disc, "INDEX.md"))
        self.assertIn("executed", idx)
        self.assertNotIn("| open |", idx)

    def test_status_sets_deferred_with_phase(self):
        p = self._run("status", "D1", "deferred:3.8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rec = read(os.path.join(self.disc, "D1.md"))
        self.assertIn("Status: deferred:3.8", rec)
        idx = read(os.path.join(self.disc, "INDEX.md"))
        self.assertIn("deferred:3.8", idx)

    def test_status_sets_planned_with_task(self):
        p = self._run("status", "D1", "planned:3.8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rec = read(os.path.join(self.disc, "D1.md"))
        self.assertIn("Status: planned:3.8", rec)

    def test_status_with_reason(self):
        p = self._run("status", "D1", "dropped", "--reason", "no longer needed")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rec = read(os.path.join(self.disc, "D1.md"))
        self.assertIn("Status: dropped", rec)
        self.assertIn("no longer needed", rec)

    def test_status_refuses_invalid(self):
        p = self._run("status", "D1", "bogus")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("invalid status", p.stdout)

    def test_status_refuses_missing_record(self):
        p = self._run("status", "D99", "executed")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("no record", p.stdout)

    def test_on_off_status_bare(self):
        """Bare status (no args) still shows on/off."""
        p = self._run("status")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("off", p.stdout)

    def test_off_new_record_without_flag(self):
        """3.9.7 T9: open-mode /discuss raises no flag; off --new-record still writes."""
        p = self._run("off", "--new-record")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.disc, "D2.md")), p.stdout)
        self.assertIn("D2 | ", read(os.path.join(self.disc, "INDEX.md")))


class DiscussionTriageTest(unittest.TestCase):
    """discussion.py triage writes a deferred item's status into its index row (T4.c1)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-triage-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.pdir = os.path.join(self.root, "phase-ends")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        self.idx = os.path.join(self.pdir, "current", "discussions", "INDEX.md")
        self.cum = os.path.join(self.pdir, "DISCUSSION_INDEX.md")
        write(self.idx, "# Discussions -- one line per record\n# id | topic | date | status | path\n"
                        "I1 | inbox bullet | 2026-09-21 | deferred | INBOX.md\n")
        write(self.cum, "# Discussions -- cumulative\n# phase | id | topic | date | status | path\n"
                        "3.4 | D1 | card format | 2026-09-20 | deferred:3.8 | a.md\r\n")
        write(os.path.join(self.pdir, "PhaseEnd_Phase3.6.md"),
              "# PhaseEnd — Phase 3.6\n\n## Deferred\n- from T11: seed wording\n\n## Changes\n")

    _run = DiscussionStatusTest._run

    def test_triage_writes_each_status(self):
        for rid, status, path in (("I1", "deferred:3.9", "idx"), ("D1", "deferred:gen4", "cum"),
                                  ("I1", "planned:3.9/T2", "idx"), ("D1", "dropped", "cum")):
            p = self._run("triage", rid, status)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("triage: %s -> %s" % (rid, status), p.stdout)
            text = read(self.idx if path == "idx" else self.cum)
            self.assertIn("| %s | %s" % (status, "INBOX.md" if path == "idx" else "a.md"), text)
        self.assertIn("| dropped | a.md\r\n", read(self.cum))      # EOL preserved

    def test_triage_phaseend_item_gets_new_row(self):
        p = self._run("triage", "P3.6-1", "deferred:3.9")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(read(self.cum), r"\n3\.6 \| P3\.6-1 \| seed wording \| \d{4}-\d\d-\d\d \| "
                                         r"deferred:3\.9 \| phase-ends/PhaseEnd_Phase3\.6\.md\n")

    def test_triage_refuses_invalid_and_unknown(self):
        for args in (("I1", "executed"), ("I1", "planned:3.9"), ("I1", "deferred:")):
            p = self._run("triage", *args)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("invalid triage status", p.stdout)
        p = self._run("triage", "D99", "dropped")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("unknown deferred item", p.stdout)


class CurateDiscussionsTest(unittest.TestCase):
    """curate.py discussions --demote --gen 3 --dry-run lists the executed record only."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-curate-disc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        pdir = os.path.join(self.root, "phase-ends")
        # cumulative discussion index with one executed and one deferred
        write(os.path.join(pdir, "DISCUSSION_INDEX.md"),
              "# Discussions -- cumulative\n"
              "# phase | id | topic | date | status | path\n"
              "3.7 | D1 | card design | 2026-09-20 | executed | phase-3.7/discussions/D1.md\n"
              "3.7 | D2 | deferred item | 2026-09-21 | deferred:3.8 | phase-3.7/discussions/D2.md\n")

    def _run(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "curate.py")]
        cmd += list(args)
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        return subprocess.run(cmd, cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def test_dry_run_lists_executed_only(self):
        p = self._run("discussions", "--demote", "--gen", "3", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("demote:", p.stdout)
        self.assertIn("D1", p.stdout)
        self.assertIn("executed", p.stdout)
        self.assertNotIn("D2", p.stdout.split("demote:")[1] if "demote:" in p.stdout else "")
        self.assertIn("dry-run", p.stdout)

    def test_demote_moves_lines(self):
        p = self._run("discussions", "--demote", "--gen", "3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # main index should still have D2 but not D1's data line
        main = read(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"))
        self.assertIn("D2", main)
        self.assertIn("deferred:3.8", main)
        # D1 should be in the gen archive
        gen = read(os.path.join(self.root, "phase-ends", "gen3", "DISCUSSION_INDEX.md"))
        self.assertIn("D1", gen)
        self.assertIn("executed", gen)


class CurateOpsTest(unittest.TestCase):
    """curate.py ops --sunset --gen 3 --dry-run on a fixture."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-curate-ops-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        # ops with two topics
        write(os.path.join(self.root, "docs", "ops", "INDEX.md"),
              "# Ops topics\n"
              "- [harness gotchas](harness-gotchas.md)\n"
              "- [old topic](old-topic.md)\n")
        write(os.path.join(self.root, "docs", "ops", "harness-gotchas.md"),
              "# Harness gotchas\nSome content.\n")
        write(os.path.join(self.root, "docs", "ops", "old-topic.md"),
              "# Old topic\nObsolete.\n")
        # HOW_WE_WORK references harness-gotchas.md but not old-topic.md
        write(os.path.join(self.root, "HOW_WE_WORK.md"),
              "# How we work\nSee docs/ops/harness-gotchas.md for details.\n")

    def _run(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "curate.py")]
        cmd += list(args)
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        return subprocess.run(cmd, cwd=self.root, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def test_dry_run_lists_unreferenced(self):
        p = self._run("ops", "--sunset", "--gen", "3", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("unreferenced: old-topic.md", p.stdout)
        self.assertIn("referenced: harness-gotchas.md", p.stdout)
        self.assertIn("dry-run", p.stdout)

    def test_sunset_moves_unreferenced(self):
        p = self._run("ops", "--sunset", "--gen", "3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # old-topic.md should be moved
        self.assertFalse(os.path.isfile(
            os.path.join(self.root, "docs", "ops", "old-topic.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "docs", "retired", "ops", "old-topic.md")))
        # index should have a link line
        idx = read(os.path.join(self.root, "docs", "ops", "INDEX.md"))
        self.assertIn("retired gen 3", idx)
        self.assertIn("old topic", idx)
        # harness-gotchas.md should still be there
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "docs", "ops", "harness-gotchas.md")))


if __name__ == "__main__":
    unittest.main()
