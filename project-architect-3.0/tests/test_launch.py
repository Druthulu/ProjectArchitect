"""launch.py seed: the age annotation and stale marker on Run in flight (T4.c2)."""

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
    """The seed's Run in flight line carries the age of the transcript and 'stale'."""

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
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"), "--dry-run"]
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env["PA_LEDGER_DIR"] = self.ledger_dir
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return read(os.path.join(self.root, ".run", "seed.md"))

    def test_seed_shows_fresh_age(self):
        """A transcript touched 60s ago shows '(last record 1m ago)' -- not stale."""
        self._write_status("run-fresh")
        self._make_transcript("run-fresh", age_seconds=60)
        seed = self._dry()
        self.assertIn("Run in flight: run-fresh", seed)
        self.assertIn("(last record 1m ago)", seed)
        self.assertNotIn("stale", seed)

    def test_seed_shows_stale_age(self):
        """A transcript touched 10 min ago shows 'stale'."""
        self._write_status("run-stale")
        self._make_transcript("run-stale", age_seconds=600)
        seed = self._dry()
        self.assertIn("Run in flight: run-stale", seed)
        self.assertIn("stale", seed)
        self.assertIn("last record", seed)

    def test_seed_unstop_cleared_note(self):
        """When unstop_cleared is set, the seed shows the clearing note."""
        self._write_status("run-unstop", unstop_cleared="run-unstop")
        seed = self._dry()
        self.assertIn("stoppedByUser cleared by the resume hook", seed)


