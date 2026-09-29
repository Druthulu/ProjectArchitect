"""T18: model-scoped (Fable) windows recorded per account in the ledger.

(a) a statusline payload with ``rate_limits.model_scoped`` records
``model_scoped:<name>`` utilization and window_instances rows for its account;
(b) without it, a fresh usage-API cache of the same account is merged, another
account's cache is not; (c) ``pa-ledger report`` shows the window beside 5h/7d.
No network: ``usage_api.poll`` and ``accounts.refresh_account`` are stubbed.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import accounts, config, db, ledger_cli, paths, statusline, usage_api  # noqa: E402

ACCT_A = "dev-a@example.com"
ACCT_B = "dev-b@example.com"
SID = "sid-t18"
FABLE_RESETS = 1789869600


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh)


class ScopedCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-scoped-")
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        statusline._JSON_CACHE.clear()
        self._poll, self._refresh = usage_api.poll, accounts.refresh_account
        usage_api.poll = lambda *a, **k: None
        accounts.refresh_account = lambda *a, **k: (None, False, None)
        self.cfg = config.defaults()
        write_json(paths.summary_path(), {"sessions": {SID: {"account": ACCT_A}}})
        self.payload = {"session_id": SID, "model": {"id": "claude-opus-5-5"},
                        "cwd": self.dir,
                        "rate_limits": {
                            "five_hour": {"used_percentage": 19, "resets_at": 1789272600},
                            "seven_day": {"used_percentage": 1, "resets_at": 1789869600}}}

    def tearDown(self):
        usage_api.poll, accounts.refresh_account = self._poll, self._refresh
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        statusline._JSON_CACHE.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def sample_twice(self):
        """first sample, then a 5h change so the change path drains into sqlite."""
        statusline.sample(self.payload, self.cfg)
        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 21
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)

    def rows(self, sql):
        conn = db.connect()
        try:
            return conn.execute(sql).fetchall()
        finally:
            db.close(conn)

    def assert_fable_rows(self, pct):
        util = self.rows("SELECT account, pct, resets_at FROM utilization "
                         "WHERE window='model_scoped:Fable'")
        self.assertTrue(util)
        self.assertEqual({r["account"] for r in util}, {ACCT_A})
        self.assertEqual({r["pct"] for r in util}, {pct})
        self.assertEqual({r["resets_at"] for r in util}, {FABLE_RESETS})
        inst = self.rows("SELECT account, resets_at FROM window_instances "
                         "WHERE window='model_scoped:Fable'")
        self.assertEqual([(r["account"], r["resets_at"]) for r in inst], [(ACCT_A, FABLE_RESETS)])

    def write_cache(self, account, age_s=60):
        write_json(os.path.join(paths.ledger_dir(), "usage_api.json"), {
            "ts": time.time() - age_s, "account": account, "next_poll": time.time() + 600,
            "windows": {"model_scoped:Fable": {"pct": 40.0, "resets_at": FABLE_RESETS},
                        "five_hour": {"pct": 99.0, "resets_at": 1}}})

    # (a)
    def test_payload_model_scoped_recorded_per_account(self):
        self.payload["rate_limits"]["model_scoped"] = [
            {"display_name": "Fable", "used_percentage": 33, "resets_at": FABLE_RESETS}]
        self.sample_twice()
        self.assert_fable_rows(33.0)

    # (b)
    def test_fresh_cache_of_same_account_is_merged(self):
        self.write_cache(ACCT_A)
        self.sample_twice()
        self.assert_fable_rows(40.0)
        # the cache's five_hour never overrides the payload's
        self.assertNotIn(99.0, [r["pct"] for r in self.rows(
            "SELECT pct FROM utilization WHERE window='five_hour'")])

    def test_cache_of_other_account_is_not_merged(self):
        self.write_cache(ACCT_B)
        self.sample_twice()
        self.assertEqual(self.rows("SELECT * FROM utilization WHERE window LIKE 'model_scoped:%'"), [])
        self.assertEqual(self.rows("SELECT * FROM window_instances "
                                   "WHERE window LIKE 'model_scoped:%'"), [])

    def test_stale_cache_is_not_merged(self):
        cache = {"ts": time.time() - 31 * 60, "account": ACCT_A,
                 "windows": {"model_scoped:Fable": {"pct": 40.0, "resets_at": FABLE_RESETS}}}
        sample = statusline.build_sample(self.payload, self.cfg, account=ACCT_A,
                                         usage_cache=cache)
        self.assertNotIn("model_scoped:Fable", sample["windows"])


class ScopedPollAccountTest(unittest.TestCase):
    """usage_api.poll stamps the account and refetches another account's cache."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-scoped-poll-")
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        self._fetch, self._token = usage_api.fetch, usage_api.read_token
        self.calls = []
        usage_api.read_token = lambda d: "tok"

        def fake_fetch(token, timeout=5.0):
            self.calls.append(1)
            return self.reply

        usage_api.fetch = fake_fetch
        self.reply = ({"limits": [{"kind": "weekly_scoped", "percent": 12,
                                   "scope": {"model": {"display_name": "Fable"}}}]}, None)
        self.cfg = config.defaults()

    def tearDown(self):
        usage_api.fetch, usage_api.read_token = self._fetch, self._token
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        shutil.rmtree(self.dir, ignore_errors=True)

    def cache(self):
        with open(os.path.join(self.dir, "usage_api.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_account_stamped_and_switch_refetches_within_backoff_rules(self):
        now = 1_800_000_000.0
        usage_api.poll(self.cfg, now=now, account=ACCT_A)
        self.assertEqual(self.cache()["account"], ACCT_A)
        usage_api.poll(self.cfg, now=now + 60, account=ACCT_A)       # within next_poll
        self.assertEqual(len(self.calls), 1)
        self.reply = (None, {"status": 429, "retry_after": 10})
        usage_api.poll(self.cfg, now=now + 120, account=ACCT_B)      # other account: refetch
        self.assertEqual(len(self.calls), 2)
        usage_api.poll(self.cfg, now=now + 130, account=ACCT_B)      # 429 backoff honoured
        self.assertEqual(len(self.calls), 2)
        self.reply = ({"limits": []}, None)
        usage_api.poll(self.cfg, now=now + 400, account=ACCT_B)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.cache()["account"], ACCT_B)


class ReportScopedTest(unittest.TestCase):
    """(c) report lists the Fable window beside five_hour/seven_day."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-scoped-report-")
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": "s_a", "account": ACCT_A, "cwd": "Z:/P",
                                 "started": "2026-09-01T09:00:00Z"})
        db.upsert_turn(conn, {"msg_id": "m0", "run_id": "s_a", "session_id": "s_a",
                              "account": ACCT_A, "ts": "2026-09-01T10:00:00Z",
                              "model": "claude-fable-5-1", "cost_usd": 3.0, "kind": "api"})
        for ts, win, pct in (("2026-09-01T10:00:00Z", "five_hour", 20.0),
                             ("2026-09-01T10:00:00Z", "seven_day", 5.0),
                             ("2026-09-01T10:00:00Z", "model_scoped:Fable", 30.0),
                             ("2026-09-01T11:00:00Z", "model_scoped:Fable", 47.0)):
            db.insert_utilization(conn, {"ts": ts, "account": ACCT_A, "session_id": "s_a",
                                         "window": win, "pct": pct, "resets_at": FABLE_RESETS,
                                         "source": "statusline"})
        db.close(conn)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, *argv):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = ledger_cli.main(list(argv))
        return rc, buf_out.getvalue(), buf_err.getvalue()

    def test_json_and_table(self):
        rc, text, errs = self.run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        acct = [a for a in json.loads(text)["accounts"] if a["account"] == ACCT_A][0]
        self.assertEqual(acct["pct_used"], {"five_hour": 20.0, "model_scoped:Fable": 47.0,
                                            "seven_day": 5.0})
        rc, text, errs = self.run_cli("report")
        self.assertEqual(rc, 0, errs)
        header = [line for line in text.splitlines() if line.startswith("account")][0]
        self.assertIn("5h", header)
        self.assertIn("7d", header)
        self.assertIn("Fable 7d", header)
        self.assertLess(header.index(" 7d"), header.index("Fable 7d"))
        self.assertIn("47%", text)


class BesidePhaseTest(unittest.TestCase):
    """T28: a ``beside <phase>`` session in the phase's window never joins the phase's sessions."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-scoped-beside-")
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        self.conn = db.connect(paths.db_path())
        db.init_schema(self.conn)
        for sid, phase in (("s_owner", "24"), ("s_beside", "beside 24")):
            db.upsert_session(self.conn, {"session_id": sid, "account": ACCT_A, "cwd": "Z:/P",
                                          "started": "2026-09-01T09:00:00Z", "phase": phase})
            for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z")):
                db.upsert_turn(self.conn, {"msg_id": "%s_m%d" % (sid, i), "run_id": sid,
                                           "session_id": sid, "account": ACCT_A, "ts": ts,
                                           "model": "claude-opus-5-5", "cost_usd": 1.0,
                                           "kind": "api"})
        self.conn.commit()

    def tearDown(self):
        db.close(self.conn)
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_ledger_cli_returns_owner_only(self):
        self.assertEqual(ledger_cli._session_ids_for_phase(
            self.conn, ["s_owner", "s_beside"], "24"), ["s_owner"])

    def test_audit_returns_owner_only(self):
        from pa import audit
        self.assertEqual(audit._session_ids_for_phase(self.conn, "Z:/P", "24"), ["s_owner"])


if __name__ == "__main__":
    unittest.main()
