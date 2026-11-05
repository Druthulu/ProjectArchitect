"""Unit tests for tools/_genplan.py and its callers (T6.c1).

    cd project-architect-3.0 && python -m unittest tests.test_genplan -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")
REPO_ROOT = os.path.dirname(PKG)
# 3.11 T1: the package's template (the repo root's is its installed copy; the public tree has only this one)
TEMPLATE = os.path.join(PKG, "templates", "PHASE_PLAN.template.md")
REPO_GEN_PLAN = os.path.join(REPO_ROOT, "GENERATION_PLAN.md")
PLAN_FIXTURE = os.path.join(HERE, "fixtures", "PHASE_PLAN.example.md")

sys.path.insert(0, TOOLS)
import _genplan                                                 # noqa: E402

FIXTURE_GEN_PLAN = """# Generation 3 — Token-economy rebuild

Goal: every phase runs through the router with an expert per task.

## Phases
- 3.6 The savings credit | milestone: the credit lands | scope: ledger | depends: 3.5 \
| status: closed | phase-end: phase-ends/PhaseEnd_Phase3.6.md
- 3.7 The install clone | milestone: the trial install runs | scope: install | depends: 3.6 \
| status: open | phase-end: phase-ends/PhaseEnd_Phase3.7.md

## Ordering rationale
Credit before install.

## Standing constraints
Stdlib only.

## Changes
- 2026-09-12 planner: generation opened — two phases
"""


def write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


class FieldsTest(unittest.TestCase):
    """`_genplan.fields` on this repo's own GENERATION_PLAN.md and on a fixture."""

    @unittest.skipUnless(os.path.exists(REPO_GEN_PLAN), "ProjectArchitect's own plan; not in the public tree")
    def test_fields_on_this_repos_generation_plan(self):
        text = read(REPO_GEN_PLAN)
        lines = _genplan.phase_lines(text)
        # 3.23: the PA 3.2 plan replaced Gen 3's lines (3.7 is gone); 3.22 is closed and stable.
        hit = [l for l in lines if _genplan.fields(l)["id"] == "3.22"]
        self.assertEqual(len(hit), 1)
        f = _genplan.fields(hit[0])
        self.assertEqual(f["id"], "3.22")
        self.assertEqual(f["status"], "closed")
        self.assertEqual(f["depends"], "3.21")
        self.assertTrue(f["name"])
        self.assertTrue(f["milestone"])
        self.assertEqual(f["phase_end"], "docs/pa3-build/3.2/3.22-partner.md")

    def test_fields_on_a_fixture_line(self):
        f = _genplan.fields(
            "- 3.7 The install clone | milestone: the trial install runs "
            "| scope: install | depends: 3.6 | status: open "
            "| phase-end: phase-ends/PhaseEnd_Phase3.7.md")
        self.assertEqual(f, {
            "id": "3.7", "name": "The install clone",
            "milestone": "the trial install runs", "scope": "install",
            "depends": "3.6", "status": "open",
            "phase_end": "phase-ends/PhaseEnd_Phase3.7.md"})

    def test_first_open_on_a_fixture(self):
        self.assertEqual(_genplan.first_open(FIXTURE_GEN_PLAN), "3.7")


