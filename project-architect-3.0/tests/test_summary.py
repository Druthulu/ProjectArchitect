"""pa.summary.projects_block: per-account slices and all-accounts totals (T10).

A fixture ledger with two accounts on one project verifies that:
- both accounts' slices are present in ``by_account``
- all-accounts totals equal the sums
- the largest-cost account's percentage fills the top-level ``pct_used``
"""

import os
import sqlite3
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import summary  # noqa: E402


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _make_db():
    """In-memory ledger with two accounts on one project, each with sessions, turns and savings."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions("
        "session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,"
        "machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,"
        "kind TEXT, agent_name TEXT, model_launch TEXT, effort_launch TEXT,"
        "version TEXT, started TEXT, ended TEXT, end_reason TEXT,"
        "cost_usd REAL, cost_source TEXT, cost_updated TEXT, phase TEXT)")
    conn.execute(
        "CREATE TABLE turns("
        "msg_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT,"
        "session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,"
        "input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,"
        "cache_write INTEGER, cache_read INTEGER, output INTEGER,"
        "thinking INTEGER, ctx INTEGER, new_tokens INTEGER,"
        "gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,"
        "cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api')")
    conn.execute(
        "CREATE TABLE savings("
        "run_id TEXT PRIMARY KEY, session_id TEXT, phase TEXT,"
        "measured_saved_usd REAL, measured_detail_json TEXT,"
        "modeled_saved_usd REAL, assumptions_json TEXT,"
        "locked INTEGER DEFAULT 0, updated TEXT)")
    # utilization table (needed by some paths)
    conn.execute(
        "CREATE TABLE utilization("
        "id INTEGER PRIMARY KEY, ts TEXT, account TEXT, machine TEXT, session_id TEXT,"
        "window TEXT, pct REAL, resets_at INTEGER, session_cost REAL, model TEXT,"
        "source TEXT DEFAULT 'statusline', reason TEXT,"
        "UNIQUE(session_id, ts, window))")
    # window_instances table
    conn.execute(
        "CREATE TABLE window_instances("
        "account TEXT, window TEXT, resets_at INTEGER, started_at INTEGER,"
        "first_seen TEXT, last_seen TEXT,"
        "quantized INTEGER, pct_per_dollar REAL, fit_method TEXT, fit_n INTEGER,"
        "fit_crossings INTEGER, fit_span REAL, fit_se REAL, fit_quality TEXT,"
        "fit_detail TEXT, reset_source TEXT,"
        "PRIMARY KEY(account, window, resets_at))")
    return conn


PROJECT = "/test/myproject"
ACCT_A = "a@example.com"
ACCT_B = "b@example.com"
# 3.11 T9: a period needs the account's own renewal day (no global fallback, no default 21)
_CFG = {"accounts": {ACCT_A: {"renewal_day": 21}, ACCT_B: {"renewal_day": 21}}}
NOW = int(time.time())
WINDOW_START = _iso(NOW - 14400)  # 4 hours ago


def _seed_two_accounts(conn):
    """Two accounts, each with a session on PROJECT, turns and savings."""
    # account A: session s-a, 2 turns at $3.00 each (opus family)
    conn.execute(
        "INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        ("s-a", ACCT_A, PROJECT, PROJECT, WINDOW_START, _iso(NOW)))
    for i in range(2):
        conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-a%d" % i, "s-a", ACCT_A, WINDOW_START, "claude-opus-4-6", 3.0))
    conn.execute(
        "INSERT INTO savings(run_id, session_id, measured_saved_usd, updated)"
        " VALUES(?, ?, ?, ?)",
        ("run-a1", "s-a", 2.0, WINDOW_START))

    # account B: session s-b, 1 turn at $1.00 (sonnet family)
    conn.execute(
        "INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        ("s-b", ACCT_B, PROJECT, PROJECT, WINDOW_START, _iso(NOW)))
    conn.execute(
        "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
        " VALUES(?, ?, ?, ?, ?, ?, 'api')",
        ("msg-b0", "s-b", ACCT_B, WINDOW_START, "claude-sonnet-5", 1.0))
    conn.execute(
        "INSERT INTO savings(run_id, session_id, measured_saved_usd, updated)"
        " VALUES(?, ?, ?, ?)",
        ("run-b1", "s-b", 0.5, WINDOW_START))
    conn.commit()


def _accounts_block():
    """Minimal accounts block for two accounts with matching window definitions."""
    def _acct(label, pct_last_5h=30.0, pct_last_7d=15.0):
        return {"label": label, "windows": {
            "five_hour": {"pct_saved": 20.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.05, "pct_last": pct_last_5h,
                          "started_at": NOW - 14400},
            "seven_day": {"pct_saved": 10.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.02, "pct_last": pct_last_7d,
                          "started_at": NOW - 14400},
        }}
    return {ACCT_A: _acct("win-a", 40.0, 20.0),
            ACCT_B: _acct("win-b", 20.0, 10.0)}


class ProjectsBlockTwoAccountsTest(unittest.TestCase):
    """T10: projects_block with two accounts on one project."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _make_db()
        _seed_two_accounts(cls.conn)
        cls.result = summary.projects_block(cls.conn, cfg=_CFG, accounts=_accounts_block())
        # find the project key
        keys = list(cls.result.keys())
        assert len(keys) == 1, "expected one project, got %s" % keys
        cls.pk = keys[0]
        cls.entry = cls.result[cls.pk]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_both_slices_present(self):
        ba = self.entry.get("by_account")
        self.assertIsInstance(ba, dict)
        self.assertIn(ACCT_A, ba)
        self.assertIn(ACCT_B, ba)

    def test_per_account_cost_in_window(self):
        """Each account's cost_in_window reflects only its own turns."""
        ba = self.entry["by_account"]
        a_cost = ba[ACCT_A]["cost_in_window"]["five_hour"]
        b_cost = ba[ACCT_B]["cost_in_window"]["five_hour"]
        self.assertAlmostEqual(sum(a_cost.values()), 6.0, places=2)
        self.assertAlmostEqual(sum(b_cost.values()), 1.0, places=2)

    def test_totals_cost_in_window_summed(self):
        """Top-level cost_in_window merges families across accounts."""
        total = self.entry["cost_in_window"]["five_hour"]
        self.assertAlmostEqual(sum(total.values()), 7.0, places=2)

    def test_totals_cost_saved_measured(self):
        """Top-level cost_saved_measured sums across accounts."""
        csm_a = self.entry["by_account"][ACCT_A]["cost_saved_measured"].get("five_hour")
        csm_b = self.entry["by_account"][ACCT_B]["cost_saved_measured"].get("five_hour")
        total_csm = self.entry["cost_saved_measured"].get("five_hour")
        # both may be None or numeric; when both are numeric the total equals the sum
        if csm_a is not None and csm_b is not None:
            self.assertAlmostEqual(total_csm, csm_a + csm_b, places=4)

    def test_lifetime_summed(self):
        """Lifetime cost_used and cost_saved_measured sum across accounts."""
        lt = self.entry["lifetime"]
        ba = self.entry["by_account"]
        expected_used = sum(ba[e]["lifetime"]["cost_used"] for e in ba)
        expected_saved = sum(ba[e]["lifetime"]["cost_saved_measured"] for e in ba)
        self.assertAlmostEqual(lt["cost_used"], expected_used, places=4)
        self.assertAlmostEqual(lt["cost_saved_measured"], expected_saved, places=4)

    def test_lifetime_ratio_recomputed(self):
        lt = self.entry["lifetime"]
        if lt["cost_used"] > 0:
            expected = lt["cost_saved_measured"] / lt["cost_used"]
            self.assertAlmostEqual(lt["ratio"], expected, places=3)

    def test_period_summed(self):
        """Period cost_used and cost_saved_measured sum across accounts."""
        period = self.entry["period"]
        ba = self.entry["by_account"]
        expected_used = sum(ba[e]["period"]["cost_used"] for e in ba)
        expected_saved = sum(ba[e]["period"]["cost_saved_measured"] for e in ba)
        self.assertAlmostEqual(period["cost_used"], expected_used, places=4)
        self.assertAlmostEqual(period["cost_saved_measured"], expected_saved, places=4)

    def test_windows_summed(self):
        """Top-level windows cost_used and net_saved sum across accounts."""
        for win in ("five_hour", "seven_day"):
            ba = self.entry["by_account"]
            expected_cu = sum((ba[e].get("windows") or {}).get(win, {}).get("cost_used", 0.0)
                              for e in ba)
            self.assertAlmostEqual(self.entry["windows"][win]["cost_used"], expected_cu, places=4)

    def test_pct_used_from_largest_cost_account(self):
        """Top-level pct_used is from the account with the largest cost in that window."""
        ba = self.entry["by_account"]
        for win in ("five_hour", "seven_day"):
            # account A has $6, account B has $1 in five_hour -> A is largest
            best_email = max(ba, key=lambda e: sum(
                (ba[e].get("cost_in_window", {}).get(win) or {}).values()))
            expected = ba[best_email].get("pct_used", {}).get(win)
            self.assertEqual(self.entry["pct_used"].get(win), expected)

    def test_per_account_pct_used_apportioned(self):
        """Each account's pct_used comes from its own meter reading."""
        ba = self.entry["by_account"]
        # A has meter 40, B has meter 20 for five_hour; each with one project, so share = meter
        a_pu = ba[ACCT_A]["pct_used"].get("five_hour")
        b_pu = ba[ACCT_B]["pct_used"].get("five_hour")
        self.assertIsNotNone(a_pu)
        self.assertIsNotNone(b_pu)
        self.assertGreater(a_pu, b_pu)


