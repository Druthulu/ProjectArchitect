"""launch.py: the --state-json report the mod reads (3.24 T3.6: launch.py starts no session, writes no seed)."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")
FIXTURES = os.path.join(HERE, "fixtures")
PLAN_FIXTURE = os.path.join(FIXTURES, "PHASE_PLAN.example.md")

GENERATION_PLAN = """# Generation 3 -- Token-economy rebuild

## Phases
- 3.4 Ground-Texture Expansion | status: open
- 3.5 Generation close | status: open
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


class SeedAgeTest(unittest.TestCase):
    """--state-json `run_in_flight` carries the age of the transcript and `stale` (was the router seed's line)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-launch-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": "python",
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), GENERATION_PLAN)
        write(os.path.join(self.root, "HOW_WE_WORK.md"), "# How we work\n")
        write(os.path.join(self.root, ".claude", "skills", "project-architect", "SKILL.md"),
              "---\nname: project-architect\n---\n## 1 Roles\n")
        write(os.path.join(self.root, "cookbook", "INDEX.md"), "# Cookbook\n")
        write(os.path.join(self.root, "rules", "INDEX.md"), "# Rules\n")
        for sub in ("tasks", "logs"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        os.makedirs(os.path.join(self.root, ".run"), exist_ok=True)

        # ledger dir with a db and config
        self.ledger_dir = os.path.join(self.root, "_ledger")
        os.makedirs(self.ledger_dir)
        self.db_path = os.path.join(self.ledger_dir, "usage-ledger.db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_runs(
            run_id TEXT PRIMARY KEY, transcript_path TEXT)""")
        conn.commit()
        self.db_conn = conn

    def _write_status(self, run_id, task="T3.1", killed_at="2026-09-20T22:01:39Z",
                      unstop_cleared=None):
        st = {"task": task, "run_id": run_id, "expert_agent_type": "expert-fable",
              "kind": "task", "attempt": 1, "killed_at": killed_at}
        if unstop_cleared:
            st["unstop_cleared"] = unstop_cleared
        write(os.path.join(self.root, ".run", "status.json"), json.dumps(st))

    def _make_transcript(self, run_id, age_seconds=0):
        tp = os.path.join(self.root, "_transcripts", "agent-%s.jsonl" % run_id)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        write(tp, "{}\n")
        if age_seconds > 0:
            past = time.time() - age_seconds
            os.utime(tp, (past, past))
        self.db_conn.execute(
            "INSERT OR REPLACE INTO agent_runs(run_id, transcript_path) VALUES(?, ?)",
            (run_id, tp))
        self.db_conn.commit()
        return tp

    def _dry(self):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"), "--state-json"]
        env = dict(os.environ)
        env["PA_LEDGER_DIR"] = self.ledger_dir
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return json.loads(p.stdout)["run_in_flight"]

    def test_state_shows_fresh_age(self):
        """A transcript touched 60s ago: 'last record 1m ago', not stale."""
        self._write_status("run-fresh")
        self._make_transcript("run-fresh", age_seconds=60)
        fl = self._dry()
        self.assertEqual(fl["run_id"], "run-fresh")
        self.assertEqual(fl["age_note"], "last record 1m ago")
        self.assertFalse(fl["stale"])

    def test_state_shows_stale_age(self):
        """A transcript touched 10 min ago is stale."""
        self._write_status("run-stale")
        self._make_transcript("run-stale", age_seconds=600)
        fl = self._dry()
        self.assertEqual(fl["run_id"], "run-stale")
        self.assertTrue(fl["stale"])
        self.assertIn("last record", fl["age_note"])


