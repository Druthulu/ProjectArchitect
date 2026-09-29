"""Tests for credit note instrumentation in the governance scripts (T2).

Every test runs its subcommand via subprocess in a temp project tree, then
checks that .run/credit/ has the expected note files with the right shape.
"""

import glob
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
Status: {status} | expert: expert-fable | ctx-at-completion: 142k | commit: abc1234 \
| coder runs: c1 opus46 done (def4567)
Done: the thing works
Files: pa/textures.py
Decisions: binding: masks are built per biome, never per chunk.
Deviations: none
Findings: the seam class only shows at biome borders.
Gotchas: generalizable: clear the mask cache between biomes.
Research: R24-001 (mask seams)
Rule candidate: masks are per biome, never per chunk
Next task needs: the blend weights from T2
Recommended: —
Verified: python -m unittest tests.test_masks -> OK (12 tests)
Full log: phase-ends/current/logs/{tid}.md
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


def notes(root):
    """Return all credit note dicts under <root>/.run/credit/."""
    cdir = os.path.join(root, ".run", "credit")
    if not os.path.isdir(cdir):
        return []
    out = []
    for f in sorted(os.listdir(cdir)):
        if f.endswith(".json"):
            with open(os.path.join(cdir, f), encoding="utf-8") as fh:
                out.append(json.load(fh))
    return out


def clear_notes(root):
    cdir = os.path.join(root, ".run", "credit")
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)


