"""journal_rollup.py: past day folders roll into <date>.md; today stays (3.23 T2)."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(PKG, "tools", "journal_rollup.py")
sys.path.insert(0, os.path.join(PKG, "tools"))

import journal_rollup as JR  # noqa: E402

TODAY = "2026-10-07"
J = ".claude-state/journal"
ENTRIES = {
    "2026-10-05": ["093000-000w-dev-msg", "093005-001r-claude-answer"],
    "2026-10-06": ["120000-000w-dev-msg", "120001-001r-claude-thinking", "120002-002r-claude-answer"],
    TODAY: ["080000-000w-dev-msg"],
}


def _entry(date, stem):
    return "---\nts: %sT%s\n---\nbody of %s\n## not a stem heading\n" % (date, stem[:6], stem)


class RollupTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-rollup-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.jdir = os.path.join(self.root, ".claude-state", "journal")
        old = time.time() - 7200
        index = []
        for date, stems in ENTRIES.items():
            os.makedirs(os.path.join(self.jdir, date))
            for stem in stems:
                p = os.path.join(self.jdir, date, stem + ".md")
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(_entry(date, stem))
                os.utime(p, (old, old))
                index.append("%sT%s | dev | msg | body \\| of %s | %s/%s/%s.md\n"
                             % (date, stem[:6], stem, J, date, stem))
        self.index_text = "".join(index)
        with open(os.path.join(self.jdir, "INDEX-2026-10.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.index_text)

    def _run(self, *extra):
        p = subprocess.run([sys.executable, TOOL, "--root", self.root, "--today", TODAY] + list(extra),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def _read(self, *parts):
        with open(os.path.join(self.jdir, *parts), encoding="utf-8", newline="") as fh:
            return fh.read()

    def test_dry_run_writes_nothing(self):
        out = self._run("--dry-run")
        self.assertEqual(out, "would roll: 2026-10-05 (2 entries)\nwould roll: 2026-10-06 (3 entries)\n")
        self.assertTrue(os.path.isdir(os.path.join(self.jdir, "2026-10-05")))
        self.assertFalse(os.path.exists(os.path.join(self.jdir, "2026-10-05.md")))
        self.assertEqual(self._read("INDEX-2026-10.md"), self.index_text)

    def test_rolls_past_days_and_is_idempotent(self):
        out = self._run()
        self.assertEqual(out, "rolled: 2026-10-05 (2 entries)\nrolled: 2026-10-06 (3 entries)\n")
        for date in ("2026-10-05", "2026-10-06"):
            self.assertFalse(os.path.exists(os.path.join(self.jdir, date)))
            day = self._read(date + ".md")
            self.assertTrue(day.startswith("# Journal %s" % date))
            self.assertEqual(JR.split_day(day), dict((s, _entry(date, s)) for s in ENTRIES[date]))
        self.assertTrue(os.path.isfile(os.path.join(self.jdir, TODAY, "080000-000w-dev-msg.md")))
        index = self._read("INDEX-2026-10.md")
        self.assertIn("| %s/2026-10-06.md#120001-001r-claude-thinking\n" % J, index)
        self.assertIn("body \\| of 093000-000w-dev-msg | %s/2026-10-05.md#093000-000w-dev-msg\n" % J, index)
        self.assertIn("| %s/%s/080000-000w-dev-msg.md\n" % (J, TODAY), index)
        self.assertEqual(len(index.splitlines()), 6)
        snapshot = (self._read("2026-10-05.md"), self._read("2026-10-06.md"), index)
        self.assertEqual(self._run(), "nothing to roll\n")
        self.assertEqual((self._read("2026-10-05.md"), self._read("2026-10-06.md"),
                          self._read("INDEX-2026-10.md")), snapshot)

    def test_recent_day_waits(self):
        p = os.path.join(self.jdir, "2026-10-06", "120002-002r-claude-answer.md")
        os.utime(p, None)                      # touched now: under 1 h old
        self.assertEqual(self._run("--dry-run"), "would roll: 2026-10-05 (2 entries)\n")


if __name__ == "__main__":
    unittest.main()