class StateOnlyTest(unittest.TestCase):
    """The preset field; 3.24 T3.6: no launch, seed or launch flags left."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-launch-card-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": sys.executable,
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), GENERATION_PLAN)
        os.makedirs(self.cur, exist_ok=True)
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))

    def _run(self, *args):
        env = dict(os.environ)
        env.pop("PA_PROJECT_ROOT", None)
        return subprocess.run([sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py")] + list(args),
                              cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    def test_state_carries_the_preset(self):
        """max20 when pa.json names none (3.10.6 T4); else pa.json's preset."""
        p = self._run("--state-json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        st = json.loads(p.stdout)
        self.assertEqual((st["mode"], st["preset"]), ("router", "max20"))
        pa = os.path.join(self.root, ".claude", "pa.json")
        conf = json.loads(read(pa))
        conf["preset"] = "pro"
        write(pa, json.dumps(conf, indent=1) + "\n")
        self.assertEqual(json.loads(self._run("--state-json").stdout)["preset"], "pro")

    def test_launch_is_gone(self):
        """3.24 T3.6: a bare call starts nothing and writes nothing; the launch flags left."""
        p = self._run()
        self.assertEqual(p.returncode, 1)
        self.assertIn("starts no session", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".run")))
        for flag in ("--dry-run", "--seed-only", "--mode", "--request"):
            self.assertEqual(self._run(flag).returncode, 2, flag)


