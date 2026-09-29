"""split_by_heading: plan/execute for rules, cookbook, ops schemes.

Fixture: 5 ``##`` headings with nested ``###``, id continuation, preamble,
re-split, idempotence, and a 2 MB generated file split under 1 s.

    cd project-architect-3.0 && python -m unittest tests.test_split -v
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa.install.split_by_heading import plan, execute, Plan, Row  # noqa: E402
from pa.install.project import Proj, Manifest                     # noqa: E402


# -- fixture text: 5 ## headings, nested ### under H2, preamble --------------

FIXTURE = """\
Preamble line 1.
Preamble line 2.

## P1 First principle
Content for P1.
Line 2 for P1.

## H2 Hygiene rule
Content for H2.

### H2a Sub-rule
Detail for H2a.

### H2b Another sub-rule
Detail for H2b.

## General guidance
This heading has no id code.
More content.

## R12 Twelfth rule
Content for R12.

## G3 Third guideline
Content for G3.
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _env(repo, dry_run=False, force=True):
    return Proj(repo=repo, dry_run=dry_run, force=force, man=Manifest())


class TestPlanRules(unittest.TestCase):
    """Fixture: 5 ## headings, nested ###, correct rule ids, preamble."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "source.md")
        _write(self.src, FIXTURE)

    def test_five_headings(self):
        p = plan(self.src, "##", "rules", "rules")
        self.assertEqual(len(p.rows), 5)
        ids = [os.path.basename(r.dest_rel).replace(".md", "")
               for r in p.rows]
        self.assertEqual(ids, ["P1", "H2", "V1", "R12", "G3"])

    def test_preamble_rel(self):
        p = plan(self.src, "##", "rules", "rules")
        self.assertEqual(p.preamble_rel, "rules/source.preamble.md")

    def test_index_lines_grammar(self):
        p = plan(self.src, "##", "rules", "rules")
        for row in p.rows:
            self.assertTrue(row.index_line.startswith("<!-- "),
                            row.index_line)
            self.assertIn("| active |", row.index_line)

    def test_no_preamble(self):
        src = os.path.join(self.tmp, "nopre.md")
        _write(src, "## A1 Only heading\nContent.\n")
        p = plan(src, "##", "rules", "rules")
        self.assertIsNone(p.preamble_rel)
        self.assertEqual(len(p.rows), 1)


class TestPlanCookbook(unittest.TestCase):
    """Cookbook scheme: C numbering, continuation from dest."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "source.md")
        _write(self.src, FIXTURE)

    def test_sequential_ids(self):
        p = plan(self.src, "##", "cookbook", "cookbook", start=1)
        ids = [os.path.basename(r.dest_rel).replace(".md", "")
               for r in p.rows]
        self.assertEqual(ids, ["C0001", "C0002", "C0003", "C0004", "C0005"])

    def test_continuation(self):
        """Numbering continues after the highest existing C file in dest."""
        cb_dir = os.path.join(self.tmp, "cookbook")
        os.makedirs(cb_dir)
        _write(os.path.join(cb_dir, "C0003.md"), "existing\n")
        _write(os.path.join(cb_dir, "C0007.md"), "existing\n")
        p = plan(self.src, "##", cb_dir, "cookbook")
        first_id = os.path.basename(p.rows[0].dest_rel).replace(".md", "")
        self.assertEqual(first_id, "C0008")

    def test_origin_in_index(self):
        p = plan(self.src, "##", "cookbook", "cookbook", start=1)
        self.assertIn("§1", p.rows[0].index_line)


