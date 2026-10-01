"""pa.repairs (3.15 T12): the versioned step runner and the account-fallback step;
the batch-gap step (3.15.1 T15), by transcript recalc and by turn rows (fake home).

Temp ``PA_LEDGER_DIR`` and a temp ``CLAUDE_CONFIG_DIR/.claude.json`` naming account A;
config ``recalc_default_account`` is B.  Never touches a live ledger or spawns a process.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import __version__, accounts, config, db, log, notify, paths, repairs  # noqa: E402

A = "a@example.com"
B = "b@example.com"
STEP = repairs.STEP_ACCOUNT_FALLBACK
ABA = "sess-aba"
BONLY = "sess-bonly"


class RepairsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-repairs-")
        self._env = {k: os.environ.get(k) for k in ("PA_LEDGER_DIR", "CLAUDE_CONFIG_DIR", "USERPROFILE", "HOME")}
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "usage-ledger")
        self.home = os.path.join(self.dir, "home")    # transcripts: <home>/.claude/projects
        os.environ["USERPROFILE"] = os.environ["HOME"] = self.home
        cfg_dir = os.path.join(self.dir, "cfg")
        os.makedirs(cfg_dir)
        os.environ["CLAUDE_CONFIG_DIR"] = cfg_dir
        with open(os.path.join(cfg_dir, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"oauthAccount": {"emailAddress": A}}, fh)
        log.set_log_path(os.path.join(self.dir, "hooks.log"))
        paths.ensure_ledger_tree()
        self.cfg = config.defaults()
        self.cfg["recalc_default_account"] = B
        self.conn = db.connect(paths.db_path())
        db.init_schema(self.conn)
        # an old ledger: not pre-stamped
        self.conn.execute("DELETE FROM meta WHERE key LIKE 'repair%'")

    def tearDown(self):
        notify.set_spawner(None)
        db.close(self.conn)
        log.set_log_path(None)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def turn(self, sid, msg, acct, ts, gap_s=60.0, **extra):
        row = {"msg_id": msg, "session_id": sid, "run_id": sid, "account": acct, "ts": ts,
               "model": "claude-opus-5-5", "input": 10, "output": 10, "cost_usd": 0.01, "gap_s": gap_s}
        row.update(extra)
        db.upsert_turn(self.conn, row)

    def switch(self, sid, ts, frm, to, **extra):
        detail = {"from": frm, "to": to, "source": "switch", "cred_key": "1:1"}
        detail.update(extra)
        db.insert_event(self.conn, "account_change", detail, session_id=sid, account=to, ts=ts)

    def util(self, sid, acct, ts, resets_at=1790000000):
        db.insert_utilization(self.conn, {"ts": ts, "account": acct, "session_id": sid, "window": "five_hour",
                                          "pct": 10.0, "resets_at": resets_at})

    def seed_aba(self):
        self.turn(ABA, "m1", A, "2026-09-20T10:00:00.000Z")
        self.switch(ABA, "2026-09-20T10:05:00Z", A, B)
        self.turn(ABA, "m2", B, "2026-09-20T10:10:00.000Z")
        self.turn(ABA, "m3", B, "2026-09-20T10:20:00.000Z")
        self.turn(ABA, "m4", A, "2026-09-20T10:30:00.000Z")
        self.util(ABA, B, "2026-09-20T10:10:00Z")
        self.util(ABA, B, "2026-09-20T10:20:00Z")
        db.upsert_window_instance(self.conn, {"account": B, "window": "five_hour", "resets_at": 1790000000,
                                              "started_at": 1789982000})
        db.upsert_session(self.conn, {"session_id": ABA, "account": B})
        accounts.stamp_session(ABA, B, source="switch")
        with open(paths.auth_cache_path(), "w", encoding="utf-8") as fh:
            json.dump({"email": B, "source": "config"}, fh)

    def seed_bonly(self):
        self.turn(BONLY, "n1", A, "2026-09-21T09:00:00.000Z")
        self.switch(BONLY, "2026-09-21T09:05:00Z", A, B)
        self.turn(BONLY, "n2", B, "2026-09-21T09:10:00.000Z")
        db.upsert_session(self.conn, {"session_id": BONLY, "account": B})

    def accounts_of(self, table, sid):
        return sorted(r[0] for r in self.conn.execute(
            "SELECT account FROM %s WHERE session_id=?" % table, (sid,)).fetchall())


class AccountFallbackTest(RepairsCase):
    def test_aba_repaired_once(self):
        self.seed_aba()
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertEqual(res[STEP], {"turns": 2, "events": 1, "sessions": 1, "utilization": 2,
                                     "window_instances": 1, "details": 1, "stamps": 1, "auth_cache": 1})
        self.assertEqual(self.accounts_of("turns", ABA), [A] * 4)
        self.assertEqual(self.accounts_of("utilization", ABA), [A, A])
        self.assertEqual(self.accounts_of("sessions", ABA), [A])
        ev = self.conn.execute("SELECT account, detail_json FROM events WHERE kind='account_change'").fetchone()
        self.assertEqual(ev[0], A)
        self.assertEqual(json.loads(ev[1])["repaired"], STEP)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM window_instances WHERE account=?",
                                           (B,)).fetchone()[0], 0)
        self.assertEqual(accounts.account_for(ABA), (A, "repair"))
        self.assertFalse(os.path.exists(paths.auth_cache_path()))
        backups = os.listdir(os.path.join(paths.ledger_dir(), "backups"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].startswith("ledger.pre-3.15_account-fallback."))
        stamp = json.loads(db.get_meta(self.conn, "repair." + STEP))
        self.assertEqual(stamp["counts"]["turns"], 2)
        self.assertEqual(db.get_meta(self.conn, "repairs_version"), __version__)
        notice = repairs.pop_notice()
        self.assertEqual(len(notice), 1)
        self.assertIn(STEP, notice[0])
        self.assertEqual(repairs.pop_notice(), [])
        self.assertEqual(repairs.account_fallback_findings(self.conn, self.cfg), [])

        # second run with the stamp removed: nothing left under B -> no change
        self.conn.execute("DELETE FROM meta WHERE key=?", ("repair." + STEP,))
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[STEP])
        self.assertEqual(json.loads(db.get_meta(self.conn, "repair." + STEP))["result"], "nothing")
        self.assertEqual(self.accounts_of("turns", ABA), [A] * 4)
        self.assertEqual(len(os.listdir(os.path.join(paths.ledger_dir(), "backups"))), 1)
        self.assertEqual(repairs.pop_notice(), [])

    def test_aba_savings_and_detail_follow(self):
        """T20: the switch detail says A and the measured savings land on A."""
        from pa import ledger_cli, savings

        self.seed_aba()
        db.upsert_agent_run(self.conn, {"run_id": ABA, "session_id": ABA, "kind": "main"})
        db.upsert_retrieval(self.conn, {"run_id": "ret-aba", "parent_run_id": ABA, "kept_out_tokens": 40000,
                                        "void": 0, "returned_at": "2026-09-20T10:01:00Z", "status": "completed"})
        ledger_cli.write_savings(self.conn, ABA, self.cfg)
        before = json.loads(self.conn.execute("SELECT measured_detail_json FROM savings WHERE run_id=?",
                                              (ABA,)).fetchone()[0])["by_account"]
        self.assertGreater(before.get(B, 0), 0)
        repairs.run(self.cfg, conn=self.conn)
        detail = json.loads(self.conn.execute("SELECT detail_json FROM events WHERE kind='account_change'")
                            .fetchone()[0])
        self.assertEqual((detail["from"], detail["to"]), (A, A))
        self.assertEqual(detail["was"], {"from": A, "to": B})
        after = json.loads(self.conn.execute("SELECT measured_detail_json FROM savings WHERE run_id=?",
                                             (ABA,)).fetchone()[0])["by_account"]
        self.assertEqual(sorted(after), [A])
        t0 = "2026-09-20T00:00:00Z"
        self.assertGreater(savings.window_saved_measured(self.conn, A, t0, None) or 0, 0)
        self.assertFalse(savings.window_saved_measured(self.conn, B, t0, None))

    def test_b_only_untouched_and_listed(self):
        self.seed_bonly()
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[STEP])
        self.assertEqual(self.accounts_of("turns", BONLY), [A, B])
        self.assertEqual(self.accounts_of("sessions", BONLY), [B])
        found = repairs.account_fallback_findings(self.conn, self.cfg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["session_id"], BONLY)
        self.assertEqual(found[0]["command"],
                         "pa_ledger.py account-switch --session %s --from %s --to %s --since %s"
                         % (BONLY, B, A, "2026-09-21T09:05:00Z"))

    def test_real_switch_not_a_candidate(self):
        self.turn(BONLY, "n1", A, "2026-09-21T09:00:00.000Z")
        self.switch(BONLY, "2026-09-21T09:05:00Z", A, B, account_source="file")
        self.turn(BONLY, "n2", B, "2026-09-21T09:10:00.000Z")
        self.assertEqual(repairs.account_fallback_findings(self.conn, self.cfg), [])

    def test_stamped_step_not_rerun(self):
        self.seed_aba()
        db.set_meta(self.conn, "repair." + STEP, "fresh")
        calls = []
        old = repairs.STEPS[:]
        repairs.STEPS[:] = [(STEP, lambda c, g: calls.append(1), lambda c, g, p: {})]
        try:
            self.assertEqual(repairs.run(self.cfg, conn=self.conn), {})
        finally:
            repairs.STEPS[:] = old
        self.assertEqual(calls, [])
        self.assertEqual(self.accounts_of("turns", ABA), [A, A, B, B])

    def test_failing_step_unstamped_version_stamped(self):
        old = repairs.STEPS[:]

        def boom(c, g):
            raise RuntimeError("boom")
        repairs.STEPS[:] = [(STEP, boom, lambda c, g, p: {})]
        try:
            repairs.run(self.cfg, conn=self.conn)
        finally:
            repairs.STEPS[:] = old
        self.assertIsNone(db.get_meta(self.conn, "repair." + STEP))
        self.assertEqual(db.get_meta(self.conn, "repairs_version"), __version__)


GAP = repairs.STEP_BATCH_GAP
GSID = "sess-gap"
# two Stop batches 2 h apart; the second opens with a prefix rewrite (cw 61000 of ctx 61010)
GAP_TURNS = [("g1", "2026-09-12T20:00:00.000Z", 60000, 0, None),
             ("g2", "2026-09-12T20:01:00.000Z", 100, 60000, 60.0),
             ("g3", "2026-09-12T22:01:00.000Z", 61000, 0, None)]


def _usage_line(msg_id, ts, write, read):
    return json.dumps({
        "type": "assistant", "uuid": "u-" + msg_id, "requestId": "req_" + msg_id, "timestamp": ts,
        "sessionId": GSID, "cwd": "/x", "version": "2.1.0",
        "message": {"id": msg_id, "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 10, "cache_creation_input_tokens": write,
                              "cache_read_input_tokens": read, "output_tokens": 50,
                              "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                 "ephemeral_1h_input_tokens": write}}}})


class BatchGapTest(RepairsCase):
    """T15: the pre-T7 batch's first turn (gap_s NULL) gets its gap and rewrite, once."""

    def seed_gap(self, transcript):
        for msg, ts, write, read, gap in GAP_TURNS:
            self.turn(GSID, msg, A, ts, gap_s=gap, input=10, cache_write=write, cache_write_1h=write,
                      cache_read=read, ctx=10 + write + read, rewrite=0, cold=0, kind="api")
        db.upsert_session(self.conn, {"session_id": GSID, "account": A})
        if transcript:
            d = os.path.join(self.home, ".claude", "projects", "slug")
            os.makedirs(d)
            with open(os.path.join(d, GSID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
                for msg, ts, write, read, _gap in GAP_TURNS:
                    fh.write(_usage_line(msg, ts, write, read) + "\n")

    def gap_row(self, msg):
        return self.conn.execute("SELECT gap_s, rewrite, cold FROM turns WHERE msg_id=?", (msg,)).fetchone()

    def check(self, transcript):
        self.seed_gap(transcript)
        self.assertEqual(repairs.detect_batch_gap(self.conn, self.cfg), {GSID: ["g3"]})
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[STEP])
        counts = res[GAP]
        self.assertEqual((counts["turns"], counts["unresolved"]), (1, 0))
        self.assertEqual((counts["sessions_recalc"], counts["sessions_rows"]),
                         (1, 0) if transcript else (0, 1))
        gap, rewrite, cold = self.gap_row("g3")
        self.assertAlmostEqual(gap, 7200.0)
        self.assertEqual((rewrite, cold), (1, 1))
        self.assertIsNone(self.gap_row("g1")[0])        # the run's true first turn
        notice = repairs.pop_notice()
        self.assertEqual(len(notice), 1)
        self.assertIn(GAP, notice[0])
        self.assertIn("gaps recomputed", notice[0])

        # second run with the stamp cleared: nothing detected, nothing changed
        self.conn.execute("DELETE FROM meta WHERE key=?", ("repair." + GAP,))
        self.assertIsNone(repairs.detect_batch_gap(self.conn, self.cfg))
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[GAP])
        self.assertAlmostEqual(self.gap_row("g3")[0], 7200.0)
        self.assertEqual(len(os.listdir(os.path.join(paths.ledger_dir(), "backups"))), 1)
        self.assertEqual(repairs.pop_notice(), [])

    def test_turn_row_path(self):
        self.check(transcript=False)

    def test_transcript_path(self):
        self.check(transcript=True)


