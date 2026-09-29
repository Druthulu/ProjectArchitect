"""Unit tests for project-architect-3.0/tools/ (M2.c2).

Every tool is exercised through its CLI, in a throw-away project tree that has
`.claude/pa.json`, `phase-ends/current/` and the PHASE_PLAN fixture.

    cd project-architect-3.0 && python -m unittest tests.test_tools -v
"""

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
PLAN_FIXTURE = os.path.join(FIXTURES, "PHASE_PLAN.example.md")
BASH = shutil.which("bash")

GENERATION_PLAN = """# Generation 3 — Token-economy rebuild

Goal: every phase runs through the router with an expert per task.

## Phases
- 3.4 Ground-Texture Expansion | milestone: gate green | scope: masks | depends: — \
| status: open | phase-end: phase-ends/PhaseEnd_Phase24.md
- 3.5 Generation close | milestone: readme rewritten | scope: docs | depends: 3.4 \
| status: open | phase-end: phase-ends/PhaseEnd_Phase25.md

## Ordering rationale
Masks before docs.

## Standing constraints
Stdlib only.

## Changes
- 2026-09-12 planner: generation opened — two phases
"""

SKILL_MD = """---
name: project-architect
description: Standing rules and house style for PA3 agents.
---

## 1 Roles
One task per fresh context.

## 11 Project headlines
- M4 — never push; the developer pushes
"""

HOW_WE_WORK = """# How we work

## Project
One line.

## Skills

## Tools
"""

