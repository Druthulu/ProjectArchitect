"""Unit tests for the two phase-end scripts, tools/phaseend_index.py and tools/genend_index.py.

Same throw-away project tree as test_tools.py (ToolCase: .claude/pa.json, phase-ends/current/,
the PHASE_PLAN fixture, a two-phase GENERATION_PLAN).

    python -m unittest discover -s project-architect-3.0/tests -p test_indexes.py
"""

import os
import shutil
import sys
import unittest

try:
    from test_tools import PKG, ToolCase, read, write
except ImportError:                      # python -m unittest tests.test_indexes
    from tests.test_tools import PKG, ToolCase, read, write

if PKG not in sys.path:
    sys.path.insert(0, PKG)

GIT = shutil.which("git")
RECAP = ("# Phase 24 recap\n\n## Recap\nFour biomes now build.\nThe bake is next.\n"
         "\n## Decisions that still bind\n- the gate is the milestone\n")
GEN_RECAP = RECAP + "\n## Generation Recap\nThe generation shipped four biomes.\n"


class ClosedPhaseCase(ToolCase):
    """The fixture phase brought to its end: T1/T2 done, T3..T5 superseded, summaries written."""

    def close_the_phase(self):
        for tid, st in (("T3.1", "superseded"), ("T4", "superseded"),
                        ("T5", "superseded")):
            self.tool("plan_edit.py", "set-status", tid, st)
        self.make_summary("T1", title="mask builder for four biomes")
        self.make_summary("T2", title="blend weights", commit="bbb2222")
        write(os.path.join(self.cur, "tasks", "phase-end.md"),
              "# phase-end — Phase 24 close\n"
              "Status: done | expert: expert-fable | ctx-at-completion: 60k "
              "| commit: fff0001 | coder runs: —\n"
              "Done: gate re-run, recap authored.\n"
              "Files: phase-ends/current/RECAP.md\n"
              "Decisions:\n"
              "- binding: the gate is the milestone; no manual checks.\n"
              "- local: the bake waits for T4\n"
              "Gotchas:\n"
              "- harness: lint wants the literal MILESTONE: green in a Verified: line, not here\n"
              "Verified: MILESTONE: green — python -m unittest discover tests -> OK\n"
              "Full log: phase-ends/current/logs/phase-end.md\n")
        write(os.path.join(self.cur, "logs", "phase-end.md"), "# phase-end log\n")
        write(os.path.join(self.cur, "research", "INDEX.md"),
              "# Research reports -- this phase\n"
              "R24-001 | T1 | mask seam classes | masks | retriever-digest "
              "| 2026-09-12 | 210 lines\n")
        write(os.path.join(self.cur, "tasks", "INDEX.md"),
              "# Task summaries -- this phase\n"
              "T1 | done | mask builder for four biomes | masks | tasks/T1.md "
              "| logs/T1.md | R24-001\n"
              "T2 | done | blend weights | blend | tasks/T2.md | logs/T2.md | —\n")

    @property
    def phase_end(self):
        return os.path.join(self.root, "phase-ends", "PhaseEnd_Phase24.md")

    @property
    def recap(self):
        return os.path.join(self.cur, "RECAP.md")