SAV = repairs.STEP_SAVINGS_ACCOUNT
HSID = "sess-hand"


class SavingsAccountTest(RepairsCase):
    """T20: a switch event re-filed to A (by hand) whose detail still says B: its window goes to A."""

    def seed_hand(self):
        self.turn(HSID, "h1", A, "2026-09-22T10:00:00.000Z")
        db.insert_event(self.conn, "account_change", {"from": A, "to": B, "source": "switch"},
                        session_id=HSID, account=A, ts="2026-09-22T10:05:00Z")
        self.turn(HSID, "h2", B, "2026-09-22T10:10:00.000Z")
        self.turn(HSID, "h3", B, "2026-09-22T10:20:00.000Z")
        self.util(HSID, B, "2026-09-22T10:10:00Z")
        db.upsert_window_instance(self.conn, {"account": B, "window": "five_hour", "resets_at": 1790000000,
                                              "started_at": 1789982000})
        self.switch(HSID, "2026-09-22T11:00:00Z", A, B, account_source="file")   # a real later switch
        self.turn(HSID, "h4", B, "2026-09-22T11:10:00.000Z")
        db.upsert_session(self.conn, {"session_id": HSID, "account": B})

    def test_hand_repaired_fixed_once(self):
        self.seed_hand()
        plan = repairs.detect_savings_account(self.conn, self.cfg)
        self.assertEqual([(e["A"], e["B"]) for e in plan["events"]], [(A, B)])
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[STEP])
        counts = res[SAV]
        self.assertEqual((counts["turns"], counts["utilization"], counts["details"], counts["window_instances"]),
                         (2, 1, 1, 1))
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT account FROM turns WHERE session_id=? ORDER BY ts", (HSID,))], [A, A, A, B])
        self.assertEqual(self.accounts_of("utilization", HSID), [A])
        d = json.loads(self.conn.execute("SELECT detail_json FROM events WHERE ts='2026-09-22T10:05:00Z'")
                       .fetchone()[0])
        self.assertEqual((d["to"], d["was"]), (A, {"from": A, "to": B}))
        notice = repairs.pop_notice()
        self.assertEqual(len(notice), 1)
        self.assertIn(SAV, notice[0])

        # second run with the stamp cleared: nothing detected, nothing changed
        self.conn.execute("DELETE FROM meta WHERE key=?", ("repair." + SAV,))
        self.assertIsNone(repairs.detect_savings_account(self.conn, self.cfg))
        res = repairs.run(self.cfg, conn=self.conn)
        self.assertIsNone(res[SAV])
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT account FROM turns WHERE session_id=? ORDER BY ts", (HSID,))], [A, A, A, B])
        self.assertEqual(repairs.pop_notice(), [])


