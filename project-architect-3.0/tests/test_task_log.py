"""Unit tests for task_log.py assemble/review (T7).

A fixture project (temp dir with .claude/pa.json, templates copied) and a fixture
ledger under PA_LEDGER_DIR verify the assembly and review commands.

    cd project-architect-3.0 && python -m unittest tests.test_task_log -v
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")
TEMPLATES = os.path.join(PKG, "templates")


def write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


SUMMARY_WITH_REVIEW = """\
# T1 — mask builder
Status: done | expert: expert-fable | ctx-at-completion: 100k | commit: abc1234 | coder runs: c1 opus46 done (def456)
Done: masks build
Files: pa/masks.py
Decisions: none
Deviations: none
Findings: none
Gotchas: none
Research: R0-001 (masks)
Next task needs: nothing
Verified: python -m unittest -> OK
Review:
  ran: the gate on localhost
  results: .run/logs/gate.log
  seen: masks build and pass
  decisions:
  - accept the merge — Recommended: merge now
  edits:
  - plan_edit.py set-status T1 done
Full log: phase-ends/current/logs/T1.md
"""


class TestTaskLogAssemble(unittest.TestCase):
    """Assembly from a fixture ledger with two agent_runs of T1."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-tl-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        # Copy templates
        tmpl_dir = os.path.join(self.root, "templates")
        for name in ("log.template.md", "REVIEW.template.md", "task.template.md"):
            src = os.path.join(TEMPLATES, name)
            if os.path.isfile(src):
                write(os.path.join(tmpl_dir, name), read(src))
        for sub in ("tasks", "logs", "research"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".run", "logs"), exist_ok=True)
        # Plan header: phase id and Approved: date bound the Timeline query
        write(os.path.join(self.cur, "PHASE_PLAN.md"),
              "# Phase 7 — demo phase\n"
              "Approved: 2026-09-20   Planner: -\n")
        # Fixture ledger with two agent_runs of T1, plus one dated before the
        # Approved date and one tagged with another phase (neither should
        # reach the Timeline)
        self.ledger_dir = tempfile.mkdtemp(prefix="pa3-ledger-")
        self.addCleanup(shutil.rmtree, self.ledger_dir, ignore_errors=True)
        db = os.path.join(self.ledger_dir, "ledger.sqlite")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE agent_runs("
            "run_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, phase TEXT, "
            "agent_type TEXT, model_seen TEXT, model_pinned TEXT, "
            "started TEXT, ended TEXT, status TEXT, kind TEXT)")
        conn.execute(
            "INSERT INTO agent_runs VALUES"
            "('r1','s1','T1',NULL,'expert-fable','claude-opus-4-6','',"
            "'2026-09-20T10:00:00Z','2026-09-20T10:30:00Z','done','task')")
        conn.execute(
            "INSERT INTO agent_runs VALUES"
            "('r2','s1','T1',NULL,'coder-opus46','claude-opus-4-6','',"
            "'2026-09-20T10:05:00Z','2026-09-20T10:25:00Z','done','task')")
        conn.execute(  # before the Approved date -- excluded by the date bound
            "INSERT INTO agent_runs VALUES"
            "('r3','s1','T1',NULL,'coder-opus46','claude-opus-4-6','',"
            "'2026-09-19T10:00:00Z','2026-09-19T10:20:00Z','done','task')")
        conn.execute(  # tagged with another phase -- excluded by the phase tag
            "INSERT INTO agent_runs VALUES"
            "('r4','s1','T1','9','coder-opus46','claude-opus-4-6','',"
            "'2026-09-20T11:00:00Z','2026-09-20T11:20:00Z','done','task')")
        conn.commit()
        conn.close()
        # Coder log
        write(os.path.join(self.cur, "logs", "T1.c1.md"),
              "# T1.c1 coder log\n\nCHANGED: pa/masks.py:1-10\n"
              "VERIFIED: gate -> OK\n")
        # Research index
        write(os.path.join(self.cur, "research", "INDEX.md"),
              "# Research reports -- this phase\n"
              "# id | task | title | tags | agent | date | lines\n"
              "R0-001 | T1 | mask seams | masks | retriever-digest"
              " | 2026-09-20 | 36 lines\n")

    # -- helpers ---------------------------------------------------------- #
    def tool(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "task_log.py")]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        env["PA_LEDGER_DIR"] = self.ledger_dir
        p = subprocess.run(cmd, cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    # -- assemble --------------------------------------------------------- #
    def test_assemble_yields_timeline_coders_research_placeholders(self):
        """Timeline has header plus two data rows (three rows total, one per
        agent_run plus a column-name header)."""
        p = self.tool("assemble", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        log = read(os.path.join(self.cur, "logs", "T1.md"))
        self.assertIn("<!-- assembled by task_log.py -->", log)
        # Timeline: header plus two data rows = three rows
        timeline = []
        in_tl = False
        for line in log.split("\n"):
            if line.strip() == "## Timeline":
                in_tl = True
                continue
            if in_tl:
                if line.startswith("## "):
                    break
                if line.startswith("- "):
                    timeline.append(line)
        self.assertEqual(len(timeline), 3,
                         "Timeline: header plus two data rows")
        self.assertIn("agent_type", timeline[0])         # header row
        self.assertIn("expert-fable", timeline[1])
        self.assertIn("coder-opus46", timeline[2])
        # r3 (before Approved:) and r4 (tagged another phase) stay out
        self.assertNotIn("2026-09-19T10:00:00Z", log)
        self.assertNotIn("2026-09-20T11:00:00Z", log)
        # Coder brief (file name and content)
        self.assertIn("T1.c1.md", log)
        self.assertIn("CHANGED: pa/masks.py", log)
        # Research
        self.assertIn("R0-001", log)
        self.assertIn("mask seams", log)
        # Placeholders
        self.assertIn("{{AUTHORED:hypotheses}}", log)
        self.assertIn("{{AUTHORED:state}}", log)

    def test_assemble_refuses_markerless_log(self):
        """assemble refuses to overwrite a hand-written log without the marker."""
        write(os.path.join(self.cur, "logs", "T1.md"),
              "# T1 full log\n\n## Timeline\n- ran the gate\n")
        p = self.tool("assemble", "T1")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("marker", p.out.lower())

    def test_assemble_commands_run_bounded_to_phase_and_task_prefix(self):
        """Commands run lists only this task's .run/logs names, at/after Approved:
        (T7.c3: used to list every .run/logs/*.log when there were no agent_runs rows)."""
        import datetime as _dt
        logs_dir = os.path.join(self.root, ".run", "logs")
        approved_ts = _dt.datetime(2026, 9, 20, tzinfo=_dt.timezone.utc).timestamp()
        for name in ("t6-unit.log", "t6c2-tools.log", "t1-gate.log", "t16-x.log"):
            write(os.path.join(logs_dir, name), "log\n")
            os.utime(os.path.join(logs_dir, name), (approved_ts + 3600, approved_ts + 3600))
        old_path = os.path.join(logs_dir, "t6-old.log")
        write(old_path, "log\n")
        os.utime(old_path, (approved_ts - 3600, approved_ts - 3600))
        p = self.tool("assemble", "T6")
        self.assertEqual(p.returncode, 0, p.out)
        log = read(os.path.join(self.cur, "logs", "T6.md"))
        cmd_lines = []
        in_cmd = False
        for line in log.split("\n"):
            if line.strip() == "## Commands run":
                in_cmd = True
                continue
            if in_cmd:
                if line.startswith("## "):
                    break
                if line.startswith("- "):
                    cmd_lines.append(line)
        self.assertEqual(cmd_lines, ["- t6-unit.log", "- t6c2-tools.log"])

    def test_assemble_preserves_authored_on_reassemble(self):
        """Re-assembling keeps the authored blocks' text."""
        self.tool("assemble", "T1")
        log_path = os.path.join(self.cur, "logs", "T1.md")
        log = read(log_path)
        log = log.replace("{{AUTHORED:hypotheses}}", "- tried X — failed")
        log = log.replace("{{AUTHORED:state}}", "- masks work")
        write(log_path, log)
        # Re-assemble
        self.tool("assemble", "T1")
        log2 = read(log_path)
        self.assertIn("- tried X — failed", log2)
        self.assertIn("- masks work", log2)
        self.assertNotIn("{{AUTHORED:", log2)

    # -- finish with placeholder check ------------------------------------ #
    def test_finish_fails_with_placeholder(self):
        """finish refuses while {{AUTHORED:...}} placeholders remain."""
        self.tool("assemble", "T1")
        write(os.path.join(self.cur, "tasks", "T1.md"), SUMMARY_WITH_REVIEW)
        p = self.tool("finish", "T1")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("AUTHORED", p.out)

    def test_finish_passes_when_filled(self):
        """finish passes once both authored blocks are filled."""
        self.tool("assemble", "T1")
        log_path = os.path.join(self.cur, "logs", "T1.md")
        log = read(log_path)
        log = log.replace("{{AUTHORED:hypotheses}}", "- tried X — failed")
        log = log.replace("{{AUTHORED:state}}", "- masks work on all biomes")
        write(log_path, log)
        write(os.path.join(self.cur, "tasks", "T1.md"), SUMMARY_WITH_REVIEW)
        p = self.tool("finish", "T1")
        self.assertEqual(p.returncode, 0, p.out)

    # -- review ----------------------------------------------------------- #
    def test_review_yields_five_fields(self):
        """A summary with a Review: block yields REVIEW.md with five fields."""
        write(os.path.join(self.cur, "tasks", "T1.md"), SUMMARY_WITH_REVIEW)
        write(os.path.join(self.cur, "PHASE_PLAN.md"),
              "# Phase 24 — Ground-Texture Expansion\n"
              "Approved: 2026-09-12\n")
        p = self.tool("review", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        review = read(os.path.join(self.cur, "REVIEW.md"))
        self.assertIn("Phase 24", review)
        self.assertIn("the gate on localhost", review)               # ran
        self.assertIn(".run/logs/gate.log", review)                  # results
        self.assertIn("masks build and pass", review)                # seen
        self.assertIn("accept the merge", review)                    # decisions
        self.assertIn("plan_edit.py set-status T1 done", review)     # edits

    def test_review_refuses_without_review_block(self):
        """review refuses when the summary has no Review: block."""
        write(os.path.join(self.cur, "tasks", "T1.md"),
              "# T1 — mask builder\nStatus: done\nDone: masks\n"
              "Files: pa/masks.py\nVerified: ok\n"
              "Full log: phase-ends/current/logs/T1.md\n")
        p = self.tool("review", "T1")
        self.assertEqual(p.returncode, 1, p.out)
        self.assertIn("Review:", p.out)


class TestFinishAdoptsPending(unittest.TestCase):
    """finish T<n> adopts pending research reports first."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-tl-adopt-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        for sub in ("tasks", "logs", "research"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        write(os.path.join(self.cur, "PHASE_PLAN.md"),
              "# Phase 7 — demo phase\nApproved: 2026-09-20   Planner: -\n")
        # A valid summary and log so finish can pass
        write(os.path.join(self.cur, "tasks", "T1.md"),
              "# T1 — mask builder\nStatus: done | commit: abc1234\n"
              "Done: masks\nFiles: pa/masks.py\nVerified: ok\n"
              "Full log: phase-ends/current/logs/T1.md\n")
        write(os.path.join(self.cur, "logs", "T1.md"),
              "# T1 full log\n## Timeline\n- ran\n")
        # A pending research report
        pdir = os.path.join(self.cur, "research", "pending")
        os.makedirs(pdir, exist_ok=True)
        write(os.path.join(pdir, "retriever-code-sig-of-decide.md"),
              "# sig of decide\ntask: T1\nagent: retriever-code\ntags: guard\n\n"
              "## Answer\n`guard.py:42 def decide(…)`\n\n## Findings\nfound\n\n"
              "## Dead ends\nnone\nsources:\n- pa/guard.py:42\n")

    def tool(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "task_log.py")]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        p = subprocess.run(cmd, cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def test_finish_adopts_pending_first(self):
        p = self.tool("finish", "T1")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("adopted:", p.out)
        self.assertIn("retriever-code-sig-of-decide.md", p.out)
        # The pending file should be gone
        self.assertFalse(os.path.isfile(
            os.path.join(self.cur, "research", "pending",
                         "retriever-code-sig-of-decide.md")))
        # The indexed report should exist
        idx = read(os.path.join(self.cur, "research", "INDEX.md"))
        self.assertIn("sig of decide", idx)


class TestGotchasScope(unittest.TestCase):
    """gotchas: current only by default; --all adds archived phases; --phase one."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-tl-got-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        pe = os.path.join(self.root, "phase-ends")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        for sub, word in (("current", "cur"), ("phase-3.8", "old"), ("phase-3.9", "mid")):
            write(os.path.join(pe, sub, "tasks", "T1.md"),
                  "# T1 — x\nGotchas: harness: %s gotcha\n" % word)

    def tool(self, *args):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "task_log.py")]
        cmd += [str(a) for a in args]
        env = dict(os.environ)
        env["PA_PROJECT_ROOT"] = self.root
        p = subprocess.run(cmd, cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        p.out = (p.stdout or "") + (p.stderr or "")
        return p

    def test_default_all_and_phase(self):
        p = self.tool("gotchas")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("cur gotcha", p.out)
        self.assertNotIn("old gotcha", p.out)
        self.assertNotIn("mid gotcha", p.out)
        p = self.tool("gotchas", "--all")
        self.assertEqual(p.returncode, 0, p.out)
        for w in ("cur", "old", "mid"):
            self.assertIn("%s gotcha" % w, p.out)
        self.assertIn("3 line(s) in 3 summaries", p.out)
        p = self.tool("gotchas", "--phase", "3.8")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("old gotcha", p.out)
        self.assertNotIn("cur gotcha", p.out)
        self.assertNotIn("mid gotcha", p.out)
        self.assertNotEqual(self.tool("gotchas", "--phase", "9.9").returncode, 0)
        self.assertNotEqual(self.tool("gotchas", "--all", "--phase", "3.8").returncode, 0)

    def test_help_names_flags(self):
        p = self.tool("gotchas", "--help")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("--all", p.out)
        self.assertIn("--phase", p.out)


if __name__ == "__main__":
    unittest.main()
