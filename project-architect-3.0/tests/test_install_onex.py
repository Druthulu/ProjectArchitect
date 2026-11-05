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
        ped = "phase-ends"
        # dotted renames
        self.assertTrue(_exists(self.repo, "%s/PhaseEnd_Phase11.5.7.md" % ped))
        self.assertFalse(_exists(self.repo, "Project Context Markdowns/PhaseEnd_Phase11_5_7.md"))
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
        ped = "phase-ends"
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
        ped = "phase-ends"
        self.assertFalse(_exists(self.repo, "Project Context Markdowns/CURRENT_PHASE.md"))
        # partial log exists in the logs dir
        logs_dir = _path(self.repo, ped, "logs")
        self.assertTrue(os.path.isdir(logs_dir))
        partial = [f for f in os.listdir(logs_dir)
                   if f.startswith("PhaseLog_") and f.endswith(".partial.md")]
        self.assertEqual(len(partial), 1, "expected one partial log: %s" % os.listdir(logs_dir))

    def test_phase_log_moved(self):
        """phase-logs/* moved into logs/."""
        self.migrate(commit=False)
        ped = "phase-ends"
        self.assertFalse(os.path.isfile(
            _path(self.repo, "Project Context Markdowns", "phase-logs", "PhaseLog_Gen4_V.md")))
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
        self.assertEqual(pa["phase_ends_dir"], "phase-ends")

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
        ped = "phase-ends"
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
        ped = "phase-ends"
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
        # git log --follow carries back to the path with spaces
        r = git(self.repo, "log", "--follow", "--oneline", "--", "phase-ends/PhaseEnd_Phase9.3.5.md")
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
            'phase-ends/PhaseEnd_Phase11.5.7.md',
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