class ProjectsBlockSingleAccountTest(unittest.TestCase):
    """T10: single-account project still works (backward compat)."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _make_db()
        # only account A
        cls.conn.execute(
            "INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            ("s-solo", ACCT_A, PROJECT, PROJECT, WINDOW_START, _iso(NOW)))
        cls.conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-solo0", "s-solo", ACCT_A, WINDOW_START, "claude-opus-4-6", 5.0))
        cls.conn.execute(
            "INSERT INTO savings(run_id, session_id, measured_saved_usd, updated)"
            " VALUES(?, ?, ?, ?)",
            ("run-solo1", "s-solo", 3.0, WINDOW_START))
        cls.conn.commit()
        accts = {ACCT_A: {"label": "win-a", "windows": {
            "five_hour": {"pct_saved": 20.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.05, "pct_last": 30.0,
                          "started_at": NOW - 14400},
            "seven_day": {"pct_saved": 10.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.02, "pct_last": 15.0,
                          "started_at": NOW - 14400},
        }}}
        cls.result = summary.projects_block(cls.conn, cfg=_CFG, accounts=accts)
        keys = list(cls.result.keys())
        assert len(keys) == 1, "expected one project, got %s" % keys
        cls.pk = keys[0]
        cls.entry = cls.result[cls.pk]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_by_account_has_one_entry(self):
        ba = self.entry.get("by_account")
        self.assertIsInstance(ba, dict)
        self.assertEqual(len(ba), 1)
        self.assertIn(ACCT_A, ba)

    def test_top_level_keys_present(self):
        """All existing top-level keys still present for backward compat."""
        for key in ("cost_in_window", "pct_used", "pct_est", "cost_saved_measured",
                     "lifetime", "period", "windows"):
            self.assertIn(key, self.entry, "missing top-level key: %s" % key)

    def test_totals_match_single_account(self):
        """With one account, totals equal that account's values."""
        ba = self.entry["by_account"][ACCT_A]
        lt = self.entry["lifetime"]
        self.assertAlmostEqual(lt["cost_used"], ba["lifetime"]["cost_used"], places=4)
        self.assertAlmostEqual(lt["cost_saved_measured"],
                               ba["lifetime"]["cost_saved_measured"], places=4)


