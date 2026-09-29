"""pa.db: schema, idempotent upserts, union readers."""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import db  # noqa: E402

EXPECTED_TABLES = ("accounts", "agent_runs", "bench", "events", "fit_pool", "meta", "prices", "retrievals",
                   "savings", "sessions", "summary", "tool_calls", "turns", "utilization",
                   "window_instances")


class SchemaTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.path = os.path.join(self.dir, "ledger.sqlite")
        self.conn = db.connect(self.path)
        db.init_schema(self.conn)

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _names(self, kind):
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? ORDER BY name", (kind,))]

    def test_all_tables_and_indexes_exist(self):
        self.assertEqual(tuple(self._names("table")), EXPECTED_TABLES)
        for ix in db.INDEX_NAMES:
            self.assertIn(ix, self._names("index"))

    def test_meta_and_price_seed(self):
        self.assertEqual(db.schema_version(self.conn), db.SCHEMA_VERSION)
        self.assertTrue(db.get_meta(self.conn, "machine"))
        self.assertTrue(db.get_meta(self.conn, "created"))
        n = self.conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        self.assertEqual(n, 14)     # was 13; 3.11 T21 added the sonnet-5-5 row
        self.assertEqual(db.seed_prices(self.conn), 0)        # does not duplicate

    def test_pragmas_applied(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0)

    def test_init_schema_is_idempotent(self):
        db.init_schema(self.conn)
        self.assertEqual(tuple(self._names("table")), EXPECTED_TABLES)
        self.assertEqual(db.migrate(self.conn), db.SCHEMA_VERSION)


class MigrateV3Test(unittest.TestCase):
    """v2 -> v3 adds the bench table (T3), ending at the v4 shape; a newer stamp is never lowered."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        self.assertIn(db.BENCH_SQL, db.SCHEMA_SQL)
        self.conn.executescript(db.SCHEMA_SQL.replace(db.BENCH_SQL, ""))   # the v2 schema
        db.set_meta(self.conn, "schema_version", "2")
        db.upsert_agent_run(self.conn, {"run_id": "r1", "agent_type": "coder", "turns": 7})

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _has_bench(self):
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bench'"
                                 ).fetchone() is not None

    def test_v2_to_v3(self):
        self.assertFalse(self._has_bench())
        self.assertEqual(db.migrate(self.conn), 7)
        self.assertEqual(db.SCHEMA_VERSION, 7)  # was 6
        self.assertTrue(self._has_bench())
        self.assertIn("attempt", [r[1] for r in self.conn.execute("PRAGMA table_info(bench)")])
        self.assertEqual(db.schema_version(self.conn), 7)
        self.assertEqual(tuple(self.conn.execute("SELECT run_id, agent_type, turns FROM agent_runs")
                               .fetchall()[0]), ("r1", "coder", 7))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0], 1)
        self.assertEqual(db.migrate(self.conn), 7)                     # second migrate: no-op
        self.assertEqual(db.schema_version(self.conn), 7)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0], 1)

    def test_newer_stamp_stays(self):
        db.set_meta(self.conn, "schema_version", "8")
        self.assertEqual(db.migrate(self.conn), 8)
        self.assertEqual(db.schema_version(self.conn), 8)


# the v3 bench DDL (3.9 T3), kept here as the migration fixture
BENCH_SQL_V3 = """
CREATE TABLE IF NOT EXISTS bench(
  run_id TEXT, arm TEXT, task_id TEXT, role TEXT, agent TEXT, model TEXT, effort TEXT, body_hash TEXT,
  fixture_hash TEXT, harness_version TEXT, price_version TEXT, suite TEXT, tier INTEGER, source TEXT,
  started REAL, secs REAL, passed INTEGER, stopped INTEGER DEFAULT 0, cost_usd REAL, turns INTEGER,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER,
  w5h_before REAL, w5h_after REAL, rubric REAL, detail TEXT,
  PRIMARY KEY(run_id, arm, task_id));