class TestPlanOps(unittest.TestCase):
    """Ops scheme: slug filenames, pipe-separated index grammar."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "source.md")
        _write(self.src, FIXTURE)

    def test_slug_filenames(self):
        p = plan(self.src, "##", "docs/ops", "ops")
        self.assertEqual(len(p.rows), 5)
        fn = os.path.basename(p.rows[0].dest_rel)
        self.assertEqual(fn, "p1-first-principle.md")

    def test_index_line_format(self):
        p = plan(self.src, "##", "docs/ops", "ops")
        parts = p.rows[0].index_line.split(" | ")
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[2].isdigit())


class TestResplit(unittest.TestCase):
    """Sections over 400 lines are re-split at the next heading level."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        lines = ["Preamble.\n", "\n"]
        lines.append("## Big Section\n")
        for i in range(200):
            lines.append("intro line %d\n" % i)
        lines.append("### Sub A\n")
        for i in range(150):
            lines.append("sub a line %d\n" % i)
        lines.append("### Sub B\n")
        for i in range(150):
            lines.append("sub b line %d\n" % i)
        lines.append("## Normal\n")
        lines.append("content\n")
        self.src = os.path.join(self.tmp, "resplit.md")
        _write(self.src, "".join(lines))

    def test_no_resplit(self):
        p = plan(self.src, "##", "rules", "rules", resplit=False)
        self.assertEqual(len(p.rows), 2)

    def test_resplit_splits(self):
        p = plan(self.src, "##", "rules", "rules", resplit=True)
        # Big Section intro + Sub A + Sub B + Normal = 4
        self.assertEqual(len(p.rows), 4)
        headings = [r.heading for r in p.rows]
        self.assertEqual(headings,
                         ["Big Section", "Sub A", "Sub B", "Normal"])

    def test_resplit_execute(self):
        env = _env(self.tmp)
        os.makedirs(os.path.join(self.tmp, "rules"), exist_ok=True)
        _write(os.path.join(self.tmp, "rules", "INDEX.md"), "")
        p = plan(self.src, "##", "rules", "rules", resplit=True)
        execute(p, env)
        for row in p.rows:
            self.assertTrue(
                os.path.isfile(env.path(row.dest_rel)),
                "missing: %s" % row.dest_rel)


class TestExecute(unittest.TestCase):
    """execute() writes files through place and appends index lines."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "source.md")
        _write(self.src, FIXTURE)
        os.makedirs(os.path.join(self.tmp, "rules"), exist_ok=True)
        _write(os.path.join(self.tmp, "rules", "INDEX.md"), "")

    def test_files_created(self):
        env = _env(self.tmp)
        p = plan(self.src, "##", "rules", "rules")
        execute(p, env)
        for row in p.rows:
            self.assertTrue(os.path.isfile(env.path(row.dest_rel)))
        self.assertTrue(os.path.isfile(env.path(p.preamble_rel)))

    def test_content_correct(self):
        env = _env(self.tmp)
        p = plan(self.src, "##", "rules", "rules")
        execute(p, env)
        text = _read(env.path(p.rows[0].dest_rel))
        self.assertTrue(text.startswith("## P1 First principle"))
        self.assertIn("Content for P1", text)

    def test_index_lines_appended(self):
        env = _env(self.tmp)
        p = plan(self.src, "##", "rules", "rules")
        execute(p, env)
        idx = _read(os.path.join(self.tmp, "rules", "INDEX.md"))
        self.assertEqual(idx.count("\n"), 5)
        self.assertIn("<!-- P1 |", idx)
        self.assertIn("<!-- V1 |", idx)

    def test_idempotent(self):
        """Second run: all files skipped, no duplicate index lines."""
        env1 = _env(self.tmp)
        p1 = plan(self.src, "##", "rules", "rules")
        execute(p1, env1)
        idx_after_1 = _read(os.path.join(self.tmp, "rules", "INDEX.md"))

        env2 = _env(self.tmp)
        p2 = plan(self.src, "##", "rules", "rules")
        execute(p2, env2)
        idx_after_2 = _read(os.path.join(self.tmp, "rules", "INDEX.md"))

        expected_files = len(p2.rows) + (1 if p2.preamble_rel else 0)
        self.assertEqual(len(env2.man.skipped), expected_files)
        self.assertEqual(len(env2.man.created), 0)
        self.assertEqual(idx_after_1, idx_after_2)

    def test_dry_run(self):
        env = _env(self.tmp, dry_run=True)
        p = plan(self.src, "##", "rules", "rules")
        execute(p, env)
        # No files actually written (place returns "planned", not created on disk)
        for row in p.rows:
            dst = env.path(row.dest_rel)
            self.assertFalse(os.path.isfile(dst),
                             "dry run should not create %s" % dst)


class TestLargeFile(unittest.TestCase):
    """A generated 2 MB file is split under 1 s."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-split-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "huge.md")
        with open(self.src, "w", encoding="utf-8", newline="\n") as fh:
            for i in range(50):
                fh.write("## S%d Section %d\n" % (i + 1, i + 1))
                fh.write("x" * 40000 + "\n")
        os.makedirs(os.path.join(self.tmp, "rules"), exist_ok=True)
        _write(os.path.join(self.tmp, "rules", "INDEX.md"), "")

    def test_under_one_second(self):
        env = _env(self.tmp)
        t0 = time.perf_counter()
        p = plan(self.src, "##", "rules", "rules")
        execute(p, env)
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(p.rows), 50)
        self.assertLess(elapsed, 1.0,
                        "split took %.2f s (limit 1 s)" % elapsed)


if __name__ == "__main__":
    unittest.main()
