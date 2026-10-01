"""Tests for ``pa.accounts``: stamp_session cred_key, refresh_account."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import accounts, db, log, paths, running


OLD = "old@example.com"
NEW = "new@example.com"
SID = "test-session-001"


class RefreshAccountTest(unittest.TestCase):
    """refresh_account detects a mid-session account switch via the credentials key."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-acct-")
        self.ledger = os.path.join(self.dir, "usage-ledger")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        # isolate from the real ~/.claude.json and hooks.log
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.dir, "cfg-empty")
        log.set_log_path(os.path.join(self.dir, "hooks.log"))
        paths.ensure_ledger_tree()
        # ensure state/accounts dir exists for stamps
        os.makedirs(paths.state_path("accounts"), exist_ok=True)

        self.conn = db.connect(paths.db_path())
        db.init_schema(self.conn)

        # seed a session row so upsert_session has something to merge into
        db.upsert_session(self.conn, {"session_id": SID, "account": OLD})

        # monkeypatch credentials_key to a controllable value
        self._orig_cred_key = accounts.credentials_key
        self._cred_key = "100:50"
        accounts.credentials_key = lambda: self._cred_key

        # monkeypatch auth_status -- tests override as needed
        self._orig_auth = accounts.auth_status
        self._auth_called = False

        # stamp with the old account under the current key
        accounts.stamp_session(SID, OLD, source="auth")

    def tearDown(self):
        accounts.credentials_key = self._orig_cred_key
        accounts.auth_status = self._orig_auth
        os.environ.pop("PA_LEDGER_DIR", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        log.set_log_path(None)
        try:
            self.conn.close()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- (a) unchanged key: no subprocess

    def test_unchanged_key_returns_stamped_account_no_subprocess(self):
        """When cred_key matches, return the stamped account without calling auth_status."""
        def _fail(*a, **k):
            raise AssertionError("auth_status must not be called")

        accounts.auth_status = _fail
        acct, changed, prev = accounts.refresh_account(SID, conn=self.conn)
        self.assertEqual(acct, OLD)
        self.assertFalse(changed)
        self.assertIsNone(prev)

    # ---- (b) changed key and new email

    def test_changed_key_new_email_updates_everything(self):
        """Changed key + different email: stamp, session row, running entry, event all updated."""
        self._cred_key = "200:50"  # simulate credential file change
        accounts.auth_status = lambda *a, **k: {"email": NEW, "source": "auth"}

        # seed a running entry
        running.ensure_session(SID, account=OLD)

        acct, changed, prev = accounts.refresh_account(SID, conn=self.conn)

        self.assertEqual(acct, NEW)
        self.assertTrue(changed)
        self.assertEqual(prev, OLD)

        # stamp updated
        stamp_acct, stamp_src = accounts.account_for(SID)
        self.assertEqual(stamp_acct, NEW)
        self.assertEqual(stamp_src, "switch")

        # sessions.account updated in the ledger
        row = self.conn.execute(
            "SELECT account FROM sessions WHERE session_id=?", (SID,)).fetchone()
        self.assertEqual(row["account"], NEW)

        # running entry updated
        live = running.session(SID)
        self.assertEqual(live.get("account"), NEW)

        # account_change event recorded
        events = self.conn.execute(
            "SELECT kind, detail_json FROM events WHERE session_id=? AND kind='account_change'",
            (SID,)).fetchall()
        self.assertEqual(len(events), 1)
        import json
        detail = json.loads(events[0]["detail_json"])
        self.assertEqual(detail["from"], OLD)
        self.assertEqual(detail["to"], NEW)
        self.assertEqual(detail["source"], "switch")
        self.assertIn("cred_key", detail)

    # ---- (c) changed key, same email: key refreshed, no event

    def test_changed_key_same_email_refreshes_key_only(self):
        """Changed key but same email: re-stamp with new key, no event, changed=False."""
        self._cred_key = "300:50"
        accounts.auth_status = lambda *a, **k: {"email": OLD, "source": "auth"}

        acct, changed, prev = accounts.refresh_account(SID, conn=self.conn)

        self.assertEqual(acct, OLD)
        self.assertFalse(changed)
        self.assertIsNone(prev)

        # the stamp's cred_key is refreshed
        from pa import fsutil
        stamp = fsutil.read_json(paths.state_path("accounts", "%s.json" % SID), {})
        self.assertEqual(stamp.get("cred_key"), "300:50")

        # no account_change event
        events = self.conn.execute(
            "SELECT id FROM events WHERE session_id=? AND kind='account_change'",
            (SID,)).fetchall()
        self.assertEqual(len(events), 0)

    # ---- (d) stamp without cred_key: one re-check

    def test_missing_cred_key_triggers_recheck(self):
        """An old stamp without cred_key triggers one auth_status call."""
        # write a stamp without cred_key
        from pa import fsutil
        stamp_path = paths.state_path("accounts", "%s.json" % SID)
        fsutil.atomic_write_json(stamp_path, {
            "session_id": SID, "account": OLD, "source": "auth",
            "ts": "2026-01-01T00:00:00Z",
        }, indent=1)

        auth_calls = []

        def _track_auth(*a, **k):
            auth_calls.append(1)
            return {"email": OLD, "source": "auth"}

        accounts.auth_status = _track_auth

        acct, changed, prev = accounts.refresh_account(SID, conn=self.conn)
        self.assertEqual(acct, OLD)
        self.assertFalse(changed)
        self.assertIsNone(prev)
        self.assertEqual(len(auth_calls), 1, "auth_status should be called once")

    # ---- (d2) changed key, CLI unavailable, config fallback: stamp kept, nothing cached

    def test_changed_key_config_source_keeps_stamp(self):
        """Key change + auth_status source config: no switch, no event, no cache (3.15 T1)."""
        import subprocess

        class _Proc:
            returncode = -1
            stdout = b""
            stderr = b""

        orig_run = subprocess.run
        subprocess.run = lambda *a, **k: _Proc()
        self.addCleanup(setattr, subprocess, "run", orig_run)
        self._cred_key = "600:50"
        cfg = {"recalc_default_account": NEW}

        self.assertEqual(accounts.auth_status(cfg, refresh=True).get("source"), "config")
        acct, changed, prev = accounts.refresh_account(SID, cfg=cfg, conn=self.conn)

        self.assertEqual((acct, changed, prev), (OLD, False, None))
        from pa import fsutil
        stamp = fsutil.read_json(paths.state_path("accounts", "%s.json" % SID), {})
        self.assertEqual(stamp.get("account"), OLD)
        self.assertEqual(stamp.get("cred_key"), "100:50")
        events = self.conn.execute(
            "SELECT id FROM events WHERE session_id=? AND kind='account_change'",
            (SID,)).fetchall()
        self.assertEqual(len(events), 0)
        self.assertEqual(accounts._cache_read(), {})

    # ---- (e) re-login restamps the tier (with and without a conn)

    def _creds(self, rate_limit_tier, subscription_type="max"):
        cfg_dir = os.path.join(self.dir, "claude")
        os.makedirs(cfg_dir, exist_ok=True)
        os.environ["CLAUDE_CONFIG_DIR"] = cfg_dir
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        with open(os.path.join(cfg_dir, ".credentials.json"), "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": "t", "subscriptionType": subscription_type,
                                         "rateLimitTier": rate_limit_tier}}, f)

    def _tier(self, email):
        row = self.conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                                (email,)).fetchone()
        return tuple(row) if row else None

    def test_relogin_restamps_tier(self):
        self._creds("default_claude_max_20x")
        self._cred_key = "400:50"
        accounts.auth_status = lambda *a, **k: {"email": NEW, "source": "auth"}
        accounts.refresh_account(SID, conn=self.conn)
        self.assertEqual(self._tier(NEW), ("max20", "credentials"))

    def test_relogin_restamps_tier_without_conn(self):
        self._creds("default_claude_max_5x")
        self._cred_key = "500:50"
        accounts.auth_status = lambda *a, **k: {"email": OLD, "source": "auth"}
        accounts.refresh_account(SID)                  # the statusline's call: no conn
        self.assertEqual(self._tier(OLD), ("max5", "credentials"))


