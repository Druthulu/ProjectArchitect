"""pa.replay: the rolling vs-monolithic figure and its integration.

Every test runs against a temporary ``PA_LEDGER_DIR``; nothing touches
the real ``~/.claude``.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config, db, paths, prices, replay, summary  # noqa: E402

SID_A = "aaaa1111-0000-4000-8000-00000000005b"
SID_B = "bbbb1111-0000-4000-8000-00000000006a"
RUN_A = SID_A
RUN_B = SID_B
ACCOUNT = "dev@example.com"
FABLE = "claude-fable-5-1"


def _recent_iso(hours_ago=1):
    """ISO timestamp *hours_ago* hours in the past."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - hours_ago * 3600))


# --------------------------------------------------------------------------- helpers

def _make_turns(sid, run_id, base_hour, n, new_tokens=2000, output=500,
                cost_usd=0.05, minute_step=5):
    """Build *n* turn dicts for :func:`replay.rolling`."""
    out = []
    for i in range(n):
        ts = "2026-09-01T%02d:%02d:00Z" % (base_hour, i * minute_step)
        out.append({
            "msg_id": "msg_%s_%d" % (sid[:8], i),
            "session_id": sid,
            "run_id": run_id,
            "ts": ts,
            "epoch": replay._parse_epoch(ts),
            "model": FABLE,
            "new_tokens": new_tokens,
            "output": output,
            "cost_usd": cost_usd,
        })
    return out


def _run_cli(*argv):
    from pa import ledger_cli
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = ledger_cli.main(list(argv))
    return rc, buf_out.getvalue(), buf_err.getvalue()


# --------------------------------------------------------------------------- unit tests

class RollingTest(unittest.TestCase):
    """Pure-function tests of rolling(), session_sums, sum_since."""

    def test_determinism(self):
        """Two rolling() runs over the same list produce identical results."""
        turns = _make_turns(SID_A, RUN_A, 10, 3)
        seeds = {}
        p1 = dict(replay.params_from_cfg(config.defaults()))
        p2 = dict(replay.params_from_cfg(config.defaults()))
        r1 = replay.rolling(turns, seeds, p1)
        r2 = replay.rolling(turns, seeds, p2)
        self.assertEqual(len(r1), len(r2))
        for a, b in zip(r1, r2):
            self.assertEqual(a, b)

    def test_two_sessions_cold_rewrite(self):
        """Session B opens with a cold rewrite of the carried context."""
        turns_a = _make_turns(SID_A, RUN_A, 10, 3)
        turns_b = _make_turns(SID_B, RUN_B, 13, 1)
        turns = turns_a + turns_b
        seeds = {}
        params = {"window": 1000000, "ttl_old_s": 3600,
                  "subtract_agent_seeds": False}
        results = replay.rolling(turns, seeds, params)
        self.assertEqual(len(results), 4)

        # session A turns are warm
        for i in range(3):
            self.assertFalse(results[i]["cold"])

        # session B's first turn is cold
        b0 = results[3]
        self.assertTrue(b0["cold"])
        # ctx after 3 turns of 2000 each starting at 0 = 6000
        # cost_replay = 6000 * write_1h + 2000 * write_1h + 500 * output
        #             = 6000*20/1e6 + 2000*20/1e6 + 500*50/1e6
        #             = 0.12 + 0.04 + 0.025 = 0.185
        self.assertAlmostEqual(b0["cost_replay"], 0.185, places=6)

    def test_window_cycle(self):
        """ctx + new > window resets ctx to 0 before the turn, with no seed cost (T30)."""
        turns = _make_turns(SID_A, RUN_A, 10, 2, new_tokens=3000, output=500)
        seeds = {}
        params = {"window": 5000, "ttl_old_s": 3600,
                  "subtract_agent_seeds": False}
        results = replay.rolling(turns, seeds, params)
        r0, r1 = results
        self.assertFalse(r0["reset"])
        self.assertEqual(r0["ctx_after"], 3000)
        # 3000 + 3000 > 5000: the window cycles to 0 first
        self.assertTrue(r1["reset"])
        self.assertEqual(r1["ctx_after"], 3000)
        # cost = prefix 0 + new(write_1h) + output; no seed write
        expected = (0 + 3000 * 20 + 500 * 50) / 1e6
        self.assertAlmostEqual(r1["cost_replay"], expected, places=6)

    def test_subtract_agent_seeds(self):
        """A run's first turn with seed_ctx 1500 and new_tokens 2000 adds 500."""
        turns = _make_turns(SID_A, RUN_A, 10, 1, new_tokens=2000, output=0)
        seeds = {RUN_A: 1500}
        params = {"window": 1000000, "ttl_old_s": 3600,
                  "subtract_agent_seeds": True}
        results = replay.rolling(turns, seeds, params)
        r = results[0]
        # effective new = max(0, 2000 - 1500) = 500
        self.assertEqual(r["ctx_after"], 500)
        expected = (0 * 0.25 + 500 * 20) / 1e6
        self.assertAlmostEqual(r["cost_replay"], expected, places=6)

    def test_session_sums(self):
        turns = _make_turns(SID_A, RUN_A, 10, 2) + _make_turns(SID_B, RUN_B, 11, 1)
        params = dict(replay.params_from_cfg(config.defaults()))
        results = replay.rolling(turns, {}, params)
        sums = replay.session_sums(results)
        self.assertIn(SID_A, sums)
        self.assertIn(SID_B, sums)
        total = sum(sums.values())
        self.assertAlmostEqual(total, sum(r["modeled_saved"] for r in results), places=6)

    def test_sum_since(self):
        turns = _make_turns(SID_A, RUN_A, 10, 2) + _make_turns(SID_B, RUN_B, 12, 1)
        params = dict(replay.params_from_cfg(config.defaults()))
        results = replay.rolling(turns, {}, params)
        full = replay.sum_since(results, "2026-09-01T00:00:00Z")
        partial = replay.sum_since(results, "2026-09-01T12:00:00Z")
        self.assertAlmostEqual(full, sum(r["modeled_saved"] for r in results), places=6)
        self.assertAlmostEqual(partial, results[2]["modeled_saved"], places=6)