class SeedCardSliceTest(unittest.TestCase):
    """The router seed prints the card slice inline under ## Card."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-launch-card-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cur = os.path.join(self.root, "phase-ends", "current")
        write(os.path.join(self.root, ".claude", "pa.json"), json.dumps({
            "pa_version": "3.0.0", "project": "demo", "python": sys.executable,
            "phase_ends_dir": "phase-ends"}, indent=1) + "\n")
        write(os.path.join(self.root, "GENERATION_PLAN.md"), GENERATION_PLAN)
        write(os.path.join(self.root, "HOW_WE_WORK.md"),
              "# How we work\n\n"
              "## Tools <!-- roles: expert coder router planner -->\nSome tools.\n\n"
              "## Developer <!-- roles: router planner -->\nDev.\n")
        write(os.path.join(self.root, ".claude", "skills", "project-architect", "SKILL.md"),
              "---\nname: project-architect\n---\n## 1 Roles\n")
        write(os.path.join(self.root, "cookbook", "INDEX.md"), "# Cookbook\n")
        write(os.path.join(self.root, "rules", "INDEX.md"), "# Rules\n")
        for sub in ("tasks", "logs", "research", "discussions"):
            os.makedirs(os.path.join(self.cur, sub), exist_ok=True)
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        os.makedirs(os.path.join(self.root, ".run"), exist_ok=True)

    def _dry(self, extra_args=None, env_extra=None):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"),
               "--dry-run"]
        if extra_args:
            cmd += extra_args
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env.pop("PA_PROJECT_ROOT", None)
        env.update(env_extra or {})
        p = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return read(os.path.join(self.root, ".run", "seed.md"))

    def test_seed_carries_the_preset_line(self):
        """Right after the head: max20 when pa.json names none; pro has no hard rung (3.10.6 T4)."""
        seed = self._dry()
        self.assertIn("tools/commit_task.sh\n\nPreset: max20 (hard rung: expert-fable)\n", seed)
        pa = os.path.join(self.root, ".claude", "pa.json")
        conf = json.loads(read(pa))
        conf["preset"] = "pro"
        write(pa, json.dumps(conf, indent=1) + "\n")
        seed = self._dry()
        self.assertIn("tools/commit_task.sh\n\nPreset: pro (hard rung: none)\n", seed)
        self.assertNotIn("Preset: max20", seed)

    def test_router_seed_has_card_slice(self):
        """The router seed contains ## Card with the slice inline."""
        seed = self._dry()
        self.assertIn("## Card", seed)
        # should contain something from the card (the Tools section)
        self.assertIn("Tools", seed)

    def test_router_seed_files_line_names_card_py(self):
        """The Files: line names card.py slice router."""
        seed = self._dry()
        self.assertIn("tools/card.py slice router", seed)

    def test_router_seed_arm_line_carries_the_registry_sid(self):
        """3.9.5 T6: the Arm now: line names the sid from ~/.claude/sessions/<CLAUDE_PID>.json."""
        home = os.path.join(self.root, ".run", "home")
        write(os.path.join(home, ".claude", "sessions", "4242.json"),
              json.dumps({"pid": 4242, "sessionId": "sid-abc"}))
        seed = self._dry(env_extra={"CLAUDE_PID": "4242", "HOME": home, "USERPROFILE": home})
        self.assertIn("Arm now: Monitor on `tail -n0 -F .run/warmer/sid-abc.wake` (timeout 30 min)",
                      seed)
        self.assertLess(seed.index("Arm now:"), seed.index("## Tasks"))

    def test_router_seed_arm_line_falls_back_without_a_sid(self):
        home = os.path.join(self.root, ".run", "home")
        seed = self._dry(env_extra={"CLAUDE_PID": "4242", "HOME": home, "USERPROFILE": home})
        self.assertIn("Arm now: Monitor on `tail -n0 -F .run/warmer/<sid>.wake`", seed)
        self.assertIn("(sid: .run/status.json router_session or the newest .run/warmer/*.pid)", seed)

    def _seed_only(self, sid=None):
        """Run --seed-only with a HOME whose registry names ``sid`` (none when None)."""
        home = os.path.join(self.root, ".run", "home-%s" % (sid or "none"))
        if sid:
            write(os.path.join(home, ".claude", "sessions", "4242.json"),
                  json.dumps({"pid": 4242, "sessionId": sid}))
        env = dict(os.environ)
        env.update({"PA_SKIP_PREFLIGHT": "1", "CLAUDE_PID": "4242", "HOME": home,
                    "USERPROFILE": home})
        env.pop("PA_PROJECT_ROOT", None)
        p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"),
                            "--seed-only"], cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return p.stdout

    def _status(self):
        return json.loads(read(os.path.join(self.root, ".run", "status.json")))

    def test_seed_only_stamps_router_session(self):
        """3.10 T28: seed-only in router mode stamps router_session from the registry sid."""
        out = self._seed_only("sid-one")
        self.assertIn("mode=router\nrouter_session=sid-one\n", out)
        st = self._status()
        self.assertEqual(st["router_session"], "sid-one")
        self.assertTrue(st["updated"].endswith("Z"))

    def test_seed_only_relaunch_takes_ownership(self):
        """A second seed-only with another sid replaces the stamp; other keys survive."""
        write(os.path.join(self.root, ".run", "status.json"),
              json.dumps({"task": "T3.1", "killed_at": "x", "router_session": "sid-old"}))
        self._seed_only("sid-one")
        self._seed_only("sid-two")
        st = self._status()
        self.assertEqual(st["router_session"], "sid-two")
        self.assertEqual((st["task"], st["killed_at"]), ("T3.1", "x"))

    def test_seed_only_without_sid_leaves_the_key(self):
        """No registry sid (and no warmer pid): router_session stays as it was."""
        write(os.path.join(self.root, ".run", "status.json"),
              json.dumps({"router_session": "sid-old"}))
        out = self._seed_only(None)
        self.assertNotIn("router_session=", out)
        self.assertEqual(self._status(), {"router_session": "sid-old"})


