"""Migration tests for the overlay-kit layout (design B J.3, fixture overlay).

Every test runs against a git-initialised copy of tests/fixtures/overlay/
via ``fixture_copy("overlay")``, with a throw-away ``--pa3-dir``.

    cd project-architect-3.0 && python -m unittest tests.test_install_overlay -v
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa.install import build_parser, main as install_main  # noqa: E402
from pa.install import detect as detect_mod                # noqa: E402
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


class OverlayCase(unittest.TestCase):
    """Base: a git-inited copy of the overlay fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-ovl-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = fixture_copy("overlay", self.tmp)

    def migrate(self, *extra, commit=True):
        rc, out = _run(self.repo, *extra, commit=commit)
        self.assertEqual(rc, 0, out)
        return out


class TestOverlayRules(OverlayCase):
    """Rules from DIGEST bullets + kit registry templates."""

    def test_digest_rules_create_files(self):
        self.migrate(commit=False)
        # DIGEST has R1, R2, R3, R101, R102, R103
        for rid in ("R1", "R2", "R3", "R101", "R102", "R103"):
            self.assertTrue(_exists(self.repo, "rules/%s.md" % rid),
                            "missing rules/%s.md" % rid)

    def test_registry_e_rules_create_files(self):
        self.migrate(commit=False)
        # registry-E has G1, G2
        for rid in ("G1", "G2"):
            self.assertTrue(_exists(self.repo, "rules/%s.md" % rid),
                            "missing rules/%s.md" % rid)

    def test_total_rule_files(self):
        """DIGEST (6) + registry-E (2) = 8 rule files."""
        self.migrate(commit=False)
        rules_dir = _path(self.repo, "rules")
        rule_files = [f for f in os.listdir(rules_dir)
                      if f.endswith(".md") and f != "INDEX.md"
                      and not f.endswith(".preamble.md")]
        self.assertEqual(len(rule_files), 8)

    def test_index_has_source_marks(self):
        """Kit-sourced rules carry ``source: <kit>`` in the index line."""
        self.migrate(commit=False)
        index = _read(self.repo, "rules/INDEX.md")
        self.assertIn("source: decomp-architect", index)

    def test_digest_rules_no_source_mark(self):
        """DIGEST-sourced rules do NOT carry a source mark."""
        self.migrate(commit=False)
        index = _read(self.repo, "rules/INDEX.md")
        # find lines for R1 - should not have source:
        for line in index.splitlines():
            if "R1 |" in line and "source:" not in line:
                return
        self.fail("expected R1 line without source: mark")


class TestOverlayFrozenDigest(OverlayCase):
    """DIGEST.md gets a frozen header once."""

    def test_frozen_header_prepended(self):
        self.migrate(commit=False)
        text = _read(self.repo, "phase-ends/DIGEST.md")
        self.assertTrue(text.startswith("<!-- frozen at the PA3 migration"))

    def test_frozen_header_once(self):
        """Second run SKIPs the frozen header (already present)."""
        self.migrate(commit=False)
        _rc, out = _run(self.repo, commit=False)
        self.assertEqual(_rc, 0, out)
        text = _read(self.repo, "phase-ends/DIGEST.md")
        count = text.count("<!-- frozen at the PA3 migration")
        self.assertEqual(count, 1, "frozen header should appear exactly once")


class TestOverlayOps(OverlayCase):
    """Ops topics = SETUP ## sections + kit ops sections."""

    def test_ops_files_from_setup(self):
        """SETUP.md has 3 ## sections -> 3 ops files."""
        self.migrate(commit=False)
        ops_dir = _path(self.repo, "docs", "ops")
        self.assertTrue(os.path.isdir(ops_dir))
        ops_files = [f for f in os.listdir(ops_dir)
                     if f.endswith(".md") and f != "INDEX.md"
                     and ".preamble." not in f
                     and "decomp" not in f.lower()]
        self.assertEqual(len(ops_files), 3)

    def test_kit_ops_section_present(self):
        """Kit ops-setup.decomp.md lands in docs/ops/."""
        self.migrate(commit=False)
        ops_dir = _path(self.repo, "docs", "ops")
        decomp_ops = [f for f in os.listdir(ops_dir)
                      if "decomp" in f.lower() and f.endswith(".md")]
        self.assertTrue(len(decomp_ops) >= 1,
                        "expected kit ops section in docs/ops/")