class ExpiredWindowSliceTest(unittest.TestCase):
    """T20: an account whose only window has reset (and with no cost) gets no project slice."""

    def test_expired_only_account_dropped(self):
        conn = _make_db()
        _seed_two_accounts(conn)
        conn.execute("UPDATE turns SET cost_usd=0.0 WHERE account=?", (ACCT_B,))
        conn.execute("DELETE FROM savings WHERE session_id='s-b'")
        conn.commit()
        accts = _accounts_block()
        accts[ACCT_B]["windows"] = {"seven_day": {"pct_per_dollar": 0.02, "pct_last": 10.0,
                                                  "started_at": NOW - 8 * 86400,
                                                  "resets_at": NOW - 86400}}
        try:
            entry = list(summary.projects_block(conn, cfg=_CFG, accounts=accts).values())[0]
        finally:
            conn.close()
        self.assertEqual(sorted(entry["by_account"]), [ACCT_A])
        self.assertAlmostEqual(sum(entry["cost_in_window"]["five_hour"].values()), 6.0, places=2)


class AccountSwitchSummaryTest(unittest.TestCase):
    """T9: accounts_block lifetime for two accounts adds up when one session switches."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _make_db()
        # A single session that switches from ACCT_A to ACCT_B mid-way.
        # savings detail has by_account splitting the measured amount.
        import json
        cls.conn.execute(
            "INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            ("s-switch", ACCT_A, PROJECT, PROJECT, WINDOW_START, _iso(NOW)))
        # Turns: 2 for A, 1 for B
        for i, acct in enumerate([ACCT_A, ACCT_A, ACCT_B]):
            cls.conn.execute(
                "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                " VALUES(?, ?, ?, ?, ?, ?, 'api')",
                ("msg-sw%d" % i, "s-switch", acct, WINDOW_START, "claude-opus-4-6", 2.0))
        # savings row with by_account detail
        detail = {"by_account": {ACCT_A: 1.5, ACCT_B: 0.5}, "saved_usd": 2.0}
        cls.conn.execute(
            "INSERT INTO savings(run_id, session_id, measured_saved_usd,"
            " measured_detail_json, updated) VALUES(?, ?, ?, ?, ?)",
            ("run-sw1", "s-switch", 2.0, json.dumps(detail), WINDOW_START))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_lifetime_adds_up(self):
        """Lifetime savings for A + B equals the session figure."""
        lt_a = summary._lifetime_for_account(self.conn, ACCT_A)
        lt_b = summary._lifetime_for_account(self.conn, ACCT_B)
        total_saved = lt_a["cost_saved_measured"] + lt_b["cost_saved_measured"]
        self.assertAlmostEqual(total_saved, 2.0, places=4,
                               msg="A's + B's lifetime savings must equal the session total")

    def test_a_gets_more(self):
        """Account A gets more savings than B (it had more turns)."""
        lt_a = summary._lifetime_for_account(self.conn, ACCT_A)
        lt_b = summary._lifetime_for_account(self.conn, ACCT_B)
        self.assertGreater(lt_a["cost_saved_measured"], lt_b["cost_saved_measured"])


class SessionsSavedNetTest(unittest.TestCase):
    """3.9.8 T4: sessions_block writes saved_net = the session-wide vanilla-net walk."""

    def test_saved_net_is_the_walk(self):
        from unittest import mock
        from pa import savings
        conn = _make_db()
        _seed_two_accounts(conn)
        calls = []

        def walk(conn_, account, t0, t1, detail=False, session=None, **kw):
            calls.append((account, t0, t1, session))
            return {"gross": 9.0, "seeds": 1.5, "net": 7.5 if t0 is None else 1.0}

        with mock.patch.object(savings, "window_saved_measured", side_effect=walk):
            out = summary.sessions_block(conn, accounts=_accounts_block())
        conn.close()
        row = out["s-a"]
        self.assertIn((ACCT_A, None, None, "s-a"), calls)
        self.assertAlmostEqual(row["saved_net"], 7.5, places=6)
        self.assertAlmostEqual(row["saved_measured"], 2.0, places=6)
        self.assertNotAlmostEqual(row["saved_net"], row["saved_measured"], places=3)
        self.assertAlmostEqual(row["windows"]["seven_day"]["net_saved"], 1.0, places=6)


class SessionsLastActiveTest(unittest.TestCase):
    """3.15.3: sessions_block keeps a session by its last activity, not its start."""

    def test_open_session_ages_by_its_last_turn(self):
        day = 86400
        conn = _make_db()
        cases = (("busy", _iso(NOW - 3 * day), None, _iso(NOW - 3600)),     # open, a turn 1 h ago
                 ("idle", _iso(NOW - 3 * day), None, _iso(NOW - 3 * day)),  # open, quiet 3 days
                 ("ended", _iso(NOW - 3 * day), _iso(NOW - 3600), None),    # ended 1 h ago
                 ("gone", _iso(NOW - 4 * day), _iso(NOW - 3 * day), None),  # ended 3 days ago
                 ("fresh", _iso(NOW - 600), None, None))                    # open, no turn yet
        for sid, started, ended, turn in cases:
            conn.execute("INSERT INTO sessions(session_id, account, project, started, ended)"
                         " VALUES(?, ?, ?, ?, ?)", (sid, ACCT_A, PROJECT, started, ended))
            if turn:
                conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd)"
                             " VALUES(?, ?, ?, ?, ?, ?)",
                             ("m-" + sid, sid, ACCT_A, turn, "claude-opus-4-6", 1.0))
        conn.commit()
        out = summary.sessions_block(conn, cfg=_CFG)
        conn.close()
        self.assertEqual(sorted(out), ["busy", "ended", "fresh"])


class EraFilterTest(unittest.TestCase):
    """T11: accounts_block and projects_block exclude pre-era sessions."""

    ERA = "2026-09-13T00:00:00Z"

    @classmethod
    def setUpClass(cls):
        conn = _make_db()
        conn.execute("CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta(key, value) VALUES('created', ?)", (cls.ERA,))
        conn.execute("INSERT INTO accounts(email) VALUES(?)", (ACCT_A,))

        # Pre-era session 1: account_source='assumed'
        conn.execute(
            "INSERT INTO sessions(session_id, account, account_source, project, cwd, started, ended)"
            " VALUES(?, ?, 'assumed', ?, ?, ?, ?)",
            ("s-pre1", ACCT_A, PROJECT, PROJECT, "2026-08-01T00:00:00Z", "2026-08-01T01:00:00Z"))
        conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-pre1", "s-pre1", ACCT_A, "2026-08-01T00:00:00Z", "claude-opus-4-6", 100.0))

        # Pre-era session 2: started before meta.created (auth, recent turn for period test)
        conn.execute(
            "INSERT INTO sessions(session_id, account, account_source, project, cwd, started, ended)"
            " VALUES(?, ?, 'auth', ?, ?, ?, ?)",
            ("s-pre2", ACCT_A, PROJECT, PROJECT, "2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z"))
        conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-pre2", "s-pre2", ACCT_A, "2026-09-22T00:00:00Z", "claude-opus-4-6", 50.0))

        # Post-era session 1
        conn.execute(
            "INSERT INTO sessions(session_id, account, account_source, project, cwd, started, ended)"
            " VALUES(?, ?, 'auth', ?, ?, ?, ?)",
            ("s-post1", ACCT_A, PROJECT, PROJECT, "2026-09-14T00:00:00Z", "2026-09-14T01:00:00Z"))
        conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-post1", "s-post1", ACCT_A, "2026-09-14T00:00:00Z", "claude-opus-4-6", 10.0))

        # Post-era session 2 (recent turn for period test + savings)
        conn.execute(
            "INSERT INTO sessions(session_id, account, account_source, project, cwd, started, ended)"
            " VALUES(?, ?, 'auth', ?, ?, ?, ?)",
            ("s-post2", ACCT_A, PROJECT, PROJECT, "2026-09-15T00:00:00Z", "2026-09-15T01:00:00Z"))
        conn.execute(
            "INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
            " VALUES(?, ?, ?, ?, ?, ?, 'api')",
            ("msg-post2", "s-post2", ACCT_A, "2026-09-22T00:00:00Z", "claude-opus-4-6", 5.0))
        conn.execute(
            "INSERT INTO savings(run_id, session_id, measured_saved_usd, updated)"
            " VALUES(?, ?, ?, ?)",
            ("run-post2", "s-post2", 1.0, "2026-09-22T00:00:00Z"))

        conn.commit()
        cls.conn = conn

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_accounts_block_lifetime_excludes_pre_era(self):
        """Lifetime cost_used counts only post-era sessions ($10 + $5 = $15)."""
        result = summary.accounts_block(self.conn, cfg=_CFG)
        acct = result.get(ACCT_A)
        self.assertIsNotNone(acct, "account %s not in result" % ACCT_A)
        lt = acct["lifetime"]
        self.assertAlmostEqual(lt["cost_used"], 15.0, places=2)

    def test_accounts_block_period_excludes_pre_era(self):
        """Period cost_used excludes pre-era even when the turn's ts is in-period."""
        result = summary.accounts_block(self.conn, cfg=_CFG)
        acct = result.get(ACCT_A)
        period = acct["period"]
        # msg-pre2 ($50) has ts in-period but session s-pre2 is pre-era -> excluded
        # msg-post2 ($5) has ts in-period and session is post-era -> counted
        self.assertAlmostEqual(period["cost_used"], 5.0, places=2)

    def test_accounts_block_era_start_stored(self):
        result = summary.accounts_block(self.conn, cfg=_CFG)
        acct = result.get(ACCT_A)
        self.assertEqual(acct.get("era_start"), self.ERA)

    def test_projects_block_lifetime_excludes_pre_era(self):
        accts = summary.accounts_block(self.conn, cfg=_CFG)
        projects = summary.projects_block(self.conn, cfg=_CFG, accounts=accts)
        for pkey, entry in projects.items():
            lt = entry.get("lifetime") or {}
            self.assertAlmostEqual(lt.get("cost_used", 0.0), 15.0, places=2)

    def test_lifetime_since_override(self):
        """A statusline.lifetime_since cfg override moves the era boundary."""
        cfg = {"statusline": {"lifetime_since": "2026-09-14T12:00:00Z"}}
        result = summary.accounts_block(self.conn, cfg=cfg)
        acct = result.get(ACCT_A)
        lt = acct["lifetime"]
        # only s-post2 ($5) is in-era (s-post1 started 2026-09-14T00:00 < override)
        self.assertAlmostEqual(lt["cost_used"], 5.0, places=2)