class SeedDeferredTest(unittest.TestCase):
    """A planner seed carries one `Deferred: <id> <text>` line per due item."""

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

    def _dry(self, extra_args=None, env_extra=None):
        cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "launch.py"),
               "--dry-run"]
        if extra_args:
            cmd += extra_args
        env = dict(os.environ)
        env["PA_SKIP_PREFLIGHT"] = "1"
        env.pop("PA_PROJECT_ROOT", None)
        env.update(env_extra or {})
        p = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        return read(os.path.join(self.root, ".run", "seed.md"))

    def test_planner_seed_has_deferred_line(self):
        """A planner seed carries one `Deferred: <id> <text>` per PhaseEnd `from T` line (T3)."""
        # no PHASE_PLAN.md → planner-phase mode
        seed = self._dry()
        self.assertIn("Deferred: P3.6-1 nothing\n", seed)
        self.assertNotIn("slice format review", seed)       # `from D1`, not a `from T` line
        self.assertNotIn("lines in", seed)

    def test_planner_seed_has_discussions_deferred(self):
        """A deferred index row is one `Deferred: <id> <topic>` line; executed rows are not (T3)."""
        seed = self._dry()
        self.assertIn("Deferred: D1 card format\n", seed)    # 3.8 is unknown to the plan -> listed
        self.assertNotIn("D2", seed)
        self.assertNotIn("Discussions deferred:", seed)


class SeedTriageScanTest(SeedDeferredTest):
    """15 phases all listed; deferrals due at or before the planned phase only (T3)."""

    test_planner_seed_has_deferred_line = None           # parent's fixture rows are replaced
    test_planner_seed_has_discussions_deferred = None

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

    def test_all_phases_listed(self):
        seed = self._dry()
        block = seed.split("## Generation phases\n")[1]
        self.assertEqual(sum(1 for l in block.split("\n") if l.startswith("- 3.")), 15)
        self.assertIn("- 3.15 Phase 15 | status: open", seed)

    def test_due_rows_listed_later_rows_not(self):
        seed = self._dry()
        for line in ("Deferred: D1 due now", "Deferred: D2 overdue", "Deferred: D3 unassigned",
                     "Deferred: I1 inbox bullet"):
            self.assertIn(line + "\n", seed)
        self.assertNotIn("D4", seed)
        self.assertNotIn("D5", seed)


class SeedPlannerGenTriageTest(SeedDeferredTest):
    """planner-gen seed: `Triage: <n> deferred items` then every open deferral (T4.c1)."""

    test_planner_seed_has_deferred_line = None           # parent's fixture rows are replaced
    test_planner_seed_has_discussions_deferred = None

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.root, "phase-ends", "PhaseEnd_Phase3.6.md"))
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

    def test_planner_gen_seed_has_triage_line(self):
        seed = self._dry(["--mode", "planner-gen"])
        self.assertIn("Triage: 4 deferred items\n", seed)
        for line in ("Deferred: D1 due now", "Deferred: D4 later", "Deferred: D5 next gen",
                     "Deferred: I1 inbox bullet"):
            self.assertIn(line + "\n", seed)
            self.assertLess(seed.index("Triage:"), seed.index(line))
        self.assertNotIn("D6", seed)

    def test_planner_phase_seed_has_no_triage_line(self):
        seed = self._dry(["--mode", "planner-phase"])
        self.assertNotIn("Triage:", seed)

    def test_agent_files_carry_the_triage_step(self):
        sess = read(os.path.join(PKG, "agents", "pa-session.md"))
        sec = sess.split("## Mode planner-gen / planner-phase")[1].split("\n## ")[0]
        for key in ("Triage", "TRIAGED", "discussion.py triage"):
            self.assertIn(key, sec)
        self.assertIn("TRIAGED", read(os.path.join(PKG, "agents", "planner-gen.md")))