SUMMARY = """# {tid} — {title}
Status: {status} | expert: expert-fable | ctx-at-completion: 142k | commit: {commit} \
| coder runs: c1 opus46 done (def4567)
Done: {done}
Files: pa/textures.py
Decisions: binding: masks are built per biome, never per chunk.
Deviations: none
Findings: the seam class only shows at biome borders.
Gotchas: generalizable: clear the mask cache between biomes.
Research: R24-001 (mask seams)
Rule candidate: masks are per biome, never per chunk
Next task needs: the blend weights from T2
Recommended: —
{verified}Full log: phase-ends/current/logs/{tid}.md
Tags: masks,textures
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


def summary(tid, title="a task", status="done", commit="abc1234",
            done="the thing works", verified=True):
    return SUMMARY.format(
        tid=tid, title=title, status=status, commit=commit, done=done,
        verified=("Verified: python -m unittest tests.test_masks -> OK (12 tests)\n"
                  if verified else ""))


class ToolCase(unittest.TestCase):
    """A temp project tree plus a `tool()` runner."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends",
            "handoff_ctx": {"expert": 350000, "coder": 300000},
            "rhythm": "autonomous"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), GENERATION_PLAN)
        write(os.path.join(self.root, "HOW_WE_WORK.md"), HOW_WE_WORK)
        write(os.path.join(self.root, ".claude", "skills", "project-architect", "SKILL.md"),
              SKILL_MD)
        write(os.path.join(self.root, "cookbook", "INDEX.md"),
              "# Cookbook -- one line per entry\n"
              "# id | title | tags | date | phase/task | origin\n"
              "C0187 | mask cache reset | masks | 2026-09-12 | 24/T1 | origin: T1\n"
              "C0188 | older note | misc | 2026-01-02 | 12/T4 | origin: T4\n")
        write(os.path.join(self.root, "rules", "INDEX.md"),
              "# Rules -- full texts in rules/<id>.md\n"
              "# id | headline | tags | active|superseded-by:<id>|sunset | origin\n"
              "M4 | never push; the developer pushes | git | active | 2.0 seed\n")
        for sub in ("tasks", "logs", "research", "discussions"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        os.makedirs(os.path.join(self.root, ".run"), exist_ok=True)

    # -- helpers ---------------------------------------------------------- #
    @property
    def plan(self):
        return os.path.join(self.cur, "PHASE_PLAN.md")

    def tool(self, script, *args, **kw):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, script)]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env.pop("PA_PROJECT_ROOT", None)
        env.pop("CLAUDE_PID", None)       # the ambient session's sid must not stamp fixtures (T28)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.update(kw.get("env") or {})
        p = subprocess.run(cmd, cwd=kw.get("cwd") or self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def ok(self, p, *needles):
        self.assertEqual(p.returncode, 0, p.out)
        for n in needles:
            self.assertIn(n, p.out)
        return p.out

    def refused(self, p, *needles):
        self.assertEqual(p.returncode, 1, p.out)
        self.assertLessEqual(len(p.out.strip().split("\n")), 40, p.out)
        for n in needles:
            self.assertIn(n, p.out)
        return p.out

    def make_summary(self, tid, **kw):
        write(os.path.join(self.cur, "tasks", "%s.md" % tid), summary(tid, **kw))
        write(os.path.join(self.cur, "logs", "%s.md" % tid),
              "# %s full log — expert-fable — 2026-09-13\n\n## Timeline\n- ran the gate\n"
              % tid)

    def git(self, *args):
        return subprocess.run(("git",) + args, cwd=self.root,
                              capture_output=True, text=True)

    def git_init(self):
        self.git("init", "-q")
        self.git("config", "user.email", "dev@example.com")
        self.git("config", "user.name", "Dev")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        write(os.path.join(self.root, ".gitignore"), ".run/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")


# ------------------------------------------------------------------------- #
class TestHelp(ToolCase):
    SCRIPTS = ["launch.py", "plan_edit.py", "status.py", "discussion.py",
               "research_add.py", "task_log.py", "rules_add.py", "skill_add.py",
               "phaseend_index.py", "genend_index.py", "curate.py"]

    def test_every_python_tool_has_help_under_40_lines(self):
        for s in self.SCRIPTS:
            p = self.tool(s, "--help")
            self.assertEqual(p.returncode, 0, "%s --help: %s" % (s, p.out))
            self.assertLessEqual(len(p.out.rstrip().split("\n")), 40, s)
            self.assertIn("usage", p.out.lower(), s)

    @unittest.skipUnless(BASH, "bash not on PATH")
    def test_shell_tools_have_help(self):
        for s in ("commit_task.sh", "cookbook_add.sh", "run.sh"):
            p = subprocess.run([BASH, os.path.join(TOOLS, s), "--help"],
                               cwd=self.root, capture_output=True, text=True)
            out = p.stdout + p.stderr
            self.assertEqual(p.returncode, 0, "%s: %s" % (s, out))
            self.assertLessEqual(len(out.rstrip().split("\n")), 40, s)
            self.assertIn(s.split(".")[0], out, s)


# ------------------------------------------------------------------------- #
class TestPlanEdit(ToolCase):

    def test_lint_fixture_prints_ok(self):
        p = self.tool("plan_edit.py", "lint", PLAN_FIXTURE)
        self.assertEqual(p.out.strip().splitlines()[-1], "OK")  # was == "OK"; the fixture's T2 names the retired expert-fable-high (one note line, 3.9.5 T15)
        self.assertEqual(p.returncode, 0)

    def test_validate_is_an_alias_of_lint(self):
        p = self.tool("plan_edit.py", "validate", PLAN_FIXTURE)
        self.assertEqual(p.out.strip().splitlines()[-1], "OK")  # was == "OK" (3.9.5 T15: T2's note line)

    def test_parse_round_trip_touches_only_the_edited_line(self):
        before = read(self.plan).rstrip("\n").split("\n")
        self.ok(self.tool("plan_edit.py", "set-status", "T5", "queued",
                          "--by", "router", "--why", "T4 unblocked it"))
        after = read(self.plan).rstrip("\n").split("\n")
        # one line appended (the ## Changes entry), one line rewritten (T5)
        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("T5 blocked -> queued", after[-1])
        self.assertIn("T4 unblocked it", after[-1])
        changed = [(i, x, y) for i, (x, y) in
                   enumerate(zip(before, after)) if x != y]
        self.assertEqual(len(changed), 1, changed)
        _i, old, new = changed[0]
        self.assertTrue(old.startswith("- T5 |"), old)
        self.assertEqual(old.replace("blocked", "queued "), new)

    def test_next_respects_deps_promotes_and_prints_wait_for(self):
        out = self.ok(self.tool("plan_edit.py", "next"))
        self.assertTrue(out.startswith("T3.1 |"), out)
        self.assertNotIn("WAIT-FOR", out)
        self.assertIn("Reopened from T3", out)          # continuation lines
        self.make_summary("T3.1")
        self.ok(self.tool("plan_edit.py", "set-status", "T3.1", "done"))
        out = self.ok(self.tool("plan_edit.py", "next"))
        self.assertTrue(out.startswith("T4 |"), out)
        self.assertIn("WAIT-FOR: tools/run.sh --wait bake --max 3000", out)
        self.assertIn("PROMOTED: T4 queued -> next", out)
        self.assertIn("- T4 | next", read(self.plan))
        # T5 is blocked behind T4
        self.ok(self.tool("plan_edit.py", "set-status", "T4", "superseded"))
        out = self.ok(self.tool("plan_edit.py", "next"))
        self.assertIn("NONE", out)
        self.assertIn("T5", out)

    def test_set_status_done_needs_a_verified_line(self):
        p = self.refused(self.tool("plan_edit.py", "set-status", "T3.1", "done"))
        self.assertIn("no summary", p)
        self.make_summary("T3.1", verified=False)
        p = self.refused(self.tool("plan_edit.py", "set-status", "T3.1", "done"))
        self.assertIn("Verified:", p)
        self.make_summary("T3.1", verified=True)
        self.ok(self.tool("plan_edit.py", "set-status", "T3.1", "done"),
                "T3.1: next -> done")

    def test_set_status_done_on_a_scratch_plan_without_tasks_dir(self):
        scratch = os.path.join(self.root, ".run", "plan_brief.md")
        shutil.copyfile(PLAN_FIXTURE, scratch)
        self.ok(self.tool("plan_edit.py", "--file", scratch, "set-status", "T3.1", "done"),
                "T3.1: next -> done")
        # the live plan, named by --file, keeps the gate
        p = self.refused(self.tool("plan_edit.py", "--file", self.plan,
                                   "set-status", "T3.1", "done"))
        self.assertIn("no summary", p)

    def test_reopen_allocates_sub_ids_and_logs_a_change(self):
        out = self.ok(self.tool("plan_edit.py", "reopen", "T1",
                                "--title", "mask builder, biome four",
                                "--done-when", "the fourth biome builds",
                                "--by", "router"))
        self.assertIn("T1.1 created (status next", out)
        text = read(self.plan)
        self.assertIn("- T1.1 | next | expert-opus55 |", text)
        self.assertIn("title: mask builder, biome four", text)
        self.assertIn("- T1 | superseded", text)
        self.assertIn("router: reopened T1 as T1.1", text)
        lines = text.split("\n")
        self.assertEqual(lines.index("- T1 | superseded | expert-opus55     "
                                     "| coder: opus46 | effort: medium "
                                     "| title: mask builder for four biomes "
                                     "| files: pa/textures.py, pa/masks.py "
                                     "| done-when: build_mask returns a Mask for every biome "
                                     "| verify: python -m unittest tests.test_masks "
                                     "| reads: — | deps: — | est-ctx: 80k "
                                     "| review: no | wait-for: —") + 1,
                         [i for i, x in enumerate(lines)
                          if x.startswith("- T1.1 ")][0])
        # a second reopen of an already-reopened id keeps counting
        self.ok(self.tool("plan_edit.py", "reopen", "T3", "--title", "seams again",
                          "--done-when", "seams listed", "--by", "critic"))
        self.assertIn("- T3.2 | next", read(self.plan))

    def test_add_task_allocates_the_next_free_integer_id(self):
        # a retired coder is refused in one line (3.9.5 T9); the plan is untouched
        before = read(self.plan)
        p = self.tool("plan_edit.py", "add-task", "--title", "mask docs", "--done-when",
                      "done", "--coder", "sonnet", "--by", "planner")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertEqual(p.out.strip().splitlines(),
                         ["refused: coder 'sonnet' is retired: use opus55 or none"])
        self.assertEqual(read(self.plan), before)
        out = self.ok(self.tool("plan_edit.py", "add-task", "--after", "T1",
                                "--title", "mask docs", "--done-when",
                                "docs/ops/masks.md exists", "--coder", "opus55",
                                "--effort", "high", "--by", "planner"))
        self.assertIn("T6 added (status queued)", out)
        text = read(self.plan).split("\n")
        idx = [i for i, x in enumerate(text) if x.startswith("- T6 |")][0]
        self.assertTrue(text[idx - 1].startswith("- T1 |"), text[idx - 1])
        self.assertIn("| expert-fable |", text[idx])
        self.assertIn("coder: opus55", text[idx])   # was --coder sonnet (retired 3.9.5 T9)
        self.assertIn("coder: sonnet", read(self.plan))   # the fixture's legacy T2 line
        self.ok(self.tool("plan_edit.py", "lint"))        # lint still accepts it
        self.ok(self.tool("plan_edit.py", "add-task", "--title", "one more",
                          "--done-when", "done", "--by", "planner"), "T7 added")

    def test_approve_stamps_and_locks_the_header(self):
        text = read(self.plan).replace(
            "Approved: 2026-09-12   Planner: claude-fable-5-1[1m]/xhigh   "
            "Plan-hash: " + "0" * 64, "Approved: —   Planner: —   Plan-hash: —")
        write(self.plan, text)
        out = self.ok(self.tool("plan_edit.py", "approve",
                                "--planner", "claude-fable-5-1[1m]/xhigh"))
        self.assertIn("Plan-hash:", out)
        stamped = read(self.plan)
        self.assertRegex(stamped, r"Approved: 20\d\d-\d\d-\d\d   Planner: "
                                  r"claude-fable-5-1\[1m\]/xhigh   Plan-hash: [0-9a-f]{64}")
        sha = read(os.path.join(self.root, ".run", "plan.sha"))
        self.assertIn("header ", sha)
        self.assertIn("file phase-ends/current/PHASE_PLAN.md", sha)
        self.refused(self.tool("plan_edit.py", "approve", "--planner", "x"),
                     "already approved")
        # an out-of-band edit above ## Tasks locks the file
        write(self.plan, stamped.replace("Milestone: every biome mask builds",
                                         "Milestone: two biome masks build"))
        self.refused(self.tool("plan_edit.py", "set-status", "T5", "queued"),
                     "header above ## Tasks changed")
        p = self.tool("plan_edit.py", "lint")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("header", p.out)

    def _set_preset(self, preset):
        pa = os.path.join(self.root, ".claude", "pa.json")
        conf = json.loads(read(pa))
        conf["preset"] = preset
        write(pa, json.dumps(conf, indent=1) + "\n")

    def test_no_hard_rung_presets_refuse_effort_high(self):
        # 3.10.6 T4: max5 and pro have no expert-fable; the fixture's T2 is effort: high
        for preset in ("max5", "pro"):
            self._set_preset(preset)
            before = read(self.plan)
            p = self.tool("plan_edit.py", "add-task", "--title", "t", "--done-when", "d",
                          "--effort", "high", "--by", "planner")
            self.assertEqual(p.returncode, 1, p.out)
            self.assertEqual(p.out.strip().splitlines(),
                             ["refused: preset %s has no hard rung: T6 carries effort: high; "
                              "mark it medium or split it" % preset])
            p = self.tool("plan_edit.py", "reopen", "T1", "--title", "t", "--done-when", "d",
                          "--effort", "high", "--by", "router")
            self.refused(p, "preset %s has no hard rung: T1.1 carries effort: high" % preset)
            self.assertEqual(read(self.plan), before)
            # a medium line is written with expert-opus55
            self.ok(self.tool("plan_edit.py", "add-task", "--title", "t", "--done-when", "d",
                              "--by", "planner"), "T6 added")
            self.assertIn("- T6 | queued | expert-opus55 |", read(self.plan))
            write(self.plan, before)
            # approve refuses over every task line before writing anything
            unstamped = before.replace(
                "Approved: 2026-09-12   Planner: claude-fable-5-1[1m]/xhigh   "
                "Plan-hash: " + "0" * 64, "Approved: —   Planner: —   Plan-hash: —")
            write(self.plan, unstamped)
            self.refused(self.tool("plan_edit.py", "approve", "--planner", "x"),
                         "preset %s has no hard rung: T2 carries effort: high" % preset)
            self.assertEqual(read(self.plan), unstamped)
            # lint reports it as an ERROR
            p = self.tool("plan_edit.py", "lint", PLAN_FIXTURE)
            self.assertEqual(p.returncode, 1, p.out)
            self.assertIn("ERROR line 27: preset %s has no hard rung: T2 carries effort: high"
                          % preset, p.out)
            write(self.plan, before)

    def test_max20_and_absent_preset_keep_effort_high(self):
        p0 = self.tool("plan_edit.py", "lint", PLAN_FIXTURE)
        self._set_preset("max20")
        p1 = self.tool("plan_edit.py", "lint", PLAN_FIXTURE)
        self.assertEqual(p1.out, p0.out)
        self.assertEqual(p1.returncode, 0, p1.out)
        self.ok(self.tool("plan_edit.py", "add-task", "--title", "t", "--done-when", "d",
                          "--effort", "high", "--by", "planner"), "T6 added")
        self.assertIn("- T6 | queued | expert-fable |", read(self.plan))

    def test_lint_catches_a_bad_status_and_a_dangling_dep(self):
        text = read(self.plan)
        text = text.replace("- T5 | blocked    |", "- T5 | stalled    |")
        text = text.replace("| deps: T3.1 |", "| deps: T9 |")
        write(self.plan, text)
        p = self.tool("plan_edit.py", "lint")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("bad status 'stalled'", p.out)
        self.assertIn("dangling dep 'T9'", p.out)
        self.assertIn("FAILED:", p.out)
        self.assertLessEqual(len(p.out.rstrip().split("\n")), 40)

    def test_lint_warns_on_a_missing_title(self):
        """3.11 T13 (developer): a task without `title:` draws a warning and still passes, so the planner
        names every task while approvals and the bench's grading stay as they were."""
        text = read(self.plan)
        line = next(l for l in text.split("\n") if l.startswith("- T5 "))
        write(self.plan, text.replace(line, re.sub(r"\| title: [^|]*", "", line)))
        p = self.tool("plan_edit.py", "lint")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("WARN  line", p.out)
        self.assertIn("T5 no 'title:'", p.out)
        self.assertIn("OK", p.out)

    def test_show_variants_stay_small(self):
        out = self.ok(self.tool("plan_edit.py", "show"))
        self.assertIn("Ground-Texture Expansion", out)
        self.assertIn("Tasks: 6", out)
        self.assertLessEqual(len(out.rstrip().split("\n")), 40)
        out = self.ok(self.tool("plan_edit.py", "show", "--tasks"))
        self.assertEqual(len(out.rstrip().split("\n")), 6)
        out = self.ok(self.tool("plan_edit.py", "show", "--task", "T2"))
        self.assertTrue(out.startswith("T2 |"))
        out = self.ok(self.tool("plan_edit.py", "show", "--section", "Interfaces"))
        self.assertIn("build_mask", out)

    def test_show_section_milestone_and_approved_print_the_header_line(self):
        out = self.ok(self.tool("plan_edit.py", "show", "--section", "Milestone"))
        self.assertEqual(out.strip(), "Milestone: every biome mask builds and the gate is green"
                         " — verified by: python -m unittest discover tests")
        out = self.ok(self.tool("plan_edit.py", "show", "--section", "Approved"))
        self.assertTrue(out.startswith("Approved: 2026-09-12"), out)
        self.refused(self.tool("plan_edit.py", "show", "--section", "Nope"), "no section")
        out = self.ok(self.tool("plan_edit.py", "show", "--help"))
        self.assertIn("Milestone", out)
        self.assertIn("Approved", out)

    def test_show_gen_phase_needs_no_phase_plan(self):
        os.remove(self.plan)
        out = self.ok(self.tool("plan_edit.py", "show", "--gen-phase", "3.5"))
        self.assertIn("id: 3.5", out)
        self.assertIn("milestone: readme rewritten", out)

    def test_gen_close_flips_the_generation_plan(self):
        self.ok(self.tool("plan_edit.py", "gen-close", "3.4", "--by", "router"),
                "3.4: status -> closed")
        text = read(os.path.join(self.root, "GENERATION_PLAN.md"))
        self.assertIn("- 3.4 Ground-Texture Expansion", text)
        self.assertRegex(text, r"- 3\.4 .*status: closed")
        self.assertRegex(text, r"- 3\.5 .*status: open")
        self.assertIn("phase 3.4 status -> closed", text)
        self.ok(self.tool("plan_edit.py", "gen-open", "3.4"))
        self.assertRegex(read(os.path.join(self.root, "GENERATION_PLAN.md")),
                         r"- 3\.4 .*status: open")

    def test_template_placeholder_header_is_not_an_approval(self):
        # a draft written from PHASE_PLAN.template.md carries `Approved: <date>`;
        # from-draft must overwrite such a plan and approve must stamp it
        lines = read(self.plan).split("\n")
        lines.insert(2, "Approved: <date>   Planner: <model/effort>   Plan-hash: <sha>")
        lines.insert(lines.index("## Tasks"), "## Triage\n- (none)\n")
        write(self.plan, "\n".join(lines))
        draft = os.path.join(self.root, ".run", "PHASE_PLAN.draft.md")
        write(draft, "\n".join(lines))
        self.ok(self.tool("plan_edit.py", "from-draft", draft))
        self.ok(self.tool("plan_edit.py", "approve", "--planner", "test/medium"))
        self.assertRegex(read(self.plan), r"(?m)^Approved: 20\d\d-\d\d-\d\d   Planner: test/medium   Plan-hash: [0-9a-f]{64}$")

    def test_lock_survives_a_crlf_checkout(self):
        # 3.3 T1: git re-checked out an approved plan with CRLF endings; the
        # header lock must compare content, not line endings
        self.ok(self.tool("plan_edit.py", "set-status", "T5", "queued", "--by", "router"))
        text = read(self.plan)          # the fixture is approved; .run/plan.sha exists now
        with open(self.plan, "wb") as fh:
            fh.write(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        self.ok(self.tool("plan_edit.py", "set-status", "T5", "next", "--by", "router"))

    def test_next_brief_prints_one_line_with_seven_fields(self):
        out = self.ok(self.tool("plan_edit.py", "next", "--brief"))
        lines = out.strip().split("\n")
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(len(lines[0]), 240)
        self.assertTrue(lines[0].startswith("T3.1 |"), lines[0])
        for field in ("coder:", "effort:", "review:", "wait-for:", "title:"):
            self.assertIn(field, lines[0])
        # promote path: set T3.1 done, next --brief promotes T4: brief, WAIT-FOR, PROMOTED
        self.make_summary("T3.1")
        self.ok(self.tool("plan_edit.py", "set-status", "T3.1", "done"))
        out2 = self.ok(self.tool("plan_edit.py", "next", "--brief"))
        brief_lines = out2.strip().split("\n")
        self.assertEqual(len(brief_lines), 3, out2)
        self.assertTrue(brief_lines[0].startswith("T4 |"), brief_lines[0])
        self.assertEqual(brief_lines[1], "WAIT-FOR: tools/run.sh --wait bake --max 3000")
        self.assertEqual(brief_lines[2], "PROMOTED: T4 queued -> next")
        # T4 is next now: the brief keeps its WAIT-FOR line, no PROMOTED
        out3 = self.ok(self.tool("plan_edit.py", "next", "--brief"))
        self.assertEqual(out3.strip().split("\n")[1:],
                         ["WAIT-FOR: tools/run.sh --wait bake --max 3000"])

    def test_next_brief_on_fixture_copy_under_240(self):
        """The milestone's wc check: cp fixture, next --brief, ≤ 240 bytes."""
        import shutil as _sh
        tmp = os.path.join(self.root, ".run", "plan_brief.md")
        _sh.copyfile(PLAN_FIXTURE, tmp)
        out = self.ok(self.tool("plan_edit.py", "--file", tmp,
                                "next", "--brief"))
        self.assertLessEqual(len(out.encode("utf-8")), 240)

    def test_launch_seed_only_two_lines(self):
        """--seed-only prints only seed path and mode."""
        p = self.tool("launch.py", "--seed-only")
        self.assertEqual(p.returncode, 0, p.out)
        lines = [l for l in p.stdout.strip().split("\n") if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(l.startswith("seed=") for l in lines))
        self.assertTrue(any(l.startswith("mode=") for l in lines))

    def test_add_task_default_coder_is_opus55(self):
        out = self.ok(self.tool("plan_edit.py", "add-task", "--title", "auto coder",
                                "--done-when", "done", "--by", "planner"), "T6 added")
        text = read(self.plan)
        self.assertIn("coder: opus55", text.split("T6")[1])

    def test_lint_accepts_legacy_coder_opus46(self):
        # the fixture already has coder: opus46 lines; lint must accept them
        self.ok(self.tool("plan_edit.py", "lint"))

    def test_grammar_names_opus55(self):
        tpl_src = os.path.join(os.path.dirname(TOOLS), "templates",
                               "PHASE_PLAN.template.md")
        tpl_dst = os.path.join(self.root, "templates", "PHASE_PLAN.template.md")
        os.makedirs(os.path.dirname(tpl_dst), exist_ok=True)
        shutil.copyfile(tpl_src, tpl_dst)
        out = self.ok(self.tool("plan_edit.py", "grammar"))
        self.assertIn("opus55", out)
        self.assertIn("expert-opus55", out)
        self.assertIn("sonnet accepted in old plans", out)
        self.assertIn("a line naming the retired expert-fable-high runs expert-fable", out)
        self.assertNotIn("only when the task line names it", out)

    def test_retired_expert_fable_high_maps_to_expert_fable(self):
        # the fixture's T2 names expert-fable-high: lint passes with one note; show prints expert-fable
        out = self.ok(self.tool("plan_edit.py", "lint"), "OK")
        self.assertIn("note: T2 names the retired expert-fable-high; it runs expert-fable", out)
        out = self.ok(self.tool("plan_edit.py", "show", "--task", "T2"))
        self.assertIn("| expert-fable |", out)
        self.assertNotIn("expert-fable-high", out)

    def test_next_brief_maps_retired_expert_fable_high(self):
        text = read(self.plan).replace("- T3.1 | next     | expert-opus55 ",
                                       "- T3.1 | next     | expert-fable-high ")
        write(self.plan, text)
        out = self.ok(self.tool("plan_edit.py", "next", "--brief"))
        self.assertIn("T3.1 | next | expert-fable |", out)

    def test_genend_assemble_one_line(self):
        """genend_index.py assemble prints one line."""
        # close phase 3.4 in GENERATION_PLAN.md so assemble can find it
        self.ok(self.tool("plan_edit.py", "gen-close", "3.4", "--by", "router"))
        self.ok(self.tool("plan_edit.py", "gen-close", "3.5", "--by", "router"))
        # create the expected PhaseEnd files
        for phase in ("3.4", "3.5"):
            pe = os.path.join(self.root, "phase-ends", "PhaseEnd_Phase%s.md" % phase)
            write(pe, "# PhaseEnd — Phase %s\n## Milestone\n## Decisions that still bind\n"
                      "## Deferred\n## Plain-English Recap\nDone.\n" % phase)
        p = self.tool("genend_index.py", "assemble", "3")
        self.assertEqual(p.returncode, 0, p.out)
        lines = [l for l in p.stdout.strip().split("\n") if l.strip()]
        self.assertEqual(len(lines), 1)


# ------------------------------------------------------------------------- #
class TestTriage(ToolCase):
    """`## Triage`: lint requires it on drafts; approve refuses a missing seed id and writes
    each decision to the discussion index (T3)."""

    def setUp(self):
        super().setUp()
        text = read(self.plan).replace(
            "Approved: 2026-09-12   Planner: claude-fable-5-1[1m]/xhigh   "
            "Plan-hash: " + "0" * 64, "Approved: —   Planner: —   Plan-hash: —")
        text = re.sub(r"^# Phase 24 ", "# Phase 3.4 ", text)
        self.draft = text
        write(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"),
              "# Discussions -- cumulative\n# phase | id | topic | date | status | path\n"
              "3.3 | D1 | card format | 2026-09-20 | deferred:3.4 | a.md\n"
              "3.3 | D2 | seed lines | 2026-09-20 | deferred | b.md\n")
        write(os.path.join(self.cur, "discussions", "INDEX.md"),
              "# Discussions -- one line per record\n# id | topic | date | status | path\n"
              "I1 | stale bullet | 2026-09-21 | deferred:3.4 | INBOX.md\n")
        write(os.path.join(self.root, "phase-ends", "PhaseEnd_Phase3.3.md"),
              "# PhaseEnd — Phase 3.3\n\n## Deferred\n- from T7: tidy the card (unconsumed)\n"
              "- from D9: not a task line\n\n## Changes\n- (none)\n")

    def _plan(self, triage):
        body = "## Triage\n" + "".join(l + "\n" for l in triage) + "\n## Tasks"
        write(self.plan, self.draft.replace("## Tasks", body, 1))

    def test_lint_requires_triage_on_a_draft_only(self):
        write(self.plan, self.draft)
        self.refused(self.tool("plan_edit.py", "lint"), "no '## Triage' section")
        self._plan(["- (none)"])
        self.ok(self.tool("plan_edit.py", "lint"), "OK")
        self._plan(["- D1: maybe"])
        self.refused(self.tool("plan_edit.py", "lint"), "triage line not")
        write(self.plan, read(PLAN_FIXTURE))                 # approved, predates ## Triage
        self.ok(self.tool("plan_edit.py", "lint"), "OK")

    def test_approve_refuses_a_missing_seed_id(self):
        self._plan(["- D1: T2 -- in scope", "- I1: drop -- stale"])
        out = self.refused(self.tool("plan_edit.py", "approve", "--planner", "t/medium"),
                           "D2", "P3.3-1")
        self.assertNotIn("D1", out)
        self.assertFalse(re.search(r"(?m)^Approved: 20", read(self.plan)))

    def test_approve_writes_decisions_to_the_index(self):
        self._plan(["- D1: T2 -- in scope", "- D2: postpone: 3.5 -- after masks",
                    "- I1: drop -- stale", "- P3.3-1: T3"])
        self.ok(self.tool("plan_edit.py", "lint"), "OK")
        self.ok(self.tool("plan_edit.py", "approve", "--planner", "t/medium"), "approved")
        cum = read(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"))
        self.assertIn("3.3 | D1 | card format | 2026-09-20 | planned:3.4/T2 | a.md\n", cum)
        self.assertIn("3.3 | D2 | seed lines | 2026-09-20 | deferred:3.5 | b.md\n", cum)
        self.assertRegex(cum, r"(?m)^3\.4 \| P3\.3-1 \| tidy the card \| 20\d\d-\d\d-\d\d \| "
                              r"planned:3\.4/T3 \| phase-ends/PhaseEnd_Phase3\.3\.md$")
        cur = read(os.path.join(self.cur, "discussions", "INDEX.md"))
        self.assertIn("I1 | stale bullet | 2026-09-21 | dropped | INBOX.md\n", cur)
        # the next seed for 3.4 lists none of the triaged items
        p = self.tool("launch.py", "--dry-run", "--mode", "planner-phase")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertNotIn("Deferred:", read(os.path.join(self.root, ".run", "seed.md")))


class TestStatus(ToolCase):

    def status(self):
        return json.loads(read(os.path.join(self.root, ".run", "status.json")))

    def test_set_read_wait_clear(self):
        self.ok(self.tool("status.py", "set", "--task", "T3.1",
                          "--agent", "expert-fable", "--coder", "opus46",
                          "--kind", "task", "--attempt", "1"),
                "status: T3.1 expert-fable task attempt 1")
        s = self.status()
        self.assertEqual(s["task"], "T3.1")
        self.assertEqual(s["expert_agent_type"], "expert-fable")
        self.assertEqual(s["coder"], "opus46")
        self.assertEqual(s["kind"], "task")
        self.assertEqual(s["phase"], "24")
        self.assertEqual(s["phase_name"], "Ground-Texture Expansion")
        self.assertEqual(s["generation"], "3.4")
        self.assertIn("seam report, per-biome", s["task_title"])
        self.assertIsNone(s["waiting"])
        self.assertTrue(s["started"].endswith("Z"))
        self.assertTrue(s["updated"].endswith("Z"))
        for banned in ("cost", "tokens", "ctx", "usd"):
            self.assertNotIn(banned, json.dumps(s).lower())
        self.ok(self.tool("status.py", "wait", "question"))
        self.assertEqual(self.status()["waiting"], "question")
        self.refused(self.tool("status.py", "wait", "nonsense"), "wait takes one of")
        self.ok(self.tool("status.py", "clear"), "cleared")
        s = self.status()
        self.assertIsNone(s["task"])
        self.assertIsNone(s["waiting"])
        self.assertEqual(s["phase"], "24")           # phase survives a clear
        self.ok(self.tool("status.py", "show"), "expert_agent_type")

    def test_inbox_consume_and_review_done(self):
        write(os.path.join(self.cur, "INBOX.md"),
              "- check the seam class on biome 4\n- the bake host is free after 22:00\n")
        out = self.ok(self.tool("status.py", "inbox-consume"),
                      "check the seam class", "archived:")
        self.assertFalse(os.path.exists(os.path.join(self.cur, "INBOX.md")))
        self.assertTrue(any(n.startswith("inbox-") for n in
                            os.listdir(os.path.join(self.cur, "discussions"))))
        self.assertLessEqual(len(out.rstrip().split("\n")), 40)
        write(os.path.join(self.cur, "REVIEW.md"), "decisions needed\n")
        self.ok(self.tool("status.py", "wait", "REVIEW.md"))
        self.ok(self.tool("status.py", "review-done"), "review consumed")
        self.assertIsNone(self.status()["waiting"])
        self.refused(self.tool("status.py", "replan-written"), "no REPLAN.md")
        write(os.path.join(self.cur, "REPLAN.md"), "trigger\n")
        self.ok(self.tool("status.py", "replan-written"), "REPLAN.md")
        self.assertEqual(self.status()["waiting"], "REPLAN.md")

    def test_clear_keeps_router_session(self):
        """3.10 T28: clear and other writers keep router_session (only set/launch change it)."""
        write(os.path.join(self.root, ".run", "status.json"),
              json.dumps({"router_session": "sid-own", "task": "T3.1"}))
        self.ok(self.tool("status.py", "clear"), "cleared")
        self.assertEqual(self.status()["router_session"], "sid-own")
        self.ok(self.tool("status.py", "wait", "question"))
        self.assertEqual(self.status()["router_session"], "sid-own")

    def test_set_stamps_the_calling_session(self):
        """3.11 T20: set stamps the session Claude Code names in CLAUDE_CODE_SESSION_ID;
        --session wins; with neither the owner is kept."""
        write(os.path.join(self.root, ".run", "status.json"),
              json.dumps({"router_session": "sid-old"}))
        out = self.ok(self.tool("status.py", "set", "--task", "T3.1"))
        self.assertNotIn("router_session=", out)
        self.assertEqual(self.status()["router_session"], "sid-old")
        self.ok(self.tool("status.py", "set", "--task", "T3.1",
                          env={"CLAUDE_CODE_SESSION_ID": "sid-new"}), "router_session=sid-new")
        self.assertEqual(self.status()["router_session"], "sid-new")
        self.ok(self.tool("status.py", "set", "--task", "T3.1", "--session", "sid-arg",
                          env={"CLAUDE_CODE_SESSION_ID": "sid-new"}), "router_session=sid-arg")
        self.assertEqual(self.status()["router_session"], "sid-arg")


# ------------------------------------------------------------------------- #
class TestDiscussion(ToolCase):

    def test_on_off_status_and_new_record(self):
        flag = os.path.join(self.root, ".run", "DISCUSSION")
        self.ok(self.tool("discussion.py", "status"))
        self.ok(self.tool("discussion.py", "on", "--topic", "seam classes"),
                "discussion ON")
        self.assertTrue(os.path.isfile(flag))
        self.ok(self.tool("discussion.py", "status"), "on |", "seam classes")
        out = self.ok(self.tool("discussion.py", "off", "--new-record"),
                      "discussion OFF", "discussions/D1.md")
        self.assertFalse(os.path.exists(flag))
        d1 = read(os.path.join(self.cur, "discussions", "D1.md"))
        self.assertIn("# D1 — seam classes", d1)
        self.assertIn("## Plan edits", d1)
        index = read(os.path.join(self.cur, "discussions", "INDEX.md"))
        self.assertIn("D1 | seam classes |", index)
        self.assertIn("phase-ends/current/discussions/D1.md", index)
        self.assertIn("D1 | seam classes", out)
        self.ok(self.tool("discussion.py", "off", "--new-record", "--topic", "bake"),
                "discussions/D2.md")
        self.ok(self.tool("discussion.py", "status"), "off")


# ------------------------------------------------------------------------- #
class TestResearchAdd(ToolCase):

    def test_new_index_find_with_atomic_ids(self):
        out = self.ok(self.tool("research_add.py", "new", "--task", "T3.1",
                                "--title", "mask seam classes",
                                "--agent", "retriever-digest", "--tags", "masks,seams"))
        self.assertIn("R24-001", out)
        self.assertIn("phase-ends/current/research/R24-001.md", out)
        second = self.ok(self.tool("research_add.py", "new", "--task", "plan",
                                   "--title", "weight normalisation",
                                   "--agent", "retriever-web"))
        self.assertIn("R24-002", second)
        self.assertTrue(os.path.isfile(
            os.path.join(self.cur, "research", ".next")))
        path = os.path.join(self.cur, "research", "R24-001.md")
        self.assertFalse(os.path.isfile(path))            # the agent writes the file
        body = out.split("--- header (start the report with these lines) ---" + chr(10), 1)[1]
        self.assertIn("# R24-001 — mask seam classes", body)
        self.assertIn("agent: retriever-digest", body)
        write(path, body.replace("## Answer (returned verbatim, <=40 lines)",
                                 "## Answer (returned verbatim, <=40 lines)\n"
                                 "Three seam classes; only the border one matters.\n"))
        line = self.ok(self.tool("research_add.py", "index", "R24-001"))
        self.assertRegex(line, r"R24-001 \| T3\.1 \| mask seam classes \| masks,seams "
                               r"\| retriever-digest \| 20\d\d-\d\d-\d\d \| \d+ lines")
        self.ok(self.tool("research_add.py", "index", "R24-001"))   # idempotent
        index = read(os.path.join(self.cur, "research", "INDEX.md"))
        self.assertEqual(index.count("R24-001 |"), 1)
        self.ok(self.tool("research_add.py", "find", "seam"), "R24-001")
        self.ok(self.tool("research_add.py", "find", "nothing-here"),
                "no research index line matches")

    def test_index_refuses_a_long_answer(self):
        out = self.ok(self.tool("research_add.py", "new", "--task", "T1", "--title", "long",
                                "--agent", "retriever-digest"))
        path = os.path.join(self.cur, "research", "R24-001.md")
        body = out.split("--- header (start the report with these lines) ---" + chr(10), 1)[1].replace("## Answer (returned verbatim, <=40 lines)",
                                  "## Answer (returned verbatim, <=40 lines)\n"
                                  + "\n".join("line %d" % i for i in range(45)))
        write(path, body)
        self.refused(self.tool("research_add.py", "index", "R24-001"),
                     "## Answer is 45 lines")


# ------------------------------------------------------------------------- #
class TestTaskLog(ToolCase):

    def test_finish_indexes_and_lints(self):
        self.make_summary("T1", title="mask builder for four biomes")
        out = self.ok(self.tool("task_log.py", "finish", "T1"))
        self.assertIn("T1 | done | mask builder for four biomes | masks,textures "
                      "| tasks/T1.md | logs/T1.md | R24-001", out)
        index = read(os.path.join(self.cur, "tasks", "INDEX.md"))
        self.assertIn("T1 | done |", index)
        self.ok(self.tool("task_log.py", "finish", "T1"))
        self.assertEqual(read(os.path.join(self.cur, "tasks",
                                           "INDEX.md")).count("T1 | done |"), 1)
        self.ok(self.tool("task_log.py", "lint", "T1"), "OK T1")

    def test_finish_refuses_over_150_lines(self):
        body = summary("T1") + "\n".join("- note %d" % i for i in range(160))
        write(os.path.join(self.cur, "tasks", "T1.md"), body)
        write(os.path.join(self.cur, "logs", "T1.md"), "# T1 full log\n")
        p = self.refused(self.tool("task_log.py", "finish", "T1"), "max 150")
        self.assertIn("FAILED", p)
        self.assertFalse(os.path.exists(os.path.join(self.cur, "tasks", "INDEX.md")))

    def test_missing_verified_is_a_warning_not_a_refusal(self):
        self.make_summary("T2", verified=False)
        out = self.ok(self.tool("task_log.py", "finish", "T2"))
        self.assertIn("WARN", out)
        self.assertIn("Verified:", out)
        self.assertIn("T2 | done |", out)

    def test_missing_log_and_tables_are_refusals(self):
        write(os.path.join(self.cur, "tasks", "T3.md"), summary("T3"))
        self.refused(self.tool("task_log.py", "lint", "T3"), "no full log")
        write(os.path.join(self.cur, "logs", "T3.md"), "# T3 full log\n")
        write(os.path.join(self.cur, "tasks", "T3.md"),
              summary("T3") + "\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.refused(self.tool("task_log.py", "lint", "T3"), "markdown table")

    def test_coder_finish(self):
        self.refused(self.tool("task_log.py", "coder-finish", "T1.c1"),
                     "no coder log")
        write(os.path.join(self.cur, "logs", "T1.c1.md"), "# T1.c1 coder log\n")
        self.ok(self.tool("task_log.py", "coder-finish", "T1.c1"), "OK T1.c1")
        self.refused(self.tool("task_log.py", "coder-finish", "T1"), "T3.c1")


# ------------------------------------------------------------------------- #
class TestRulesAdd(ToolCase):

    def test_add_supersede_retire_and_promote(self):
        write(os.path.join(self.root, "body.md"),
              "Masks are built per biome. Never per chunk.\n")
        out = self.ok(self.tool("rules_add.py", "add", "--id", "R28",
                                "--title", "masks are per biome", "--group", "F",
                                "--file", "body.md", "--origin", "Phase 24",
                                "--tags", "masks"))
        self.assertIn("rules/R28.md", out)
        self.assertIn("R28 | masks are per biome | masks | active | Phase 24", out)
        body = read(os.path.join(self.root, "rules", "R28.md"))
        self.assertIn("# R28 — masks are per biome", body)
        self.assertIn("status: active", body)
        self.assertIn("Never per chunk", body)
        self.refused(self.tool("rules_add.py", "add", "--id", "R28",
                               "--title", "again", "--file", "body.md"),
                     "already exists")
        self.ok(self.tool("rules_add.py", "supersede", "M4", "--by", "R28"),
                "superseded-by:R28")
        self.assertIn("M4 | never push; the developer pushes | git "
                      "| superseded-by:R28 |",
                      read(os.path.join(self.root, "rules", "INDEX.md")))
        self.ok(self.tool("rules_add.py", "retire", "M4"), "sunset")
        out = self.ok(self.tool("rules_add.py", "promote", "R28"),
                      "promoted R28")
        skill = read(os.path.join(self.root, ".claude", "skills",
                                  "project-architect", "SKILL.md"))
        self.assertIn("- R28 — masks are per biome", skill)
        self.ok(self.tool("rules_add.py", "promote", "R28"), "already promoted")

    def test_promote_refuses_over_budget(self):
        skill = os.path.join(self.root, ".claude", "skills", "project-architect", "SKILL.md")
        write(skill, read(skill) + "\n".join(
            "- X%d — a long standing headline that eats the section budget" % i
            for i in range(30)) + "\n")
        write(os.path.join(self.root, "body.md"), "text\n")
        self.ok(self.tool("rules_add.py", "add", "--id", "R29",
                          "--title", "one more", "--file", "body.md"))
        self.refused(self.tool("rules_add.py", "promote", "R29"), "max 400")
        self.assertNotIn("R29", read(skill))


# ------------------------------------------------------------------------- #
class TestSkillAdd(ToolCase):

    def test_scaffold_and_description_limit(self):
        write(os.path.join(self.cur, "logs", "T1.md"),
              "# T1 full log\n\n## Workflow\n1. run the gate\n2. read the tail\n\n"
              "## Timeline\nnot captured\n")
        out = self.ok(self.tool("skill_add.py", "bake-gate",
                                "--from", "phase-ends/current/logs/T1.md#Workflow",
                                "--description", "Run the overnight bake gate and "
                                                 "read its tail."))
        self.assertIn(".claude/skills/bake-gate/SKILL.md", out)
        skill = read(os.path.join(self.root, ".claude", "skills",
                                  "bake-gate", "SKILL.md"))
        self.assertIn("name: bake-gate", skill)
        self.assertIn("1. run the gate", skill)
        self.assertNotIn("not captured", skill)
        self.assertIn("- bake-gate — Run the overnight bake gate",
                      read(os.path.join(self.root, "HOW_WE_WORK.md")))
        self.refused(self.tool("skill_add.py", "bake-gate", "--description", "x"),
                     "already exists")
        self.refused(self.tool("skill_add.py", "too-long", "--description", "x" * 121),
                     "max 120")
        self.refused(self.tool("skill_add.py", "Bad Name", "--description", "x"),
                     "lowercase-with-dashes")


# ------------------------------------------------------------------------- #
class TestLaunch(ToolCase):

    def dry(self, *args):
        p = self.tool("launch.py", "--dry-run", *args)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertLessEqual(len(p.out.rstrip().split("\n")), 40, p.out)
        cmd = [x for x in p.out.split("\n") if x.startswith("claude ")]
        self.assertEqual(len(cmd), 1, p.out)
        return p.out, cmd[0]

    def test_state_router_when_the_plan_is_approved(self):
        out, cmd = self.dry()
        self.assertIn("state=router (approved plan)", out)
        self.assertEqual(cmd, 'claude --agent pa-session --model claude-sonnet-5-5[1m] '
                              '--effort medium --permission-mode auto '
                              '--name "pa:demo:router:P24"')
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("# PA3 session seed -- router -- Phase 24 (Gen 3)", seed)
        self.assertIn("## Tasks", seed)
        self.assertIn("T3.1 |", seed)
        self.assertIn("## Next", seed)
        launch = json.loads(read(os.path.join(self.root, ".run", "launch.json")))
        self.assertEqual(launch["mode"], "router")
        self.assertEqual(launch["phase"], "24")
        self.assertEqual(launch["model"], "claude-sonnet-5-5[1m]")

    def test_seed_names_the_running_expert_after_a_resume(self):
        """A session resumed mid-task: the seed tells the router which run to continue."""
        self.tool("status.py", "set", "--task", "T3.1", "--agent", "expert-fable", "--coder", "sonnet",
                  "--kind", "task", "--attempt", "1")
        write(os.path.join(self.root, ".run", "status.json"), read(
            os.path.join(self.root, ".run", "status.json")).replace('"run_id": null', '"run_id": "a1b2c3d4000000005"'))
        self.dry()
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("Running: T3.1 run a1b2c3d4000000005 (attempt 1, task)", seed)
        self.assertIn("SendMessage that run first", seed)
        self.tool("status.py", "clear")
        self.dry()
        self.assertNotIn("Running:", read(os.path.join(self.root, ".run", "seed.md")))

    def test_seed_names_a_killed_run_to_respawn(self):
        """T13: killed_at -- the seed says respawn with progress."""
        self.tool("status.py", "set", "--task", "T3.1", "--agent", "expert-fable", "--coder", "sonnet",
                  "--kind", "task", "--attempt", "1")
        status_path = os.path.join(self.root, ".run", "status.json")
        st = json.loads(read(status_path))
        st["run_id"] = "a1b2c3d4000000005"
        st["killed_at"] = "2026-09-20T22:01:39Z"
        write(status_path, json.dumps(st))
        # without progress file: the seed says transcript was not found
        self.dry()
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("Run in flight: a1b2c3d4000000005 (task T3.1, expert-fable) was killed "
                      "with the previous process at 2026-09-20T22:01:39Z", seed)
        self.assertIn('continue it first: SendMessage(to="a1b2c3d4000000005"', seed)
        self.assertIn("when the send fails", seed)
        self.assertIn("respawn T3.1 as attempt 2", seed)
        self.assertIn("no progress file: the transcript was not found", seed)
        self.assertIn("SendMessage", seed)
        # with progress file present
        write(os.path.join(self.cur, "TASK_PROGRESS.md"), "# progress\n")
        self.dry()
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("PROGRESS: phase-ends/current/TASK_PROGRESS.md", seed)
        self.assertNotIn("no progress file", seed)
        # without killed_at, the run_id alone stays on the plain "Running:" wording
        del st["killed_at"]
        write(status_path, json.dumps(st))
        self.dry()
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertNotIn("Run in flight:", seed)
        self.assertIn("Running: T3.1 run a1b2c3d4000000005", seed)
        self.tool("status.py", "clear")
        self.dry()
        self.assertNotIn("Run in flight:", read(os.path.join(self.root, ".run", "seed.md")))

    def test_seed_only_writes_the_seed_and_does_not_launch(self):
        p = self.tool("launch.py", "--seed-only")
        self.assertEqual(p.returncode, 0, p.out)
        lines = [l for l in p.stdout.strip().split("\n") if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(l.startswith("seed=") for l in lines))
        self.assertTrue(any(l.startswith("mode=") for l in lines))
        self.assertFalse([x for x in p.out.split("\n") if x.startswith("claude ")], p.out)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("## Next", seed)
        launch = json.loads(read(os.path.join(self.root, ".run", "launch.json")))
        self.assertEqual(launch["agent"], "pa-session")

    def test_from_draft_writes_the_plan_only_before_approval(self):
        draft = os.path.join(self.root, "draft.md")
        write(draft, read(self.plan).replace("Approved: 2026-09-12", "Approved: —")
              .replace("## Tasks", "## Triage\n- (none)\n\n## Tasks", 1))
        p = self.tool("plan_edit.py", "from-draft", draft)
        self.assertNotEqual(p.returncode, 0, p.out)          # approved plan, no REPLAN.md
        write(os.path.join(self.cur, "REPLAN.md"), "Trigger: the milestone moved\n")
        p = self.tool("plan_edit.py", "from-draft", draft)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("archived phase-ends/current/PHASE_PLAN.v1.md", p.out)
        self.assertIn("Approved: —", read(self.plan))
        os.remove(os.path.join(self.cur, "REPLAN.md"))

    def test_status_clear_drops_the_phase_when_the_plan_is_gone(self):
        self.ok(self.tool("status.py", "set", "--task", "T1"), "status:")
        st = json.loads(read(os.path.join(self.root, ".run", "status.json")))
        self.assertEqual(st["generation"], "3.4")                # the generation-plan phase id
        self.assertEqual(st["phase"], "24")
        os.remove(self.plan)
        self.ok(self.tool("status.py", "clear"), "cleared")
        st = json.loads(read(os.path.join(self.root, ".run", "status.json")))
        self.assertIsNone(st["phase"])
        self.assertIsNone(st["phase_name"])
        self.assertIsNone(st["generation"])

    def test_research_new_reserves_without_creating_the_file(self):
        p = self.tool("research_add.py", "new", "--task", "T1", "--title", "why", "--agent", "retriever-code")
        self.assertEqual(p.returncode, 0, p.out)
        rid, relpath = p.out.split("\n")[0], p.out.split("\n")[1]
        path = os.path.join(self.root, relpath)
        self.assertFalse(os.path.isfile(path), "new must not pre-create the report")
        self.assertIn("# %s" % rid, p.out)                       # the header is printed instead
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".run", "research-reserved", rid)))
        p2 = self.tool("research_add.py", "new", "--task", "T1", "--title", "again", "--agent", "retriever-code")
        self.assertNotEqual(p2.out.split("\n")[0], rid)          # the reservation holds the id
        header = p.out.split("--- header (start the report with these lines) ---\n", 1)[1]
        write(path, header + "\n## Answer\nfound it\n\n## Findings\n-\n\n## Dead ends\n-\n")
        self.ok(self.tool("research_add.py", "index", rid), rid)
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".run", "research-reserved", rid)))

    def test_state_planner_gen_without_a_generation_plan(self):
        os.remove(os.path.join(self.root, "GENERATION_PLAN.md"))
        out, cmd = self.dry()
        self.assertIn("state=planner-gen", out)
        self.assertIn("--agent pa-session", cmd)
        self.assertIn("--model claude-sonnet-5-5[1m]", cmd)
        self.assertIn("--effort medium", cmd)
        self.assertNotIn("--permission-mode", cmd)

    def test_state_planner_gen_when_every_phase_is_closed(self):
        gp = os.path.join(self.root, "GENERATION_PLAN.md")
        write(gp, read(gp).replace("status: open", "status: closed"))
        out, _cmd = self.dry()
        self.assertIn("state=planner-gen (no open generation phase)", out)

    def test_state_planner_phase_without_an_approved_plan(self):
        text = read(self.plan).replace("Approved: 2026-09-12", "Approved: —")
        write(self.plan, text)
        out, cmd = self.dry()
        self.assertIn("state=planner-phase (PHASE_PLAN.md not approved)", out)
        self.assertIn("--agent pa-session", cmd)
        os.remove(self.plan)
        out, _cmd = self.dry()
        self.assertIn("state=planner-phase (no PHASE_PLAN.md)", out)

    def test_state_planner_phase_for_replan_and_review(self):
        write(os.path.join(self.cur, "REPLAN.md"), "Trigger: the milestone moved\n")
        out, cmd = self.dry()
        self.assertIn("state=planner-phase (REPLAN.md present)", out)
        self.assertIn("--agent pa-session", cmd)
        self.assertIn("present: phase-ends/current/REPLAN.md",
                      read(os.path.join(self.root, ".run", "seed.md")))
        os.remove(os.path.join(self.cur, "REPLAN.md"))
        write(os.path.join(self.cur, "REVIEW.md"), "What ran: the bake\n")
        out, cmd = self.dry()
        self.assertIn("state=review (REVIEW.md present)", out)
        self.assertEqual(cmd, 'claude --agent pa-session --model claude-sonnet-5-5[1m] '
                              '--effort medium --name "pa:demo:review:P24"')

    def test_next_mode_overrides_once(self):
        self.ok(self.tool("launch.py", "--request", "review"), "next_mode: review")
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".run", "next_mode")))
        out, cmd = self.dry()
        self.assertIn("state=review (.run/next_mode (consumed))", out)
        self.assertIn("--agent pa-session", cmd)
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".run", "next_mode")))
        out, cmd = self.dry()
        self.assertIn("state=router", out)

    def test_mode_model_effort_perm_and_resume_overrides(self):
        out, cmd = self.dry("--mode", "planner-phase", "--effort", "high",
                            "--model", "claude-opus-5", "--perm", "acceptEdits")
        self.assertIn("state=planner-phase (--mode)", out)
        self.assertEqual(cmd, 'claude --agent pa-session --model claude-opus-5 '
                              '--effort high --permission-mode acceptEdits '
                              '--name "pa:demo:planner-phase:P24"')
        out, cmd = self.dry("--resume", "abc-123")
        self.assertEqual(cmd, "claude --resume abc-123 --effort medium")

    def test_router_permission_mode_is_configurable(self):
        pa = os.path.join(self.root, ".claude", "pa.json")
        conf = json.loads(read(pa))
        conf["router_permission_mode"] = "acceptEdits"
        write(pa, json.dumps(conf, indent=1) + "\n")
        _out, cmd = self.dry()
        self.assertIn("--permission-mode acceptEdits", cmd)

    def test_plain_is_the_fallback_session(self):
        out, cmd = self.dry("--plain")
        self.assertIn("state=plain", out)
        self.assertIn("--agent plain", cmd)                      # a design session, not the router
        self.assertTrue(cmd.startswith("claude --agent plain --model claude-fable-5-1[1m] "
                                       "--effort high"), cmd)

    def test_check_fable_reads_the_ledger_summary_if_present(self):
        out, _cmd = self.dry("--check-fable")
        self.assertTrue("Fable window" in out or "summary.json" in out, out)

    def test_refuses_outside_a_pa3_project(self):
        other = tempfile.mkdtemp(prefix="pa3-none-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        p = self.tool("launch.py", "--dry-run", cwd=other)
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("pa.json", p.out)


# ------------------------------------------------------------------------- #
@unittest.skipUnless(BASH and shutil.which("git"), "bash/git not on PATH")
class TestShellTools(ToolCase):

    def sh(self, script, *args, **kw):
        p = subprocess.run([BASH, os.path.join(TOOLS, script)] + [str(a) for a in args],
                           cwd=self.root, capture_output=True, text=True,
                           env=kw.get("env"))
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def sh_env_msg(self, script, task, msg):
        """Pass a multi-line message through the environment: the Windows argv
        boundary eats embedded newlines."""
        env = dict(os.environ)
        env["PA_TEST_MSG"] = msg
        p = subprocess.run([BASH, "-c",
                            'exec bash "$1" "$2" "$PA_TEST_MSG"', "_",
                            os.path.join(TOOLS, script), task],
                           cwd=self.root, capture_output=True, text=True, env=env)
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def test_commit_task_stages_and_leaves_strays(self):
        # 3.3 T1: an unrelated untracked file is listed and left alone (parallel
        # coders share one tree); it is committed only when passed explicitly
        self.git_init()
        write(os.path.join(self.root, "HOW_WE_WORK.md"),
              read(os.path.join(self.root, "HOW_WE_WORK.md")) + "\n## Rhythm\nauto\n")
        self.make_summary("T1")
        write(os.path.join(self.root, "stray.py"), "print('hi')\n")
        p = self.sh("commit_task.sh", "T1", "mask builder green")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("untracked files outside", p.out)
        self.assertIn("stray.py", p.out)
        files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                               cwd=self.root, capture_output=True, text=True).stdout
        self.assertNotIn("stray.py", files)
        self.assertIn("phase-ends/current/tasks/T1.md", files.replace("\\", "/"))
        # 3.9 T1: a dirty card stays unstaged unless passed
        self.assertNotIn("HOW_WE_WORK.md", files)
        dirty = subprocess.run(["git", "status", "--short", "HOW_WE_WORK.md"],
                               cwd=self.root, capture_output=True, text=True).stdout
        self.assertIn("HOW_WE_WORK.md", dirty)
        others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                cwd=self.root, capture_output=True, text=True).stdout
        self.assertIn("stray.py", others)
        p = self.sh("commit_task.sh", "T1", "stray added", "stray.py", "HOW_WE_WORK.md")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertRegex(p.stdout.split("\n")[0], r"^[0-9a-f]{7,}  T1: stray added")
        log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=self.root,
                             capture_output=True, text=True).stdout
        self.assertIn("T1: stray added", log)
        files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                               cwd=self.root, capture_output=True, text=True).stdout
        self.assertIn("stray.py", files)
        self.assertIn("HOW_WE_WORK.md", files)     # staged when passed
        self.assertEqual(len(p.stdout.rstrip().split("\n")), 2)

    def test_commit_task_refuses_bad_messages(self):
        self.git_init()
        p = self.sh_env_msg("commit_task.sh", "T1", "two\nlines")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("one line", p.out)
        p = self.sh("commit_task.sh", "T1", "x" * 101)
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("max 100", p.out)
        write(os.path.join(self.root, "note.md"), "x\n")
        p = self.sh("commit_task.sh", "T1", "nothing changed but an untracked file")
        self.assertEqual(p.returncode, 1, p.out)

    def test_commit_task_stages_only_its_paths_and_keeps_one_prefix(self):
        """3.1 T14: no sweep of dirty tracked files; a message carrying the id is not prefixed twice."""
        self.git_init()
        self.tool("plan_edit.py", "set-status", "T3.1", "superseded")     # the router's dirty plan edit
        write(os.path.join(self.root, "cookbook", "INDEX.md"),
              read(os.path.join(self.root, "cookbook", "INDEX.md"))
              + "C0189 | later | misc | 2026-09-19 | 24/T2 | origin: T2\n")
        self.make_summary("T2", title="blend weights")
        p = self.sh("commit_task.sh", "T2", "T2: blend weights green")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertRegex(p.stdout.split("\n")[0], r"^[0-9a-f]{7,}  T2: blend weights green$")

        def shown():
            out = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                                 cwd=self.root, capture_output=True, text=True).stdout
            return out.replace("\\", "/")

        def dirty():
            out = subprocess.run(["git", "status", "--short"], cwd=self.root,
                                 capture_output=True, text=True).stdout
            return out.replace("\\", "/")

        self.assertIn("phase-ends/current/tasks/T2.md", shown())
        self.assertIn("phase-ends/current/logs/T2.md", shown())
        self.assertNotIn("PHASE_PLAN.md", shown())
        self.assertNotIn("cookbook/INDEX.md", shown())
        self.assertIn("PHASE_PLAN.md", dirty())                 # still dirty, untouched
        self.assertIn("cookbook/INDEX.md", dirty())
        # the router commits its own plan edit with an explicit path
        p = self.sh("commit_task.sh", "router", "T3.1 superseded",
                    "phase-ends/current/PHASE_PLAN.md")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("router: T3.1 superseded", p.stdout)
        self.assertEqual(shown().split(), ["phase-ends/current/PHASE_PLAN.md"])
        self.assertIn("cookbook/INDEX.md", dirty())
        self.assertNotIn("PHASE_PLAN.md", dirty())

    def test_cookbook_add_allocates_ids_and_finds(self):
        write(os.path.join(self.root, "body.md"), "Clear the mask cache first.\n")
        p = self.sh("cookbook_add.sh", "--title", "mask cache reset",
                    "--tags", "masks,cache,biome", "--task", "T1", "--file", "body.md")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("cookbook/C0001.md", p.out)
        self.assertIn("C0001 | mask cache reset | masks,cache,biome |", p.out)
        self.assertIn("| 24/T1 |", p.out)
        body = read(os.path.join(self.root, "cookbook", "C0001.md"))
        self.assertIn("# C0001 — mask cache reset", body)
        self.assertIn("Clear the mask cache first.", body)
        p = self.sh("cookbook_add.sh", "--title", "second", "--file", "body.md")
        self.assertIn("cookbook/C0002.md", p.out)
        p = self.sh("cookbook_add.sh", "--find", "mask cache")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("C0001", p.out)
        p = self.sh("cookbook_add.sh", "--find", "biome")            # by tag
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("C0001 | mask cache reset", p.out)
        self.assertNotIn("C0002", p.out)
        p = self.sh("cookbook_add.sh", "--title", "no body")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("--file", p.out)

    def test_run_sh_logs_tails_and_greps(self):
        p = self.sh("run.sh", "hello", "--", "echo", "hello-world")
        self.assertEqual(p.returncode, 0, p.out)
        lines = p.out.rstrip().split("\n")
        self.assertEqual(lines[0], "exit=0 log=.run/logs/hello.log lines=1")
        self.assertEqual(lines[1], "hello-world")
        self.assertEqual(read(os.path.join(self.root, ".run", "logs",
                                           "hello.log")).strip(), "hello-world")
        p = self.sh("run.sh", "three", "--grep", "needle", "--",
                    "bash", "-c", "echo hay; echo needle; echo hay")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("lines=3", p.out)
        self.assertIn("2:needle", p.out)
        self.assertNotIn("hay", p.out.split("\n", 1)[1])
        p = self.sh("run.sh", "boom", "--", "bash", "-c", "echo bad >&2; exit 3")
        self.assertEqual(p.returncode, 3, p.out)
        self.assertIn("exit=3 log=.run/logs/boom.log", p.out)
        self.assertIn("bad", p.out)
        self.assertLessEqual(len(p.out.rstrip().split("\n")), 40)
        p = self.sh("run.sh", "nope")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("no command after --", p.out)

    def test_run_sh_bg_and_wait(self):
        p = self.sh("run.sh", "--bg", "bgjob", "--", "bash", "-c",
                    "echo started; echo finished")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertRegex(p.out, r"bg=bgjob pid=\d+ log=\.run/logs/bgjob\.log")
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".run", "logs",
                                                    "bgjob.pid")))
        p = self.sh("run.sh", "--wait", "bgjob", "--max", "60")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("exit=0 log=.run/logs/bgjob.log", p.out)
        self.assertIn("finished", p.out)
        # the wrapper closes stdin, so a prompting command cannot stall (3.9.6 T1)
        with open(os.path.join(self.root, ".run", "logs", "bgjob.cmd.sh"),
                  encoding="utf-8") as fh:
            cmd = [ln for ln in fh.read().splitlines() if "bgjob.log" in ln]
        self.assertEqual(len(cmd), 1, cmd)
        self.assertTrue(cmd[0].rstrip().endswith("< /dev/null"), cmd[0])
        p = self.sh("run.sh", "--bg", "bgread", "--", "bash", "-c",
                    "read -r x; echo read-rc=$?")
        self.assertEqual(p.returncode, 0, p.out)
        p = self.sh("run.sh", "--wait", "bgread", "--max", "60")
        self.assertIn("read-rc=1", p.out)
        p = self.sh("run.sh", "--wait", "never-started")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("pid file", p.out)


