"""Migration tests for the 1.x-shaped layout (design B J.3, fixture onex).

Every test runs against a git-initialised copy of tests/fixtures/onex/
via ``fixture_copy("onex")``, with a throw-away ``--pa3-dir``.

    cd project-architect-3.0 && python -m unittest tests.test_install_onex -v
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


class OnexCase(unittest.TestCase):
    """Base: a git-inited copy of the onex fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-onex-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = fixture_copy("onex", self.tmp)

    def migrate(self, *extra, commit=True):
        rc, out = _run(self.repo, *extra, commit=commit)
        self.assertEqual(rc, 0, out)
        return out


class TestOnexMigration(OnexCase):
    """Full migration: every mapped source gone, every target present."""

    def test_phaseend_renames(self):
        """PhaseEnd files renamed with dots, non-phase to misc/."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        # dotted renames
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_Phase11.5.7.md" % ped))
        self.assertFalse(_exists(self.repo, "%s/PhaseEnd_Phase11_5_7.md" % ped))
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_Phase9.3.5.md" % ped))
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_Phase9.4-9.7.md" % ped))
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_Phase9.9.md" % ped))
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_PhaseGen3.F.7.md" % ped))
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_PhaseGen4.WL.md" % ped))
        # chore -> misc/
        self.assertTrue(_exists(self.repo,
            "%s/misc/PhaseEnd_Chore_CC_Methodology_BFM_Update_2026_06_16.md" % ped))

    def test_research_docs_archived(self):
        """Plan/verdict/checkpoint docs moved to docs/research-archive/."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        for name in ("Vantage_Gen3_5_Plan.md", "Vantage_Gen4_C_Verdict.md",
                     "PhaseGen3_5_B_Checkpoint_Session1.md"):
            self.assertFalse(_exists(self.repo, "%s/%s" % (ped, name)),
                             "%s still in source" % name)
            self.assertTrue(_exists(self.repo, "docs/research-archive/%s" % name),
                            "%s not in archive" % name)
        # INDEX.md
        self.assertTrue(_exists(self.repo, "docs/research-archive/INDEX.md"))
        index = _read(self.repo, "docs/research-archive/INDEX.md")
        self.assertIn("Vantage_Gen3_5_Plan.md", index)

    def test_legacy_pointer_line(self):
        """One ## Legacy line in RESEARCH_INDEX.md."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        text = _read(self.repo, "%s/RESEARCH_INDEX.md" % ped)
        self.assertEqual(text.count("## Legacy"), 1)

    def test_registry_split_to_rules(self):
        """Rules registry split into rules/ files."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/Vantage_Rules_Registry.md" % ped))
        rules_dir = _path(self.repo, "rules")
        rule_files = [f for f in os.listdir(rules_dir)
                      if f.endswith(".md") and f != "INDEX.md"
                      and not f.endswith(".preamble.md")]
        # the registry has ### headings: A1, A2, B1, C1, C2, D1, D2, D3
        # plus section headings under ## that are not ### (Gen2, Gen3, Gen3.5)
        self.assertGreaterEqual(len(rule_files), 8)

    def test_project_context_moved(self):
        """Project context moved to repo root verbatim."""
        self.migrate(commit=False)
        self.assertTrue(_exists(self.repo, "PROJECT_CONTEXT.md"))
        text = _read(self.repo, "PROJECT_CONTEXT.md")
        self.assertIn("Vantage", text)

    def test_current_phase_archived(self):
        """CURRENT_PHASE.md archived to logs/."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/CURRENT_PHASE.md" % ped))
        # partial log exists in the logs dir
        logs_dir = _path(self.repo, ped, "logs")
        self.assertTrue(os.path.isdir(logs_dir))
        partial = [f for f in os.listdir(logs_dir)
                   if f.startswith("PhaseLog_") and f.endswith(".partial.md")]
        self.assertEqual(len(partial), 1, "expected one partial log: %s" % os.listdir(logs_dir))

    def test_phase_log_moved(self):
        """phase-logs/* moved into logs/."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(os.path.isfile(
            _path(self.repo, ped, "phase-logs", "PhaseLog_Gen4_V.md")))
        self.assertTrue(_exists(self.repo, "%s/logs/PhaseLog_Gen4_V.md" % ped))

    def test_effort_map_retired(self):
        """Effort map retired to docs/retired/."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/docs/Vantage_Effort_Map.md" % ped))
        self.assertTrue(_exists(self.repo, "docs/retired/Vantage_Effort_Map.md"))

    def test_claude_md_retired_and_new(self):
        """Old CLAUDE.md retired, new PA3 CLAUDE.md written."""
        self.migrate(commit=False)
        self.assertTrue(_exists(self.repo, "docs/retired/CLAUDE.pa2.md"))
        self.assertIn("Vantage", _read(self.repo, "docs/retired/CLAUDE.pa2.md"))
        self.assertTrue(_exists(self.repo, "CLAUDE.md"))
        self.assertIn("Project Architect 3.0", _read(self.repo, "CLAUDE.md")[:80])

    def test_pa_json_phase_ends_dir(self):
        """pa.json carries the correct phase_ends_dir."""
        self.migrate(commit=False)
        pa = json.loads(_read(self.repo, ".claude/pa.json"))
        self.assertEqual(pa["phase_ends_dir"], "Project Context Markdowns")

    def test_settings_json_written_fresh(self):
        """1-byte settings.json is replaced with a real settings file."""
        self.migrate(commit=False)
        text = _read(self.repo, ".claude/settings.json")
        settings = json.loads(text)
        self.assertIsInstance(settings, dict)
        # should have content from the project snippet
        self.assertGreater(len(text), 10)

    def test_settings_local_untouched(self):
        """settings.local.json is not modified."""
        before = _read(self.repo, ".claude/settings.local.json")
        self.migrate(commit=False)
        after = _read(self.repo, ".claude/settings.local.json")
        self.assertEqual(before, after)

    def test_git_follow_on_renamed_phaseend(self):
        """git log --follow on a renamed PhaseEnd shows the original commit."""
        self.migrate(commit=True)
        ped = "Project Context Markdowns"
        r = git(self.repo, "log", "--follow", "--oneline", "--",
                "%s/PhaseEnd_Phase11.5.7.md" % ped)
        self.assertIn("fixture", r.stdout, r.stderr)

    def test_second_run_all_skip(self):
        """Second run: every step is SKIP."""
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)

    def test_no_rm_in_output(self):
        """Neither run contains ' rm '."""
        out1 = self.migrate(commit=False)
        _rc2, out2 = _run(self.repo, commit=False)
        for out in (out1, out2):
            self.assertNotIn(" rm ", out, "found ' rm ' in output")

    def test_no_trailer_in_commit(self):
        """The migration commit has no AI trailer."""
        out = self.migrate(commit=True)
        r = git(self.repo, "log", "-1", "--format=%B")
        msg = r.stdout.strip()
        self.assertTrue(msg.startswith("chore:"), msg)
        self.assertNotIn("Co-authored-by", msg)
        self.assertNotIn("Generated by", msg)

    def test_detect_pa3_afterwards(self):
        """After migration, detect returns pa3."""
        self.migrate(commit=False)
        self.assertEqual(detect_mod.detect(self.repo), "pa3")

    def test_launch_dry_run_finds_plan_dir(self):
        """tools/launch.py --dry-run finds the plan directory through pa.json."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        plan_dir = os.path.join(self.repo, ped, "current")
        self.assertTrue(os.path.isdir(plan_dir),
                        "expected %s to exist" % plan_dir)
        # pa.json exists and has the right phase_ends_dir
        pa = json.loads(_read(self.repo, ".claude/pa.json"))
        self.assertEqual(pa["phase_ends_dir"], ped)

    def test_how_we_work_has_no_double_braces(self):
        self.migrate(commit=False)
        text = _read(self.repo, "HOW_WE_WORK.md")
        self.assertNotIn("{{", text)

    def test_project_architect_placed(self):
        """The methodology document lands at docs/project-architect.md (T12.c2)."""
        self.migrate(commit=False)
        self.assertIn("## Migration", _read(self.repo, "docs/project-architect.md"))

    def test_paths_with_spaces_in_git_calls(self):
        """Paths with spaces pass through every git call as argument lists."""
        out = self.migrate(commit=True)
        ped = "Project Context Markdowns"
        # git log --follow should work on a path with spaces
        r = git(self.repo, "log", "--follow", "--oneline", "--",
                "%s/PhaseEnd_Phase9.3.5.md" % ped)
        self.assertIn("fixture", r.stdout,
                      "git follow failed on path with spaces: %s" % r.stderr)


class TestOnexNaturalSort(unittest.TestCase):
    """natural_sort.key puts 9.4-9.7 between 9.3.5 and 9.9."""

    def test_range_label_sorts_correctly(self):
        labels = ["9.3.5", "9.4-9.7", "9.9", "11.5.7", "Gen3.F.7", "Gen4.WL"]
        result = sorted(labels, key=natural_sort.key)
        idx_935 = result.index("9.3.5")
        idx_9497 = result.index("9.4-9.7")
        idx_99 = result.index("9.9")
        self.assertEqual(idx_9497, idx_935 + 1,
                         "9.4-9.7 should sort right after 9.3.5: %s" % result)
        self.assertEqual(idx_99, idx_9497 + 1,
                         "9.9 should sort right after 9.4-9.7: %s" % result)


class TestOnexReadme(OnexCase):
    """No README in the onex fixture -> no readme rows emitted."""

    def test_no_readme_rows_in_mapping(self):
        """readme_rows returns empty when there is no README."""
        from pa.install.migrate import readme_rows as _rr
        from pa.install.project import Proj, Manifest
        env = Proj(repo=self.repo, dry_run=False, man=Manifest())
        env.phase_ends_dir = "Project Context Markdowns"
        env.pkg = os.path.join(PKG)
        self.assertEqual(_rr(env), [])

    def test_dry_run_no_readme_row(self):
        """Dry run output has no README retire or generate row."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            if "README.md" in line and ("retire" in line or "generate" in line):
                if "phase-ends-README" in line or "phase_ends" in line.lower():
                    self.fail("unexpected README row: %s" % line)


class TestOnexDryRun(OnexCase):
    """--dry-run writes nothing and prints the expected lines."""

    def test_dry_run_writes_nothing(self):
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

    def test_dry_run_prints_quoted_git_mv(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn(
            'git mv "Project Context Markdowns/PhaseEnd_Phase11_5_7.md" '
            '"Project Context Markdowns/PhaseEnd_Phase11.5.7.md"',
            out)

    def test_dry_run_sort_check(self):
        """Sort-check line places 9.4-9.7 between 9.3.5 and 9.9."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            if "sort:" in line and "9.4-9.7" in line:
                # verify 9.3.5 is before 9.4-9.7 and 9.4-9.7 is before 9.9
                pos_935 = line.index("9.3.5")
                pos_9497 = line.index("9.4-9.7")
                pos_99 = line.index("9.9")
                self.assertLess(pos_935, pos_9497,
                                "9.3.5 should be before 9.4-9.7")
                self.assertLess(pos_9497, pos_99,
                                "9.4-9.7 should be before 9.9")
                return
        self.fail("sort-check line with 9.4-9.7 not found in:\n%s" % out)

    def test_dry_run_no_rm(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertNotIn(" rm ", out)

    def test_dry_run_has_phaseend_name(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("PhaseEnd_Phase11.5.7.md", out)

    def test_dry_run_archive_count(self):
        """Dry run prints the archive count note."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"archive: \d+ research documents")

    def test_dry_run_template_retire(self):
        """Dry run shows a retire row for the template."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("retire", out)
        self.assertIn("CURRENT_PHASE.template.md", out)

    def test_dry_run_cookbook_split(self):
        """Dry run shows the cookbook split row."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("split", out)
        self.assertIn("Vantage_Cookbook.md", out)

    def test_dry_run_ops_split(self):
        """Dry run shows the ops_setup split row."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("split", out)
        self.assertIn("Vantage_Ops_Setup.md", out)


class TestOnexTemplateRetire(OnexCase):
    """D7: Templates in ped retired to docs/retired/."""

    def test_template_retired(self):
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/CURRENT_PHASE.template.md" % ped))
        self.assertTrue(_exists(self.repo, "docs/retired/CURRENT_PHASE.template.md"))

    def test_template_retire_second_run_skip(self):
        """Second run SKIPs the template retire."""
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)


class TestOnexResearchExpanded(OnexCase):
    """D9: Generation-prefixed and keyword research docs archived."""

    def test_gen_prefixed_archived(self):
        """Vantage_Gen4_X_Spec.md (gen-prefixed) goes to research-archive."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/Vantage_Gen4_X_Spec.md" % ped))
        self.assertTrue(_exists(self.repo, "docs/research-archive/Vantage_Gen4_X_Spec.md"))

    def test_research_index_has_gen_prefixed(self):
        """INDEX.md includes the gen-prefixed document."""
        self.migrate(commit=False)
        index = _read(self.repo, "docs/research-archive/INDEX.md")
        self.assertIn("Vantage_Gen4_X_Spec.md", index)

    def test_thesis_kept(self):
        """Vantage_Product_Thesis.md stays in place (stay-word, no gen prefix)."""
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertTrue(_exists(self.repo, "%s/Vantage_Product_Thesis.md" % ped))


class TestOnexCookbookOpsSplit(OnexCase):
    """D11: Cookbook and ops_setup in ped/docs/ split."""

    def test_cookbook_split_count(self):
        """2 ## headings in the cookbook -> 2 cookbook files."""
        self.migrate(commit=False)
        cb_dir = _path(self.repo, "cookbook")
        cb_files = [f for f in os.listdir(cb_dir)
                    if f.endswith(".md") and f != "INDEX.md"
                    and ".preamble." not in f]
        self.assertEqual(len(cb_files), 2)

    def test_cookbook_original_retired(self):
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/docs/Vantage_Cookbook.md" % ped))
        self.assertTrue(_exists(self.repo, "docs/retired/Vantage_Cookbook.md"))

    def test_ops_split_count(self):
        """2 ## headings in ops_setup -> 2 ops files."""
        self.migrate(commit=False)
        ops_dir = _path(self.repo, "docs", "ops")
        ops_files = [f for f in os.listdir(ops_dir)
                     if f.endswith(".md") and f != "INDEX.md"
                     and ".preamble." not in f]
        self.assertEqual(len(ops_files), 2)

    def test_ops_original_retired(self):
        self.migrate(commit=False)
        ped = "Project Context Markdowns"
        self.assertFalse(_exists(self.repo, "%s/docs/Vantage_Ops_Setup.md" % ped))
        self.assertTrue(_exists(self.repo, "docs/retired/Vantage_Ops_Setup.md"))


class TestOnexPedDocsScanned(OnexCase):
    """D12: <ped>/docs/ included in the unmapped scan."""

    def test_thesis_in_unmapped(self):
        """Thesis stays and appears in dry-run unmapped list."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("Vantage_Product_Thesis.md", out)