class RunnerTest(RepairsCase):
    def test_request_spawns_once_while_locked(self):
        spawned = []
        notify.set_spawner(lambda cmd, env: spawned.append((cmd, env)))
        self.assertTrue(repairs.request(self.cfg))
        self.assertFalse(repairs.request(self.cfg))
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0][0][-2:], ["-m", "pa.repairs"])
        self.assertIn("PYTHONPATH", spawned[0][1])
        self.assertTrue(os.path.exists(paths.state_path("repairs.lock")))

    def test_fresh_ledger_prestamped(self):
        conn = db.connect(os.path.join(self.dir, "fresh.sqlite"))
        try:
            db.init_schema(conn)
            self.assertEqual(db.get_meta(conn, "repairs_version"), __version__)
            for sid in repairs.step_ids():
                self.assertEqual(db.get_meta(conn, "repair." + sid), "fresh")
        finally:
            db.close(conn)

    def test_refresh_account_carries_account_source_file(self):
        accounts.stamp_session("sess-x", B, source="auth", extra={"cred_key": "old:1"})
        db.upsert_session(self.conn, {"session_id": "sess-x", "account": B})
        acct, changed, prev = accounts.refresh_account("sess-x", cfg=self.cfg, conn=self.conn)
        self.assertEqual((acct, changed, prev), (A, True, B))
        detail = json.loads(self.conn.execute(
            "SELECT detail_json FROM events WHERE session_id='sess-x' AND kind='account_change'").fetchone()[0])
        self.assertEqual(detail["account_source"], "file")


if __name__ == "__main__":
    unittest.main()
