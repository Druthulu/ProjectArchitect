"""Tests for tools/analysis/seed_floor.py — record, check, show."""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

# 3.11 T27: a dev-repo tool (the repo's tools/analysis), absent from the package and a public clone
SEED_FLOOR = os.path.join(os.path.dirname(PKG), "tools", "analysis", "seed_floor.py")
if not os.path.isfile(SEED_FLOOR):
    raise unittest.SkipTest("seed_floor.py is a dev-repo tool; not in the package")


def _run_seed_floor(argv):
    """Import and run seed_floor.main(argv), return exit code."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("seed_floor", SEED_FLOOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(argv)


class TestSeedFloorTool(unittest.TestCase):
    """record/check/show on a fixture sqlite built with the schema."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_sf_tool_")
        cls._db_path = os.path.join(cls._tmpdir, "ledger.sqlite")
        cls._out_path = os.path.join(cls._tmpdir, "seed_floor.json")

        conn = sqlite3.connect(cls._db_path)
        conn.row_factory = sqlite3.Row

        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_runs("
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

        # retriever-code: two runs, seeds 7500, 7000
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rc1', 's1', 'retriever-code', 7500, '2026-09-20T01:00:00Z')")
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rc2', 's1', 'retriever-code', 7000, '2026-09-20T02:00:00Z')")
        # retriever-digest: one run, seed 10000
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rd1', 's1', 'retriever-digest', 10000, '2026-09-20T03:00:00Z')")
        # retriever-web: one run, seed 6000
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rw1', 's1', 'retriever-web', 6000, '2026-09-20T04:00:00Z')")

        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        # Clean the output file before each test
        if os.path.exists(self._out_path):
            os.remove(self._out_path)

    def test_record_before(self):
        """record --label before writes seed_ctx for each type."""
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                              "record", "--label", "before"])
        self.assertEqual(rc, 0)
        with open(self._out_path) as f:
            data = json.load(f)
        self.assertIn("before", data)
        # newest retriever-code run is rc2 (later started)
        self.assertEqual(data["before"]["retriever-code"]["run_id"], "rc2")
        self.assertEqual(data["before"]["retriever-code"]["seed_ctx"], 7000)
        self.assertEqual(data["before"]["retriever-digest"]["seed_ctx"], 10000)
        self.assertEqual(data["before"]["retriever-web"]["seed_ctx"], 6000)

    def test_check_one_label_exits_1(self):
        """check exits 1 when only before is recorded."""
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "before"])
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path, "check"])
        self.assertEqual(rc, 1)

    def test_check_all_below_exits_0(self):
        """check exits 0 when every after < before."""
        # Write before with current data
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "before"])
        # Manually write after with lower values
        with open(self._out_path) as f:
            data = json.load(f)
        data["after"] = {
            "retriever-code": {"run_id": "a1", "seed_ctx": 6500, "ts": "t"},
            "retriever-digest": {"run_id": "a2", "seed_ctx": 9500, "ts": "t"},
            "retriever-web": {"run_id": "a3", "seed_ctx": 5500, "ts": "t"},
        }
        with open(self._out_path, "w") as f:
            json.dump(data, f)
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path, "check"])
        self.assertEqual(rc, 0)

    def test_check_one_not_below_exits_1(self):
        """check exits 1 when one after >= before."""
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "before"])
        with open(self._out_path) as f:
            data = json.load(f)
        data["after"] = {
            "retriever-code": {"run_id": "a1", "seed_ctx": 6500, "ts": "t"},
            "retriever-digest": {"run_id": "a2", "seed_ctx": 11000, "ts": "t"},  # >= before
            "retriever-web": {"run_id": "a3", "seed_ctx": 5500, "ts": "t"},
        }
        with open(self._out_path, "w") as f:
            json.dump(data, f)
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path, "check"])
        self.assertEqual(rc, 1)

    def test_show(self):
        """show prints the JSON."""
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "before"])
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path, "show"])
        self.assertEqual(rc, 0)

    def test_record_preserves_other_label(self):
        """recording after does not drop before."""
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "before"])
        _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                         "record", "--label", "after"])
        with open(self._out_path) as f:
            data = json.load(f)
        self.assertIn("before", data)
        self.assertIn("after", data)


class TestSeedFloorNullFallback(unittest.TestCase):
    """A run with NULL seed_ctx falls back to the first turn's ctx."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="pa_sf_null_")
        cls._db_path = os.path.join(cls._tmpdir, "ledger.sqlite")
        cls._out_path = os.path.join(cls._tmpdir, "seed_floor.json")

        conn = sqlite3.connect(cls._db_path)
        conn.row_factory = sqlite3.Row

        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_runs("
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

        conn.execute(
            "CREATE TABLE IF NOT EXISTS turns("
            "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
            "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
            "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
            "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
            "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
            "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
            "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")

        # retriever-code with NULL seed_ctx
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rc_null', 's1', 'retriever-code', NULL, '2026-09-20T01:00:00Z')")
        # Two turns for that run — first has ctx=7777
        conn.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, ctx, kind)"
                     " VALUES('t1', 'rc_null', 's1', '2026-09-20T01:00:01Z', 7777, 'api')")
        conn.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, ctx, kind)"
                     " VALUES('t2', 'rc_null', 's1', '2026-09-20T01:00:02Z', 9000, 'api')")

        # retriever-digest with seed_ctx set (no fallback needed)
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rd1', 's1', 'retriever-digest', 10000, '2026-09-20T02:00:00Z')")
        # retriever-web with seed_ctx set
        conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, seed_ctx, started)"
                     " VALUES('rw1', 's1', 'retriever-web', 6000, '2026-09-20T03:00:00Z')")

        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        if os.path.exists(self._out_path):
            os.remove(self._out_path)

    def test_null_seed_ctx_uses_first_turn(self):
        """A run with NULL seed_ctx gets the first turn's ctx as seed."""
        rc = _run_seed_floor(["--db", self._db_path, "--out", self._out_path,
                              "record", "--label", "before"])
        self.assertEqual(rc, 0)
        with open(self._out_path) as f:
            data = json.load(f)
        # retriever-code should use 7777 (first turn ctx)
        self.assertEqual(data["before"]["retriever-code"]["seed_ctx"], 7777)
        # retriever-digest uses seed_ctx directly
        self.assertEqual(data["before"]["retriever-digest"]["seed_ctx"], 10000)


if __name__ == "__main__":
    unittest.main()