# ------------------------------------------------------------------------- #
@unittest.skipUnless(BASH and shutil.which("git"), "bash/git not on PATH")
class TestCurate(ToolCase):
    """T9: curate.py demotions for memory, cookbook, rules and HOW_WE_WORK trim."""

    def setUp(self):
        super().setUp()
        self.git_init()
        # set up a memory directory with two files and an index
        self.memdir = os.path.join(self.root, ".claude-state", "memory")
        os.makedirs(self.memdir, exist_ok=True)
        write(os.path.join(self.memdir, "MEMORY.md"),
              "- [Dev profile](user-dev.md) — solo dev\n"
              "- [Rebuild state](rebuild.md) — 3.1 closed\n")
        write(os.path.join(self.memdir, "user-dev.md"),
              "---\nname: user-dev\n---\nDev T, solo developer.\n")
        write(os.path.join(self.memdir, "rebuild.md"),
              "---\nname: rebuild\n---\nPhase 3.1 closed. Next: 3.2.\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add memory")
        # set up HOW_WE_WORK with standing decisions
        write(os.path.join(self.root, "HOW_WE_WORK.md"),
              "# How we work\n\n## Project\nOne line.\n\n"
              "## Standing decisions\n"
              "- 2026-01-01, plan: old decision that is superseded.\n"
              "- 2026-09-12, plan: expert = Fable 5.1 medium by default.\n"
              "- 2026-09-20, developer: newer decision.\n\n"
              "## Tools\n")
        # add rule files for the git mv tests
        write(os.path.join(self.root, "rules", "M4.md"),
              "# M4 — never push\nstatus: active\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add hww and rules")

    def test_memory_demotion_preserves_content(self):
        p = self.tool("curate.py", "memory", "--demote", "rebuild.md",
                       "--gen", "3", "--dir", self.memdir)
        self.ok(p, "demote: rebuild.md")
        # the file is gone
        self.assertFalse(os.path.isfile(os.path.join(self.memdir, "rebuild.md")))
        # content preserved in gen3.md
        archive = read(os.path.join(self.memdir, "gen3.md"))
        self.assertIn("## rebuild", archive)
        self.assertIn("Phase 3.1 closed", archive)
        self.assertIn("name: rebuild", archive)
        # index line moved
        index = read(os.path.join(self.memdir, "MEMORY.md"))
        self.assertNotIn("rebuild.md", index)
        self.assertIn("- [Generation 3 memories](gen3.md)", index)
        self.assertIn("1 entries", index)
        # the other memory stays
        self.assertIn("user-dev.md", index)

    def test_memory_demotion_count_updates(self):
        self.tool("curate.py", "memory", "--demote", "rebuild.md",
                  "--gen", "3", "--dir", self.memdir)
        # demote the second one too
        self.tool("curate.py", "memory", "--demote", "user-dev.md",
                  "--gen", "3", "--dir", self.memdir)
        index = read(os.path.join(self.memdir, "MEMORY.md"))
        self.assertIn("2 entries", index)
        # the link line appears only once
        self.assertEqual(index.count("Generation 3 memories"), 1)

    def test_memory_dry_run_writes_nothing(self):
        p = self.tool("curate.py", "memory", "--dry-run", "--demote", "rebuild.md",
                       "--gen", "3", "--dir", self.memdir)
        self.ok(p, "dry-run")
        self.assertTrue(os.path.isfile(os.path.join(self.memdir, "rebuild.md")))
        self.assertFalse(os.path.isfile(os.path.join(self.memdir, "gen3.md")))
        index = read(os.path.join(self.memdir, "MEMORY.md"))
        self.assertIn("rebuild.md", index)

    def test_cookbook_demotion_git_mv(self):
        # add cookbook entries tracked in git
        write(os.path.join(self.root, "cookbook", "C0187.md"),
              "# C0187 — mask cache reset\ntags: masks\n")
        self.git("add", "cookbook/C0187.md")
        self.git("commit", "-q", "-m", "add cookbook entry")
        p = self.tool("curate.py", "cookbook", "--demote", "C0187",
                       "--gen", "3")
        self.ok(p, "demote: C0187")
        # file moved
        self.assertFalse(os.path.isfile(os.path.join(self.root, "cookbook", "C0187.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "cookbook", "gen3", "C0187.md")))
        # gen index created
        gen_idx = read(os.path.join(self.root, "cookbook", "gen3", "INDEX.md"))
        self.assertIn("C0187 |", gen_idx)
        # main index updated: entry removed, link added
        main_idx = read(os.path.join(self.root, "cookbook", "INDEX.md"))
        self.assertNotIn("C0187 | mask cache reset | masks | 2026-09-12", main_idx)
        self.assertIn("gen3 | Generation 3 entries | see cookbook/gen3/INDEX.md", main_idx)
        # link line appears only once
        self.assertEqual(main_idx.count("gen3 |"), 1)

    def test_cookbook_dry_run(self):
        write(os.path.join(self.root, "cookbook", "C0187.md"),
              "# C0187 — mask cache reset\n")
        self.git("add", "cookbook/C0187.md")
        self.git("commit", "-q", "-m", "add cookbook")
        p = self.tool("curate.py", "cookbook", "--dry-run", "--demote", "C0187",
                       "--gen", "3")
        self.ok(p, "dry-run")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "cookbook", "C0187.md")))

    def test_rules_demotion_active_stays_marked(self):
        p = self.tool("curate.py", "rules", "--demote", "M4", "--gen", "3")
        self.ok(p, "demote: M4")
        # file moved
        self.assertFalse(os.path.isfile(os.path.join(self.root, "rules", "M4.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "rules", "gen3", "M4.md")))
        # active rule keeps its main index line, marked demoted
        main_idx = read(os.path.join(self.root, "rules", "INDEX.md"))
        self.assertIn("M4 |", main_idx)
        self.assertIn("demoted:gen3", main_idx)
        # gen index has the line
        gen_idx = read(os.path.join(self.root, "rules", "gen3", "INDEX.md"))
        self.assertIn("M4 |", gen_idx)
        # link line
        self.assertIn("gen3 | Generation 3 entries", main_idx)
        self.assertEqual(main_idx.count("gen3 |"), 1)

    def test_rules_dry_run(self):
        p = self.tool("curate.py", "rules", "--dry-run", "--demote", "M4", "--gen", "3")
        self.ok(p, "dry-run")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "rules", "M4.md")))

    def test_hww_report_lists_candidates(self):
        p = self.tool("curate.py", "how-we-work", "--report")
        self.ok(p, "candidate")
        # the report lists the old decision
        self.assertIn("old decision", p.out)
        # the file size check
        self.assertNotIn("dry-run", p.out)

    def test_hww_report_before_date(self):
        p = self.tool("curate.py", "how-we-work", "--report", "--before", "2026-09-01")
        self.ok(p)
        self.assertIn("old decision", p.out)
        # the September decisions should not appear as candidates
        self.assertNotIn("expert = Fable", p.out)

    def test_hww_retire_moves_lines(self):
        # get the hash of the old decision line
        import hashlib
        line = "- 2026-01-01, plan: old decision that is superseded."
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:8]
        p = self.tool("curate.py", "how-we-work", "--retire", h, "--gen", "3")
        self.ok(p, "retire")
        # line removed from HOW_WE_WORK
        hww = read(os.path.join(self.root, "HOW_WE_WORK.md"))
        self.assertNotIn("old decision that is superseded", hww)
        # line preserved in retired file
        retired = read(os.path.join(self.root, "docs", "retired",
                                    "HOW_WE_WORK.gen3.md"))
        self.assertIn("old decision that is superseded", retired)
        self.assertIn("## Standing decisions", retired)
        # other lines stay
        self.assertIn("expert = Fable", hww)

    def test_hww_retire_dry_run(self):
        import hashlib
        line = "- 2026-01-01, plan: old decision that is superseded."
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:8]
        p = self.tool("curate.py", "how-we-work", "--dry-run", "--retire", h, "--gen", "3")
        self.ok(p, "dry-run")
        hww = read(os.path.join(self.root, "HOW_WE_WORK.md"))
        self.assertIn("old decision that is superseded", hww)

    def _write_multiline_hww(self):
        write(os.path.join(self.root, "HOW_WE_WORK.md"),
              "# How we work\n\n## Project\nOne line.\n\n"
              "## Standing decisions\n"
              "- 2026-01-01, plan: one-line decision.\n"
              "- 2026-01-02, plan: three-line decision that\n"
              "  spans multiple lines\n"
              "  for extra context.\n"
              "- 2026-09-20, developer: newer decision.\n\n"
              "## Tools\n")

    def test_hww_report_shows_multiline_count(self):
        self._write_multiline_hww()
        p = self.tool("curate.py", "how-we-work", "--report")
        self.ok(p, "one-line decision", "three-line decision that")
        self.assertIn("(3 lines)", p.out)
        self.assertNotIn("one-line decision. (", p.out)

    def test_hww_retire_multiline_bullet(self):
        import hashlib
        self._write_multiline_hww()
        line = "- 2026-01-02, plan: three-line decision that"
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:8]
        p = self.tool("curate.py", "how-we-work", "--retire", h, "--gen", "3")
        self.ok(p, "retire")
        # all three lines removed from HOW_WE_WORK
        hww = read(os.path.join(self.root, "HOW_WE_WORK.md"))
        self.assertNotIn("three-line decision that", hww)
        self.assertNotIn("spans multiple lines", hww)
        self.assertNotIn("for extra context", hww)
        # the one-line bullet is untouched
        self.assertIn("one-line decision", hww)
        # all three lines preserved in the archive
        retired = read(os.path.join(self.root, "docs", "retired",
                                    "HOW_WE_WORK.gen3.md"))
        self.assertIn("three-line decision that", retired)
        self.assertIn("spans multiple lines", retired)
        self.assertIn("for extra context", retired)

    def test_hww_retire_multiline_dry_run(self):
        import hashlib
        self._write_multiline_hww()
        line = "- 2026-01-02, plan: three-line decision that"
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:8]
        p = self.tool("curate.py", "how-we-work", "--dry-run", "--retire", h, "--gen", "3")
        self.ok(p, "dry-run")
        hww = read(os.path.join(self.root, "HOW_WE_WORK.md"))
        self.assertIn("three-line decision that", hww)
        self.assertIn("spans multiple lines", hww)
        self.assertFalse(os.path.isfile(os.path.join(
            self.root, "docs", "retired", "HOW_WE_WORK.gen3.md")))


