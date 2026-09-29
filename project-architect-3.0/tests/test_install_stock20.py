"""Migration tests for the stock 2.0 layout (design B J.3, fixture stock20).

Every test runs against a git-initialised copy of tests/fixtures/stock20/
via ``fixture_copy("stock20")``, with a throw-away ``--pa3-dir``.

    cd project-architect-3.0 && python -m unittest tests.test_install_stock20 -v
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa.install import build_parser, main as install_main  # noqa: E402
from pa.install import detect as detect_mod                # noqa: E402
from pa.install import natural_sort                        # noqa: E402
from pa.install import project as pj                       # noqa: E402
from tests.test_install_project import fixture_copy, steps, git  # noqa: E402

PY = sys.executable
_STEP_RE = re.compile(r"^(P\d+)\s+\S.*?\s+(SKIP|DONE|FAIL)\s\s")


def _run(repo, *extra, commit=True):
    """Run the installer on *repo*; returns (rc, output)."""
    pa3 = os.path.join(os.path.dirname(repo), "pa3")
    os.makedirs(pa3, exist_ok=True)
    with open(os.path.join(pa3, "VERSION"), "w") as fh:
        fh.write("version: 3.0.0-test\n")
    argv = ["--project", repo, "--python", PY, "--pa3-dir", pa3, "--yes"]
    if not commit:
        argv.append("--no-commit")
    argv.extend(extra)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = install_main(argv)
    return rc, buf.getvalue()


def _path(repo, *parts):
    return os.path.join(repo, *parts)


def _exists(repo, rel):
    return os.path.isfile(_path(repo, *rel.split("/")))


def _read(repo, rel):
    with open(_path(repo, *rel.split("/")), "r", encoding="utf-8") as fh:
        return fh.read()


class Stock20Case(unittest.TestCase):
    """Base: a git-inited copy of the stock20 fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-s20-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = fixture_copy("stock20", self.tmp)

    def migrate(self, *extra, commit=True):
        rc, out = _run(self.repo, *extra, commit=commit)
        self.assertEqual(rc, 0, out)
        return out