class DropResetStartTest(unittest.TestCase):
    """T21.1.1.1.1: a drop reset's window_instances.started_at becomes the window's started_at."""

    def test_drop_started_at_wins(self):
        conn = _make_db()
        conn.execute("CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO accounts(email) VALUES(?)", (ACCT_A,))
        resets = NOW + 3 * 86400
        drop_start = NOW - 5 * 3600
        conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct, resets_at)"
                     " VALUES(?, ?, 's', 'seven_day', 2.0, ?)", (_iso(NOW), ACCT_A, resets))
        conn.execute("INSERT INTO window_instances(account, window, resets_at, started_at,"
                     " reset_source) VALUES(?, 'seven_day', ?, ?, 'drop')",
                     (ACCT_A, resets, drop_start))
        conn.commit()
        result = summary.accounts_block(conn, cfg=_CFG)
        conn.close()
        self.assertEqual(result[ACCT_A]["windows"]["seven_day"]["started_at"], drop_start)


class LedgerCostInWindowTest(unittest.TestCase):
    """fix-8: windows[win].ledger_cost_in_window = kind='api' turns cost since the window start,
    below the ratio threshold too; cost_in_window stays the harness cost-state figure."""

    def test_ledger_cost_in_window(self):
        conn = _make_db()
        conn.execute("CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO accounts(email) VALUES(?)", (ACCT_A,))
        resets = NOW + 3 * 86400
        start = NOW - 5 * 3600
        for ts, cost in ((NOW - 7200, 30.0), (NOW, 20.0)):
            conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct, resets_at,"
                         " session_cost) VALUES(?, ?, 's', 'seven_day', 2.0, ?, ?)",
                         (_iso(ts), ACCT_A, resets, cost * 3))
        conn.execute("INSERT INTO window_instances(account, window, resets_at, started_at,"
                     " reset_source) VALUES(?, 'seven_day', ?, ?, 'drop')", (ACCT_A, resets, start))
        rows = (("m-old", NOW - 6 * 3600, 'api', 100.0), ("m-1", NOW - 3600, 'api', 3.0),
                ("m-2", NOW - 60, 'api', 4.5), ("m-x", NOW - 60, 'agent', 9.0))
        for mid, ts, kind, cost in rows:
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, 's', ?, ?, 'claude-opus-4-6', ?, ?)",
                         (mid, ACCT_A, _iso(ts), cost, kind))
        conn.commit()
        expected = conn.execute(
            "SELECT SUM(cost_usd) FROM turns WHERE kind='api' AND account=? AND ts>=?",
            (ACCT_A, _iso(start))).fetchone()[0]
        harness = summary._cost_in_window(conn, ACCT_A, "seven_day", start)
        w = summary.accounts_block(conn, cfg=_CFG)[ACCT_A]["windows"]["seven_day"]
        conn.close()
        self.assertAlmostEqual(expected, 7.5, places=6)
        self.assertAlmostEqual(w["ledger_cost_in_window"], expected, places=6)
        self.assertEqual(w["cost_in_window"], harness)
        self.assertNotEqual(w["fit_method"], "ratio")  # pct 2 < fit.ratio_min_pct


