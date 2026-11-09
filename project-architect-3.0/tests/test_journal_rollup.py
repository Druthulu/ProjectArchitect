"""journal_rollup.py: past day folders roll into <date>.md; today stays (3.23 T2); `entries` reads both forms (3.32.15)."""

import json
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
        self.assertEqual(out, "removed: INDEX-2026-10.md (the month index is retired)\n"
                              "rolled: 2026-10-05 (2 entries)\nrolled: 2026-10-06 (3 entries)\n")
        for date in ("2026-10-05", "2026-10-06"):
            self.assertFalse(os.path.exists(os.path.join(self.jdir, date)))
            day = self._read(date + ".md")
            self.assertTrue(day.startswith("# Journal %s" % date))
            self.assertEqual(JR.split_day(day), dict((s, _entry(date, s)) for s in ENTRIES[date]))
        self.assertTrue(os.path.isfile(os.path.join(self.jdir, TODAY, "080000-000w-dev-msg.md")))
        snapshot = (self._read("2026-10-05.md"), self._read("2026-10-06.md"))
        self.assertEqual(self._run(), "nothing to roll\n")
        self.assertEqual((self._read("2026-10-05.md"), self._read("2026-10-06.md")), snapshot)

    def test_the_month_index_is_retired(self):
        """3.32.15: nothing reads INDEX-<YYYY-MM>.md but /pa-bugreport, and the mod's upkeep re-read the whole month
        after every journal write (GitHub #15): a roll-up deletes the old files, with nothing to roll too."""
        with open(os.path.join(self.jdir, "INDEX-2026-09.md"), "w", encoding="utf-8") as fh:
            fh.write("x | dev | msg | a | %s/2026-09-01.md#000000-001r-dev-msg\n" % J)
        out = subprocess.run([sys.executable, TOOL, "--root", self.root, "--today", "2026-10-01"],
                             capture_output=True, text=True, encoding="utf-8").stdout
        self.assertEqual(out, "removed: INDEX-2026-09.md (the month index is retired)\n"
                              "removed: INDEX-2026-10.md (the month index is retired)\nnothing to roll\n")
        self.assertEqual([n for n in os.listdir(self.jdir) if n.startswith("INDEX-")], [])
        self.assertTrue(os.path.isdir(os.path.join(self.jdir, "2026-10-05")))

    def _entries(self, *args):
        p = subprocess.run([sys.executable, TOOL, "entries", "--root", self.root] + list(args),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return [json.loads(line) for line in p.stdout.splitlines()]

    def test_entries_reads_both_forms_in_name_order(self):
        """3.32.15: the mod's seed and recovery digest read the journal through `entries`: packed day files and day
        folders alike, one JSON line per entry, ASCII."""
        self._run()
        rows = self._entries()
        self.assertEqual([r["name"] for r in rows],
                         ["%s/%s.md" % (d, s) for d in sorted(ENTRIES) for s in ENTRIES[d]])
        self.assertEqual(rows[0]["path"], "%s/2026-10-05.md#093000-000w-dev-msg" % J)
        self.assertEqual(rows[-1]["path"], "%s/%s/080000-000w-dev-msg.md" % (J, TODAY))
        self.assertEqual(rows[2]["text"], _entry("2026-10-06", "120000-000w-dev-msg"))
        self.assertEqual([r["name"] for r in self._entries("--since", "2026-10-06", "--match=-dev-msg$")],
                         ["2026-10-06/120000-000w-dev-msg.md", "%s/080000-000w-dev-msg.md" % TODAY])
        self.assertEqual([r["name"] for r in self._entries("--after", "2026-10-06/120001", "--last", "2")],
                         ["2026-10-06/120002-002r-claude-answer.md", "%s/080000-000w-dev-msg.md" % TODAY])
        self.assertEqual(self._entries("--match=-claude-answer$", "--max-chars", "7")[0]["text"], "---\nts:")
        one = len(json.dumps(rows[-1])) + 1
        self.assertEqual([r["name"] for r in self._entries("--budget-chars", str(one + 1))], [rows[-1]["name"]])
        p = subprocess.run([sys.executable, TOOL, "entries", "--root", self.root, "--match=("],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(p.stdout.startswith("refused: --match ("))

    def test_entries_reads_a_day_past_4_mib_and_a_stem_in_both_forms_once(self):
        """3.32.15: a busy day's file passes the 4 MiB the mod's $.fs reads (GitHub #15: 7.4 MB; bfm-decomp's
        2026-11-06: 13.4 MB): Python reads it whole. A stem in the day file and its folder (a roll-up between the
        write and the delete) comes once, the folder's."""
        date, big = "2026-10-01", "x" * (5 * 1024 * 1024)
        with open(os.path.join(self.jdir, date + ".md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Journal %s (2 entries, rolled by tools/journal_rollup.py)\n## 100000-001w-dev-msg\n---\n---\n%s\n"
                     "## 100001-002r-claude-answer\n%s" % (date, big, _entry(date, "100001-002r-claude-answer")))
        os.makedirs(os.path.join(self.jdir, date))
        with open(os.path.join(self.jdir, date, "100001-002r-claude-answer.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("the folder's copy\n")
        rows = self._entries("--match=^10000[01]-")
        self.assertEqual([r["path"] for r in rows], ["%s/%s.md#100000-001w-dev-msg" % (J, date),
                                                      "%s/%s/100001-002r-claude-answer.md" % (J, date)])
        self.assertEqual(len(rows[0]["text"]), len(big) + 9)
        self.assertEqual(rows[1]["text"], "the folder's copy\n")

    def test_external_coder_and_four_digit_entries_roll(self):
        """3.32.9: an external coder's entries (writer x) and a watcher's past its 999th (four digits) roll with the
        rest; they had stayed loose forever (bfm-decomp: 4,927 files)."""
        date, stems = "2026-10-03", ["101500-001x-coder-spawn", "101600-1000w-coder-thinking", "101700-002r-claude-answer"]
        old = time.time() - 7200
        os.makedirs(os.path.join(self.jdir, date))
        for stem in stems:
            p = os.path.join(self.jdir, date, stem + ".md")
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_entry(date, stem))
            os.utime(p, (old, old))
        self.assertIn("rolled: 2026-10-03 (3 entries)\n", self._run())
        self.assertFalse(os.path.exists(os.path.join(self.jdir, date)))
        self.assertEqual(JR.split_day(self._read(date + ".md")), dict((s, _entry(date, s)) for s in stems))

    def test_one_roll_up_at_a_time(self):
        """3.32.9: a roll-up holds <root>/.run/journal_rollup.lock; another one meanwhile (the 8-hour timer's and a
        start's, or two sessions of one project) rolls nothing; a lock older than ten minutes is a crashed one's."""
        lock = os.path.join(self.root, ".run", "journal_rollup.lock")
        os.makedirs(os.path.dirname(lock))
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("1\n")
        self.assertTrue(self._run().endswith("another roll-up is running\n"))
        self.assertTrue(os.path.isdir(os.path.join(self.jdir, "2026-10-05")))
        old = time.time() - 1200
        os.utime(lock, (old, old))
        self.assertTrue(self._run().startswith("rolled: 2026-10-05 (2 entries)\n"))
        self.assertFalse(os.path.exists(lock))

    def test_recent_day_waits(self):
        p = os.path.join(self.jdir, "2026-10-06", "120002-002r-claude-answer.md")
        os.utime(p, None)                      # touched now: under 1 h old
        self.assertEqual(self._run("--dry-run"), "would roll: 2026-10-05 (2 entries)\n")


if __name__ == "__main__":
    unittest.main()