# ------------------------------------------------------------------------- #
class TestCurateSeed(ToolCase):
    """T9: the curate seed line in launch.py."""

    def _close_every_phase(self):
        # planner-gen mode: no open generation phase
        gp = os.path.join(self.root, "GENERATION_PLAN.md")
        write(gp, re.sub(r"status: \w+", "status: closed", read(gp)))

    def test_seed_curate_line_with_generation_end(self):
        # create a GenerationEnd file
        write(os.path.join(self.root, "phase-ends", "GenerationEnd_3.md"),
              "# Generation 3 close\n")
        out, _cmd = TestLaunch.dry(self)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertNotIn("curate:", seed)                      # router mode
        os.remove(self.plan)
        out, _cmd = TestLaunch.dry(self)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertNotIn("curate:", seed)                      # planner-phase: a generation is open
        self._close_every_phase()
        out, _cmd = TestLaunch.dry(self)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("curate: closing gen 3 -> opening gen 4", seed)   # planner-gen

    def test_seed_curate_line_with_legacy_entries(self):
        # write LEGACY_INDEX with entries
        write(os.path.join(self.root, "phase-ends", "LEGACY_INDEX.md"),
              "# Legacy PhaseEnds (pre-PA3)\n# header\n"
              "11.5.7 | a phase | done | 2025-01-01 | path\n")
        os.remove(self.plan)
        self._close_every_phase()
        out, _cmd = TestLaunch.dry(self)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertIn("curate: migration -> gen legacy", seed)

    def test_seed_no_curate_line_without_genend(self):
        out, _cmd = TestLaunch.dry(self)
        seed = read(os.path.join(self.root, ".run", "seed.md"))
        self.assertNotIn("curate:", seed)