class WeeksByWindowTest(unittest.TestCase):
    """fix-9: month/lifetime weeks sum each seven-day instance's spend at its own rate;
    fix-9.c2: saved from the per-run rows by return week; account and session entries too."""

    def test_each_instance_at_its_own_rate(self):
        conn = _make_db()
        day = 86400
        # (resets_at, samples, max pct): $100 of account spend in each of the first two
        insts = ((NOW - 14 * day, 12, 50.0), (NOW - 7 * day, 12, 25.0), (NOW + 3 * day, 3, 90.0))
        for k, (resets, n, pct) in enumerate(insts):
            for i in range(n):
                conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct,"
                             " resets_at) VALUES(?, ?, ?, 'seven_day', ?, ?)",
                             (_iso(resets - 6 * day + i * 3600), ACCT_A, "u%d" % k,
                              pct * (i + 1) / n, resets))
        conn.execute("INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
                     " VALUES(?, ?, ?, ?, ?, ?)",
                     ("s-p", ACCT_A, PROJECT, PROJECT, _iso(NOW - 31 * day), _iso(NOW)))
        conn.execute("INSERT INTO sessions(session_id, account, project, cwd) VALUES(?, ?, ?, ?)",
                     ("s-o", ACCT_A, "/test/other", "/test/other"))
        turns = [("p-pre", "s-p", NOW - 30 * day, 10.0),               # before both: unrated
                 ("p-1", "s-p", NOW - 20 * day, 40.0), ("o-1", "s-o", NOW - 20 * day, 60.0),
                 ("p-2", "s-p", NOW - 13 * day, 40.0), ("o-2", "s-o", NOW - 13 * day, 60.0),
                 ("p-3", "s-p", NOW - day, 5.0)]                        # 3-sample instance: unrated
        for mid, sid, ts, cost in turns:
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, ?, ?, ?, 'claude-opus-4-6', ?, 'api')",
                         (mid, sid, ACCT_A, _iso(ts), cost))
        # fix-9.c2: savings rows timed by their run's return; $20 in each rated instance,
        # $7 returned before both, one run without an agent_runs row (session start: unrated)
        conn.execute("CREATE TABLE agent_runs(run_id TEXT PRIMARY KEY, session_id TEXT,"
                     " started TEXT, ended TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO accounts(email) VALUES(?)", (ACCT_A,))
        for rid, saved, ended in (("r-1", 20.0, NOW - 19 * day), ("r-2", 20.0, NOW - 12 * day),
                                  ("r-pre", 7.0, NOW - 29 * day), ("r-none", 3.0, None)):
            conn.execute("INSERT INTO savings(run_id, session_id, measured_saved_usd, updated)"
                         " VALUES(?, 's-p', ?, ?)", (rid, saved, _iso(NOW)))
            if ended is not None:
                conn.execute("INSERT INTO agent_runs(run_id, session_id, started, ended)"
                             " VALUES(?, 's-p', ?, ?)", (rid, _iso(ended - 3600), _iso(ended)))
        conn.commit()
        wk = summary._weeks_by_window(conn, ACCT_A, {"s-p"}, None)
        self.assertAlmostEqual(wk["weeks_saved"], 20 * 0.005 + 20 * 0.0025, places=6)
        self.assertAlmostEqual(wk["unrated_saved_usd"], 10.0, places=6)   # r-pre + r-none
        acct = summary.accounts_block(conn, cfg=_CFG)[ACCT_A]
        for blk in ("lifetime", "period"):
            for fld in summary.WEEKS_FIELDS:
                self.assertIn(fld, acct[blk])
        self.assertAlmostEqual(acct["lifetime"]["weeks_used"], 100 * 0.005 + 100 * 0.0025,
                               places=6)
        sess = summary.sessions_block(conn, cfg=_CFG)["s-p"]
        for fld in summary.WEEKS_FIELDS:
            self.assertIn(fld, sess)
        self.assertAlmostEqual(sess["weeks_used"], 0.30, places=6)
        self.assertEqual(wk["instances"], 2)
        self.assertAlmostEqual(wk["weeks_used"], 0.30, places=6)   # 0.20 + 0.10
        self.assertNotAlmostEqual(wk["weeks_used"], 80 * 0.005, places=3)
        self.assertNotAlmostEqual(wk["weeks_used"], 80 * 0.0025, places=3)
        self.assertAlmostEqual(wk["unrated_usd"], 15.0, places=6)
        accts = {ACCT_A: {"label": "a", "windows": {}}}
        lt = summary.projects_block(conn, cfg=_CFG, accounts=accts)[summary._project_key(PROJECT)]
        # 3.11 T9: the same ledger without the account's renewal day: no period, no month weeks, the
        # lifetime untouched, and the project names the account whose month is unknown
        unknown_acct = summary.accounts_block(conn, cfg={})[ACCT_A]
        unknown_proj = summary.projects_block(conn, cfg={}, accounts=accts)[summary._project_key(PROJECT)]
        conn.close()
        self.assertAlmostEqual(lt["lifetime"]["weeks_used"], 0.30, places=6)
        self.assertAlmostEqual(lt["by_account"][ACCT_A]["lifetime"]["unrated_usd"], 15.0, places=6)
        self.assertIn("weeks_used", lt["period"])
        self.assertEqual(unknown_acct["period"], {"start": None, "renewal_day": None, "unknown": True})
        self.assertIn("weeks_used", unknown_acct["lifetime"])
        self.assertEqual(unknown_proj["period"].get("unknown_accounts"), [ACCT_A])
        self.assertTrue(unknown_proj["by_account"][ACCT_A]["period"].get("unknown"))

    def test_account_weeks_follow_the_era(self):
        """fix-9.c3: the account block's weeks cover only era sessions, like its dollars."""
        conn = _make_db()
        day = 86400
        conn.execute("CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, label TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO accounts(email) VALUES(?)", (ACCT_A,))
        conn.execute("INSERT INTO meta(key, value) VALUES('created', ?)", (_iso(NOW - 10 * day),))
        resets = NOW - day                        # one rated instance: 40% over $100
        for i in range(12):
            conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct,"
                         " resets_at) VALUES(?, ?, 'u', 'seven_day', ?, ?)",
                         (_iso(resets - 6 * day + i * 3600), ACCT_A, 40.0 * (i + 1) / 12, resets))
        for sid, started in (("s-old", NOW - 20 * day), ("s-new", NOW - 9 * day)):
            conn.execute("INSERT INTO sessions(session_id, account, account_source, project, cwd,"
                         " started) VALUES(?, ?, 'auth', ?, ?, ?)",
                         (sid, ACCT_A, PROJECT, PROJECT, _iso(started)))
        turns = [("o-in", "s-old", NOW - 7 * day, 60.0),     # pre-era: in the rate, not the weeks
                 ("o-out", "s-old", NOW - 15 * day, 1000.0),  # pre-era, unrated: in neither
                 ("n-in", "s-new", NOW - 7 * day, 40.0),
                 ("n-out", "s-new", NOW - day // 2, 5.0)]     # era, after the instance: unrated
        for mid, sid, ts, cost in turns:
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, ?, ?, ?, 'claude-opus-4-6', ?, 'api')",
                         (mid, sid, ACCT_A, _iso(ts), cost))
        conn.commit()
        lt = summary.accounts_block(conn, cfg=_CFG)[ACCT_A]["lifetime"]
        conn.close()
        self.assertAlmostEqual(lt["cost_used"], 45.0, places=6)
        self.assertAlmostEqual(lt["weeks_used"], 40 * 0.004, places=6)
        self.assertAlmostEqual(lt["unrated_usd"], 5.0, places=6)