"""


class MigrateV4Test(unittest.TestCase):
    """v3 -> v4 recreates bench keyed per attempt (3.9.7 T5); old rows become attempt 1."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        self.conn.executescript(db.SCHEMA_SQL.replace(db.BENCH_SQL, BENCH_SQL_V3))   # the v3 schema
        db.set_meta(self.conn, "schema_version", "3")
        for tid, passed, cost in (("a", 1, 0.25), ("b", 0, 0.5), ("c", None, None)):
            self.conn.execute("INSERT INTO bench (run_id, arm, task_id, passed, cost_usd, rubric, turns)"
                              " VALUES ('r1', 'k', ?, ?, ?, 0.5, 3)", (tid, passed, cost))

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self):
        return [tuple(r) for r in self.conn.execute(
            "SELECT run_id, arm, task_id, attempt, passed, cost_usd, rubric, turns, failure_kind,"
            " consequence_cost FROM bench ORDER BY task_id")]

    def test_v3_to_v4_in_place(self):
        self.assertNotIn("attempt", [r[1] for r in self.conn.execute("PRAGMA table_info(bench)")])
        self.assertEqual(db.migrate(self.conn), 7)
        self.assertEqual(db.schema_version(self.conn), 7)
        want = [("r1", "k", "a", 1, 1, 0.25, 0.5, 3, None, None),
                ("r1", "k", "b", 1, 0, 0.5, 0.5, 3, None, None),
                ("r1", "k", "c", 1, None, None, 0.5, 3, None, None)]
        self.assertEqual(self._rows(), want)
        pk = sorted((r[5], r[1]) for r in self.conn.execute("PRAGMA table_info(bench)") if r[5])
        self.assertEqual([c for _, c in pk], ["run_id", "arm", "task_id", "attempt"])
        db.upsert(self.conn, "bench", {"run_id": "r1", "arm": "k", "task_id": "a", "attempt": 2,
                                       "passed": 0}, "replace")
        self.assertEqual(len(self._rows()), 4)                        # a second attempt is a new row
        self.conn.execute("DELETE FROM bench WHERE attempt=2")
        self.assertEqual(db.migrate(self.conn), 7)                     # second migrate: no-op
        self.assertEqual(self._rows(), want)
        self.assertNotIn("bench_v4", [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")])

    def test_init_schema_alone_upgrades_a_v3_bench(self):
        db.init_schema(self.conn)                  # the hooks' and the installer's path
        self.assertEqual(db.schema_version(self.conn), 7)
        self.assertEqual([r[3] for r in self._rows()], [1, 1, 1])


class MigrateV5Test(unittest.TestCase):
    """v4 -> v5 adds sessions.harness_cost_usd (T30); rows keep their cost."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        self.conn.executescript(db.SCHEMA_SQL.replace(", harness_cost_usd REAL", ""))   # v4 schema
        db.set_meta(self.conn, "schema_version", "4")
        self.conn.execute("INSERT INTO sessions (session_id, cost_usd, cost_source)"
                          " VALUES ('s1', 1.5, 'turns')")

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cols(self):
        return [r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")]

    def test_v4_to_v5(self):
        self.assertNotIn("harness_cost_usd", self._cols())
        self.assertEqual(db.migrate(self.conn), 7)
        self.assertIn("harness_cost_usd", self._cols())
        db.upsert_session(self.conn, {"session_id": "s1", "harness_cost_usd": 2.0})
        self.assertEqual(tuple(self.conn.execute(
            "SELECT cost_usd, cost_source, harness_cost_usd FROM sessions").fetchone()),
            (1.5, "turns", 2.0))
        self.assertEqual(db.migrate(self.conn), 7)                     # second migrate: no-op

    def test_init_schema_alone_adds_the_column(self):
        db.init_schema(self.conn)                  # the hooks' path
        self.assertIn("harness_cost_usd", self._cols())


class MigrateV6Test(unittest.TestCase):
    """v5 -> v6 adds the fit_pool table (T32.1)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        ddl = db.SCHEMA_SQL
        a = ddl.index("CREATE TABLE IF NOT EXISTS fit_pool(")
        b = ddl.index("CREATE TABLE IF NOT EXISTS prices(")
        self.conn.executescript(ddl[:a] + ddl[b:])                     # the v5 schema
        db.set_meta(self.conn, "schema_version", "5")

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _has_pool(self):
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fit_pool'"
                                 ).fetchone() is not None

    def test_v5_to_v6(self):
        self.assertFalse(self._has_pool())
        self.assertEqual(db.migrate(self.conn), 7)
        self.assertTrue(self._has_pool())
        db.upsert_fit_pool(self.conn, {"tier": "max", "window": "seven_day", "family": "opus",
                                       "rate": 0.001, "se": 0.0001, "n": 60, "n_instances": 2})
        db.upsert_fit_pool(self.conn, {"tier": "max", "window": "seven_day", "family": "opus",
                                       "rate": 0.002, "n": 90})
        self.assertEqual(tuple(self.conn.execute("SELECT rate, se, n FROM fit_pool").fetchone()),
                         (0.002, None, 90))                            # replace: the new fit wins
        self.assertEqual(db.migrate(self.conn), 7)                     # second migrate: no-op


class MigrateV7Test(unittest.TestCase):
    """v6 -> v7 adds accounts.tier and accounts.tier_source; rows keep their values."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        ddl = db.SCHEMA_SQL
        self.assertIn(", tier TEXT, tier_source TEXT);", ddl)
        self.conn.executescript(ddl.replace(", tier TEXT, tier_source TEXT);", ");", 1))   # v6
        db.set_meta(self.conn, "schema_version", "6")
        self.conn.execute("INSERT INTO accounts (email, label, first_seen) VALUES ('a@x.io', 'A', 't0')")

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cols(self):
        return [r[1] for r in self.conn.execute("PRAGMA table_info(accounts)")]

    def test_v6_to_v7(self):
        self.assertNotIn("tier", self._cols())
        self.assertEqual(db.migrate(self.conn), 7)
        self.assertIn("tier", self._cols())
        self.assertIn("tier_source", self._cols())
        db.upsert_account(self.conn, {"email": "a@x.io", "tier": "max20", "tier_source": "credentials"})
        self.assertEqual(tuple(self.conn.execute(
            "SELECT label, first_seen, tier, tier_source FROM accounts").fetchone()),
            ("A", "t0", "max20", "credentials"))
        self.assertEqual(db.migrate(self.conn), 7)                     # second migrate: no-op

    def test_init_schema_alone_adds_the_columns(self):
        db.init_schema(self.conn)                  # the hooks' path (open_db)
        self.assertIn("tier_source", self._cols())


class UpsertTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-db-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    def test_turns_replace_keeps_the_last_values(self):
        row = {"msg_id": "msg_1", "run_id": "r1", "session_id": "s1", "output": 2,
               "ctx": 100, "cost_usd": 0.01}
        db.upsert_turn(self.conn, row)
        db.upsert_turn(self.conn, dict(row, output=164, ctx=58120, cost_usd=0.9))
        n, out, ctx = self._one("SELECT COUNT(*), output, ctx FROM turns")
        self.assertEqual((n, out, ctx), (1, 164, 58120))

    def test_agent_runs_merge_does_not_erase_earlier_columns(self):
        db.upsert_agent_run(self.conn, {"run_id": "a1", "session_id": "s1",
                                        "agent_type": "expert-fable", "status": "running",
                                        "spawn_depth": 1})
        db.upsert_agent_run(self.conn, {"run_id": "a1", "status": "completed", "turns": 20,
                                        "cost_usd": 1.25})
        rows = self.conn.execute("SELECT * FROM agent_runs").fetchall()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["agent_type"], "expert-fable")      # kept from the first write
        self.assertEqual(r["spawn_depth"], 1)
        self.assertEqual(r["status"], "completed")             # overwritten by the second
        self.assertEqual(r["turns"], 20)

    def test_sessions_and_accounts_merge(self):
        db.upsert_session(self.conn, {"session_id": "s1", "account": "a@b.c",
                                      "started": "t0", "kind": "router"})
        db.upsert_session(self.conn, {"session_id": "s1", "ended": "t1",
                                      "cost_usd": 4.2, "cost_source": "cost-state"})
        r = self._one("SELECT COUNT(*), account, started, ended, cost_usd FROM sessions")
        self.assertEqual(tuple(r), (1, "a@b.c", "t0", "t1", 4.2))
        db.upsert_account(self.conn, {"email": "a@b.c", "label": "win-main", "first_seen": "t0"})
        db.upsert_account(self.conn, {"email": "a@b.c", "last_seen": "t9"})
        r = self._one("SELECT COUNT(*), label, first_seen, last_seen FROM accounts")
        self.assertEqual(tuple(r), (1, "win-main", "t0", "t9"))

    def test_utilization_insert_or_ignore_on_the_unique_key(self):
        sample = {"ts": "2026-09-12T10:00:00Z", "session_id": "s1", "window": "five_hour",
                  "pct": 19.0, "resets_at": 1789000000, "account": "a@b.c"}
        db.insert_utilization(self.conn, sample)
        db.insert_utilization(self.conn, dict(sample, pct=22.0))      # same (sid, ts, window)
        db.insert_utilization(self.conn, dict(sample, window="seven_day"))
        n, pct = self._one("SELECT COUNT(*), MIN(pct) FROM utilization")
        self.assertEqual((n, pct), (2, 19.0))

    def test_retrievals_savings_summary_and_window_instances(self):
        db.upsert_retrieval(self.conn, {"run_id": "ret1", "parent_run_id": "a1",
                                        "kept_out_tokens": 38210, "status": "completed"})
        db.upsert_retrieval(self.conn, {"run_id": "ret1", "void": 1})
        self.assertEqual(tuple(self._one(
            "SELECT COUNT(*), kept_out_tokens, void FROM retrievals")), (1, 38210, 1))
        db.upsert_savings(self.conn, {"run_id": "a1", "measured_saved_usd": 9.1, "locked": 0})
        db.upsert_savings(self.conn, {"run_id": "a1", "locked": 1})
        self.assertEqual(tuple(self._one(
            "SELECT COUNT(*), measured_saved_usd, locked FROM savings")), (1, 9.1, 1))
        db.upsert_window_instance(self.conn, {"account": "a@b.c", "window": "five_hour",
                                              "resets_at": 1789000000, "fit_n": 3})
        db.upsert_window_instance(self.conn, {"account": "a@b.c", "window": "five_hour",
                                              "resets_at": 1789000000, "fit_n": 14,
                                              "fit_quality": "ok"})
        self.assertEqual(tuple(self._one(
            "SELECT COUNT(*), fit_n, fit_quality FROM window_instances")), (1, 14, "ok"))
        db.upsert_summary(self.conn, {"account": "a@b.c", "window": "five_hour", "cost_used": 71.2})
        db.upsert_summary(self.conn, {"account": "a@b.c", "window": "five_hour", "cost_used": 80.0})
        self.assertEqual(tuple(self._one("SELECT COUNT(*), cost_used FROM summary")), (1, 80.0))

    def test_events_always_append(self):
        db.insert_event(self.conn, "reset", {"window": "five_hour"}, session_id="s1")
        db.insert_event(self.conn, "reset", {"window": "five_hour"}, session_id="s1")
        self.assertEqual(self._one("SELECT COUNT(*) FROM events")[0], 2)

    def test_unknown_columns_are_dropped_and_unknown_tables_raise(self):
        db.upsert_turn(self.conn, {"msg_id": "m9", "not_a_column": 1, "output": 3})
        self.assertEqual(self._one("SELECT output FROM turns WHERE msg_id='m9'")[0], 3)
        with self.assertRaises(KeyError):
            db.upsert(self.conn, "nope", {"x": 1})

    def test_turn_row_from_request(self):
        req = {"msg_id": "m1", "model": "claude-fable-5-1", "ts_iso": "2026-09-12T10:00:00Z",
               "input": 10, "cache_write": 1000, "cache_write_5m": None, "cache_write_1h": None,
               "split": False, "cache_read": 50000, "output": 100, "thinking": 20,
               "ctx": 51010, "new_tokens": 1010, "gap_s": 12.0, "cold": False, "rewrite": False}
        row = db.turn_row_from_request(req, run_id="a1", session_id="s1", account="a@b.c",
                                       ttl_default="1h")
        self.assertEqual(row["cache_write_1h"], 1000)
        self.assertEqual(row["cache_write_5m"], 0)
        self.assertEqual(row["ttl_assumed"], "1h")
        self.assertAlmostEqual(row["cost_usd"],
                               (10 * 10 + 1000 * 20 + 50000 * 0.25 + 100 * 50) / 1e6, places=12)
        db.upsert_turn(self.conn, row)
        self.assertEqual(self._one("SELECT run_id, account, kind FROM turns WHERE msg_id='m1'")[:],
                         ("a1", "a@b.c", "api"))
        row5 = db.turn_row_from_request(req, ttl_default="5m")
        self.assertEqual((row5["cache_write_5m"], row5["cache_write_1h"], row5["ttl_assumed"]),
                         (1000, 0, "5m"))


class UnionReaderTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-union-")
        self.root = os.path.join(self.dir, "wsl-ledger")
        os.makedirs(self.root)
        conn = db.connect(os.path.join(self.root, "ledger.sqlite"))
        db.init_schema(conn, machine="wsl")
        db.upsert_turn(conn, {"msg_id": "m_remote", "run_id": "r", "cost_usd": 2.5})
        conn.commit()
        db.close(conn)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_union_copy_then_read_only_connection(self):
        copy_path = db.union_copy(self.root)
        self.assertTrue(copy_path and os.path.exists(copy_path))
        self.assertNotEqual(os.path.abspath(copy_path),
                            os.path.abspath(os.path.join(self.root, "ledger.sqlite")))
        readers = db.union_readers([self.root])
        try:
            self.assertEqual(len(readers), 1)
            self.assertEqual(readers[0]["path"], self.root)
            self.assertIsNone(readers[0]["account"])
            conn = readers[0]["conn"]
            self.assertEqual(db.get_meta(conn, "machine"), "wsl")
            self.assertEqual(conn.execute(
                "SELECT cost_usd FROM turns WHERE msg_id='m_remote'").fetchone()[0], 2.5)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
        finally:
            for r in readers:
                db.close(r["conn"])

    def test_dict_entry_carries_account_and_label(self):
        readers = db.union_readers([{"path": self.root, "account": "a@b.c", "label": "wsl-bfm"}])
        try:
            self.assertEqual(readers[0]["account"], "a@b.c")
            self.assertEqual(readers[0]["label"], "wsl-bfm")
        finally:
            for r in readers:
                db.close(r["conn"])

    def test_unchanged_source_reuses_the_copy(self):
        first = db.union_copy(self.root)
        stamp = os.path.getmtime(first)
        second = db.union_copy(self.root)
        self.assertEqual(first, second)
        self.assertEqual(stamp, os.path.getmtime(second))       # not re-copied

    def test_unreachable_roots_are_skipped(self):
        self.assertIsNone(db.union_copy(os.path.join(self.dir, "does-not-exist")))
        self.assertEqual(db.union_readers([os.path.join(self.dir, "nope")]), [])
        self.assertEqual(db.union_readers(None), [])


if __name__ == "__main__":
    unittest.main()