class TestOverlayCookbook(OverlayCase):
    """Cookbook split with index lines from cookbook-index.md."""

    def test_cookbook_files_equal_heading_count(self):
        """4 ## headings in the cookbook -> 4 cookbook files."""
        self.migrate(commit=False)
        cb_dir = _path(self.repo, "cookbook")
        cb_files = [f for f in os.listdir(cb_dir)
                    if f.endswith(".md") and f != "INDEX.md"
                    and ".preamble." not in f]
        self.assertEqual(len(cb_files), 4)

    def test_cookbook_index_has_lines_from_cookbook_index_md(self):
        """Index rows use the cookbook-index.md bullets."""
        self.migrate(commit=False)
        index = _read(self.repo, "cookbook/INDEX.md")
        # the cookbook-index.md has section references like §1, §3
        self.assertIn("§", index)


class TestOverlayCookbookIndexRetire(OverlayCase):
    """D8: cookbook-index.md retired after cookbook split."""

    def test_cookbook_index_retired(self):
        self.migrate(commit=False)
        self.assertFalse(_exists(self.repo, "docs/cookbook-index.md"))
        self.assertTrue(_exists(self.repo, "docs/retired/cookbook-index.md"))

    def test_cookbook_index_retire_dry_run(self):
        """Dry run shows the cookbook-index retire row."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("retire", out)
        self.assertIn("cookbook-index.md", out)


class TestOverlaySettings(OverlayCase):
    """Ghidra hooks stay intact and first in settings.json."""

    def test_ghidra_hooks_intact_and_first(self):
        self.migrate(commit=False)
        text = _read(self.repo, ".claude/settings.json")
        settings = json.loads(text)
        hooks = settings.get("hooks", {})
        # SessionStart should exist with the Ghidra hook
        ss = hooks.get("SessionStart", [])
        self.assertTrue(len(ss) >= 1, "expected SessionStart hooks")
        first_group = ss[0]
        first_hook_cmd = first_group["hooks"][0].get("command", "")
        self.assertIn("ghidra", first_hook_cmd.lower(),
                      "Ghidra hook should be first")


class TestOverlayCollision(OverlayCase):
    """A kit agent that collides with a PA3 agent -> FAIL."""

    def test_colliding_agent_fails_and_no_file(self):
        """Add a colliding agent to .claude/agents/, run -> FAIL."""
        # create a project agent with a name that collides with PA3's pa-session.md
        agents_dir = _path(self.repo, ".claude", "agents")
        os.makedirs(agents_dir, exist_ok=True)
        with open(os.path.join(agents_dir, "pa-session.md"), "w") as fh:
            fh.write("# colliding agent\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add colliding agent")
        rc, out = _run(self.repo, commit=False)
        self.assertNotEqual(rc, 0, "expected FAIL on collision")
        self.assertIn("FAIL", out)
        # no migration files should have been written
        self.assertFalse(_exists(self.repo, "rules/R1.md"),
                         "rules should not be written on collision")


class TestOverlayIdempotent(OverlayCase):
    """Second run is all SKIP."""

    def test_second_run_all_skip(self):
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)


class TestOverlayNoRm(OverlayCase):
    """No ` rm ` in any run's output."""

    def test_no_rm_in_output(self):
        out1 = self.migrate(commit=False)
        _rc2, out2 = _run(self.repo, commit=False)
        for out in (out1, out2):
            self.assertNotIn(" rm ", out, "found ' rm ' in output")


class TestOverlayNoTrailer(OverlayCase):
    """No AI trailer in commit."""

    def test_no_trailer(self):
        self.migrate(commit=True)
        r = git(self.repo, "log", "-1", "--format=%B")
        msg = r.stdout.strip()
        self.assertTrue(msg.startswith("chore:"), msg)
        self.assertNotIn("Co-authored-by", msg)
        self.assertNotIn("Generated by", msg)


class TestOverlayDetectPa3(OverlayCase):
    """After migration, detect says pa3."""

    def test_detect_pa3_afterwards(self):
        self.migrate(commit=False)
        self.assertEqual(detect_mod.detect(self.repo), "pa3")


class TestOverlayProjectArchitect(OverlayCase):
    """The methodology document lands at docs/project-architect.md (T12.c2)."""

    def test_project_architect_placed(self):
        self.migrate(commit=False)
        self.assertIn("## Migration", _read(self.repo, "docs/project-architect.md"))