class CreditCase(unittest.TestCase):
    """Temp project tree for credit note tests."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-credit-")
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
              "C0187 | mask cache reset | masks | 2026-09-12 | 24/T1 | origin: T1\n")
        write(os.path.join(self.root, "rules", "INDEX.md"),
              "# Rules -- full texts in rules/<id>.md\n"
              "# id | headline | tags | active|superseded-by:<id>|sunset | origin\n"
              "M4 | never push; the developer pushes | git | active | 2.0 seed\n")
        for sub in ("tasks", "logs", "research", "discussions"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        os.makedirs(os.path.join(self.root, ".run"), exist_ok=True)

    def tool(self, script, *args, **kw):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, script)]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env.pop("PA_PROJECT_ROOT", None)
        env.update(kw.get("env") or {})
        p = subprocess.run(cmd, cwd=kw.get("cwd") or self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def make_summary(self, tid, title="a task"):
        write(os.path.join(self.cur, "tasks", "%s.md" % tid), SUMMARY.format(
            tid=tid, title=title, status="done"))
        write(os.path.join(self.cur, "logs", "%s.md" % tid),
              "# %s full log\n\n## Timeline\n- ran the gate\n" % tid)


# ------------------------------------------------------------------ tests -- #

class TestCreditModule(CreditCase):
    """_credit.py note() and CLI."""

    def test_note_writes_json(self):
        """note() creates .run/credit/<ns>-<pid>.json with the right shape."""
        p = self.tool("_credit.py", "note",
                      "--path", os.path.join(self.root, "GENERATION_PLAN.md"),
                      "--script", "test", "--sub", "demo",
                      "--removed", "10", "--added", "20", "--anchor", "5")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 1)
        n = ns[0]
        self.assertEqual(n["kind"], "script")
        self.assertEqual(n["script"], "test")
        self.assertEqual(n["sub"], "demo")
        self.assertEqual(n["removed_chars"], 10)
        self.assertEqual(n["added_chars"], 20)
        self.assertEqual(n["anchor_chars"], 5)
        self.assertFalse(n["whole"])
        self.assertNotIn("\\", n["path"])  # no backslashes
        self.assertIsInstance(n["ts"], float)
        self.assertIsInstance(n["file_chars"], int)

    def test_note_whole_flag(self):
        p = self.tool("_credit.py", "note",
                      "--path", os.path.join(self.root, "HOW_WE_WORK.md"),
                      "--script", "test", "--whole")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 1)
        self.assertTrue(ns[0]["whole"])

    def test_runsh_cli(self):
        p = self.tool("_credit.py", "runsh", "--sub", "gate", "--kept-chars", "500")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 1)
        n = ns[0]
        self.assertEqual(n["kind"], "runsh")
        self.assertEqual(n["script"], "run.sh")
        self.assertEqual(n["sub"], "gate")
        self.assertEqual(n["kept_chars"], 500)

    def test_outline_cli(self):
        p = self.tool("_credit.py", "outline", os.path.join(self.root, "HOW_WE_WORK.md"),
                      "--file-chars", "900", "--cap-chars", "800", "--emitted", "40")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 1)
        n = ns[0]
        self.assertEqual((n["kind"], n["script"], n["path"]), ("outline", "outline.py", "HOW_WE_WORK.md"))
        self.assertEqual((n["file_chars"], n["cap_chars"], n["emitted_chars"]), (900, 800, 40))

    def test_outline_tool_notes(self):
        """outline.py leaves one outline note: cap = the first 2,000 lines, emitted = printed."""
        path = os.path.join(self.root, "big.md")
        write(path, "".join("# h%d\n" % i for i in range(2500)))
        p = self.tool("outline.py", path)
        self.assertEqual(p.returncode, 0, p.out)
        ns = [n for n in notes(self.root) if n["kind"] == "outline"]
        self.assertEqual(len(ns), 1)
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines(True)
        self.assertEqual(ns[0]["cap_chars"], sum(len(l) for l in lines[:2000]))
        self.assertEqual(ns[0]["file_chars"], sum(len(l) for l in lines))
        self.assertEqual(ns[0]["emitted_chars"], len(p.stdout))


class TestPlanEditCredit(CreditCase):
    """plan_edit.py subcommands leave the right credit notes."""

    def test_show_notes_read(self):
        """show (any form) -> read note with removed=added=0."""
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "show")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1, "show should leave a note")
        n = ns[0]
        self.assertEqual(n["script"], "plan_edit.py")
        self.assertEqual(n["sub"], "show")
        self.assertEqual(n["removed_chars"], 0)
        self.assertEqual(n["added_chars"], 0)
        self.assertIn("PHASE_PLAN.md", n["path"])

    def test_show_section_notes_read(self):
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "show", "--section", "Context")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        self.assertEqual(ns[0]["sub"], "show")

    def test_show_task_notes_read(self):
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "show", "--task", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        self.assertEqual(ns[0]["sub"], "show")

    def test_set_status_notes_write(self):
        """set-status -> note with removed/added/anchor > 0."""
        self.make_summary("T3.1", "seam report")
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "set-status", "T3.1", "done", "--by", "expert")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1, "set-status should leave a note")
        n = ns[0]
        self.assertEqual(n["script"], "plan_edit.py")
        self.assertEqual(n["sub"], "set-status")
        self.assertGreater(n["removed_chars"], 0)
        self.assertGreater(n["added_chars"], 0)
        self.assertGreater(n["anchor_chars"], 0)
        self.assertIn("PHASE_PLAN.md", n["path"])

    def test_next_promotes_and_notes(self):
        """next (when it promotes) -> write note."""
        # T3.1 is next, T4 is queued with deps T3.1; set T3.1 done so T4 becomes promotable
        self.make_summary("T3.1", "seam report")
        self.tool("plan_edit.py", "set-status", "T3.1", "done", "--by", "expert")
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "next")
        self.assertEqual(p.returncode, 0, p.out)
        if "PROMOTED" in p.out:
            ns = notes(self.root)
            self.assertTrue(len(ns) >= 1, "next promotion should leave a note")
            n = ns[0]
            self.assertEqual(n["sub"], "next")
            self.assertGreater(n["removed_chars"], 0)
            self.assertGreater(n["added_chars"], 0)

    def test_append_change_notes(self):
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "append-change", "test note", "--by", "router")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        n = ns[0]
        self.assertEqual(n["sub"], "append-change")
        self.assertGreater(n["added_chars"], 0)
        self.assertEqual(n["removed_chars"], 0)

    def test_gen_close_notes(self):
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "gen-close", "3.4", "--by", "router")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        n = ns[0]
        self.assertEqual(n["sub"], "gen-close")
        self.assertGreater(n["removed_chars"], 0)
        self.assertGreater(n["added_chars"], 0)
        self.assertIn("GENERATION_PLAN.md", n["path"])

    def test_lint_leaves_no_note(self):
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "lint")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 0, "lint should leave no note")

    def test_from_draft_leaves_no_note(self):
        draft = os.path.join(self.root, ".run", "draft.md")
        write(draft, read(os.path.join(self.cur, "PHASE_PLAN.md")))
        # remove approval so from-draft accepts it
        text = read(draft)
        text = text.replace("Approved:", "# Approved:")
        write(draft, text)
        # remove the existing plan's approval
        plan = os.path.join(self.cur, "PHASE_PLAN.md")
        pt = read(plan)
        pt = pt.replace("Approved:", "# Approved:")
        write(plan, pt)
        clear_notes(self.root)
        p = self.tool("plan_edit.py", "from-draft", draft)
        ns = notes(self.root)
        self.assertEqual(len(ns), 0, "from-draft should leave no note")


class TestTaskLogCredit(CreditCase):
    """task_log.py finish -> credit note on tasks/INDEX.md."""

    def test_finish_notes_index(self):
        self.make_summary("T1", "mask builder")
        clear_notes(self.root)
        p = self.tool("task_log.py", "finish", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1, "finish should leave a note")
        n = ns[0]
        self.assertEqual(n["script"], "task_log.py")
        self.assertEqual(n["sub"], "finish")
        self.assertGreater(n["added_chars"], 0)
        self.assertIn("INDEX.md", n["path"])

    def test_lint_leaves_no_note(self):
        self.make_summary("T1", "mask builder")
        clear_notes(self.root)
        p = self.tool("task_log.py", "lint", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertEqual(len(ns), 0, "lint should leave no note")


class TestResearchAddCredit(CreditCase):

    def test_new_notes_whole(self):
        clear_notes(self.root)
        p = self.tool("research_add.py", "new",
                      "--task", "T1", "--title", "test", "--agent", "retriever-code")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        n = ns[0]
        self.assertEqual(n["script"], "research_add.py")
        self.assertTrue(n["whole"])

    def test_index_notes_added(self):
        # allocate and write a minimal report
        p = self.tool("research_add.py", "new",
                      "--task", "T1", "--title", "test", "--agent", "retriever-code")
        self.assertEqual(p.returncode, 0, p.out)
        rid = p.out.strip().split("\n")[0]
        rpath = p.out.strip().split("\n")[1]
        abs_rpath = os.path.join(self.root, rpath.replace("/", os.sep))
        header = "# %s — test\ntask: T1 · agent: retriever-code · model: - · date: 2026-01-01 · tags: -\nsources:\n\n## Answer (returned verbatim, <=40 lines)\nfoo\n\n## Findings\n\n## Dead ends\n" % rid
        write(abs_rpath, header)
        clear_notes(self.root)
        p = self.tool("research_add.py", "index", rid)
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        n = ns[0]
        self.assertEqual(n["script"], "research_add.py")
        self.assertEqual(n["sub"], "index")
        self.assertGreater(n["added_chars"], 0)
        self.assertIn("INDEX.md", n["path"])


class TestRulesAddCredit(CreditCase):

    def test_add_notes_whole_and_index(self):
        clear_notes(self.root)
        p = self.tool("rules_add.py", "add", "--id", "R99", "--title", "test rule",
                      "--tags", "test", "--origin", "T2")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 2, "add should note the rule file and the index")
        wholes = [n for n in ns if n["whole"]]
        indexes = [n for n in ns if "INDEX.md" in n["path"]]
        self.assertTrue(len(wholes) >= 1, "rule file should be whole")
        self.assertTrue(len(indexes) >= 1, "index should be noted")

    def test_supersede_notes_both(self):
        # first add a rule to supersede
        self.tool("rules_add.py", "add", "--id", "R88", "--title", "old",
                  "--tags", "test", "--origin", "T1")
        self.tool("rules_add.py", "add", "--id", "R89", "--title", "new",
                  "--tags", "test", "--origin", "T2")
        clear_notes(self.root)
        p = self.tool("rules_add.py", "supersede", "R88", "--by", "R89")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1, "supersede should leave notes")

    def test_promote_notes_skill(self):
        self.tool("rules_add.py", "add", "--id", "R77", "--title", "test promote",
                  "--tags", "test", "--origin", "T2")
        clear_notes(self.root)
        p = self.tool("rules_add.py", "promote", "R77")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 1)
        n = ns[0]
        self.assertEqual(n["sub"], "promote")
        self.assertGreater(n["added_chars"], 0)


class TestSkillAddCredit(CreditCase):

    def test_add_notes_whole_and_hww(self):
        clear_notes(self.root)
        p = self.tool("skill_add.py", "test-skill",
                      "--description", "a test skill")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 2, "skill_add should note skill file and HOW_WE_WORK")
        wholes = [n for n in ns if n["whole"]]
        self.assertTrue(len(wholes) >= 1, "skill file should be whole")
        hww = [n for n in ns if "HOW_WE_WORK" in n["path"]]
        self.assertTrue(len(hww) >= 1, "HOW_WE_WORK should be noted")


class TestDiscussionCredit(CreditCase):

    def test_off_new_record_notes(self):
        # turn on first
        self.tool("discussion.py", "on", "--topic", "test")
        clear_notes(self.root)
        p = self.tool("discussion.py", "off", "--new-record")
        self.assertEqual(p.returncode, 0, p.out)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 2, "off --new-record should note record and index")
        wholes = [n for n in ns if n["whole"]]
        self.assertTrue(len(wholes) >= 1, "record should be whole")
        indexes = [n for n in ns if "INDEX.md" in n["path"]]
        self.assertTrue(len(indexes) >= 1, "index should be noted")


@unittest.skipUnless(BASH, "bash not on PATH")
class TestCookbookAddCredit(CreditCase):

    def test_cookbook_add_notes(self):
        body = os.path.join(self.root, ".run", "body.md")
        write(body, "This is the body.\n")
        clear_notes(self.root)
        p = subprocess.run(
            [BASH, os.path.join(TOOLS, "cookbook_add.sh"),
             "--title", "test entry", "--tags", "test", "--file", body],
            cwd=self.root, capture_output=True, text=True,
            env={**os.environ, "PA_SKIP_PREFLIGHT": "1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ns = notes(self.root)
        self.assertTrue(len(ns) >= 2, "cookbook_add should note entry and index")
        wholes = [n for n in ns if n["whole"]]
        self.assertTrue(len(wholes) >= 1, "entry should be whole")
        indexes = [n for n in ns if n.get("added_chars", 0) > 0 and not n["whole"]]
        self.assertTrue(len(indexes) >= 1, "index line should have added_chars")


@unittest.skipUnless(BASH, "bash not on PATH")
class TestRunShCredit(CreditCase):

    def test_runsh_notes_kept_chars(self):
        """run.sh -> runsh note with kept_chars = log - tail."""
        clear_notes(self.root)
        p = subprocess.run(
            [BASH, os.path.join(TOOLS, "run.sh"), "credit-test", "--",
             sys.executable, "-c",
             "for i in range(100): print('line', i)"],
            cwd=self.root, capture_output=True, text=True,
            env={**os.environ, "PA_SKIP_PREFLIGHT": "1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ns = notes(self.root)
        runsh = [n for n in ns if n["kind"] == "runsh"]
        self.assertTrue(len(runsh) >= 1, "run.sh should leave a runsh note")
        n = runsh[0]
        self.assertEqual(n["script"], "run.sh")
        self.assertEqual(n["sub"], "credit-test")
        # 100 lines printed, tail shows at most 39 -> kept_chars > 0
        self.assertGreater(n["kept_chars"], 0)


if __name__ == "__main__":
    unittest.main()