class TestStock20Migration(Stock20Case):
    """Full migration: every mapped source gone, every target present."""

    def test_mapped_sources_gone_and_targets_present(self):
        out = self.migrate(commit=False)
        # old CLAUDE.md retired to docs/retired/CLAUDE.pa2.md
        self.assertTrue(_exists(self.repo, "docs/retired/CLAUDE.pa2.md"))
        self.assertIn("TerrainDiffusion",
                       _read(self.repo, "docs/retired/CLAUDE.pa2.md"))
        # new CLAUDE.md is PA3
        self.assertTrue(_exists(self.repo, "CLAUDE.md"))
        self.assertIn("Project Architect 3.0", _read(self.repo, "CLAUDE.md")[:80])
        # registry split -> rules/
        self.assertFalse(_exists(self.repo, "RULES_REGISTRY.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/RULES_REGISTRY.md"))
        # cookbook split
        self.assertFalse(_exists(self.repo, "docs/7dtd-modding-cookbook.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/7dtd-modding-cookbook.md"))
        # ops split
        self.assertFalse(_exists(self.repo, "docs/ops-setup.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/ops-setup.md"))
        # retirements
        self.assertFalse(_exists(self.repo, "docs/effort-map.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/effort-map.md"))
        self.assertTrue(_exists(self.repo, "docs/project-architect.md"))
        self.assertIn("## Migration", _read(self.repo, "docs/project-architect.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/project-architect-2.0.md"))
        self.assertFalse(_exists(self.repo, "phase-ends/CURRENT_PHASE.template.md"))
        self.assertFalse(_exists(self.repo, "phase-ends/PhaseEnd.template.md"))
        # current phase archived
        self.assertFalse(_exists(self.repo, "phase-ends/CURRENT_PHASE.md"))
        self.assertTrue(_exists(self.repo, "phase-ends/logs/PhaseLog_24.partial.md"))

    def test_rule_files_equal_registry_heading_count(self):
        """11 ### headings in the registry -> 11 rule files."""
        self.migrate(commit=False)
        rules_dir = _path(self.repo, "rules")
        rule_files = [f for f in os.listdir(rules_dir)
                      if f.endswith(".md") and f != "INDEX.md"
                      and not f.endswith(".preamble.md")]
        self.assertEqual(len(rule_files), 11)

    def test_cookbook_files_equal_heading_count(self):
        """3 ## headings in the cookbook -> 3 cookbook files."""
        self.migrate(commit=False)
        cb_dir = _path(self.repo, "cookbook")
        cb_files = [f for f in os.listdir(cb_dir)
                    if f.endswith(".md") and f != "INDEX.md"
                    and ".preamble." not in f]
        self.assertEqual(len(cb_files), 3)

    def test_ops_topics_equal_heading_count(self):
        """3 ## headings in ops-setup.md -> 3 ops topic files."""
        self.migrate(commit=False)
        ops_dir = _path(self.repo, "docs", "ops")
        ops_files = [f for f in os.listdir(ops_dir)
                     if f.endswith(".md") and f != "INDEX.md"
                     and ".preamble." not in f]
        self.assertEqual(len(ops_files), 3)

    def test_git_follow_on_moved_file(self):
        """git log --follow on a moved file shows the pre-move commit."""
        self.migrate(commit=True)
        r = git(self.repo, "log", "--follow", "--oneline", "--",
                "docs/retired/CLAUDE.pa2.md")
        self.assertIn("fixture", r.stdout, r.stderr)

    def test_backup_hook_gone_and_settings_parse(self):
        self.migrate(commit=False)
        text = _read(self.repo, ".claude/settings.json")
        settings = json.loads(text)
        # backup hook should be gone
        self.assertNotIn("backup-claude-state", text)
        # settings should parse
        self.assertIsInstance(settings, dict)

    def test_how_we_work_has_no_double_braces(self):
        self.migrate(commit=False)
        text = _read(self.repo, "HOW_WE_WORK.md")
        self.assertNotIn("{{", text)

    def test_second_run_is_all_skip(self):
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        # every mapping step should be SKIP (bootstrap/files are also SKIP on second run)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)

    def test_no_rm_in_output(self):
        """Neither run's output contains ' rm '."""
        out1 = self.migrate(commit=False)
        _rc2, out2 = _run(self.repo, commit=False)
        for out in (out1, out2):
            self.assertNotIn(" rm ", out, "found ' rm ' in output")

    def test_detect_pa3_afterwards(self):
        self.migrate(commit=False)
        self.assertEqual(detect_mod.detect(self.repo), "pa3")

    def test_launch_dry_run_finds_plan_dir(self):
        self.migrate(commit=False)
        plan_dir = os.path.join(self.repo, "phase-ends", "current")
        self.assertTrue(os.path.isdir(plan_dir))

    def test_no_trailer_in_commit(self):
        """The migration commit has no AI trailer."""
        out = self.migrate(commit=True)
        r = git(self.repo, "log", "-1", "--format=%B")
        msg = r.stdout.strip()
        self.assertTrue(msg.startswith("chore:"), msg)
        self.assertNotIn("Co-authored-by", msg)
        self.assertNotIn("Generated by", msg)

    def test_legacy_index_has_one_entry_per_phaseend(self):
        """D1: LEGACY_INDEX.md has 3 entries (one per PhaseEnd in the fixture)."""
        self.migrate(commit=False)
        text = _read(self.repo, "phase-ends/LEGACY_INDEX.md")
        # skip comment lines: entries are non-comment, non-blank lines
        entries = [l for l in text.splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(len(entries), 3,
                         "expected 3 entries, got %d: %s" % (len(entries), entries))

    def test_generation_plan_has_no_placeholder(self):
        """D1: GENERATION_PLAN.md contains no '<one line' placeholder."""
        self.migrate(commit=False)
        text = _read(self.repo, "GENERATION_PLAN.md")
        self.assertNotIn("<one line", text)

    def test_git_status_clean_after_commit(self):
        """D2: git status --short is empty after a committed migration."""
        self.migrate(commit=True)
        r = git(self.repo, "status", "--short")
        self.assertEqual(r.stdout.strip(), "",
                         "untracked/modified after commit:\n%s" % r.stdout)

    def test_no_bak_outside_run(self):
        """D3: no *.bak-* files outside .run/ after migration."""
        self.migrate(commit=False)
        for dp, dns, fns in os.walk(self.repo):
            dns[:] = [d for d in dns if d != ".git"]
            rel_dir = os.path.relpath(dp, self.repo).replace("\\", "/")
            if rel_dir.startswith(".run"):
                continue
            for name in fns:
                self.assertFalse(".bak-" in name,
                                 "backup outside .run/: %s/%s" % (rel_dir, name))

    def test_no_kept_readme_line(self):
        """D4: the 'kept' close line must not name a file replaced by rows."""
        out = self.migrate(commit=False)
        for line in out.splitlines():
            if line.startswith("kept "):
                self.assertNotIn("phase-ends/README.md", line)

    def test_how_we_work_no_empty_tagline_dash(self):
        """D6: no '— .' when the tagline is empty."""
        self.migrate(commit=False)
        text = _read(self.repo, "HOW_WE_WORK.md")
        self.assertNotIn(" — .", text)

    def test_first_run_no_skip_for_generate(self):
        """No generate row shows SKIP on the first run (P1 excepted)."""
        out = self.migrate(commit=False)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) != "P1" and "generate" in line.lower():
                self.assertNotEqual(m.group(2), "SKIP",
                                    "unexpected SKIP on first run: %s" % line)

    def test_second_run_zero_updated(self):
        """The second run's close line reports 0 updated."""
        self.migrate(commit=False)
        _rc, out2 = _run(self.repo, commit=False)
        for line in out2.splitlines():
            if "close" in line.lower() and "updated" in line:
                self.assertIn("0 updated", line,
                              "expected 0 updated on second run: %s" % line)
                break
        else:
            self.fail("no close line with 'updated' in second run output")