class RebuildFailureLogTest(unittest.TestCase):
    """A failed rebuild names its error in hooks.log (3.14.2 T2: the WSL ledger logged 59 bare
    ``summary_rebuild_failed`` lines while a missing column failed every rebuild)."""

    def test_the_failure_line_carries_the_exception(self):
        import shutil
        import tempfile
        from unittest import mock

        from pa import log

        tmp = tempfile.mkdtemp(prefix="pa3-summary-")
        old_env, old_log = os.environ.get("PA_LEDGER_DIR"), log._LOG_PATH
        os.environ["PA_LEDGER_DIR"] = tmp
        log.set_log_path(os.path.join(tmp, "hooks.log"))
        try:
            boom = sqlite3.OperationalError("no such column: fit_detail")
            with mock.patch.object(summary, "era_start", return_value=""), \
                    mock.patch.object(summary, "accounts_block", side_effect=boom):
                self.assertIsNone(summary.rebuild(sqlite3.connect(":memory:"), {}))
            with open(os.path.join(tmp, "hooks.log"), encoding="utf-8") as fh:
                line = fh.read().strip().splitlines()[-1]
            self.assertIn("summary_rebuild_failed", line)
            self.assertIn("OperationalError: no such column: fit_detail", line)
            self.assertFalse(os.path.exists(os.path.join(tmp, "summary.json")))   # nothing written
        finally:
            log.set_log_path(old_log)
            if old_env is None:
                os.environ.pop("PA_LEDGER_DIR", None)
            else:
                os.environ["PA_LEDGER_DIR"] = old_env
            shutil.rmtree(tmp, ignore_errors=True)