# ------------------------------------------------------------------------- #
class TestPhaseEnd(ClosedPhaseCase):

    def test_assemble_then_fill_from_recap(self):
        self.close_the_phase()
        out = self.ok(self.tool("phaseend_index.py", "assemble", "24"),
                      "phase-ends/PhaseEnd_Phase24.md")
        self.assertLessEqual(len(out.rstrip().split("\n")), 40)
        text = read(self.phase_end)
        self.assertIn("# PhaseEnd — Phase 24: Ground-Texture Expansion "
                      "(implements Gen 3.4)", text)
        self.assertIn("Tasks: 2 done, 4 superseded", text)
        self.assertIn("Confirmed by the developer at the close.", text)     # 3.25 T4: no closer
        self.assertIn("- phase-end — Phase 24 close | Done: gate re-run", text)
        self.assertIn("- T1 — mask builder for four biomes | Done: the thing works "
                      "| commit: abc1234 | tasks/T1.md | logs/T1.md", text)
        self.assertIn("- phase-end — the gate is the milestone; no manual checks.", text)   # a bulleted binding line
        self.assertNotIn("- phase-end — local:", text)
        self.assertIn("- T1 — masks are per biome, never per chunk", text)   # rules
        self.assertIn("## Rules proposed", text)
        self.assertIn("C0187 | mask cache reset", text)
        self.assertNotIn("C0188", text)                    # other phase
        self.assertIn("R24-001 | T1 | mask seam classes", text)
        self.assertIn("- T3 | superseded", text)
        self.assertNotIn("{{AUTHORED:", text)                # 3.25 T4: `-`, never a placeholder
        self.assertIn("## Plain-English Recap\n-\n", text)
        self.assertIn("2026-09-12 planner: plan approved", text)   # Changes copied
        again = read(self.phase_end)
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        self.assertEqual(again, read(self.phase_end))      # idempotent

        write(self.recap, RECAP)
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        text = read(self.phase_end)
        self.assertNotIn("{{AUTHORED:", text)
        self.assertIn("Four biomes now build.", text)
        self.assertIn("- the gate is the milestone", text)

        os.remove(self.recap)                              # a later run keeps the filled blocks
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        text = read(self.phase_end)
        self.assertNotIn("{{AUTHORED:", text)
        self.assertIn("Four biomes now build.", text)
        self.assertIn("- the gate is the milestone", text)

    @unittest.skipUnless(GIT, "git not on PATH")
    def test_archive_moves_current_and_extends_the_indexes(self):
        self.close_the_phase()
        write(self.recap, "## Recap\nDone.\n\n## Decisions that still bind\n- gate is the gate\n")
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        self.git_init()
        out = self.ok(self.tool("phaseend_index.py", "--archive"),
                      "archived:", "phase-ends/phase-24")
        # output ≤ 5 lines plus RUN: lines; main lines come before notes
        info_lines = [l for l in out.strip().split("\n")
                      if l.strip() and not l.startswith("RUN:")]
        self.assertLessEqual(len(info_lines), 5)
        self.assertIn("gen-close 3.4", out)
        archived = os.path.join(self.root, "phase-ends", "phase-24")
        self.assertTrue(os.path.isdir(archived))
        for name in ("PHASE_PLAN.md", "RECAP.md", "tasks/T1.md", "logs/T1.md",
                     "research/INDEX.md"):
            self.assertTrue(os.path.isfile(os.path.join(archived, name)), name)
        self.assertFalse(os.path.isfile(self.plan))         # fresh current/
        self.assertTrue(os.path.isfile(os.path.join(self.cur, "tasks", "INDEX.md")))
        self.assertEqual(os.listdir(os.path.join(self.cur, "logs")), [])
        ti = read(os.path.join(self.root, "phase-ends", "TASK_INDEX.md"))
        self.assertIn("24 | T1 | done | mask builder for four biomes | masks "
                      "| phase-24/tasks/T1.md | phase-24/logs/T1.md | R24-001", ti)
        ri = read(os.path.join(self.root, "phase-ends", "RESEARCH_INDEX.md"))
        self.assertIn("24 | R24-001 | T1 | mask seam classes", ri)
        self.assertIn("phase-24/research/R24-001.md", ri)
        self.refused(self.tool("phaseend_index.py", "--archive"), "nothing to archive")

    @unittest.skipUnless(GIT, "git not on PATH")
    def test_archive_refuses_until_assembled_and_needs_no_recap(self):
        """3.25 T4: the placeholder refusal is gone; archive with no RECAP.md archives."""
        self.close_the_phase()
        self.git_init()
        self.refused(self.tool("phaseend_index.py", "--archive"), "assemble 24 first")
        self.assertTrue(os.path.isfile(self.plan))          # nothing moved
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        self.ok(self.tool("phaseend_index.py", "--archive"), "archived:")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "phase-ends", "phase-24")))

    def test_agent_runs_section_is_generated_from_the_ledger(self):
        """3.1 T11: one line per ledger run stamped with this phase; never a crash without a ledger."""
        from pa import db

        ledger = os.path.join(self.root, "ledger")
        os.makedirs(ledger)
        conn = db.connect(os.path.join(ledger, "ledger.sqlite"))
        db.init_schema(conn)
        # 3.24 T17i: only this project's runs are picked (by the session's project)
        db.upsert_session(conn, {"session_id": "s1", "project": self.root})
        db.upsert_session(conn, {"session_id": "s2", "project": self.root})
        db.upsert_agent_run(conn, {
            "run_id": "a1", "session_id": "s1", "kind": "expert", "agent_type": "expert-fable",
            "task_id": "T1", "phase": "24", "model_seen": "claude-fable-5-1", "effort": "medium",
            "ctx_at_end": 142000, "cost_usd": 0.412, "status": "completed",
            "started": "2026-09-13T10:00:00Z"})
        db.upsert_agent_run(conn, {
            "run_id": "r1", "session_id": "s1", "kind": "retriever", "agent_type": "retriever-code",
            "parent_run_id": "a1", "task_id": "T1", "phase": "24",
            "model_seen": "claude-haiku-4-5", "ctx_at_end": 27617, "cost_usd": 0.085,
            "status": "completed", "started": "2026-09-13T10:05:00Z"})
        db.upsert_agent_run(conn, {
            "run_id": "z1", "session_id": "s2", "kind": "expert", "agent_type": "expert-elsewhere",
            "task_id": "T9", "phase": "23", "status": "completed",
            "started": "2026-09-13T11:00:00Z"})
        db.upsert_agent_run(conn, {
            "run_id": "s1", "session_id": "s1", "kind": "router", "agent_type": "pa-session",
            "phase": "24", "status": "completed", "started": "2026-09-13T09:00:00Z"})
        db.upsert_savings(conn, {"run_id": "a1", "session_id": "s1", "phase": "24",
                                 "measured_saved_usd": 1.3, "locked": 1})
        db.close(conn)
        self.close_the_phase()
        out = self.ok(self.tool("phaseend_index.py", "assemble", "24",
                                env={"PA_LEDGER_DIR": ledger}), "runs: 2")
        text = read(self.phase_end)
        self.assertIn("## Agent runs", text)
        self.assertIn("- T1 | expert-fable | expert | claude-fable-5-1 | medium | ctx 142k "
                      "| $0.412 | saved $1.30 | completed | parent -", text)
        self.assertIn("- T1 | retriever-code | retriever | claude-haiku-4-5 | - | ctx 28k "
                      "| $0.085 | saved - | completed | parent a1", text)
        self.assertNotIn("expert-elsewhere", text)
        self.assertNotIn("| router |", text)                       # main sessions never appear
        self.ok(self.tool("phaseend_index.py", "assemble", "24",
                          env={"PA_LEDGER_DIR": os.path.join(self.root, "nowhere")}))
        self.assertIn("- (no ledger at", read(self.phase_end))

    def test_gotchas_lists_tagged_lines_with_file_and_line(self):
        """3.1 T17: task_log.py gotchas gathers every tagged line; --kind narrows."""
        self.close_the_phase()
        out = self.ok(self.tool("task_log.py", "gotchas"))
        self.assertRegex(out, r"tasks/T1\.md:\d+ generalizable: clear the mask cache between biomes\.")
        self.assertRegex(out, r"tasks/T2\.md:\d+ binding: masks are built per biome, never per chunk\.")
        self.assertRegex(out, r"tasks/phase-end\.md:\d+ binding: the gate is the milestone")
        self.assertRegex(out, r"tasks/phase-end\.md:\d+ harness: lint wants the literal")
        self.assertIn("6 line(s) in 3 summaries", out)
        out = self.ok(self.tool("task_log.py", "gotchas", "--kind", "generalizable"))
        self.assertNotIn("binding:", out)
        self.assertIn("2 line(s)", out)
        p = self.tool("task_log.py", "gotchas", "--kind", "bogus")
        self.assertNotEqual(p.returncode, 0)

    def test_legacy_index_sorts_naturally(self):
        pdir = os.path.join(self.root, "phase-ends")
        for label, title in (("9", "Terrain seams"), ("9.5", "Migration"),
                             ("10", "Canopy")):
            write(os.path.join(pdir, "PhaseEnd_Phase%s.md" % label),
                  "# PhaseEnd Phase %s — %s\nMilestone: the gate is green\n"
                  "Date: 2026-0%s-01\n" % (label, title, label[0]))
        out = self.ok(self.tool("phaseend_index.py", "legacy-index"),
                      "LEGACY_INDEX.md (3 entries)")
        self.assertLessEqual(len(out.rstrip().split("\n")), 40)
        rows = [x for x in read(os.path.join(pdir, "LEGACY_INDEX.md")).split("\n")
                if x and not x.startswith("#")]
        self.assertEqual([r.split(" | ")[0] for r in rows], ["9", "9.5", "10"])
        self.assertIn("Terrain seams", rows[0])
        self.assertIn("the gate is green", rows[0])
        self.assertIn("phase-ends/PhaseEnd_Phase9.md", rows[0])