# ------------------------------------------------------------------------- #
class TestCurateAgentFile(ToolCase):
    """T9: the memory-curator agent file's frontmatter parses."""

    def test_agent_frontmatter(self):
        agent_path = os.path.join(os.path.dirname(TOOLS), "agents",
                                  "memory-curator.md")
        self.assertTrue(os.path.isfile(agent_path), agent_path)
        text = read(agent_path)
        # frontmatter is between --- markers
        parts = text.split("---")
        self.assertGreaterEqual(len(parts), 3, "no frontmatter delimiters")
        fm = parts[1]
        self.assertIn("name: memory-curator", fm)
        self.assertIn("model: claude-fable-5-1[1m]", fm)
        self.assertIn("effort: medium", fm)
        self.assertIn("omitClaudeMd: true", fm)
        self.assertNotIn("claude-opus-4-6[1m]", fm)    # 3.10.6 T2: the ladder owns tier models
        self.assertIn("cacheTtl: 5m", fm)
        # body is under 60 lines
        body = "---".join(parts[2:])
        self.assertLessEqual(len(body.strip().split("\n")), 60,
                             "agent body exceeds 60 lines")


class TestPhaseEndAudit(ToolCase):
    """T10: ``phaseend_index.py assemble`` writes ``## Audit``, ``lint`` passes."""

    def _close_phase(self):
        """Mark remaining tasks done/superseded, populate summaries and write RECAP."""
        # Patch the plan: mark T3.1 done, T4 superseded, T5 superseded
        plan_path = self.plan
        text = open(plan_path, "r", encoding="utf-8").read()
        text = text.replace("T3.1 | next", "T3.1 | done")
        text = text.replace("T4 | queued", "T4 | superseded")
        text = text.replace("T5 | blocked", "T5 | superseded")
        with open(plan_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        for tid in ("T1", "T2", "T3.1"):
            self.make_summary(tid, status="done")
        with open(os.path.join(self.root, "phase-ends", "current", "RECAP.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("MILESTONE: green\n\n## Recap\nAll done.\n\n"
                     "## Decisions that still bind\n"
                     "- the gate is the milestone; no manual checks.\n")

    def test_assemble_writes_audit_section(self):
        self._close_phase()
        self.tool("phaseend_index.py", "assemble", "24")
        pe = os.path.join(self.root, "phase-ends", "PhaseEnd_Phase24.md")
        self.assertTrue(os.path.isfile(pe), "PhaseEnd not created")
        with open(pe, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Audit", text)
        # Audit should come after Research and before Agent runs
        audit_pos = text.index("## Audit")
        research_pos = text.index("## Research")
        runs_pos = text.index("## Agent runs")
        self.assertGreater(audit_pos, research_pos)
        self.assertLess(audit_pos, runs_pos)

    def test_assemble_then_lint_passes(self):
        self._close_phase()
        self.tool("phaseend_index.py", "assemble", "24")
        self.tool("phaseend_index.py", "assemble", "24")
        p = self.tool("phaseend_index.py", "lint")
        self.ok(p, "OK")


class TestPhaseEndLintFile(ToolCase):
    """D5: ``phaseend_index.py lint --file PATH`` checks a standalone file."""

    def test_good_file_passes(self):
        path = os.path.join(self.root, "good.md")
        write(path, "# PhaseEnd -- Phase 23.5: PA3 migration\n\n"
                     "## Milestone\nMigration complete.\n\n"
                     "## Tasks\n- move files\n\n"
                     "## Plain-English Recap\nAll moved.\n")
        p = self.tool("phaseend_index.py", "lint", "--file", path)
        self.ok(p, "OK")

    def test_placeholder_markers_fail(self):
        path = os.path.join(self.root, "bad.md")
        write(path, "# PhaseEnd\n\n## Milestone\n{{AUTHORED:recap}}\n\n"
                     "## Plain-English Recap\ndone\n")
        p = self.tool("phaseend_index.py", "lint", "--file", path)
        self.refused(p, "placeholder marker")

    def test_template_marker_fails(self):
        path = os.path.join(self.root, "tmpl.md")
        write(path, "# PhaseEnd\n\n## Milestone\n<one line: what this is>\n\n"
                     "## Plain-English Recap\ndone\n")
        p = self.tool("phaseend_index.py", "lint", "--file", path)
        self.refused(p, "template marker")

    def test_missing_section_fails(self):
        path = os.path.join(self.root, "nosec.md")
        write(path, "# PhaseEnd\n\n## Milestone\nDone.\n")
        p = self.tool("phaseend_index.py", "lint", "--file", path)
        self.refused(p, "missing ## Plain-English Recap")

    def test_placeholder_word_fails(self):
        path = os.path.join(self.root, "ph.md")
        write(path, "# PhaseEnd\n\n## Milestone\nPLACEHOLDER here\n\n"
                     "## Plain-English Recap\ndone\n")
        p = self.tool("phaseend_index.py", "lint", "--file", path)
        self.refused(p, "PLACEHOLDER")


class TestAdoptPending(ToolCase):

    def _pending(self, name, title, task="T1", agent="retriever-code", tags="a,b",
                 answer="The answer.\n"):
        d = os.path.join(self.cur, "research", "pending")
        os.makedirs(d, exist_ok=True)
        text = ("# %s\ntask: %s\nagent: %s\ntags: %s\n\n"
                "## Answer\n%s\n## Findings\nfound things\n\n## Dead ends\nnone\n"
                "sources:\n- file:1\n" % (title, task, agent, tags, answer))
        write(os.path.join(d, name), text)

    def test_adopt_pending(self):
        self._pending("retriever-code-some-question.md", "some question")
        out = self.ok(self.tool("research_add.py", "adopt", "--task", "T1"))
        self.assertIn("adopted:", out)
        self.assertIn("R24-001", out)
        self.assertIn("retriever-code-some-question.md", out)
        # The R<N>-<nnn>.md file exists with correct header and body verbatim
        rpath = os.path.join(self.cur, "research", "R24-001.md")
        self.assertTrue(os.path.isfile(rpath))
        content = read(rpath)
        self.assertIn("# R24-001", content)
        self.assertIn("The answer.", content)
        self.assertIn("## Findings", content)
        # The INDEX line was appended
        index = read(os.path.join(self.cur, "research", "INDEX.md"))
        self.assertIn("R24-001 |", index)
        self.assertIn("some question", index)
        # The pending file is gone
        self.assertFalse(os.path.isfile(
            os.path.join(self.cur, "research", "pending",
                         "retriever-code-some-question.md")))

    def test_adopt_twice_noop(self):
        self._pending("retriever-code-q.md", "a question")
        self.ok(self.tool("research_add.py", "adopt"))
        # Second adopt: no pending files, no output, exit 0
        p = self.tool("research_add.py", "adopt")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.out.strip(), "")


class TestRetrieverFilesCut(ToolCase):

    AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "agents")

    def _read_agent(self, name):
        path = os.path.join(self.AGENTS_DIR, name)
        return read(path)

    def _body(self, text):
        """The text after the closing --- of YAML frontmatter."""
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else text.strip()

    def test_retriever_code(self):
        text = self._read_agent("retriever-code.md")
        body = self._body(text)
        self.assertNotIn("Bash", text)
        self.assertLess(len(body), 900)  # was 700: 3.9.5 T4 adds the warmer's "." line and the 20th-call line
        self.assertIn("NOT FOUND:", body)
        self.assertIn("REPORT: pending/", body)
        self.assertIn("tools: Read, Grep, Glob, Write", text)

    def test_retriever_digest(self):
        text = self._read_agent("retriever-digest.md")
        body = self._body(text)
        self.assertNotIn("Bash", text)
        self.assertLess(len(body), 900)  # was 700: 3.9.5 T4 adds the warmer's "." line and the 20th-call line
        self.assertIn("NOT FOUND:", body)
        self.assertIn("REPORT: pending/", body)
        self.assertIn("tools: Read, Grep, Glob, Write", text)

    def test_retriever_web(self):
        text = self._read_agent("retriever-web.md")
        body = self._body(text)
        self.assertNotIn("Bash", text)
        self.assertLess(len(body), 900)  # was 700: 3.9.5 T4 adds the warmer's "." line and the 20th-call line
        self.assertIn("NOT FOUND:", body)
        self.assertIn("REPORT: pending/", body)
        self.assertIn("tools: WebSearch, WebFetch, Write", text)

    def test_planner_phase_effort_rule(self):
        # 3.9.5 T16 (developer 2026-09-24): the packaged body carries the high-mark rule
        body = self._body(self._read_agent("planner-phase.md"))
        rule = ("Mark `effort: high` only when the task's done-when rests on a judgment no test can "
                "arbitrate (a design decision, a harness probe, a proof read from evidence), never for "
                "size or importance; at most one task in five per phase plan; the Rationale names the "
                "judgment for each high mark.")
        self.assertIn(rule, " ".join(body.split()))


if __name__ == "__main__":
    unittest.main()