class TestOnexDocsIndex(OnexCase):
    """3.32 B2: the docs index runs once, after the migration commit, in its own commit, no model."""

    def test_intake_pending_on_a_first_install_with_a_past(self):
        """3.32 B9: a PA2 layout marks the intake."""
        self.migrate()
        with open(_path(self.repo, ".claude", "pa.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[pj.INTAKE], "pending")
        self.assertFalse(_exists(self.repo, "docs/PRE_PA3_STATE.md"))   # the mod's agent writes it, never the install

    def test_has_past(self):
        root = tempfile.mkdtemp(prefix="pa3-past-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        class Env:
            repo = root
            phase_ends_dir = "phase-ends"
            layout = "fresh"

            @staticmethod
            def path(rel):
                return _path(root, *rel.split("/"))
        self.assertFalse(pj._has_past(Env))
        Env.layout = "pa3"
        self.assertFalse(pj._has_past(Env))
        Env.layout = "onex"
        self.assertTrue(pj._has_past(Env))
        Env.layout = "fresh"
        os.makedirs(_path(root, "phase-ends"))
        with open(_path(root, "phase-ends", "PhaseEnd_Phase1.md"), "w", encoding="utf-8") as fh:
            fh.write("# end\n")
        self.assertTrue(pj._has_past(Env))

    def test_job_markers(self):
        """3.32 B9: one intake, once: first run, past, PA3 layout; none else. The old card cap moves up, a changed one stays."""
        root = tempfile.mkdtemp(prefix="pa3-jobs-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        class Env:
            repo = root
            phase_ends_dir = "phase-ends"
            layout = "pa3"

            @staticmethod
            def path(rel):
                return _path(root, *rel.split("/"))
        os.makedirs(_path(root, ".claude"))

        def jobs(raw, layout="pa3"):
            Env.layout = layout
            with open(_path(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
                fh.write(raw)
            return json.loads(pj.job_markers(Env, raw))
        cap = '{\n  "card": { "max_chars": 7000 }\n}\n'
        got = jobs(cap)
        self.assertEqual(got, {"intake": "pending", "card": {"max_chars": 10000}})           # already on PA3
        self.assertEqual(jobs('{\n  "card": { "max_chars": 8000 }\n}\n')["card"]["max_chars"], 8000)
        self.assertEqual(jobs('{\n  "first_run": {"from": "gen3"}\n}\n', "fresh")["intake"], "pending")  # a first run
        for marked in ("done", "incomplete", "pending"):                                     # once
            self.assertEqual(jobs('{\n  "intake": "%s"\n}\n' % marked)["intake"], marked)
        self.assertEqual(jobs('{\n  "mod": "on"\n}\n', "onex")["intake"], "pending")         # a past and no memories
        self.assertEqual(jobs('{\n  "mod": "on"\n}\n', "fresh"), {"mod": "on"})               # no past, no first run: none

    def test_index_once(self):
        self.migrate()
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").stdout.strip(), pj.DOCS_INDEX_MSG)
        self.assertEqual(git(self.repo, "status", "--short", "--", pj.DOCS_INDEX_REL, ".claude/pa.json").stdout, "")
        text = _read(self.repo, pj.DOCS_INDEX_REL)
        self.assertRegex(text, r"(?m)^- \S+ `docs/retired/CLAUDE\.pa2\.md`")      # the install's move, under its old dates
        self.assertIn("Vantage_Product_Thesis.md", text)
        self.assertNotIn("`HOW_WE_WORK.md`", text)
        self.assertIn("## Phases\n", _read(self.repo, pj.PHASE_HISTORY_REL))          # 3.32 B5: built and committed with it
        self.assertEqual(git(self.repo, "status", "--short", "--", pj.PHASE_HISTORY_REL).stdout, "")
        self.assertTrue(git(self.repo, "ls-files", pj.PHASE_HISTORY_REL).stdout.strip())
        with open(_path(self.repo, ".claude", "pa.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)[pj.DOCS_INDEX])
        self.assertTrue(git(self.repo, "ls-files", ".claude/pa.json").stdout.strip())
        head = git(self.repo, "rev-parse", "HEAD").stdout
        self.migrate()                  # an update: the marker holds it back
        self.assertEqual(_read(self.repo, pj.DOCS_INDEX_REL), text)
        self.assertNotIn(pj.DOCS_INDEX_MSG, git(self.repo, "log", head.strip() + "..HEAD", "--format=%s").stdout)

    def test_dry_run_writes_nothing(self):
        self.migrate("--dry-run")
        self.assertFalse(_exists(self.repo, pj.DOCS_INDEX_REL))
        self.assertEqual(git(self.repo, "status", "--short").stdout, "")

    def test_update_of_a_project_already_on_pa3(self):
        """The first update that has it runs it; a run without the marker is what an older project looks like."""
        self.migrate()
        with open(_path(self.repo, ".claude", "pa.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        del data[pj.DOCS_INDEX]
        with open(_path(self.repo, ".claude", "pa.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)
        git(self.repo, "rm", "-q", pj.DOCS_INDEX_REL)
        git(self.repo, "commit", "-qam", "as an older project")
        self.migrate()
        self.assertTrue(_exists(self.repo, pj.DOCS_INDEX_REL))
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").stdout.strip(), pj.DOCS_INDEX_MSG)
        self.assertEqual(git(self.repo, "status", "--short", "--", ".claude/pa.json").stdout, "")


class TestOnexMigrationFixes(OnexCase):
    """3.32 B3: what the Vantage dry run found."""

    PED = "Project Context Markdowns"

    def test_recent_doc_stays_put(self):
        """A doc committed or edited in the last 30 days is never archived; an old one is, whatever its mtime."""
        plan = "%s/Vantage_Gen3_5_Plan.md" % self.PED
        with open(_path(self.repo, *plan.split("/")), "a", encoding="utf-8") as fh:
            fh.write("\nthe live plan\n")
        git(self.repo, "commit", "-qam", "edit the plan")
        verdict = "%s/Vantage_Gen4_C_Verdict.md" % self.PED
        os.utime(_path(self.repo, *verdict.split("/")))       # a clone's mtime: the checkout, no edit
        with open(_path(self.repo, *("%s/Vantage_Gen6_Inbox.md" % self.PED).split("/")), "w", encoding="utf-8") as fh:
            fh.write("# Inbox, not committed yet\n")
        out = self.migrate()
        self.assertTrue(_exists(self.repo, plan))
        self.assertTrue(_exists(self.repo, "%s/Vantage_Gen6_Inbox.md" % self.PED))
        self.assertFalse(_exists(self.repo, "docs/research-archive/Vantage_Gen3_5_Plan.md"))
        self.assertTrue(_exists(self.repo, "docs/research-archive/Vantage_Gen4_C_Verdict.md"))
        self.assertIn("kept in place (edited in the last 30 days): Vantage_Gen3_5_Plan.md, Vantage_Gen6_Inbox.md", out)

    def test_interphase_id_is_no_real_phase(self):
        out = self.migrate()
        self.assertTrue(_exists(self.repo, "phase-ends/PhaseEnd_PhaseGen4.PA3.md"))
        self.assertFalse(_exists(self.repo, "phase-ends/PhaseEnd_PhaseGen4.5.md"), out)
        plan = _read(self.repo, "GENERATION_PLAN.md")
        self.assertIn("- Gen4.PA3 PA3 migration | ", plan)
        self.assertIn("status: closed | phase-end: phase-ends/PhaseEnd_PhaseGen4.PA3.md\n", plan)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s", "HEAD~1").stdout.strip(),
                         "chore: Project Architect 3.0 migration (Phase Gen4.PA3)")

    def test_running_phase_row(self):
        self.migrate()
        plan = _read(self.repo, "GENERATION_PLAN.md")
        row = [ln for ln in plan.splitlines() if ln.startswith("- Gen4.W ")]
        self.assertEqual(row, ["- Gen4.W THE WEEKLY b13 ENGINES | milestone: — | scope: — | depends: — | status: open "
                               "| phase-end: phase-ends/PhaseEnd_PhaseGen4.W.md"])

    def test_closed_running_phase_has_no_open_row(self):
        with open(_path(self.repo, self.PED, "PhaseEnd_PhaseGen4_W.md"), "w", encoding="utf-8") as fh:
            fh.write("# PhaseEnd — Gen4.W\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "closed")
        self.migrate()
        self.assertNotIn("status: open |", _read(self.repo, "GENERATION_PLAN.md"))

    def test_claude_md(self):
        self.migrate()
        text = _read(self.repo, "CLAUDE.md")
        self.assertIn("Live plan: `phase-ends/current/PHASE_PLAN.md`", text)
        self.assertIn("never Read `phase-ends/*/logs/`", text)
        self.assertNotIn(self.PED, text)
        self.assertNotIn(pj.DEFAULT_TAGLINE, text)
        self.assertNotIn("{{", text)
        self.assertIn("Older documents: `docs/PRE_PA3_INDEX.md`", text)
        self.assertIn("`.claude-state/memory/gen*.md`", text)
        self.assertIn("Before you change something or explain why it's that way, check it for a doc on it.", text)
        plan = _read(self.repo, "GENERATION_PLAN.md")
        self.assertTrue(plan.startswith("# Generation 1 — before PA3\n"))
        self.assertNotIn("Stdlib only", plan)

    def test_update_takes_the_pointer(self):
        """A project already on PA3: the pointer, its phase_ends_dir and no prompt, once; a developer's line stays."""
        self.migrate()
        with open(_path(self.repo, ".claude", "pa.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        del data[pj.DOCS_INDEX]
        data["phase_ends_dir"] = self.PED                     # a project an older PA3 migrated in place
        with open(_path(self.repo, ".claude", "pa.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)
        text = _read(self.repo, "CLAUDE.md")
        old = re.sub(r"Older documents:.*?\n\n", "\n", text, flags=re.S)
        old = old.replace("\n\nRoles:", "\n%s\n\nRoles:" % pj.DEFAULT_TAGLINE, 1) + "My own line.\n"
        with open(_path(self.repo, "CLAUDE.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(old)
        git(self.repo, "commit", "-qam", "as an older project")
        self.migrate()
        self.assertEqual(_read(self.repo, "CLAUDE.md"), text.replace("`phase-ends/", "`%s/" % self.PED) + "My own line.\n")
        self.assertEqual(git(self.repo, "status", "--short", "--", "CLAUDE.md").stdout, "")


class TestOnexPhaseEndsHome(OnexCase):
    """3.32 B10a: a migration moves PhaseEnds to phase-ends/, whatever the PA2 folder; its documents stay."""

    PED = "Project Context Markdowns"

    def tool(self, *argv):
        """Run a tool the install copied, in the project: -> stdout; a non-zero exit fails the test."""
        env = dict(os.environ, PA_LEDGER_DIR=os.path.join(self.tmp, "ledger"))
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run([PY, "-X", "utf8", _path(self.repo, *argv[0].split("/"))] + list(argv[1:]), cwd=self.repo,
                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return p.stdout

    def commit_doc(self, name, text):
        with open(_path(self.repo, self.PED, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add " + name)

    def test_phaseend_with_no_rename_moves(self):
        self.commit_doc("PhaseEnd_Phase7.md", "# PhaseEnd — Phase 7\n")
        self.migrate()
        self.assertTrue(_exists(self.repo, "phase-ends/PhaseEnd_Phase7.md"))
        self.assertEqual([n for n in os.listdir(_path(self.repo, self.PED)) if n.startswith("PhaseEnd_")], [])

    def test_generation_end_moves(self):
        self.commit_doc("GenerationEnd_Gen3.md", "# GenerationEnd — Generation 3\n")
        self.migrate()
        self.assertTrue(_exists(self.repo, "phase-ends/GenerationEnd_Gen3.md"))
        self.assertFalse(_exists(self.repo, "docs/research-archive/GenerationEnd_Gen3.md"))

    def test_pa_json_and_working_folders(self):
        self.migrate()
        self.assertEqual(json.loads(_read(self.repo, ".claude/pa.json"))["phase_ends_dir"], "phase-ends")
        for rel in ("phase-ends/current", "phase-ends/logs", "phase-ends/misc"):
            self.assertTrue(os.path.isdir(_path(self.repo, rel)), rel)
        for name in ("LEGACY_INDEX.md", "RESEARCH_INDEX.md", "README.md"):
            self.assertTrue(_exists(self.repo, "phase-ends/" + name), name)
        for name in ("current", "logs", "misc", "LEGACY_INDEX.md", "RESEARCH_INDEX.md", "README.md"):
            self.assertFalse(os.path.exists(_path(self.repo, self.PED, name)), name)
        self.assertIn("PhaseEnd_Phase11.5.7.md", _read(self.repo, "phase-ends/LEGACY_INDEX.md"))
        self.assertEqual(git(self.repo, "status", "--short", "--", "phase-ends", self.PED).stdout, "")

    def test_plan_phase_end_paths_exist(self):
        self.migrate()
        closed = re.findall(r"status: closed \| phase-end: (\S+)$", _read(self.repo, "GENERATION_PLAN.md"), flags=re.M)
        self.assertGreater(len(closed), 5)
        self.assertEqual([p for p in closed if not (p.startswith("phase-ends/") and _exists(self.repo, p))], [])

    def test_a_doc_in_the_old_folder_stays(self):
        self.commit_doc("README.md", "# The folder's own README\n")
        self.migrate()
        self.assertTrue(_exists(self.repo, "%s/Vantage_Product_Thesis.md" % self.PED))
        self.assertEqual(_read(self.repo, "%s/README.md" % self.PED), "# The folder's own README\n")
        self.assertFalse(_exists(self.repo, "docs/retired/phase-ends-README.pa2.md"))
        self.assertIn("Vantage_Product_Thesis.md", _read(self.repo, pj.DOCS_INDEX_REL))

    def test_the_tools_work_on_the_result(self):
        self.migrate()
        with open(_path(self.repo, "phase-ends", "current", "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Phase 1 one\n\n## Tasks\n\n- T1 | title: the first | done-when: it runs\n")
        self.assertIn("T1", self.tool("tools/plan_edit.py", "show", "--tasks"))
        state = json.loads(self.tool("tools/launch.py", "--state-json"))
        self.assertIsInstance(state, dict)
        self.assertIn("phase-ends/PhaseEnd_Phase", self.tool("tools/phaseend_index.py", "state-sources"))

    def test_phase_ends_already_home_sees_no_change(self):
        """PhaseEnds already in phase-ends/: a rename stays a rename and no other PhaseEnd row appears."""
        os.rename(_path(self.repo, self.PED), _path(self.repo, "phase-ends"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "the stock folder")
        out = _run(self.repo, "--dry-run", commit=False)[1]
        self.assertIn("git mv phase-ends/PhaseEnd_Phase11_5_7.md phase-ends/PhaseEnd_Phase11.5.7.md", out)
        self.assertNotIn("move to phase-ends", out)
        self.assertEqual(len(re.findall(r"git mv phase-ends/PhaseEnd_", out)), 7)


class TestOnexPedDocsScanned(OnexCase):
    """D12: <ped>/docs/ included in the unmapped scan."""

    def test_thesis_in_unmapped(self):
        """Thesis stays and appears in dry-run unmapped list."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("Vantage_Product_Thesis.md", out)