class TestOverlayReadme(OverlayCase):
    """The 2.0 phase-ends README is retired and replaced by the PA3 template."""

    def test_readme_equals_template(self):
        self.migrate(commit=False)
        readme = _read(self.repo, "phase-ends/README.md")
        self.assertIn("## Layout", readme)
        self.assertIn("TASK_INDEX.md", readme)

    def test_retired_readme_holds_2_0_text(self):
        self.migrate(commit=False)
        retired = _read(self.repo, "docs/retired/phase-ends-README.pa2.md")
        self.assertIn("DIGEST.md", retired)

    def test_retired_readme_git_follow(self):
        self.migrate(commit=True)
        r = git(self.repo, "log", "--follow", "--oneline", "--",
                "docs/retired/phase-ends-README.pa2.md")
        self.assertIn("fixture", r.stdout, r.stderr)

    def test_second_run_readme_rows_skip(self):
        """Second run: the readme rows are SKIP."""
        self.migrate(commit=False)
        rc, out = _run(self.repo, commit=False)
        self.assertEqual(rc, 0, out)
        for line in out.splitlines():
            m = _STEP_RE.match(line)
            if m and m.group(1) not in ("P1",):
                self.assertEqual(m.group(2), "SKIP",
                                 "expected SKIP: %s" % line)


class TestOverlayDryRun(OverlayCase):
    """Dry run writes nothing, names registry-E and decomp-architect, no rm."""

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

    def test_dry_run_names_decomp_and_registry(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("decomp-architect", out)
        self.assertIn("registry-E", out)

    def test_dry_run_no_rm(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertNotIn(" rm ", out)

    def test_dry_run_kept_line(self):
        """kept: line names PhaseEnd files."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("kept:", out)
        self.assertIn("PhaseEnd file(s) in phase-ends/ (LEGACY_INDEX.md)", out)

    def test_dry_run_readme_rows(self):
        """Dry run shows the two README rows."""
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("retire phase-ends/README.md", out)
        self.assertIn("generate phase-ends/README.md", out)


class TestOverlayIndexLines(OverlayCase):
    """T4.c1: index lines count once in the manifest and print once per dest."""

    def test_counts_are_distinct_paths(self):
        out = self.migrate(commit=False)
        m = re.search(r"(\d+) created, (\d+) updated", out)
        self.assertIsNotNone(m, out)
        upd = [l.strip()[2:] for l in out.splitlines() if l.strip().startswith("~ ")]
        self.assertEqual(len(upd), len(set(upd)), upd)
        self.assertEqual(int(m.group(2)), len(upd))
        # both indexes are created by this run, then appended to: created, not updated
        self.assertNotIn("rules/INDEX.md", upd)
        self.assertNotIn("cookbook/INDEX.md", upd)

    def test_dry_run_prints_each_index_once(self):
        rc, out = _run(self.repo, "--dry-run", commit=False)
        self.assertEqual(rc, 0, out)
        lines = out.splitlines()
        for dst, n in (("rules/INDEX.md", 8), ("cookbook/INDEX.md", 4)):
            hits = [l for l in lines if "generate %s" % dst in l]
            self.assertEqual(len(hits), 1, hits)
            self.assertIn("(%d lines)" % n, hits[0])

    def test_execute_steps_each_index_once(self):
        out = self.migrate(commit=False)
        hits = [l for l in out.splitlines()
                if _STEP_RE.match(l) and "rules/INDEX.md" in l]
        self.assertEqual(len(hits), 1, hits)

    def test_fresh_split_index_counted_created(self):
        """T4.c3: a split index absent before the split is created, not updated."""
        self.assertFalse(_exists(self.repo, "docs/ops/INDEX.md"))
        out = self.migrate(commit=False)
        self.assertTrue(_exists(self.repo, "docs/ops/INDEX.md"))
        upd = [l.strip()[2:] for l in out.splitlines() if l.strip().startswith("~ ")]
        self.assertNotIn("docs/ops/INDEX.md", upd)
        self.assertNotIn("cookbook/INDEX.md", upd)
        m = re.search(r"(\d+) created, (\d+) updated", out)
        self.assertIsNotNone(m, out)
        self.assertEqual(int(m.group(2)), len(upd))