class RebuildOutsideLockTest(unittest.TestCase):
    """T11 (3.15): blocks are computed outside the summary.json lock; overlapping rebuilds
    coalesce, the later-started result wins, and nothing logs ``summary_rebuild_failed``."""

    def setUp(self):
        import tempfile

        from pa import log

        self.tmp = tempfile.mkdtemp(prefix="pa3-summary-")
        self.old_env, self.old_log = os.environ.get("PA_LEDGER_DIR"), log._LOG_PATH
        os.environ["PA_LEDGER_DIR"] = self.tmp
        log.set_log_path(os.path.join(self.tmp, "hooks.log"))
        self.path = os.path.join(self.tmp, "summary.json")

    def tearDown(self):
        import shutil

        from pa import log

        log.set_log_path(self.old_log)
        if self.old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self.old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mocks(self, hold=None, entered=None):
        """Mock every block; ``accounts_block`` in thread ``A`` blocks on ``hold``."""
        import threading
        from unittest import mock

        def _who():
            return threading.current_thread().name

        def _accounts(conn, cfg=None, readers=None):
            if hold is not None and _who() == "A":
                entered.set()
                hold.wait(10)
            return {"x@y": {"who": _who()}}

        return [
            mock.patch.object(summary, "era_start", return_value=""),
            mock.patch.object(summary, "accounts_block", side_effect=_accounts),
            mock.patch.object(summary, "projects_block",
                              side_effect=lambda *a, **k: {"p": {"who": _who()}}),
            mock.patch.object(summary, "sessions_block",
                              side_effect=lambda *a, **k: {"s-" + _who(): {}}),
            mock.patch.object(summary, "alerts_block", return_value=[]),
            mock.patch.object(summary, "_seed_for_projects", return_value=[]),
            mock.patch.object(summary, "_modeled_block", return_value=None),
        ]

    def _run(self, patches, fn):
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return fn()

    def _rebuild_in(self, name, out, **kw):
        import threading

        def _go():
            out[name] = summary.rebuild(sqlite3.connect(":memory:"), {}, **kw)

        t = threading.Thread(target=_go, name=name)
        t.start()
        return t

    def _hooks_log(self):
        try:
            with open(os.path.join(self.tmp, "hooks.log"), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def test_lock_is_free_during_compute(self):
        import threading

        from pa import fsutil

        hold, entered, out = threading.Event(), threading.Event(), {}

        def _body():
            a = self._rebuild_in("A", out)
            self.assertTrue(entered.wait(5))
            try:
                got = fsutil.locked_update(self.path, lambda d: d or {"probe": 1}, timeout_ms=100)
            finally:
                hold.set()
                a.join(10)
            return got

        got = self._run(self._mocks(hold, entered), _body)
        self.assertEqual(got, {"probe": 1})
        self.assertIsInstance(out["A"], dict)
        self.assertNotIn("summary_rebuild_failed", self._hooks_log())

    def test_later_started_rebuild_wins(self):
        import json
        import threading

        hold, entered, out = threading.Event(), threading.Event(), {}

        def _body():
            a = self._rebuild_in("A", out)
            self.assertTrue(entered.wait(5))
            time.sleep(0.02)                      # B starts strictly after A
            b = self._rebuild_in("B", out)
            b.join(10)
            hold.set()
            a.join(10)

        self._run(self._mocks(hold, entered), _body)
        self.assertIsInstance(out["A"], dict)
        self.assertIsInstance(out["B"], dict)
        self.assertNotIn("summary_rebuild_failed", self._hooks_log())
        with open(self.path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["accounts"], {"x@y": {"who": "B"}})
        self.assertEqual(doc["projects"], {"p": {"who": "B"}})
        self.assertIn("s-B", doc["sessions"])
        self.assertEqual(out["A"]["accounts"], {"x@y": {"who": "B"}})

    def test_sessions_only_keeps_windows_group(self):
        import json

        def _body():
            summary.rebuild(sqlite3.connect(":memory:"), {})
            with open(self.path, encoding="utf-8") as fh:
                first = json.load(fh)
            time.sleep(0.02)
            summary.rebuild(sqlite3.connect(":memory:"), {}, sessions_only=True)
            with open(self.path, encoding="utf-8") as fh:
                return first, json.load(fh)

        first, doc = self._run(self._mocks(), _body)
        self.assertEqual(doc["accounts"], first["accounts"])
        self.assertEqual(doc["projects"], first["projects"])
        self.assertEqual(doc["meta"]["computed_at"]["windows"],
                         first["meta"]["computed_at"]["windows"])
        self.assertGreater(doc["meta"]["computed_at"]["sessions"],
                           first["meta"]["computed_at"]["sessions"])


def _seed_ledger(path, sessions, turns, pct=None):
    """A real-schema ledger at ``path``: ``sessions`` [(sid, project)], ``turns``
    [(msg_id, sid, cost)] for ACCT_A an hour ago; ``pct``: one seven_day sample."""
    from pa import db

    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = db.connect(path)
    db.init_schema(conn)
    conn.execute("INSERT OR IGNORE INTO accounts(email) VALUES(?)", (ACCT_A,))
    for sid, proj in sessions:
        conn.execute("INSERT INTO sessions(session_id, account, account_source, project, cwd)"
                     " VALUES(?, ?, 'auth', ?, ?)", (sid, ACCT_A, proj, proj))
    for mid, sid, cost in turns:
        conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                     " VALUES(?, ?, ?, ?, 'claude-opus-4-6', ?, 'api')",
                     (mid, sid, ACCT_A, _iso(NOW - 3600), cost))
    if pct is not None:
        conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct, resets_at)"
                     " VALUES(?, ?, 's-l', 'seven_day', ?, ?)",
                     (_iso(NOW - 60), ACCT_A, pct, NOW + 3 * 86400))
    conn.commit()
    return conn


