"""Tests for pa.audit — the carry audit module."""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

# The package root
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)
FIXTURES = os.path.join(PKG, "tests", "fixtures", "audit")


class TestAudit(unittest.TestCase):
    """Audit against fixture transcripts and a temp ledger."""

    @classmethod
    def setUpClass(cls):
        """Build a temp ledger with sessions, agent_runs, turns, tool_calls."""
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_audit_test_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)

        # Point PA_LEDGER_DIR at our temp ledger
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        # Build a project dir with phase-ends containing the fixture AUDIT.md and PHASE_PLAN.md
        cls._project_dir = os.path.join(cls._tmpdir, "project")
        pe_dir = os.path.join(cls._project_dir, "phase-ends", "phase-3.5")
        os.makedirs(pe_dir)
        shutil.copy(os.path.join(FIXTURES, "AUDIT.md"), pe_dir)
        shutil.copy(os.path.join(FIXTURES, "PHASE_PLAN.md"), pe_dir)

        # Copy transcripts into a projects-dir structure so paths resolve
        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-0001-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)
        os.makedirs(os.path.join(sid_dir, "tool-results"), exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, "main.jsonl"),
                    os.path.join(cls._projects, slug, sid + ".jsonl"))
        shutil.copy(os.path.join(FIXTURES, "agent-sub001.jsonl"),
                    os.path.join(sid_dir, "subagents", "agent-run001.jsonl"))

        # Write a fake spill file on disk
        spill_path = os.path.join(sid_dir, "tool-results", "spill001.txt")
        with open(spill_path, "w") as f:
            f.write("x" * 5000)

        # Create the ledger database
        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "3.5"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")
        c.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, phase)"
                  " VALUES(?, ?, ?, ?)",
                  ("run001", sid, "coder-opus46", "3.5"))

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i in range(10):
            c.execute("INSERT INTO turns(msg_id, session_id, ts, model, kind,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, 'api', 5000, 500, 500, 2000, 500)",
                      ("msg%03d" % i, sid, "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]"))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.execute("INSERT INTO tool_calls(id, session_id, phase, script, kept_tokens, emission_tokens)"
                  " VALUES(?, ?, '3.5', 'mytool', 100, 50)", ("tc1", sid))
        c.execute("INSERT INTO tool_calls(id, session_id, phase, script, kept_tokens, emission_tokens)"
                  " VALUES(?, ?, '3.5', 'mytool', 200, 30)", ("tc2", sid))
        c.commit()

        # Store for tests
        cls._sid = sid
        cls._slug = slug

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _audit(self):
        from pa import audit
        cfg = {
            "audit": {
                "file_chars_flag": 500000,
                "growth_flag": 0.2,
                "noise_patterns": [
                    "warning: in the working copy of",
                    "CRLF will be replaced",
                    "usage: ",
                    "No such file or directory",
                ],
            }
        }
        return audit.audit_phase(
            self._conn, cfg, self._project_dir, "3.5")

    def test_reads_by_file(self):
        result = self._audit()
        reads = dict(result["reads_by_file"])
        # The main transcript has a Read of PHASE_PLAN.md and a Read of the spill file
        plan_key = None
        for fp, _ in result["reads_by_file"]:
            if "PHASE_PLAN.md" in fp:
                plan_key = fp
        self.assertIsNotNone(plan_key, "PHASE_PLAN.md should appear in reads")
        self.assertGreater(reads[plan_key]["n"], 0)

    def test_writes_by_file(self):
        result = self._audit()
        writes = dict(result["writes_by_file"])
        # The Write of /proj/out.py
        found = any("/proj/out.py" in fp or "out.py" in fp for fp in writes)
        self.assertTrue(found, "out.py should appear in writes: %s" % list(writes.keys()))

    def test_results_by_kind(self):
        result = self._audit()
        kinds = dict(result["results_by_kind"])
        self.assertIn("Read", kinds)
        self.assertIn("run.sh", kinds)

    def test_whole_plan_reads(self):
        result = self._audit()
        wp = result["whole_plan_reads"]
        # One Read of PHASE_PLAN.md + one plan_edit.py show without section = 2
        self.assertGreaterEqual(wp["n"], 2)

    def test_spilled(self):
        result = self._audit()
        sp = result["spilled"]
        self.assertEqual(sp["n"], 1)
        self.assertEqual(sp["read_whole"], 1)
        # chars should be the disk size of the spill file (5000)
        self.assertEqual(sp["chars"], 5000)

    def test_noise(self):
        result = self._audit()
        noise = result["noise"]
        self.assertTrue(len(noise) > 0, "noise patterns should match")
        matched = any("warning: in the working copy of" in pat for pat in noise)
        self.assertTrue(matched, "git warning should be detected as noise")

    def test_carry_per_request(self):
        result = self._audit()
        cpr = result["carry_per_request"]
        self.assertGreater(cpr["chars_per_request"], 0)
        self.assertIn("read", cpr["prices"])
        self.assertIn("write_1h", cpr["prices"])
        self.assertIn("output", cpr["prices"])

    def test_candidates(self):
        result = self._audit()
        candidates = result["candidates"]
        self.assertTrue(len(candidates) > 0, "should find mytool candidate")
        mytool = [c for c in candidates if c["name"] == "mytool"]
        self.assertEqual(len(mytool), 1)
        self.assertTrue(mytool[0]["ratified"])
        # measured = sum of (kept_tokens + emission_tokens) = (100+50) + (200+30) = 380
        self.assertEqual(mytool[0]["measured"], 380)

    def test_flags(self):
        result = self._audit()
        flags = result["flags"]
        # Should have whole-plan reads flag
        wp_flags = [f for f in flags if "whole-plan reads" in f]
        self.assertTrue(len(wp_flags) > 0, "whole-plan reads flag expected")
        # Should have spilled flag
        sp_flags = [f for f in flags if "spilled" in f]
        self.assertTrue(len(sp_flags) > 0, "spilled flag expected")

    def test_render_md(self):
        from pa import audit
        result = self._audit()
        md = audit.render_md(result)
        lines = md.strip().split("\n")
        self.assertLessEqual(len(lines), 60, "render_md should be <= 60 lines, got %d" % len(lines))
        self.assertTrue(md.startswith("### Carry audit"))

    def test_under_2s(self):
        t0 = time.time()
        self._audit()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 2.0, "audit should complete under 2s, took %.2fs" % elapsed)


def _slug(path):
    """Replicate paths.project_slug."""
    import re
    return re.sub(r"[\\/:. ]", "-", str(path or ""))