class TestStock20NaturalSort(unittest.TestCase):
    """natural_sort.key puts label 23.5 between 23 and 24."""

    def test_interphase_sorts_correctly(self):
        # legacy_index.rows drops the "Phase" prefix, so sorting happens on
        # bare numeric labels; natural_sort.key puts 23.5 between 23 and 24
        labels = ["0", "9", "23", "23.5"]
        result = sorted(labels, key=natural_sort.key)
        idx_23 = result.index("23")
        idx_235 = result.index("23.5")
        self.assertEqual(idx_235, idx_23 + 1,
                         "23.5 should sort right after 23: %s" % result)


class TestStock20Readme(Stock20Case):
    """The 2.0 phase-ends README is retired and replaced by the PA3 template."""

    def test_readme_equals_template(self):
        self.migrate(commit=False)
        readme = _read(self.repo, "phase-ends/README.md")
        self.assertIn("## Layout", readme)
        self.assertIn("TASK_INDEX.md", readme)

    def test_retired_readme_holds_2_0_text(self):
        self.migrate(commit=False)
        retired = _read(self.repo, "docs/retired/phase-ends-README.pa2.md")
        self.assertIn("CURRENT_PHASE.md", retired)

    def test_retired_readme_git_follow(self):
        self.migrate(commit=True)
        r = git(self.repo, "log", "--follow", "--oneline", "--",
                "docs/retired/phase-ends-README.pa2.md")
        self.assertIn("fixture", r.stdout, r.stderr)

    def test_second_run_readme_rows_skip(self):
        """Second run: the readme retire and generate are SKIP."""
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)