class UnweightedFamilyShareTest(unittest.TestCase):
    """T17.c2 (3.15): a family with spend but no fit weight counts at pct_per_dollar on
    both sides of the apportioning, so the shares still add up to the meter."""

    def test_unweighted_family_counts_at_pct_per_dollar(self):
        conn = _make_db()
        other = "/test/otherproject"
        ts = _iso(NOW - 3600)
        for sid, proj in (("s-a", PROJECT), ("s-b", other)):
            conn.execute("INSERT INTO sessions(session_id, account, project, cwd, started, ended)"
                         " VALUES(?, ?, ?, ?, ?, ?)", (sid, ACCT_A, proj, proj, ts, _iso(NOW)))
        for mid, sid, model in (("m1", "s-a", "claude-opus-4-6"), ("m2", "s-a", "claude-sonnet-4-5"),
                                ("m3", "s-b", "claude-opus-4-6")):
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, ?, ?, ?, ?, ?, 'api')", (mid, sid, ACCT_A, ts, model, 10.0))
        conn.commit()
        win = {"pct_per_dollar": 0.01, "pct_last": 50.0, "started_at": NOW - 7200,
               "fit_detail": {"opus": {"weight": 0.02}},
               "ledger_cost_by_family": {"opus": 20.0, "sonnet": 10.0},
               "ledger_cost_in_window": 30.0}
        res = summary.projects_block(conn, cfg=_CFG, accounts={ACCT_A: {"windows": {"seven_day": win}}})
        conn.close()
        a = res[summary._project_key(PROJECT)]["by_account"][ACCT_A]
        b = res[summary._project_key(other)]["by_account"][ACCT_A]
        # A: 10*2 + 10*1 = 30, B: 10*2 = 20, account: 20*2 + 10*1 = 50 -> 30 and 20 of the 50 meter
        self.assertAlmostEqual(a["pct_used"]["seven_day"], 30.0, places=3)
        self.assertAlmostEqual(b["pct_used"]["seven_day"], 20.0, places=3)
        self.assertAlmostEqual(a["pct_est"]["seven_day"], 20.0, places=3)    # report figure unchanged


class ExtraRootRebuildTest(unittest.TestCase):
    """T17 (3.15): a rebuild without readers opens the config's extra_roots itself; the
    projects' shares divide by the account's whole spend; the read rule keeps the windows
    group on disk when a root cannot be read."""

    def setUp(self):
        import tempfile
        from unittest import mock

        from pa import db, log

        self.tmp = tempfile.mkdtemp(prefix="pa3-summary-")
        self.old_env, self.old_log = os.environ.get("PA_LEDGER_DIR"), log._LOG_PATH
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.tmp, "local")
        log.set_log_path(os.path.join(self.tmp, "hooks.log"))
        self.union = mock.patch.object(db, "union_dir",
                                       return_value=os.path.join(self.tmp, "union"))
        self.union.start()
        self.conn = _seed_ledger(os.path.join(self.tmp, "local", "ledger.sqlite"),
                                 [("s-l", PROJECT)], [("m-l", "s-l", 10.0)], pct=55.0)
        self.path = os.path.join(self.tmp, "local", "summary.json")

    def tearDown(self):
        import shutil

        from pa import log

        self.conn.close()
        self.union.stop()
        log.set_log_path(self.old_log)
        if self.old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self.old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, root):
        return {"accounts": {ACCT_A: {"renewal_day": 21}},
                "extra_roots": [{"path": root, "account": ACCT_A}]}

    def test_local_project_share_of_the_whole_spend(self):
        other = os.path.join(self.tmp, "other")
        _seed_ledger(os.path.join(other, "ledger.sqlite"), [("s-o", None)],
                     [("m-o", "s-o", 100.0)]).close()           # no project key
        doc = summary.rebuild(self.conn, self._cfg(other))
        w = doc["accounts"][ACCT_A]["windows"]["seven_day"]
        self.assertAlmostEqual(w["ledger_cost_in_window"], 110.0, places=6)
        pu = doc["projects"][summary._project_key(PROJECT)]["by_account"][ACCT_A]["pct_used"]
        self.assertAlmostEqual(pu["seven_day"], 55.0 * 10 / 110, places=2)    # ~9 % of the meter

    def test_unreadable_root_keeps_the_windows_group(self):
        import json
        from unittest import mock

        from pa import db

        bad = os.path.join(self.tmp, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "ledger.sqlite"), "wb") as fh:
            fh.write(b"not a database" * 100)
        prev = {"accounts": {"old": 1}, "projects": {"p": 1}, "sessions": {},
                "alerts": [{"kind": "seed_growth", "n": 1}],
                "meta": {"computed_at": {"windows": 123.0, "sessions": 100.0}}}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(prev, fh)
        real = db.union_readers
        with mock.patch.object(db, "union_readers", side_effect=real) as ur:
            doc = summary.rebuild(self.conn, self._cfg(bad))
        self.assertEqual(ur.call_count, 2)                      # one invalidate-and-retry
        self.assertEqual(doc["accounts"], {"old": 1})
        self.assertEqual(doc["projects"], {"p": 1})
        self.assertIn({"kind": "seed_growth", "n": 1}, doc["alerts"])
        self.assertEqual(doc["meta"]["computed_at"]["windows"], 123.0)
        self.assertGreater(doc["meta"]["computed_at"]["sessions"], 100.0)   # sessions written
        self.assertFalse(os.path.exists(os.path.join(
            db._union_dest_dir(db._root_db(bad)), "ledger.sqlite")))         # torn copy dropped
        with open(os.path.join(self.tmp, "hooks.log"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("summary_extra_root_failed", text)
        self.assertIn("bad", text)
        self.assertNotIn("summary_rebuild_failed", text)
        os.remove(self.path)                                     # absent: no windows group at all
        doc = summary.rebuild(self.conn, self._cfg(os.path.join(self.tmp, "missing")))
        self.assertNotIn("windows", doc["meta"]["computed_at"])
        self.assertEqual(doc["accounts"], {})


if __name__ == "__main__":
    unittest.main()