class SeedUpgradeTest(SeedDeferredTest):
    """A seed carries `Upgrade: <n> file(s)` when UPGRADE.md lists open conflicts (T8, T15, T20)."""

    def _upgrade(self, text):
        write(os.path.join(self.root, ".claude", "pa3-upgrade", "UPGRADE.md"), text)

    def test_upgrade_line_present(self):
        self._upgrade("# Upgrade\n\n- a.md | base 1 | yours +u1 | upstream 2 | compare: no series"
                      " | edited in: — | default: keep yours\n- b.md | base unknown | yours 0123abcd"
                      " | upstream 3 | compare: no series | edited in: T4 | default: keep yours\n")
        seed = self._dry()
        self.assertIn("Upgrade: 2 file(s) in .claude/pa3-upgrade/UPGRADE.md await a resolution "
                      "(keep | upstream | merged) by an upgrade expert; the router spawns it before"
                      " the first task; nothing is copied by hand", seed)
        line = [ln for ln in seed.splitlines() if ln.startswith("Upgrade:")][0]
        self.assertNotIn("planner", line)                # T15: an upgrade expert, never the planner

    def test_upgrade_line_skips_resolved(self):
        """T20: a line carrying ` | resolve:` is not counted; all resolved -> no line."""
        head = "# Upgrade\n\n- a.md | base 1 | default: keep yours | resolve: keep\n"
        self._upgrade(head + "- b.md | base 2 | default: keep yours\n")
        self.assertIn("Upgrade: 1 file(s) in", self._dry())
        self._upgrade(head)
        self.assertNotIn("Upgrade:", self._dry())

    def test_upgrade_line_absent_without_file(self):
        self.assertNotIn("Upgrade:", self._dry())

    def test_upgrade_line_absent_header_only(self):
        self._upgrade("# Upgrade\n\n")
        self.assertNotIn("Upgrade:", self._dry())
        self._upgrade("")
        self.assertNotIn("Upgrade:", self._dry())


class SeedCurateTest(SeedDeferredTest):
    """3.14 T2: the migration curate line in any mode until pa.json carries memory_routed."""

    test_planner_seed_has_deferred_line = None
    test_planner_seed_has_discussions_deferred = None
    MIGRATION = "curate: migration -> gen legacy\n"

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

    def test_migration_line_without_marker(self):
        self._legacy()
        self.assertIn(self.MIGRATION, self._dry())

    def test_no_migration_line_with_marker(self):
        self._legacy()
        self._mark()
        self.assertNotIn("curate:", self._dry())

    def test_migration_line_in_router_mode(self):
        self._legacy()
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        seed = self._dry(["--mode", "router"])
        self.assertIn("Loop from here", seed)
        self.assertIn(self.MIGRATION, seed)

    def test_native_project_no_line(self):
        shutil.copyfile(PLAN_FIXTURE, os.path.join(self.cur, "PHASE_PLAN.md"))
        self.assertNotIn("curate:", self._dry(["--mode", "router"]))
        self.assertNotIn("curate:", self._dry(["--mode", "planner-gen"]))

    def test_planner_gen_closing_line_with_marker(self):
        self._legacy()
        self._mark()
        write(os.path.join(self.root, "phase-ends", "GenerationEnd_3.md"), "# Gen 3 end\n")
        self.assertIn("curate: closing gen 3 -> opening gen 4\n", self._dry(["--mode", "planner-gen"]))
        self.assertNotIn("curate:", self._dry(["--mode", "planner-phase"]))

    def test_empty_memory_set_no_line(self):
        """3.14 T4: only MEMORY.md and the genlegacy.md archive left -> no migration line."""
        self._legacy()
        mem = os.path.join(self.root, ".claude-state", "memory")
        os.remove(os.path.join(mem, "keep.md"))
        write(os.path.join(mem, "genlegacy.md"), "## old\n\nold\n")
        self.assertNotIn("curate:", self._dry())

    def test_kept_memories_zero_route_ends_line(self):
        """3.14 T4: all memories kept; the zero-spec legacy route marks pa.json; the line stops."""
        self._legacy()
        self.assertIn(self.MIGRATION, self._dry())
        env = dict(os.environ, PA_PROJECT_ROOT=self.root)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "curate.py"), "memory",
                            "--route", "--gen", "legacy"], cwd=self.root, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("memory_routed", read(os.path.join(self.root, ".claude", "pa.json")))
        self.assertNotIn("curate:", self._dry())


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