class SeedDeferredTest(unittest.TestCase):
    """--state-json `deferred`: one item per due deferral (was the planner seed's Deferred lines)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-launch-def-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": sys.executable,
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), GENERATION_PLAN)
        write(os.path.join(self.root, "HOW_WE_WORK.md"), "# How we work\n")
        write(os.path.join(self.root, ".claude", "skills", "project-architect", "SKILL.md"),
              "---\nname: project-architect\n---\n## 1 Roles\n")
        write(os.path.join(self.root, "cookbook", "INDEX.md"), "# Cookbook\n")
        write(os.path.join(self.root, "rules", "INDEX.md"), "# Rules\n")
        for sub in ("tasks", "logs", "research", "discussions"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".run"), exist_ok=True)
        # a PhaseEnd with deferred lines
        write(os.path.join(self.root, "phase-ends", "PhaseEnd_Phase3.6.md"),
              "# PhaseEnd — Phase 3.6\n\n## Deferred\n"
              "- from T11: nothing\n"
              "- from D1: slice format review → 3.8\n\n## Changes\n- (none)\n")
        # a cumulative discussion index with a deferred record
        write(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"),
              "# Discussions -- cumulative\n"
              "# phase | id | topic | date | status | path\n"
              "3.6 | D1 | card format | 2026-09-20 | deferred:3.8 | phase-3.6/discussions/D1.md\n"
              "3.6 | D2 | seed lines | 2026-09-21 | executed | phase-3.6/discussions/D2.md\n")

    def _state(self):
        """--state-json as a dict (the mod's read; 3.24 T3: the planner seed's Deferred lines left)."""
        env = dict(os.environ)
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"), "--state-json"],
                           cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return json.loads(p.stdout)

    def _deferred(self):
        return {d["id"]: d["text"] for d in self._state()["deferred"]}

    def test_planner_state_has_deferred_item(self):
        """One deferred item per PhaseEnd `from T` line (T3)."""
        # no PHASE_PLAN.md → mode "" (was planner-phase)
        self.assertEqual(self._state()["mode"], "")
        got = self._deferred()
        self.assertEqual(got.get("P3.6-1"), "nothing")
        self.assertNotIn("slice format review", got.values())   # `from D1`, not a `from T` line

    def test_planner_state_has_discussions_deferred(self):
        """A deferred index row is one item; executed rows are not (T3)."""
        got = self._deferred()
        self.assertEqual(got.get("D1"), "card format")    # 3.8 is unknown to the plan -> listed
        self.assertNotIn("D2", got)


class SeedTriageScanTest(SeedDeferredTest):
    """15 phases all listed; deferrals due at or before the planned phase only (T3)."""

    test_planner_state_has_deferred_item = None           # parent's fixture rows are replaced
    test_planner_state_has_discussions_deferred = None

    def setUp(self):
        super().setUp()
        gp = ["# Generation 3", "", "## Phases"]
        for n in range(1, 16):
            gp.append("- 3.%d Phase %d | status: %s" % (n, n, "closed" if n < 5 else "open"))
        write(os.path.join(self.root, "GENERATION_PLAN.md"), "\n".join(gp) + "\n")
        write(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"),
              "# Discussions -- cumulative\n"
              "# phase | id | topic | date | status | path\n"
              "3.4 | D1 | due now | 2026-09-20 | deferred:3.5 | a.md\n"
              "3.2 | D2 | overdue | 2026-09-20 | deferred:3.3 | b.md\n"
              "3.4 | D3 | unassigned | 2026-09-20 | deferred | c.md\n"
              "3.4 | D4 | later | 2026-09-20 | deferred:3.9 | d.md\n"
              "3.4 | D5 | next gen | 2026-09-20 | deferred:gen4 | e.md\n")
        write(os.path.join(self.cur, "discussions", "INDEX.md"),
              "# Discussions -- one line per record\n# id | topic | date | status | path\n"
              "I1 | inbox bullet | 2026-09-21 | deferred:3.5 | INBOX.md\n")

    def test_due_rows_listed_later_rows_not(self):
        got = self._deferred()
        for did, text in (("D1", "due now"), ("D2", "overdue"), ("D3", "unassigned"),
                          ("I1", "inbox bullet")):
            self.assertEqual(got.get(did), text)
        self.assertNotIn("D4", got)
        self.assertNotIn("D5", got)


class SeedPlannerGenTriageTest(SeedDeferredTest):
    """planner-gen (no open phase): every open deferral (T4.c1; 3.24 T3: in --state-json)."""

    test_planner_state_has_deferred_item = None           # parent's fixture rows are replaced
    test_planner_state_has_discussions_deferred = None

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.root, "phase-ends", "PhaseEnd_Phase3.6.md"))
        write(os.path.join(self.root, "GENERATION_PLAN.md"),
              GENERATION_PLAN.replace("status: open", "status: closed"))
        write(os.path.join(self.root, "phase-ends", "DISCUSSION_INDEX.md"),
              "# Discussions -- cumulative\n"
              "# phase | id | topic | date | status | path\n"
              "3.4 | D1 | due now | 2026-09-20 | deferred:3.5 | a.md\n"
              "3.4 | D4 | later | 2026-09-20 | deferred:3.9 | d.md\n"
              "3.4 | D5 | next gen | 2026-09-20 | deferred:gen4 | e.md\n"
              "3.4 | D6 | done | 2026-09-20 | executed | f.md\n")
        write(os.path.join(self.cur, "discussions", "INDEX.md"),
              "# Discussions -- one line per record\n# id | topic | date | status | path\n"
              "I1 | inbox bullet | 2026-09-21 | deferred | INBOX.md\n")

    def test_planner_gen_state_lists_every_open_deferral(self):
        st = self._state()
        self.assertEqual(st["mode"], "planner-gen")
        got = {d["id"]: d["text"] for d in st["deferred"]}
        self.assertEqual(got, {"D1": "due now", "D4": "later", "D5": "next gen", "I1": "inbox bullet"})


class SeedUpgradeTest(SeedDeferredTest):
    """--state-json `upgrade`: `Upgrade: <n> file(s)` when UPGRADE.md lists open conflicts (T8, T15, T20)."""

    def _upgrade(self, text):
        write(os.path.join(self.root, ".claude", "pa3-upgrade", "UPGRADE.md"), text)

    def test_upgrade_line_present(self):
        self._upgrade("# Upgrade\n\n- a.md | base 1 | yours +u1 | upstream 2 | compare: no series"
                      " | edited in: — | default: keep yours\n- b.md | base unknown | yours 0123abcd"
                      " | upstream 3 | compare: no series | edited in: T4 | default: keep yours\n")
        line = self._state()["upgrade"]
        self.assertEqual("Upgrade: 2 file(s) in .claude/pa3-upgrade/UPGRADE.md await a resolution "
                         "(keep | upstream | merged) by an upgrade expert, run as a task before the phase's"
                         " first one; nothing is copied by hand", line)
        self.assertNotIn("planner", line)                # T15: an upgrade expert, never the planner

    def test_upgrade_line_skips_resolved(self):
        """T20: a line carrying ` | resolve:` is not counted; all resolved -> no line."""
        head = "# Upgrade\n\n- a.md | base 1 | default: keep yours | resolve: keep\n"
        self._upgrade(head + "- b.md | base 2 | default: keep yours\n")
        self.assertIn("Upgrade: 1 file(s) in", self._state()["upgrade"])
        self._upgrade(head)
        self.assertIsNone(self._state()["upgrade"])

    def test_upgrade_line_absent_without_file(self):
        self.assertIsNone(self._state()["upgrade"])

    def test_upgrade_line_absent_header_only(self):
        self._upgrade("# Upgrade\n\n")
        self.assertIsNone(self._state()["upgrade"])
        self._upgrade("")
        self.assertIsNone(self._state()["upgrade"])


class SeedCurateTest(SeedDeferredTest):
    """3.14 T2: the migration curate line in any mode until pa.json carries memory_routed."""

    test_planner_state_has_deferred_item = None
    test_planner_state_has_discussions_deferred = None
    MIGRATION = "curate: migration -> gen legacy"

    def _legacy(self):
        write(os.path.join(self.root, "phase-ends", "LEGACY_INDEX.md"),
              "# Legacy index\nold/PhaseEnd_1.md | phase 1\n")
        mem = os.path.join(self.root, ".claude-state", "memory")
        write(os.path.join(mem, "MEMORY.md"), "- [Keep](keep.md) — a kept memory\n")
        write(os.path.join(mem, "keep.md"), "a kept memory\n")

    def _mark(self):
        p = os.path.join(self.root, ".claude", "pa.json")
        conf = json.loads(read(p))
        conf["memory_routed"] = "3.14"
        write(p, json.dumps(conf, indent=2) + "\n")

    def _curate(self, mode):
        st = self._state()
        self.assertEqual(st["mode"], mode)
        return st["curate"]

    def _gen_closed(self):
        write(os.path.join(self.root, "GENERATION_PLAN.md"),
              GENERATION_PLAN.replace("status: open", "status: closed"))

    def test_migration_line_without_marker(self):
        self._legacy()
        self.assertEqual(self.MIGRATION, self._curate(""))

    def test_no_migration_line_with_marker(self):
        self._legacy()
        self._mark()
        self.assertIsNone(self._curate(""))

    def test_migration_line_in_router_mode(self):
        self._legacy()
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        self.assertEqual(self.MIGRATION, self._curate("router"))

    def test_native_project_no_line(self):
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        self.assertIsNone(self._curate("router"))
        self._gen_closed()
        self.assertIsNone(self._curate("planner-gen"))

    def test_planner_gen_closing_line_with_marker(self):
        self._legacy()
        self._mark()
        write(os.path.join(self.root, "phase-ends", "GenerationEnd_3.md"), "# Gen 3 end\n")
        self.assertIsNone(self._curate(""))                # no phase plan (was planner-phase)
        self._gen_closed()
        self.assertEqual("curate: closing gen 3 -> opening gen 4", self._curate("planner-gen"))

    def test_empty_memory_set_no_line(self):
        """3.14 T4: only MEMORY.md and the genlegacy.md archive left -> no migration line."""
        self._legacy()
        mem = os.path.join(self.root, ".claude-state", "memory")
        os.remove(os.path.join(mem, "keep.md"))
        write(os.path.join(mem, "genlegacy.md"), "## old\n\nold\n")
        self.assertIsNone(self._curate(""))

    def test_kept_memories_zero_route_ends_line(self):
        """3.14 T4: all memories kept; the zero-spec legacy route marks pa.json; the line stops."""
        self._legacy()
        self.assertEqual(self.MIGRATION, self._curate(""))
        env = dict(os.environ, PA_PROJECT_ROOT=self.root)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "curate.py"), "memory",
                            "--route", "--gen", "legacy"], cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("memory_routed", read(os.path.join(self.root, ".claude", "pa.json")))
        self.assertIsNone(self._curate(""))


class AuditFlagSortTest(unittest.TestCase):
    """3.14.1: the newest PhaseEnd by the natural rule; an id like 3_5 beside 37.5 raised TypeError."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-launch-pe-")
        sys.path.insert(0, TOOLS)
        import launch
        self.launch = launch

    def tearDown(self):
        sys.path.remove(TOOLS)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mixed_ids_pick_the_newest(self):
        pe = os.path.join(self.root, "phase-ends")
        for pid in ("3_5", "9", "37.5", "37.10", "12a"):
            write(os.path.join(pe, "PhaseEnd_Phase%s.md" % pid), "# PhaseEnd %s\n\n## Audit\n- flag: %s\n" % (pid, pid))
        self.assertEqual(self.launch._audit_flag_line(self.root), "Audit flag: 1 flags in PhaseEnd_Phase37.10.md")


if __name__ == "__main__":
    unittest.main()