class TestPhaseSpanVersions(unittest.TestCase):
    """phase_span picks the earliest approval across PHASE_PLAN*.md files."""

    def setUp(self):
        import sys
        sys.path.insert(0, PKG)
        self.tmpdir = tempfile.mkdtemp(prefix="pa_span_")
        self.root = os.path.join(self.tmpdir, "repo")
        self.phase_dir = os.path.join(self.root, "phase-ends", "phase-2.1")
        os.makedirs(self.phase_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_plan(self, name, approved, hash_val):
        path = os.path.join(self.phase_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Phase 2.1 -- test\n")
            fh.write("Approved: %s   Plan-hash: %s\n" % (approved, hash_val))

    def test_earliest_approved_date_wins(self):
        """When git is unavailable, the earliest Approved: date across versions is t0."""
        self._write_plan("PHASE_PLAN.md", "2026-09-20", "hash_main")
        self._write_plan("PHASE_PLAN.v1.md", "2026-09-18", "hash_v1")
        from pa import phases
        # git pickaxe won't find these fake hashes, so it falls back to Approved dates
        t0, t1 = phases.phase_span(self.root, "2.1")
        self.assertTrue(t0.startswith("2026-09-18"), "t0 should be the v1 date, got %s" % t0)

    def test_phase_span_returns_utc_z(self):
        """phase_span always returns UTC Z strings."""
        self._write_plan("PHASE_PLAN.md", "2026-09-20", "hash_main")
        from pa import phases
        t0, t1 = phases.phase_span(self.root, "2.1")
        self.assertTrue(t0.endswith("Z"), "t0 should end with Z, got %s" % t0)
        self.assertTrue(t1.endswith("Z"), "t1 should end with Z, got %s" % t1)

    def test_to_utc_z_offset(self):
        """_to_utc_z converts offset timestamps to UTC Z."""
        from pa.phases import _to_utc_z
        result = _to_utc_z("2026-09-21T17:26:01-06:00")
        self.assertEqual(result, "2026-09-21T23:26:01Z")

    def test_to_utc_z_already_z(self):
        """_to_utc_z passes through Z strings unchanged."""
        from pa.phases import _to_utc_z
        result = _to_utc_z("2026-09-21T23:26:01Z")
        self.assertEqual(result, "2026-09-21T23:26:01Z")

    def test_to_utc_z_positive_offset(self):
        """_to_utc_z converts positive offset timestamps to UTC Z."""
        from pa.phases import _to_utc_z
        result = _to_utc_z("2026-09-22T02:00:00+05:30")
        self.assertEqual(result, "2026-09-21T20:30:00Z")


class TestSpanFiltering(unittest.TestCase):
    """Records outside the span are excluded from audit counts."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_span_filter_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-span-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)

        # Transcript: 4 tool_use/tool_result pairs, 2 inside span, 2 outside
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")
        records = [
            {"timestamp": "2026-09-19T10:00:00Z", "message": {
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu1", "name": "Read",
                     "input": {"file_path": "/proj/before.py"}}]}},
            {"timestamp": "2026-09-19T10:00:01Z", "message": {
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu1",
                     "content": "B" * 1000}]}},
            {"timestamp": "2026-09-20T12:00:00Z", "message": {
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu2", "name": "Read",
                     "input": {"file_path": "/proj/inside.py"}}]}},
            {"timestamp": "2026-09-20T12:00:01Z", "message": {
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu2",
                     "content": "I" * 2000}]}},
            {"timestamp": "2026-09-20T18:00:00Z", "message": {
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu3", "name": "Read",
                     "input": {"file_path": "/proj/inside2.py"}}]}},
            {"timestamp": "2026-09-20T18:00:01Z", "message": {
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu3",
                     "content": "J" * 500}]}},
            {"timestamp": "2026-09-21T10:00:00Z", "message": {
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu4", "name": "Read",
                     "input": {"file_path": "/proj/after.py"}}]}},
            {"timestamp": "2026-09-21T10:00:01Z", "message": {
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu4",
                     "content": "A" * 3000}]}},
        ]
        with open(transcript_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "4.0"))
        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i, ts in enumerate(["2026-09-19T10:00:00Z", "2026-09-20T12:00:00Z",
                                "2026-09-20T18:00:00Z", "2026-09-21T10:00:00Z"]):
            c.execute("INSERT INTO turns(msg_id, session_id, ts, model, kind,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, 'api', 1000, 100, 100, 500, 200)",
                      ("span_msg%d" % i, sid, ts, "claude-opus-4-6[1m]"))
        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.commit()
        cls._sid = sid

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_span_filters_records(self):
        """Only records inside the span should be counted."""
        from pa import audit
        span = ("2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z")
        result = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                   sids=[self._sid], span=span)
        reads = dict(result["reads_by_file"])
        inside_keys = [k for k in reads if "inside" in k]
        self.assertEqual(len(inside_keys), 2,
                         "two inside reads expected, got %s" % list(reads.keys()))
        outside_keys = [k for k in reads if "before" in k or "after" in k]
        self.assertEqual(len(outside_keys), 0,
                         "outside reads should be excluded, got %s" % list(reads.keys()))

    def test_offset_and_utc_select_same(self):
        """A span in -06:00 and the same span in Z select the same records."""
        from pa import audit
        span_utc = ("2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z")
        span_offset = ("2026-09-19T18:00:00-06:00", "2026-09-20T17:59:59-06:00")
        r_utc = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                  sids=[self._sid], span=span_utc)
        r_off = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                  sids=[self._sid], span=span_offset)
        reads_utc = set(k for k, _ in r_utc["reads_by_file"])
        reads_off = set(k for k, _ in r_off["reads_by_file"])
        self.assertEqual(reads_utc, reads_off,
                         "UTC and offset spans should select the same records")

    def test_n_requests_filtered_by_span(self):
        """carry_per_request.n_requests counts only turns within the span."""
        from pa import audit
        span = ("2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z")
        result = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                   sids=[self._sid], span=span)
        self.assertEqual(result["carry_per_request"]["n_requests"], 2)

    def test_render_md_header_with_span(self):
        """render_md header includes range, sessions, and requests."""
        from pa import audit
        span = ("2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z")
        result = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                   sids=[self._sid], span=span)
        md = audit.render_md(result)
        header = md.split("\n")[0]
        self.assertIn("2026-09-20T00:00:00Z", header)
        self.assertIn("2026-09-20T23:59:59Z", header)
        self.assertIn("1 sessions", header)
        self.assertIn("2 requests", header)

    def test_render_md_open_phase_says_open_and_is_idempotent(self):
        """An open phase (span end None) prints 'open' and renders identically twice."""
        from pa import audit
        span = ("2026-09-20T00:00:00Z", None)
        result = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                   sids=[self._sid], span=span)
        md1 = audit.render_md(result)
        md2 = audit.render_md(result)
        self.assertEqual(md1, md2)
        header = md1.split("\n")[0]
        self.assertIn("2026-09-20T00:00:00Z → open UTC", header)

    def test_render_md_noise_lists_patterns(self):
        """Noise line lists each pattern with chars and count."""
        from pa import audit
        cfg = {"audit": {"noise_patterns": ["IIII", "BBBB"]}}
        result = audit.audit_phase(self._conn, cfg, self._project_dir, "4.0",
                                   sids=[self._sid])
        md = audit.render_md(result)
        noise_lines = [l for l in md.split("\n") if l.startswith("- noise:")]
        self.assertTrue(len(noise_lines) > 0, "should have a noise line")
        self.assertIn("IIII:", noise_lines[0])
        self.assertIn("—", noise_lines[0])  # em dash

    def test_no_span_includes_all(self):
        """Without span, all records are counted (backward compat)."""
        from pa import audit
        result = audit.audit_phase(self._conn, {}, self._project_dir, "4.0",
                                   sids=[self._sid])
        reads = dict(result["reads_by_file"])
        self.assertEqual(len(reads), 4, "all 4 reads expected without span")


class TestAuditT5(unittest.TestCase):
    """T5: spilled command/reader, tool-source paths, noise lines, MCP blocks."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_t5_audit_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-t5-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)
        os.makedirs(os.path.join(sid_dir, "tool-results"), exist_ok=True)

        # Copy the T5 fixture transcript
        import shutil as _shutil
        _shutil.copy(os.path.join(FIXTURES, "t5_main.jsonl"),
                     os.path.join(cls._projects, slug, sid + ".jsonl"))

        # Write a fake spill file on disk
        spill_path = os.path.join(sid_dir, "tool-results", "t5spill.txt")
        with open(spill_path, "w") as f:
            f.write("x" * 3000)

        # Create the ledger database
        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "5.0"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i in range(5):
            c.execute("INSERT INTO turns(msg_id, session_id, ts, model, kind,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, 'api', 5000, 500, 500, 2000, 500)",
                      ("t5msg%03d" % i, sid, "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]"))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.commit()
        cls._sid = sid

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _audit(self):
        from pa import audit
        cfg = {"audit": {
            "noise_patterns": ["warning: in the working copy of"],
        }}
        return audit.audit_phase(self._conn, cfg, self._project_dir, "5.0",
                                 sids=[self._sid])

    def test_spilled_command_and_reader(self):
        """Spilled items carry the producing command and the reader role."""
        result = self._audit()
        sp = result["spilled"]
        self.assertEqual(sp["n"], 1)
        self.assertEqual(sp["read_whole"], 1)
        items = sp["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertTrue(item["read_whole"])
        self.assertIn("cat bigfile.txt", item["command"])
        self.assertEqual(item["reader"], "main")

    def test_tool_source_reads_paths(self):
        """tool_source_reads items list path, chars, and role."""
        result = self._audit()
        ts = result["tool_source_reads"]
        self.assertGreater(ts["n"], 0)
        items = ts.get("items", [])
        self.assertTrue(len(items) > 0, "tool_source_reads should have items")
        found = any("tools/plan_edit.py" in item["path"] for item in items)
        self.assertTrue(found, "plan_edit.py should appear in tool_source_reads items")
        for item in items:
            self.assertIn("path", item)
            self.assertIn("chars", item)
            self.assertIn("role", item)

    def test_noise_by_lines(self):
        """Noise counts matched lines per pattern, not whole results."""
        result = self._audit()
        noise = result["noise"]
        self.assertIn("warning: in the working copy of", noise)
        entry = noise["warning: in the working copy of"]
        # The fixture has 2 lines matching "warning: in the working copy of"
        self.assertEqual(entry["lines"], 2)
        self.assertGreater(entry["chars"], 0)
        # chars should be the sum of the matching lines, not the whole result
        self.assertLess(entry["chars"], 200)

    def test_mcp_blocks(self):
        """MCP instruction blocks are detected with server names."""
        result = self._audit()
        mcp = result["mcp_blocks"]
        self.assertEqual(mcp["n"], 1)
        self.assertGreater(mcp["chars"], 0)
        self.assertIn("filesystem", mcp["servers"])

    def test_render_md_spilled_detail(self):
        """render_md prints command and reader under the spilled flag."""
        from pa import audit
        result = self._audit()
        md = audit.render_md(result)
        flag_lines = [l for l in md.split("\n") if "spilled" in l and "read whole" in l
                      and l.startswith("- flag:")]
        self.assertTrue(len(flag_lines) > 0, "spilled flag should exist")
        # the detail line should follow
        all_lines = md.split("\n")
        idx = all_lines.index(flag_lines[0])
        detail = all_lines[idx + 1]
        self.assertIn("command:", detail)
        self.assertIn("reader:", detail)
        self.assertIn("cat bigfile.txt", detail)

    def test_render_md_noise_lines(self):
        """render_md prints noise as lines per pattern."""
        from pa import audit
        result = self._audit()
        md = audit.render_md(result)
        noise_lines = [l for l in md.split("\n") if l.startswith("- noise:")]
        self.assertTrue(len(noise_lines) > 0)
        self.assertIn("lines", noise_lines[0])

    def test_render_md_mcp_in_results(self):
        """render_md prints the MCP row inside results_by_kind."""
        from pa import audit
        result = self._audit()
        md = audit.render_md(result)
        mcp_lines = [l for l in md.split("\n") if "MCP blocks" in l]
        self.assertTrue(len(mcp_lines) > 0, "MCP row should appear in results")
        self.assertIn("filesystem", mcp_lines[0])


class TestWholeReads(unittest.TestCase):
    """T3: whole_reads — Read without offset/limit over guard.whole_read_chars."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_wr_audit_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-wr-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)

        shutil.copy(os.path.join(FIXTURES, "t3_main.jsonl"),
                    os.path.join(cls._projects, slug, sid + ".jsonl"))

        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "6.0"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i in range(3):
            c.execute("INSERT INTO turns(msg_id, session_id, ts, model, kind,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, 'api', 5000, 500, 500, 2000, 500)",
                      ("wrmsg%03d" % i, sid, "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]"))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.commit()
        cls._sid = sid

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _audit(self):
        from pa import audit
        cfg = {
            "guard": {"whole_read_chars": 20000},
            "audit": {},
        }
        return audit.audit_phase(self._conn, cfg, self._project_dir, "6.0",
                                 sids=[self._sid])

    def test_exactly_one_whole_read_item(self):
        """Only the 25k Read without a range should be flagged."""
        result = self._audit()
        wr = result["whole_reads"]
        self.assertEqual(wr["n"], 1)
        self.assertEqual(len(wr["items"]), 1)
        item = wr["items"][0]
        self.assertIn("big_file.py", item["file"])
        self.assertEqual(item["role"], "main")
        self.assertGreater(item["chars"], 20000)

    def test_whole_reads_flag_lines(self):
        """The flag lists role, file and chars."""
        result = self._audit()
        flags = result["flags"]
        wr_flags = [f for f in flags if "whole reads over" in f]
        self.assertTrue(len(wr_flags) > 0, "whole reads flag expected")
        # detail line follows
        detail = [f for f in flags if f.startswith("  file:")]
        self.assertTrue(len(detail) > 0, "detail lines expected under flag")
        self.assertIn("big_file.py", detail[0])
        self.assertIn("role: main", detail[0])

    def test_18k_not_flagged(self):
        """18k content chars (raw > 20k due to prefixes) should not be flagged."""
        result = self._audit()
        wr = result["whole_reads"]
        for item in wr["items"]:
            self.assertNotIn("medium_file.py", item["file"],
                             "18k content read should not be whole_reads item")


class TestSeedFloor(unittest.TestCase):
    """T3: seed_floor — retriever seed_ctx medians by type."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_sf_audit_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn

        sid = "sess-sf-0001-0001-000000000001"
        prev_sid = "sess-sf-prev-0001-000000000001"

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, phase)"
                  " VALUES(?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, "7.0"))
        c.execute("INSERT INTO sessions(session_id, project, cwd, phase)"
                  " VALUES(?, ?, ?, ?)",
                  (prev_sid, cls._project_dir, cls._project_dir, "6.0"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")

        # Current phase: retriever-code (3 runs), retriever-digest (2 runs)
        for i, sc in enumerate([7000, 7500, 8000]):
            c.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, phase, seed_ctx,"
                      " started) VALUES(?, ?, 'retriever-code', '7.0', ?, ?)",
                      ("rc%d" % i, sid, sc, "2026-09-20T%02d:00:00Z" % i))
        for i, sc in enumerate([10000, 11000]):
            c.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, phase, seed_ctx,"
                      " started) VALUES(?, ?, 'retriever-digest', '7.0', ?, ?)",
                      ("rd%d" % i, sid, sc, "2026-09-20T%02d:00:00Z" % (i + 3)))

        # Previous phase: retriever-code only (2 runs)
        for i, sc in enumerate([7200, 7800]):
            c.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, phase, seed_ctx,"
                      " started) VALUES(?, ?, 'retriever-code', '6.0', ?, ?)",
                      ("prc%d" % i, prev_sid, sc, "2026-09-19T%02d:00:00Z" % i))

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i in range(3):
            c.execute("INSERT INTO turns(msg_id, session_id, ts, model, kind,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, 'api', 5000, 500, 500, 2000, 500)",
                      ("sfmsg%03d" % i, sid, "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]"))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.commit()

        cls._sid = sid
        cls._prev_sid = prev_sid

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _audit(self):
        from pa import audit
        cfg = {"audit": {}}
        return audit.audit_phase(self._conn, cfg, self._project_dir, "7.0",
                                 prev_phase="6.0",
                                 sids=[self._sid], prev_sids=[self._prev_sid])

    def test_seed_floor_medians(self):
        """seed_floor has medians for both retriever types."""
        result = self._audit()
        sf = result["seed_floor"]
        self.assertIn("retriever-code", sf)
        self.assertIn("retriever-digest", sf)
        # retriever-code: [7000, 7500, 8000] -> median 7500
        rc = sf["retriever-code"]
        self.assertEqual(rc["n"], 3)
        self.assertEqual(rc["median"], 7500)
        self.assertEqual(rc["min"], 7000)
        self.assertEqual(rc["max"], 8000)
        # retriever-digest: [10000, 11000] -> median 10500
        rd = sf["retriever-digest"]
        self.assertEqual(rd["n"], 2)
        self.assertEqual(rd["median"], 10500)

    def test_prev_median(self):
        """prev_median present for retriever-code, absent for retriever-digest."""
        result = self._audit()
        sf = result["seed_floor"]
        # retriever-code prev: [7200, 7800] -> median 7500
        self.assertEqual(sf["retriever-code"]["prev_median"], 7500)
        # retriever-digest: no prev phase runs -> dash
        self.assertEqual(sf["retriever-digest"]["prev_median"], "—")


class TestRouter(unittest.TestCase):
    """T3: router — per main session ctx_end, growth, top_kinds."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_rtr_audit_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-rtr-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)

        shutil.copy(os.path.join(FIXTURES, "t3_router.jsonl"),
                    os.path.join(cls._projects, slug, sid + ".jsonl"))

        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "8.0"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")
        # main thread run
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, agent_type, phase)"
                  " VALUES(?, ?, 'other', NULL, '8.0')", (sid, sid))
        # Two expert runs
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, agent_type, task_id,"
                  " phase, started, ended)"
                  " VALUES(?, ?, 'expert', 'expert-fable', 'T1', '8.0',"
                  " '2026-09-20T03:30:00Z', '2026-09-20T04:30:00Z')",
                  ("expert1", sid))
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, agent_type, task_id,"
                  " phase, started, ended)"
                  " VALUES(?, ?, 'expert', 'expert-fable', 'T2', '8.0',"
                  " '2026-09-20T06:30:00Z', '2026-09-20T07:30:00Z')",
                  ("expert2", sid))

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        # 12 turns for the main thread, with ctx values
        # Turns at hours 00-11, main thread (run_id = sid)
        # expert1: started 03:30, ended 04:30
        #   last before 03:30 -> hour 3 (03:00) ctx=30000
        #   first after 04:30 -> hour 5 (05:00) ctx=42000, delta=12000
        # expert2: started 06:30, ended 07:30
        #   last before 06:30 -> hour 6 (06:00) ctx=55000
        #   first after 07:30 -> hour 8 (08:00) ctx=64000, delta=9000
        ctx_values = [
            10000, 15000, 20000,   # hours 0, 1, 2
            30000,                  # hour 3 (last before expert1 03:30)
            35000,                  # hour 4 (during expert1)
            42000,                  # hour 5 (first after expert1 04:30)
            55000,                  # hour 6 (last before expert2 06:30)
            58000,                  # hour 7 (during expert2)
            64000,                  # hour 8 (first after expert2 07:30)
            70000, 75000, 80000,    # hours 9, 10, 11
        ]
        for i, ctx in enumerate(ctx_values):
            c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, model, kind, ctx,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, ?, 'api', ?, 5000, 500, 500, 2000, 500)",
                      ("rtrmsg%03d" % i, sid, sid,
                       "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]", ctx))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        # sid2: a main session whose turns all lie outside the span
        # (tagged for the phase at its start, but nothing inside the window).
        sid2 = "sess-rtr-0002-0002-000000000002"
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, NULL, ?)",
                  (sid2, cls._project_dir, cls._project_dir, "8.0"))
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, agent_type, phase)"
                  " VALUES(?, ?, 'other', NULL, '8.0')", (sid2, sid2))
        c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, model, kind, ctx,"
                  " input, cache_write_5m, cache_write_1h, cache_read, output)"
                  " VALUES(?, ?, ?, ?, ?, 'api', ?, 5000, 500, 500, 2000, 500)",
                  ("rtrmsg-out", sid2, sid2, "2026-09-19T12:00:00Z",
                   "claude-opus-4-6[1m]", 10000))

        # sid3: a headless "bench-x" session with requests inside the span
        # but no spawned agent runs.
        sid3 = "sess-rtr-0003-0003-000000000003"
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, NULL, ?)",
                  (sid3, cls._project_dir, cls._project_dir, "8.0"))
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, agent_type, phase)"
                  " VALUES(?, ?, 'other', 'bench-x', '8.0')", (sid3, sid3))
        c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, model, kind, ctx,"
                  " input, cache_write_5m, cache_write_1h, cache_read, output)"
                  " VALUES(?, ?, ?, ?, ?, 'api', ?, 5000, 500, 500, 2000, 500)",
                  ("rtrmsg-bx", sid3, sid3, "2026-09-20T01:00:00Z",
                   "claude-opus-4-6[1m]", 5000))

        c.commit()
        cls._sid = sid
        cls._sid2 = sid2
        cls._sid3 = sid3
        cls._span = ("2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z")

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _audit(self, router_ctx_flag=300000):
        from pa import audit
        cfg = {"audit": {"router_ctx_flag": router_ctx_flag}}
        return audit.audit_phase(self._conn, cfg, self._project_dir, "8.0",
                                 sids=[self._sid])

    def _audit_span(self, sids):
        from pa import audit
        cfg = {"audit": {"router_ctx_flag": 300000}}
        return audit.audit_phase(self._conn, cfg, self._project_dir, "8.0",
                                 sids=sids, span=self._span)

    def test_ctx_end(self):
        """ctx_end is the last main turn's ctx."""
        result = self._audit()
        router = result["router"]
        self.assertEqual(len(router), 1)
        # last turn ctx = 80000
        self.assertEqual(router[0]["ctx_end"], 80000)

    def test_growth(self):
        """growth entries: T1 and T2 with computed deltas."""
        result = self._audit()
        router = result["router"]
        growth = router[0]["growth"]
        self.assertEqual(len(growth), 2)
        # T1: last before 03:30 = 03:00 ctx=30000
        #     first after 04:30 = 05:00 ctx=42000, delta=12000
        self.assertEqual(growth[0], ("T1", 12000))
        # T2: last before 06:30 = 06:00 ctx=55000
        #     first after 07:30 = 08:00 ctx=64000, delta=9000
        self.assertEqual(growth[1], ("T2", 9000))

    def test_top_kinds_main_only(self):
        """top_kinds from the main transcript only."""
        result = self._audit()
        router = result["router"]
        top = router[0]["top_kinds"]
        # Should have Read, run.sh, bash other, tools/plan_edit.py, Agent, etc.
        kinds = [k for k, _, _ in top]
        self.assertIn("Read", kinds)

    def test_router_ctx_flag(self):
        """router ctx flag fires when ctx_end exceeds threshold."""
        result = self._audit(router_ctx_flag=50000)
        flags = result["flags"]
        ctx_flags = [f for f in flags if "router end context" in f]
        self.assertTrue(len(ctx_flags) > 0, "router ctx flag should fire at 50k")

    def test_router_ctx_flag_no_fire(self):
        """router ctx flag does not fire when below threshold."""
        result = self._audit(router_ctx_flag=300000)
        flags = result["flags"]
        ctx_flags = [f for f in flags if "router end context" in f]
        self.assertEqual(len(ctx_flags), 0, "router ctx flag should not fire at 300k")

    def test_outside_span_session_dropped(self):
        """A main session with turns outside the span only gets no row."""
        result = self._audit_span([self._sid, self._sid2])
        session_ids = [r["session_id"] for r in result["router"]]
        self.assertIn(self._sid, session_ids)
        self.assertNotIn(self._sid2, session_ids)

    def test_bench_session_without_spawn_dropped(self):
        """A bench-x session with requests in span but no agent runs gets no row."""
        result = self._audit_span([self._sid, self._sid3])
        session_ids = [r["session_id"] for r in result["router"]]
        self.assertIn(self._sid, session_ids)
        self.assertNotIn(self._sid3, session_ids)

    def test_pa_session_and_main_still_produce_rows(self):
        """The existing pa-session/main fixture still produces its row with all three sids present."""
        result = self._audit_span([self._sid, self._sid2, self._sid3])
        session_ids = [r["session_id"] for r in result["router"]]
        self.assertEqual(session_ids, [self._sid])


class TestRenderMdOrder(unittest.TestCase):
    """The three new rows appear in order: whole reads, seed floor, router."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_order_audit_")
        cls._ledger_dir = os.path.join(cls._tmpdir, "ledger")
        os.makedirs(cls._ledger_dir)
        os.environ["PA_LEDGER_DIR"] = cls._ledger_dir

        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)

        cls._projects = os.path.join(cls._tmpdir, "projects")
        slug = _slug(cls._project_dir)
        sid = "sess-ord-0001-0001-000000000001"
        sid_dir = os.path.join(cls._projects, slug, sid)
        os.makedirs(os.path.join(sid_dir, "subagents"), exist_ok=True)

        shutil.copy(os.path.join(FIXTURES, "t3_router.jsonl"),
                    os.path.join(cls._projects, slug, sid + ".jsonl"))

        db_path = os.path.join(cls._ledger_dir, "ledger.sqlite")
        cls._conn = sqlite3.connect(db_path)
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        transcript_path = os.path.join(cls._projects, slug, sid + ".jsonl")

        c.execute("CREATE TABLE IF NOT EXISTS sessions("
                  "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
                  "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
                  "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
                  "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
                  "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
        c.execute("INSERT INTO sessions(session_id, project, cwd, transcript_path, phase)"
                  " VALUES(?, ?, ?, ?, ?)",
                  (sid, cls._project_dir, cls._project_dir, transcript_path, "9.0"))

        c.execute("CREATE TABLE IF NOT EXISTS agent_runs("
                  "run_id TEXT PRIMARY KEY, session_id TEXT, parent_run_id TEXT,"
                  "parent_source TEXT, kind TEXT, agent_type TEXT, task_id TEXT,"
                  "phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,"
                  "model_pinned TEXT, model_seen TEXT, effort TEXT,"
                  "started TEXT, ended TEXT, status TEXT,"
                  "turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_read INTEGER, output INTEGER, thinking INTEGER,"
                  "cost_usd REAL, handoff_fired INTEGER DEFAULT 0,"
                  "locked INTEGER DEFAULT 0, lock_source TEXT,"
                  "transcript_path TEXT, transcript_bytes INTEGER)")
        c.execute("INSERT INTO agent_runs(run_id, session_id, kind, phase)"
                  " VALUES(?, ?, 'other', '9.0')", (sid, sid))
        c.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, phase, seed_ctx,"
                  " started) VALUES(?, ?, 'retriever-code', '9.0', 7500, ?)",
                  ("rc-ord", sid, "2026-09-20T00:00:00Z"))

        c.execute("CREATE TABLE IF NOT EXISTS turns("
                  "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
                  "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
                  "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
                  "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
                  "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
                  "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
                  "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
        for i in range(5):
            c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, model, kind, ctx,"
                      " input, cache_write_5m, cache_write_1h, cache_read, output)"
                      " VALUES(?, ?, ?, ?, ?, 'api', ?, 5000, 500, 500, 2000, 500)",
                      ("ordmsg%03d" % i, sid, sid,
                       "2026-09-20T%02d:00:00Z" % i,
                       "claude-opus-4-6[1m]", 10000 + i * 5000))

        c.execute("CREATE TABLE IF NOT EXISTS tool_calls("
                  "id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,"
                  "ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,"
                  "file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,"
                  "kept_tokens INTEGER, model TEXT)")
        c.commit()
        cls._sid = sid

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_render_order(self):
        """whole reads, seed floor, router appear in that order after spilled."""
        from pa import audit
        cfg = {"audit": {}}
        result = audit.audit_phase(self._conn, cfg, self._project_dir, "9.0",
                                   sids=[self._sid])
        md = audit.render_md(result)
        all_lines = md.split("\n")
        wr_idx = sf_idx = rt_idx = None
        for i, line in enumerate(all_lines):
            if line.startswith("- whole reads"):
                wr_idx = i
            elif line.startswith("- seed floor"):
                sf_idx = i
            elif line.startswith("- router:"):
                rt_idx = i
        self.assertIsNotNone(wr_idx, "whole reads line not found in render_md")
        self.assertIsNotNone(sf_idx, "seed floor line not found in render_md")
        self.assertIsNotNone(rt_idx, "router line not found in render_md")
        self.assertLess(wr_idx, sf_idx, "whole reads should come before seed floor")
        self.assertLess(sf_idx, rt_idx, "seed floor should come before router")

    def test_outline_credit_row(self):
        """Two outlines, one followed by a ranged read: 5000 - 100 - 900 tokens = 16.0k chars."""
        from pa import audit
        c = self._conn
        for rid, ts, kind, path, ft, em, rt in (
                ("oc-1", "2026-09-20T01:00:00Z", "outline", "pa/a.py", 5000, 100, 100),
                ("oc-2", "2026-09-20T01:00:05Z", "outline-read", "pa/a.py", 5000, 0, 900),
                ("oc-3", "2026-09-20T02:00:00Z", "outline", "pa/b.py", 3000, 80, 80)):
            c.execute("INSERT INTO tool_calls(id, session_id, run_id, ts, kind, path,"
                      " file_tokens, emission_tokens, result_tokens) VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, self._sid, self._sid, ts, kind, path, ft, em, rt))
        c.commit()
        result = audit.audit_phase(self._conn, {"audit": {}}, self._project_dir, "9.0",
                                   sids=[self._sid])
        md = audit.render_md(result)
        self.assertIn("- outline credit: 2 outlines · 1 followed by a ranged read"
                      " · 16.0k chars credited", md)


class TestWarmPingsToasts(unittest.TestCase):
    """3.9.5 T5: warm pings vs rewrites replaced; sent waiting toasts by cause."""

    MODEL = "claude-fable-5-1"

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_warm_audit_")
        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)
        cls._conn = sqlite3.connect(os.path.join(cls._tmpdir, "ledger.sqlite"))
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        from pa import db
        c.executescript(db.SCHEMA_SQL)                  # the real ledger schema
        sid, rid = "sess-warm-1", "a8cfdc7600000000f"
        cls._sid, cls._sid2, cls._sid3, cls._sid4 = sid, "sess-warm-2", "sess-warm-3", "sess-warm-4"

        def ping(ts, s, r, n=1, cap=None):
            d = {"run_id": r, "session_id": s, "ttl": "5m", "idle_s": 263, "n": n, "via": "monitor"}
            if cap is not None:
                d["cap"] = cap                          # 3.9.5 T8; older rows have none (cap 3)
            c.execute("INSERT INTO events(ts, session_id, run_id, kind, detail_json)"
                      " VALUES(?, ?, ?, 'warm_ping', ?)", (ts, s, r, json.dumps(d)))

        def turn(ts, gap, rw, cost, s=sid, r=rid):
            c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, model, ctx, gap_s,"
                      " rewrite, cost_usd, ttl_assumed) VALUES(?,?,?,?,?,?,?,?,?,'split')",
                      ("m-" + r + ts, r, s, ts, cls.MODEL, 100000, gap, rw, cost))

        turn("2026-09-20T01:00:00Z", 10.0, 0, 0.05)
        ping("2026-09-20T01:04:20Z", sid, rid)
        turn("2026-09-20T01:04:25Z", 265.0, 0, 0.01)     # ping turn
        turn("2026-09-20T01:07:00Z", 155.0, 0, 0.05)     # warmed wait 420 s, rewrite 0
        ping("2026-09-20T01:11:20Z", sid, rid)
        turn("2026-09-20T01:11:25Z", 265.0, 0, 0.01)     # ping turn
        turn("2026-09-20T01:15:00Z", 215.0, 1, 0.40)     # warmed wait 480 s, rewrite 1
        turn("2026-09-20T01:30:00Z", 900.0, 1, 0.40)     # unwarmed long gap: not counted
        ping("2026-09-20T01:40:00Z", sid, rid)
        turn("2026-09-20T01:41:30Z", 90.0, 0, 0.02)      # 90 s after the event: not a ping turn
        # session 2: a ping whose wait stays under the TTL -> w = 0
        ping("2026-09-20T02:04:20Z", cls._sid2, "run-2")
        turn("2026-09-20T02:00:00Z", 10.0, 0, 0.05, cls._sid2, "run-2")
        turn("2026-09-20T02:04:25Z", 200.0, 0, 0.01, cls._sid2, "run-2")
        turn("2026-09-20T02:05:00Z", 35.0, 0, 0.05, cls._sid2, "run-2")
        # session 3 (T8): the 12th ping of 12, then a 635 s gap -> past the cap (m = 1, k = 0)
        turn("2026-09-20T03:00:00Z", 10.0, 0, 0.05, cls._sid3, "run-3")
        ping("2026-09-20T03:04:20Z", cls._sid3, "run-3", n=12, cap=12)
        turn("2026-09-20T03:04:25Z", 265.0, 0, 0.01, cls._sid3, "run-3")
        turn("2026-09-20T03:15:00Z", 635.0, 1, 0.40, cls._sid3, "run-3")
        # session 4 (T8): ping 5 of 12, then a rewrite -> inside the cap window (k = 1, m = 0)
        turn("2026-09-20T04:00:00Z", 10.0, 0, 0.05, cls._sid4, "run-4")
        ping("2026-09-20T04:04:20Z", cls._sid4, "run-4", n=5, cap=12)
        turn("2026-09-20T04:04:25Z", 265.0, 0, 0.01, cls._sid4, "run-4")
        turn("2026-09-20T04:11:05Z", 400.0, 1, 0.40, cls._sid4, "run-4")
        for cause, level, sent in (("question", "waiting", 1), ("question", "waiting", 1),
                                   ("question", "waiting", 0), ("review", "all", 1),
                                   ("weird", "waiting", 1), ("subagent-stop", "waiting", 1)):
            c.execute("INSERT INTO events(ts, session_id, kind, detail_json)"
                      " VALUES('2026-09-20T01:50:00Z', ?, 'toast', ?)",
                      (sid, json.dumps({"cause": cause, "level": level, "sent": sent,
                                        "reason": "level waiting", "title": "PA3"})))
        c.commit()

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _md(self, sid):
        from pa import audit
        return audit.render_md(audit.audit_phase(self._conn, {"audit": {}}, self._project_dir,
                                                 "9.5", sids=[sid]))

    def test_warm_row(self):
        from pa import prices
        b = 2 * 100000 * prices.price_for(self.MODEL)["write_5m"] / 1e6
        self.assertIn("- warm pings: 3 pings over 1 runs, 2 warmed waits over the TTL,"
                      " rewrites across warmed waits 1, waits past the cap 0, pings cost $0.0200"
                      " vs rewrites replaced $%.4f, cheaper than one rewrite: yes\n" % b,
                      self._md(self._sid))

    def test_warm_row_no_warmed_wait(self):
        self.assertIn("- warm pings: 1 pings over 1 runs, 0 warmed waits over the TTL,"
                      " rewrites across warmed waits 0, waits past the cap 0, pings cost $0.0100"
                      " vs rewrites replaced $0.0000, cheaper than one rewrite: n/a\n",
                      self._md(self._sid2))

    def test_warm_row_past_the_cap(self):
        """3.9.5 T8: a wait that outlasts the cap is counted in w and m, never in k or b."""
        self.assertIn("- warm pings: 1 pings over 1 runs, 1 warmed waits over the TTL,"
                      " rewrites across warmed waits 0, waits past the cap 1, pings cost $0.0100"
                      " vs rewrites replaced $0.0000, cheaper than one rewrite: no\n",
                      self._md(self._sid3))

    def test_warm_row_rewrite_inside_the_cap_window(self):
        from pa import prices
        b = 100000 * prices.price_for(self.MODEL)["write_5m"] / 1e6
        self.assertIn("- warm pings: 1 pings over 1 runs, 1 warmed waits over the TTL,"
                      " rewrites across warmed waits 1, waits past the cap 0, pings cost $0.0100"
                      " vs rewrites replaced $%.4f, cheaper than one rewrite: yes\n" % b,
                      self._md(self._sid4))

    def test_toast_row(self):
        self.assertIn("- toasts by cause (waiting): question 2, review 0, replan 0, permission 0,"
                      " input 0, discussion 0, crash 0, idle 0, stop 0, subagent-stop 1, model 0,"
                      " other 1\n", self._md(self._sid))


class TestRouterTurnsReasks(unittest.TestCase):
    """3.9.5 T12: router turns by cause with the relay cost; retriever re-asks."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_turns_audit_")
        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)
        cls._conn = sqlite3.connect(os.path.join(cls._tmpdir, "ledger.sqlite"))
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        from pa import db
        c.executescript(db.SCHEMA_SQL)
        cls._sid = sid = "sess-turns-1"

        def write(name, recs):
            p = os.path.join(cls._tmpdir, name)
            with open(p, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            return p

        def user(content, meta=False):
            r = {"type": "user", "timestamp": "2026-09-20T01:00:00Z",
                 "message": {"role": "user", "content": content}}
            if meta:
                r["isMeta"] = True
            return r

        def asst(mid):
            return {"type": "assistant", "timestamp": "2026-09-20T01:00:01Z",
                    "message": {"role": "assistant", "id": mid,
                                "content": [{"type": "text", "text": "ok"}]}}

        tres = user([{"type": "tool_result", "tool_use_id": "t1", "content": "x"}])
        relay = ('<task-notification>\n<task-id>bkk8vej9b</task-id>\n<summary>Monitor event:'
                 ' "warmer relay: ab5c46f0fc1309"</summary>\n<event>warm ab5c46f0fc1309 262</event>')
        main = [
            user("hello"), asst("m-o1"), tres, asst("m-o1b"),                     # other
            user([{"type": "text", "text": relay}]), asst("m-r1"), asst("m-r1"),  # relay
            tres, asst("m-r2"),
            user("<task-notification>\n<task-id>b1</task-id>\n<event>[Monitor expired after"
                 " 30m with no events delivered]</event>"), asst("m-e1"),       # re-arm
            user("<task-notification>\n<task-id>a9dc</task-id>\n<tool-use-id>tu1</tool-use-id>"
                 "\n<output-file>x</output-file>"), asst("m-l1"),               # loop
            user('Another Claude session sent a message:\n<agent-message from="a9dc">hi'),
            user("<local-command-caveat>meta</local-command-caveat>", meta=True),
            asst("m-l2"),                                                       # loop
            user(relay),                                                        # no assistant
            user("later"), asst("m-o2"),                                        # other
        ]
        c.execute("INSERT INTO sessions(session_id, project, transcript_path) VALUES(?,?,?)",
                  (sid, cls._project_dir, write("main.jsonl", main)))
        for mid, cost in (("m-r1", 0.01), ("m-r2", 0.02), ("m-o1", 0.50), ("m-e1", 0.30)):
            c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, cost_usd)"
                      " VALUES(?,?,?,'2026-09-20T01:00:01Z',?)", (mid, sid, sid, cost))
        cls._sid2 = sid2 = "sess-turns-2"                   # meta hand-back opens a turn
        hand = user([{"type": "text", "text": 'Another Claude session sent a message:\n'
                      '<agent-message from="a9dc">done'}], meta=True)
        c.execute("INSERT INTO sessions(session_id, project, transcript_path) VALUES(?,?,?)",
                  (sid2, cls._project_dir, write("main2.jsonl", [
                      user(relay), asst("h-r1"), hand, asst("h-l1")])))
        for mid, cost in (("h-r1", 0.04), ("h-l1", 2.00)):
            c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, cost_usd)"
                      " VALUES(?,?,?,'2026-09-20T01:00:01Z',?)", (mid, sid2, sid2, cost))

        def run(rid, parent, brief, started, atype="retriever-code", exists=True):
            tp = (write("agent-%s.jsonl" % rid, [user(brief), asst("x-" + rid)]) if exists
                  else os.path.join(cls._tmpdir, "missing-%s.jsonl" % rid))
            c.execute("INSERT INTO agent_runs(run_id, session_id, parent_run_id, agent_type,"
                      " started, transcript_path) VALUES(?,?,?,?,?,?)",
                      (rid, sid, parent, atype, started, tp))

        run("r1", "P1", "Where is `_compute_toasts` in project-architect-3.0/pa/audit.py:762?",
            "2026-09-20T01:01:00Z")
        run("r2", "P1", "Callers of def audit_phase in ./Project-Architect-3.0\\pa\\audit.py:10-20",
            "2026-09-20T01:02:00Z")                                   # re-ask: same path
        run("r3", "P1", "What does tools/run.sh do?", "2026-09-20T01:03:00Z")
        run("r4", "P2", "Read project-architect-3.0/pa/audit.py", "2026-09-20T01:04:00Z")
        run("r5", None, "Read project-architect-3.0/pa/audit.py", "2026-09-20T01:05:00Z")
        run("r6", "P1", "Read tools/run.sh", "2026-09-20T01:06:00Z", exists=False)
        run("r7", "P1", "Where is class Foo defined?", "2026-09-20T01:07:00Z")
        run("r8", "P1", "Who subclasses class Foo?", "2026-09-20T01:08:00Z")  # re-ask: name
        run("c1", "P1", "Read tools/run.sh", "2026-09-20T01:09:00Z", atype="coder-opus55")
        c.commit()

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _md(self):
        from pa import audit
        return audit.render_md(audit.audit_phase(self._conn, {"audit": {}}, self._project_dir,
                                                 "9.5", sids=[self._sid]))

    def test_router_turns_row(self):
        self.assertIn("- router turns by cause: loop 1, relay 1, re-arm 1, other 3,"
                      " relay cost $0.0300\n", self._md())

    def test_router_turns_meta_handback(self):
        from pa import audit
        md = audit.render_md(audit.audit_phase(self._conn, {"audit": {}}, self._project_dir,
                                               "9.5", sids=[self._sid2]))
        self.assertIn("- router turns by cause: loop 1, relay 1, re-arm 0, other 0,"
                      " relay cost $0.0400\n", md)

    def test_retriever_reasks_row(self):
        self.assertIn("- retriever re-asks: 2 of 7 retriever briefs repeat a lookup of the"
                      " same run\n", self._md())