# --------------------------------------------------------------------------- integration

class ReplayDBCase(unittest.TestCase):
    """Base: a temp ledger with two sessions, agent_runs, and turns."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-replay-")
        self.ledger = os.path.join(self.dir, "ledger")
        os.makedirs(self.ledger)
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        self._populate()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def conn(self):
        c = db.connect(paths.db_path())
        db.init_schema(c)
        return c

    def _populate(self):
        # use recent timestamps so sessions_block (48h window) includes them
        self.ts_a_start = _recent_iso(2)        # 2 hours ago
        self.ts_a_end = _recent_iso(1.5)
        self.ts_b_start = _recent_iso(1)        # 1 hour ago
        self.ts_b_end = _recent_iso(0.5)
        conn = self.conn()
        try:
            conn.execute(
                "INSERT INTO accounts(email) VALUES(?)", (ACCOUNT,))
            conn.execute(
                "INSERT INTO sessions(session_id, account, kind, started, ended, "
                "cost_usd, cost_source) VALUES(?,?,?,?,?,?,?)",
                (SID_A, ACCOUNT, "direct", self.ts_a_start,
                 self.ts_a_end, 1.0, "turns"))
            conn.execute(
                "INSERT INTO sessions(session_id, account, kind, started, ended, "
                "cost_usd, cost_source) VALUES(?,?,?,?,?,?,?)",
                (SID_B, ACCOUNT, "direct", self.ts_b_start,
                 self.ts_b_end, 0.5, "turns"))
            conn.execute(
                "INSERT INTO agent_runs(run_id, session_id, kind, agent_type, "
                "started, ended, status, turns, seed_ctx, locked) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (RUN_A, SID_A, "session", "pa-session",
                 self.ts_a_start, self.ts_a_end, "done", 3, 20000, 1))
            conn.execute(
                "INSERT INTO agent_runs(run_id, session_id, kind, agent_type, "
                "started, ended, status, turns, seed_ctx, locked) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (RUN_B, SID_B, "session", "pa-session",
                 self.ts_b_start, self.ts_b_end, "done", 1, 20000, 1))
            for i in range(3):
                ts = "2026-09-01T10:%02d:00Z" % (i * 5)
                conn.execute(
                    "INSERT INTO turns(msg_id, run_id, session_id, account, ts, "
                    "model, new_tokens, output, cost_usd, kind) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("msg_a_%d" % i, RUN_A, SID_A, ACCOUNT, ts, FABLE,
                     2000, 500, 0.05, "api"))
            conn.execute(
                "INSERT INTO turns(msg_id, run_id, session_id, account, ts, "
                "model, new_tokens, output, cost_usd, kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("msg_b_0", RUN_B, SID_B, ACCOUNT, "2026-09-01T13:00:00Z",
                 FABLE, 2000, 500, 0.05, "api"))
            # pre-existing measured savings row
            conn.execute(
                "INSERT INTO savings(run_id, session_id, measured_saved_usd, "
                "locked, updated) VALUES(?,?,?,?,?)",
                ("run_expert_a", SID_A, 5.0, 1, self.ts_a_end))
            conn.commit()
        finally:
            db.close(conn)


class SummaryRebuildTest(ReplayDBCase):

    def test_modeled_in_savings_rows(self):
        """rebuild upserts savings rows with modeled_saved_usd and assumptions_json."""
        conn = self.conn()
        try:
            doc = summary.rebuild(conn, config.defaults())
            self.assertIsNotNone(doc)
            rows = conn.execute(
                "SELECT run_id, modeled_saved_usd, assumptions_json "
                "FROM savings WHERE run_id IN (?,?)", (SID_A, SID_B)).fetchall()
            by_sid = {r["run_id"]: r for r in rows}
            self.assertIn(SID_A, by_sid)
            self.assertIn(SID_B, by_sid)
            for sid in (SID_A, SID_B):
                self.assertIsNotNone(by_sid[sid]["modeled_saved_usd"])
                assume = json.loads(by_sid[sid]["assumptions_json"])
                self.assertIn("prices_version", assume)
                self.assertIn("n_turns", assume)
        finally:
            db.close(conn)

    def test_measured_row_preserved(self):
        """An existing measured savings row keeps its measured_saved_usd."""
        conn = self.conn()
        try:
            summary.rebuild(conn, config.defaults())
            row = conn.execute(
                "SELECT measured_saved_usd FROM savings WHERE run_id=?",
                ("run_expert_a",)).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row["measured_saved_usd"], 5.0)
        finally:
            db.close(conn)

    def test_period_cost_saved_modeled(self):
        """accounts[a].period.cost_saved_modeled equals the replay sum."""
        conn = self.conn()
        try:
            cfg = config.defaults()
            cfg["accounts"] = {ACCOUNT: {"renewal_day": 21}}   # 3.11 T9: a period needs the account's day
            doc = summary.rebuild(conn, cfg)
            acct_block = doc["accounts"].get(ACCOUNT)
            self.assertIsNotNone(acct_block)
            period = acct_block["period"]
            self.assertIsNotNone(period.get("cost_saved_modeled"))

            turns, seeds = replay.account_turns(conn, ACCOUNT)
            params = dict(replay.params_from_cfg(cfg))
            results = replay.rolling(turns, seeds, params)
            expected = replay.sum_since(results, period["start"])
            self.assertAlmostEqual(
                period["cost_saved_modeled"], round(expected, 6), places=4)
        finally:
            db.close(conn)

    def test_session_saved_modeled_set(self):
        """sessions[sid].saved_modeled is set for each session."""
        conn = self.conn()
        try:
            doc = summary.rebuild(conn, config.defaults())
            sessions = doc.get("sessions", {})
            for sid in (SID_A, SID_B):
                self.assertIn(sid, sessions)
                self.assertIsNotNone(sessions[sid].get("saved_modeled"))
        finally:
            db.close(conn)


class ReportTest(ReplayDBCase):

    def test_report_json_saved_modeled(self):
        """report --json shows saved_modeled per session and assumptions."""
        rc, text, errs = _run_cli("report", "--json", "--window", "all",
                                  "--account", ACCOUNT)
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        self.assertIn("assumptions", doc)
        self.assertIn("prices_version", doc["assumptions"])
        for entry in doc["accounts"]:
            if entry["account"] == ACCOUNT:
                self.assertIsNotNone(entry["saved_modeled"])
                for row in entry["rows"]:
                    if row["key"] in (SID_A, SID_B):
                        self.assertIn("saved_modeled", row)


# --------------------------------------------------------------------------- source scan

class SourceScanTest(unittest.TestCase):

    def test_no_sum_of_measured_and_modeled(self):
        """No line of pa/*.py matches measured.*+.*modeled or vice versa."""
        pa_dir = os.path.join(ROOT, "pa")
        patterns = [
            re.compile(r"measured.*\+.*modeled"),
            re.compile(r"modeled.*\+.*measured"),
        ]
        violations = []
        for fname in sorted(os.listdir(pa_dir)):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(pa_dir, fname)
            with open(fpath, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    # strip trailing comments and strings before checking
                    code = line.split("#")[0]
                    for pat in patterns:
                        if pat.search(code):
                            violations.append(
                                "%s:%d: %s" % (fname, lineno, line.strip()))
        self.assertEqual(violations, [],
                         "measured+modeled sum:\n" + "\n".join(violations))


# --------------------------------------------------------------------------- statusline

class BasesTest(ReplayDBCase):
    """T30: replay.bases() returns one vanilla set: the plain 1M window that cycles."""

    def test_one_vanilla_basis(self):
        conn = self.conn()
        try:
            b = replay.bases(config.defaults(), conn)
            self.assertEqual(sorted(b), ["vanilla"])
            self.assertNotIn("pa2", b)
            v = b["vanilla"]
            self.assertEqual(v["window"], 1000000)
            self.assertEqual(v["ttl_old_s"], 3600)
            self.assertEqual(v["_source"], {"window": "savings.window_tokens",
                                            "ttl_old_s": "fixed 1h"})
        finally:
            db.close(conn)

    def test_window_from_config(self):
        cfg = config.defaults()
        cfg["savings"]["window_tokens"] = 200000
        self.assertEqual(replay.params_from_cfg(cfg)["window"], 200000)
        self.assertEqual(replay.assumptions(replay.params_from_cfg(cfg), [])["window"], 200000)


if __name__ == "__main__":
    unittest.main()