class TestStock20DryRun(Stock20Case):
    """--dry-run writes nothing and prints git mv lines + unmapped list."""

    def test_dry_run_writes_nothing(self):
        # take a hash of the tree before
        import hashlib
        def tree_hash(root):
            h = hashlib.sha256()
            for dp, dns, fns in os.walk(root):
                dns[:] = sorted(d for d in dns if d != ".git")
                for name in sorted(fns):
                    full = os.path.join(dp, name)
                    h.update(os.path.relpath(full, root).replace("\\", "/").encode())
                    with open(full, "rb") as fh:
                        h.update(fh.read())
            return h.hexdigest()
        before = tree_hash(self.repo)
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        after = tree_hash(self.repo)
        self.assertEqual(before, after, "dry run should not change the tree")

    def test_dry_run_prints_git_mv(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("git mv", out)

    def test_dry_run_no_unmapped_when_all_covered(self):
        """When every file is mapped or kept, no unmapped: block appears."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        # stock20 has no unmapped files after the kept exclusions + readme rows
        self.assertNotIn("unmapped:", out)

    def test_dry_run_kept_line(self):
        """kept: line names PROJECT_CONTEXT.md and PhaseEnd files."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("kept:", out)
        self.assertIn("PROJECT_CONTEXT.md (constitution)", out)
        self.assertIn("PhaseEnd file(s) in phase-ends/ (LEGACY_INDEX.md)", out)

    def test_dry_run_no_phaseend_in_unmapped(self):
        """PhaseEnd files do not appear under unmapped:."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        in_unmapped = False
        for line in out.splitlines():
            if "unmapped:" in line:
                in_unmapped = True
                continue
            if in_unmapped and line.strip():
                self.assertNotIn("PhaseEnd_", line)

    def test_dry_run_no_project_context_in_unmapped(self):
        """PROJECT_CONTEXT.md does not appear under unmapped:."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        in_unmapped = False
        for line in out.splitlines():
            if "unmapped:" in line:
                in_unmapped = True
                continue
            if in_unmapped and line.strip():
                self.assertNotIn("PROJECT_CONTEXT.md", line)

    def test_dry_run_readme_rows(self):
        """Dry run prints the two README rows (retire + generate)."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("retire phase-ends/README.md", out)
        self.assertIn("generate phase-ends/README.md", out)

    def test_dry_run_no_rm(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertNotIn(" rm ", out)


class TestTextGenerator(unittest.TestCase):
    """The generic `text` generate row: write once, append/prepend once (3.3 T4 expert)."""

    def _env(self):
        from pa.install.project import Proj, Manifest
        tmp = tempfile.mkdtemp(prefix="pa3-textgen-")
        self.addCleanup(shutil.rmtree, tmp, True)
        return Proj(repo=tmp, dry_run=False, man=Manifest())

    def test_write_append_prepend_are_idempotent(self):
        from pa.install import migrate
        env = self._env()
        row = migrate.Row("generate", None, "docs/x/INDEX.md", "index",
                          {"generator": "text", "text": "# x\n"})
        self.assertEqual(migrate._do_generate(env, row)[0], "DONE")
        self.assertTrue(migrate._check_done(env, row))
        app = migrate.Row("generate", None, "docs/x/INDEX.md", "legacy line",
                          {"generator": "text", "text": "- legacy\n", "mode": "append"})
        self.assertFalse(migrate._check_done(env, app))
        self.assertEqual(migrate._do_generate(env, app)[0], "DONE")
        self.assertTrue(migrate._check_done(env, app))
        self.assertEqual(migrate._do_generate(env, app)[0], "DONE")
        pre = migrate.Row("generate", None, "docs/x/INDEX.md", "frozen header",
                          {"generator": "text", "text": "<!-- frozen -->\n", "mode": "prepend"})
        self.assertEqual(migrate._do_generate(env, pre)[0], "DONE")
        with open(os.path.join(env.repo, "docs", "x", "INDEX.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "<!-- frozen -->\n# x\n- legacy\n")