# ------------------------------------------------------------------------- #
class TestGenEnd(ClosedPhaseCase):

    def test_assemble_from_phase_ends(self):
        pdir = os.path.join(self.root, "phase-ends")
        write(os.path.join(pdir, "PhaseEnd_Phase24.md"),
              "# PhaseEnd -- Phase 24: Ground-Texture Expansion\n\n"
              "## Decisions that still bind\n- the gate is the milestone\n"
              "- masks are per biome\n\n## Deferred\n- T5 | blocked — bake report\n")
        write(os.path.join(pdir, "PhaseEnd_Phase25.md"),
              "# PhaseEnd -- Phase 25: Generation close\n\n"
              "## Decisions that still bind\n- the gate is the milestone\n\n"
              "## Deferred\n- (none)\n")
        out = self.ok(self.tool("genend_index.py", "assemble", "3"),
                      "phase-ends/GenerationEnd_3.md", "placeholder left")
        text = read(os.path.join(pdir, "GenerationEnd_3.md"))
        self.assertIn("# GenerationEnd — Generation 3: Token-economy rebuild", text)
        self.assertIn("- 3.4 | Ground-Texture Expansion | milestone: gate green", text)
        self.assertEqual(text.count("- the gate is the milestone"), 1)  # deduped
        self.assertIn("- masks are per biome", text)
        self.assertIn("- T5 | blocked — bake report", text)
        self.assertIn("{{AUTHORED:recap}}", text)

        write(self.recap, "## Generation Recap\nThe generation shipped four biomes.\n")
        self.ok(self.tool("genend_index.py", "assemble", "3"))
        text = read(os.path.join(pdir, "GenerationEnd_3.md"))
        self.assertNotIn("{{AUTHORED:", text)
        self.assertIn("The generation shipped four biomes.", text)
        self.ok(self.tool("plan_edit.py", "gen-close", "3.4"))
        self.ok(self.tool("plan_edit.py", "gen-close", "3.5"))
        self.ok(self.tool("genend_index.py", "assemble", "3"))

    @unittest.skipUnless(GIT, "git not on PATH")
    def test_generation_recap_survives_the_archive(self):
        """The order the router runs: assemble, archive, gen-close, genend assemble."""
        self.close_the_phase()
        write(self.recap, GEN_RECAP)
        self.ok(self.tool("phaseend_index.py", "assemble", "24"))
        self.git_init()
        self.ok(self.tool("phaseend_index.py", "--archive"), "archived:")
        self.assertFalse(os.path.isfile(self.recap))
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "phase-ends", "phase-24", "RECAP.md")))
        self.ok(self.tool("plan_edit.py", "gen-close", "3.4"))
        out = self.ok(self.tool("genend_index.py", "assemble", "3"))
        self.assertNotIn("placeholder left", out)
        text = read(os.path.join(self.root, "phase-ends", "GenerationEnd_3.md"))
        self.assertNotIn("{{AUTHORED:", text)
        self.assertIn("The generation shipped four biomes.", text)
        self.assertIn("- the gate is the milestone", text)     # from the PhaseEnd


if __name__ == "__main__":
    unittest.main()