class FamilyIdTest(unittest.TestCase):
    """3.32 B5b: a project's own phase ids (`Gen5.x`) are phases; a task id is not."""

    TEXT = ("- Gen5.PA3 PA3 migration | status: closed\n"
            "- Gen5.4.6 The next one | status: open\n"
            "- Alpha.PA3 A named family | status: closed\n"
            "- 5.PA3 A numeric family | status: closed\n"
            "- T3.1 | a task, not a phase | status: open\n")

    def test_phase_lines_and_first_open(self):
        self.assertEqual([_genplan.fields(l)["id"] for l in _genplan.phase_lines(self.TEXT)],
                         ["Gen5.PA3", "Gen5.4.6", "Alpha.PA3", "5.PA3"])
        self.assertEqual(_genplan.first_open(self.TEXT), "Gen5.4.6")

    def test_other_open_phases_and_the_plan_header(self):
        import phaseend_index as PI
        tmp = tempfile.mkdtemp(prefix="pa3-genplan-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        write(os.path.join(tmp, "GENERATION_PLAN.md"), "## Phases\n" + self.TEXT)
        self.assertEqual(PI._other_open_phases(tmp, "Gen5.PA3"), ["Gen5.4.6"])
        self.assertEqual(PI._other_open_phases(tmp, "Gen5.4.6"), [])
        cur = os.path.join(tmp, "phase-ends", "current")
        write(os.path.join(cur, "PHASE_PLAN.md"),
              "# Phase Gen5.4.6 — x        (implements GENERATION_PLAN.md phase Gen5.4.6)\n")
        self.assertEqual(PI.plan_bits(tmp)["gen"], "Gen5.4.6")
        write(os.path.join(cur, "PHASE_PLAN.md"), "# Phase 3.7 — x        (implements GENERATION_PLAN.md phase 3.7)\n")
        self.assertEqual(PI.plan_bits(tmp)["gen"], "3.7")

    def test_launch_gen_phases_sees_them(self):
        import launch
        tmp = tempfile.mkdtemp(prefix="pa3-genplan-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        write(os.path.join(tmp, "GENERATION_PLAN.md"), "## Phases\n" + self.TEXT)
        self.assertEqual([p[0] for p in launch.gen_phases(tmp)[:2]], ["Gen5.PA3", "Gen5.4.6"])

    def test_the_plan_header_readers_see_them(self):
        """3.32 B8a: task_log, status and launch read a `Gen5.x` header's phase; a numeric one as before."""
        import launch
        import status
        import task_log
        tmp = tempfile.mkdtemp(prefix="pa3-genplan-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        plan = os.path.join(tmp, "phase-ends", "current", "PHASE_PLAN.md")
        for phase, gen in (("Gen5.4.6", "Gen5"), ("3.7", "3")):
            write(plan, "# Phase %s — x        (implements GENERATION_PLAN.md phase %s)\nApproved: 2026-10-31\n" % (phase, phase))
            self.assertEqual(task_log._plan_header(tmp), (phase, "2026-10-31"))
            self.assertEqual(status.plan_facts(tmp)[:2], (phase, phase))
            self.assertEqual(launch.generation_id(tmp, {}), gen)

    def test_gen_close_on_a_family_id(self):
        tmp = tempfile.mkdtemp(prefix="pa3-genplan-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        write(os.path.join(tmp, "GENERATION_PLAN.md"), "## Phases\n" + self.TEXT + "\n## Changes\n")
        res = subprocess.run([sys.executable, os.path.join(TOOLS, "plan_edit.py"), "gen-close", "Gen5.4.6", "--by", "router"],
                             cwd=tmp, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(read(os.path.join(tmp, "GENERATION_PLAN.md")), r"- Gen5\.4\.6 .*status: closed")


class ToolCase(unittest.TestCase):
    """A throw-away project tree for the plan_edit.py / research_add.py CLIs."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-genplan-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), FIXTURE_GEN_PLAN)
        os.makedirs(self.cur, exist_ok=True)
        os.makedirs(os.path.join(self.root, "templates"), exist_ok=True)
        shutil.copyfile(TEMPLATE, os.path.join(self.root, "templates",
                                                "PHASE_PLAN.template.md"))

    def tool(self, script, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, script)]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def ok(self, p, *needles):
        self.assertEqual(p.returncode, 0, p.out)
        for n in needles:
            self.assertIn(n, p.out)
        return p.out


class ShowGenPhaseTest(ToolCase):

    def setUp(self):
        super().setUp()
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))

    def test_show_gen_phase_with_id_prints_seven_lines(self):
        out = self.ok(self.tool("plan_edit.py", "show", "--gen-phase", "3.7"),
                      "milestone:")
        lines = out.rstrip("\n").split("\n")
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines[0], "id: 3.7")
        self.assertTrue(lines[2].startswith("milestone:"))
        self.assertEqual(lines[6], "phase-end: phase-ends/PhaseEnd_Phase3.7.md")

    def test_show_gen_phase_without_id_defaults_to_first_open(self):
        out = self.ok(self.tool("plan_edit.py", "show", "--gen-phase"))
        self.assertEqual(out.split("\n")[0], "id: 3.7")


class GrammarTest(ToolCase):

    def setUp(self):
        super().setUp()
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))

    def test_grammar_has_the_loose_line_and_stays_small(self):
        # 3.25 T4: the loose line; status and agent are optional positional columns
        out = self.ok(self.tool("plan_edit.py", "grammar"), "| done-when:", "positional")
        self.assertLess(len(out), 1500)


class GenCloseOpenTest(ToolCase):
    """Unchanged from the existing tests/test_tools.py coverage."""

    def setUp(self):
        super().setUp()
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))

    def test_gen_close_and_gen_open(self):
        self.ok(self.tool("plan_edit.py", "gen-close", "3.7", "--by", "router"),
                "3.7: status -> closed")
        text = read(os.path.join(self.root, "GENERATION_PLAN.md"))
        self.assertIn("- 3.7 The install clone", text)
        self.assertRegex(text, r"- 3\.7 .*status: closed")
        self.assertIn("phase 3.7 status -> closed", text)
        self.ok(self.tool("plan_edit.py", "gen-open", "3.7"))
        self.assertRegex(read(os.path.join(self.root, "GENERATION_PLAN.md")),
                         r"- 3\.7 .*status: open")


class ResearchAddPhaseFallbackTest(ToolCase):
    """No current/PHASE_PLAN.md; the fallback is the first open generation phase."""

    def test_research_new_allocates_from_the_open_phase(self):
        p = self.tool("research_add.py", "new", "--task", "T1", "--title", "why",
                      "--agent", "retriever-code")
        self.assertEqual(p.returncode, 0, p.out)
        rid = p.out.split("\n")[0]
        self.assertEqual(rid, "R3.7-001")


if __name__ == "__main__":
    unittest.main()