class TestBeside(unittest.TestCase):
    """T28: a ``beside <phase>`` session gets its own row, never the phase total."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_beside_audit_")
        cls._project_dir = os.path.join(cls._tmpdir, "project")
        os.makedirs(cls._project_dir)
        cls._conn = sqlite3.connect(os.path.join(cls._tmpdir, "ledger.sqlite"))
        cls._conn.row_factory = sqlite3.Row
        c = cls._conn
        from pa import db
        c.executescript(db.SCHEMA_SQL)
        for sid, tag, costs in (("own-sess-1", "9.9", (0.5, 0.25)),
                                ("beside-sess-2", "beside 9.9", (1.0, 0.5, 0.25))):
            c.execute("INSERT INTO sessions(session_id, project, started, phase) VALUES(?,?,?,?)",
                      (sid, cls._project_dir, "2026-09-20T01:00:00Z", tag))
            for i, cost in enumerate(costs):
                c.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, cost_usd, kind)"
                          " VALUES(?,?,?,?,?,'api')",
                          ("%s-m%d" % (sid, i), sid, sid, "2026-09-20T01:0%d:00Z" % i, cost))
        c.commit()

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_beside_own_row(self):
        from pa import audit
        for sids in (None, ["own-sess-1", "beside-sess-2"]):
            r = audit.audit_phase(self._conn, {"audit": {}}, self._project_dir, "9.9", sids=sids)
            self.assertEqual(r["n_sessions"], 1)
            self.assertEqual([(b["session_id"], b["cost_usd"], b["n_turns"]) for b in r["beside"]],
                             [("beside-sess-2", 1.75, 3)])
        md = audit.render_md(audit.audit_phase(
            self._conn, {"audit": {}}, self._project_dir, "9.9",
            span=("2026-09-20T00:00:00Z", "2026-09-20T02:00:00Z")))
        self.assertIn("1 sessions,", md)
        self.assertIn("- beside 9.9: session beside-s $1.75 (3 turns), not attributed\n", md)


if __name__ == "__main__":
    unittest.main()