class AuthStatusSourceTest(unittest.TestCase):
    """auth_status: .claude.json first, absolute-path CLI fallback, failures logged (3.15 T10)."""

    def setUp(self):
        import subprocess

        self.dir = tempfile.mkdtemp(prefix="pa3-auth-")
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "usage-ledger")
        paths.ensure_ledger_tree()
        self.cfg = os.path.join(self.dir, "cfg")
        os.makedirs(self.cfg)
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.log_file = os.path.join(self.dir, "hooks.log")
        log.set_log_path(self.log_file)
        self.fake_exe = os.path.join(self.dir, "bin", "claude")
        orig_run, orig_which = subprocess.run, shutil.which
        shutil.which = lambda name, *a, **k: self.fake_exe
        self.addCleanup(setattr, subprocess, "run", orig_run)
        self.addCleanup(setattr, shutil, "which", orig_which)
        self.subprocess = subprocess

    def tearDown(self):
        os.environ.pop("PA_LEDGER_DIR", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        log.set_log_path(None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_claude_json_wins_without_subprocess(self):
        with open(os.path.join(self.cfg, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": NEW, "organizationUuid": "u-1",
                                        "organizationName": "Org"}, "other": 1}, f)

        def _fail(*a, **k):
            raise AssertionError("subprocess.run must not be called")

        self.subprocess.run = _fail
        st = accounts.auth_status(refresh=True)
        self.assertEqual((st["email"], st["source"], st["via"]), (NEW, "auth", "claude_json"))
        self.assertEqual((st["org_id"], st["org_name"]), ("u-1", "Org"))
        self.assertEqual(accounts._cache_read().get("email"), NEW)

    def test_no_file_falls_back_to_cli_absolute_path(self):
        calls = []

        class _Proc:
            returncode = 0
            stdout = ("Email: %s\n" % OLD).encode()
            stderr = b""

        def _run(argv, **k):
            calls.append(argv)
            return _Proc()

        self.subprocess.run = _run
        st = accounts.auth_status(refresh=True)
        self.assertEqual((st["email"], st["source"], st["via"]), (OLD, "auth", "cli"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isabs(calls[0][0]), calls[0][0])
        self.assertEqual(calls[0][1:], ["auth", "status"])

    def test_cli_failures_logged(self):
        for exc in (FileNotFoundError("claude"),
                    self.subprocess.TimeoutExpired(["claude"], 3.0)):
            def _run(*a, _exc=exc, **k):
                raise _exc

            self.subprocess.run = _run
            st = accounts.auth_status(refresh=True)
            self.assertNotEqual(st.get("source"), "auth")
            with open(self.log_file, encoding="utf-8") as f:
                lines = [ln for ln in f if "auth_status_fail" in ln]
            self.assertTrue(lines, type(exc).__name__)
            self.assertIn('"exc": "%s"' % type(exc).__name__, lines[-1])
            self.assertIn('"elapsed_s":', lines[-1])
            self.assertIn(json.dumps(self.fake_exe)[1:-1], lines[-1])


class TierTest(unittest.TestCase):
    """tier_from map, stamp_tier rules, tier_for precedence."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-tier-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _tier(self, email):
        row = self.conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                                (email,)).fetchone()
        return tuple(row) if row else None

    def test_map(self):
        self.assertEqual(accounts.tier_from("max", "default_claude_max_20x"), "max20")
        self.assertEqual(accounts.tier_from("max", "default_claude_max_5x"), "max5")
        self.assertEqual(accounts.tier_from("pro", None), "pro")
        self.assertEqual(accounts.tier_from("pro", "default_claude_ai"), "pro")
        for sub, rate in (("team", None), ("enterprise", "default_claude_enterprise"),
                          ("max", "something_new"), (None, None), ("free", None)):
            self.assertIsNone(accounts.tier_from(sub, rate), (sub, rate))

    def test_precedence(self):
        self.assertEqual(accounts.tier_for(self.conn, OLD, {}), "max5")
        self.assertEqual(accounts.tier_for(self.conn, OLD, {"tier": "bogus"}), "max5")
        self.assertEqual(accounts.tier_for(self.conn, OLD, {"tier": "pro"}), "pro")
        self.assertEqual(accounts.tier_for(None, OLD, {"tier": "max20"}), "max20")
        accounts.stamp_tier(self.conn, OLD, "max20")
        self.assertEqual(accounts.tier_for(self.conn, OLD, {"tier": "pro"}), "max20")
        accounts.stamp_tier(self.conn, OLD, "pro", source="override")
        self.assertEqual(accounts.tier_for(self.conn, OLD, {"tier": "max5"}), "pro")

    def test_none_is_no_change(self):
        accounts.stamp_tier(self.conn, OLD, None)
        self.assertIsNone(self._tier(OLD))
        accounts.stamp_tier(self.conn, OLD, "max5")
        accounts.stamp_tier(self.conn, OLD, None)
        self.assertEqual(self._tier(OLD), ("max5", "credentials"))

    def test_override_survives_restamp_and_clears(self):
        db.upsert_account(self.conn, {"email": OLD, "first_seen": "t0"})
        accounts.stamp_tier(self.conn, OLD, "max20", source="override")
        self.assertEqual(accounts.stamp_tier(self.conn, OLD, "max5"), "max20")
        self.assertEqual(self._tier(OLD), ("max20", "override"))
        accounts.stamp_tier(self.conn, OLD, None, source="override")          # clear
        self.assertEqual(self._tier(OLD), (None, None))
        accounts.stamp_tier(self.conn, OLD, "max5")
        self.assertEqual(self._tier(OLD), ("max5", "credentials"))
        self.assertEqual(self.conn.execute("SELECT first_seen FROM accounts").fetchone()[0], "t0")


if __name__ == "__main__":
    unittest.main()
