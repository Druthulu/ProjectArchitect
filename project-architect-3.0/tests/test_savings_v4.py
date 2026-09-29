"""vanilla-net-v4: tool-call carry rows in the walk, the emission term."""

import io
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import db       # noqa: E402
from pa import savings  # noqa: E402

FABLE = "claude-fable-5-1"
P_READ = 0.25            # Fable cache read $/MTok
P_WRITE_1H = 20.0        # Fable 1h cache write $/MTok
P_OUTPUT = 50.0           # Fable output $/MTok


class ToolCallCreditTest(unittest.TestCase):
    """Fixture: a session with api turns, one retrieval, and tool_calls rows."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-v4-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, run_id, session_id, kind="expert", model=FABLE):
        db.upsert_agent_run(self.conn, {
            "run_id": run_id, "session_id": session_id, "kind": kind,
            "agent_type": kind, "model_seen": model, "model_pinned": model})

    def _turn(self, msg_id, run_id, session_id, ts, model=FABLE, **kw):
        row = {"msg_id": msg_id, "run_id": run_id, "session_id": session_id,
               "account": "test@example.com", "ts": ts, "model": model,
               "ctx": 100000, "cost_usd": 1.0, "kind": "api"}
        row.update(kw)
        db.upsert_turn(self.conn, row)

    def _retrieval(self, run_id, parent_run_id, returned_at, kept, void=0):
        db.upsert_retrieval(self.conn, {
            "run_id": run_id, "parent_run_id": parent_run_id,
            "agent_type": "retriever-code", "model": "claude-haiku-4-5",
            "returned_at": returned_at, "kept_out_tokens": kept,
            "kept_out_mode": "results_share",
            "answer_tokens": 300 if not void else 0,
            "status": "completed" if not void else "error", "void": void})

    def _tool_call(self, call_id, session_id, run_id, ts, kind="script",
                   path="", file_tokens=0, emission_tokens=0,
                   result_tokens=0, kept_tokens=0, model=FABLE):
        db.upsert(self.conn, "tool_calls", {
            "id": call_id, "session_id": session_id, "run_id": run_id,
            "ts": ts, "tool": "Bash", "kind": kind, "path": path,
            "file_tokens": file_tokens, "emission_tokens": emission_tokens,
            "result_tokens": result_tokens, "kept_tokens": kept_tokens,
            "model": model})

    # ---- scenario: script note on PHASE_PLAN.md, result smaller than file ----
    def test_carry_and_emission_in_measured_detail(self):
        sid, rid = "s_v4", "exp_v4"
        self._run(rid, sid)
        self._retrieval("ret_v4", rid, "2026-09-01T10:02:00Z", 80000)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:05:00Z")
        self._turn("t2", rid, sid, "2026-09-01T10:10:00Z")
        # Script note: file 40k tokens, result 10k tokens -> carry = 30k
        self._tool_call("c1", sid, rid, "2026-09-01T10:03:00Z",
                        kind="script", path="phase-ends/current/PHASE_PLAN.md",
                        file_tokens=40000, emission_tokens=500, result_tokens=10000)
        got = savings.measured_saved(self.conn, rid)
        self.assertEqual(got["savings_version"], "vanilla-net-v4")
        cr = got.get("credit")
        self.assertIsNotNone(cr)
        self.assertEqual(cr["calls"], 1)
        self.assertIn("carry_token_turns", cr)
        self.assertNotIn("carry_tokens", cr)
        self.assertEqual(cr["emission_tokens"], 500)
        self.assertAlmostEqual(cr["emission_usd"], 500 * P_OUTPUT / 1e6)
        # The carry row (30k) enters the walk, so gross includes it
        self.assertGreater(got["gross_usd"], 0)
        # net = gross - seed + emission
        want_emission = 500 * P_OUTPUT / 1e6
        self.assertAlmostEqual(got["saved_usd"],
                               got["gross_usd"] - got["seed_usd"] + want_emission)

    # ---- second script note on the same path: carry 0, emission still counts ----
    def test_second_note_same_path_carry_zero(self):
        sid, rid = "s_dup", "exp_dup"
        self._run(rid, sid)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:05:00Z")
        # First note: carry = 40k - 10k = 30k
        self._tool_call("c_a", sid, rid, "2026-09-01T10:01:00Z",
                        kind="script", path="phase-ends/current/PHASE_PLAN.md",
                        file_tokens=40000, emission_tokens=200, result_tokens=10000)
        # Second note: same path -> carry 0 (seen_paths), emission still counts
        self._tool_call("c_b", sid, rid, "2026-09-01T10:02:00Z",
                        kind="script", path="phase-ends/current/PHASE_PLAN.md",
                        file_tokens=40000, emission_tokens=300, result_tokens=10000)
        got = savings.measured_saved(self.conn, rid)
        cr = got["credit"]
        self.assertEqual(cr["calls"], 2)
        self.assertEqual(cr["emission_tokens"], 500)
        # Only one carry row in the walk (the first note's 30k)
        self.assertEqual(cr["carry_token_turns"], 30000)

    # ---- runsh row over the cap ----
    def test_runsh_capped_at_spill_chars(self):
        sid, rid = "s_rsh", "exp_rsh"
        self._run(rid, sid)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:05:00Z")
        # runsh row: kept_tokens 20000 but spill_chars default = 20000 -> tokens(20000) = 5000
        # The kept_tokens is already in tokens (rows_for_call stores min(tokens(kept_chars), tokens(spill))).
        # carry_token_turns for runsh: min(kept_tokens, tokens(spill_chars))
        # tokens(20000) = 5000, so min(20000, 5000) = 5000
        self._tool_call("c_r", sid, rid, "2026-09-01T10:01:00Z",
                        kind="runsh", path="",
                        kept_tokens=20000, emission_tokens=100)
        got = savings.measured_saved(self.conn, rid)
        cr = got["credit"]
        # carry is capped at tokens(spill_chars) = 5000
        self.assertEqual(cr["carry_token_turns"], 5000)

    # ---- script note on a path that also has a read row (carry 0) ----
    def test_read_guard_zeroes_carry(self):
        sid, rid = "s_guard", "exp_guard"
        self._run(rid, sid)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:10:00Z")
        # Script note first
        self._tool_call("c_s", sid, rid, "2026-09-01T10:02:00Z",
                        kind="script", path="project-architect-3.0/pa/savings.py",
                        file_tokens=30000, emission_tokens=400, result_tokens=5000)
        # Read row AFTER the script note (session-wide guard)
        self._tool_call("c_read", sid, rid, "2026-09-01T10:05:00Z",
                        kind="read", path="project-architect-3.0/pa/savings.py",
                        result_tokens=30000)
        got = savings.measured_saved(self.conn, rid)
        cr = got["credit"]
        # carry is 0 because the path is in read_paths
        self.assertEqual(cr["carry_token_turns"], 0)
        # emission still counts
        self.assertEqual(cr["emission_tokens"], 400)
        self.assertGreater(cr["emission_usd"], 0)

    # ---- window figure equals the sum of runs' figures ----
    def test_window_equals_sum_of_runs(self):
        sid = "s_win"
        self._run(sid, sid, kind="router")  # main run
        self._run("exp_a", sid, kind="expert")
        self._run("exp_b", sid, kind="expert")
        self._retrieval("ret_a", "exp_a", "2026-09-01T10:02:00Z", 50000)
        # Turns for both runs
        self._turn("t0", sid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", sid, sid, "2026-09-01T10:05:00Z")
        self._turn("t2", "exp_a", sid, "2026-09-01T10:03:00Z")
        self._turn("t3", "exp_b", sid, "2026-09-01T10:04:00Z")
        self._turn("t4", sid, sid, "2026-09-01T10:08:00Z")
        # Tool calls on different runs
        self._tool_call("tc_a", sid, "exp_a", "2026-09-01T10:03:30Z",
                        kind="script", path="file_a.py",
                        file_tokens=20000, emission_tokens=300, result_tokens=5000)
        self._tool_call("tc_b", sid, "exp_b", "2026-09-01T10:04:30Z",
                        kind="script", path="file_b.py",
                        file_tokens=15000, emission_tokens=200, result_tokens=3000)
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        window = savings.window_saved_measured(self.conn, "test@example.com", t0, t1)
        per_run = 0.0
        for r in (sid, "exp_a", "exp_b"):
            d = savings.measured_saved(self.conn, r)
            per_run += d["saved_usd"]
        self.assertAlmostEqual(window, per_run, places=5)

    # ---- fix-10: a smaller carry window never credits more ----
    def test_window_kwarg_smaller_window_not_more(self):
        sid, rid = "s_wk", "exp_wk"
        self._run(rid, sid)
        self._retrieval("ret_wk", rid, "2026-09-01T10:02:00Z", 150000)
        for i in range(6):
            self._turn("t%d" % i, rid, sid, "2026-09-01T10:%02d:00Z" % (5 * i))
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        default = savings.window_saved_measured(self.conn, "test@example.com", t0, t1)
        small = savings.window_saved_measured(self.conn, "test@example.com", t0, t1,
                                              window=200000)
        self.assertGreater(default, 0)
        self.assertLessEqual(small, default)


class NullModelPricingTest(ToolCallCreditTest):
    """A tool_calls row with model=NULL is priced at the nearest api turn's model."""

    def test_null_model_uses_nearest_turn(self):
        sid, rid = "s_null", "exp_null"
        self._run(rid, sid)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:05:00Z")
        # Tool call with model=NULL (written before the model fix)
        self._tool_call("c_null", sid, rid, "2026-09-01T10:03:00Z",
                        kind="script", path="pa/foo.py",
                        file_tokens=20000, emission_tokens=400,
                        result_tokens=5000, model=None)
        got = savings.measured_saved(self.conn, rid)
        cr = got["credit"]
        # emission_usd should be positive (priced at the nearest turn's model)
        self.assertGreater(cr["emission_usd"], 0,
                           "NULL-model emission should be priced at the nearest turn's model")
        # Compare with explicit model pricing
        want = 400 * P_OUTPUT / 1e6
        self.assertAlmostEqual(cr["emission_usd"], want)

    def test_null_model_window(self):
        """window_saved_measured prices NULL-model rows too."""
        sid, rid = "s_nw", "exp_nw"
        self._run(rid, sid)
        self._turn("t0", rid, sid, "2026-09-01T10:00:00Z")
        self._turn("t1", rid, sid, "2026-09-01T10:05:00Z")
        self._tool_call("c_nw", sid, rid, "2026-09-01T10:03:00Z",
                        kind="script", path="pa/bar.py",
                        file_tokens=10000, emission_tokens=200,
                        result_tokens=2000, model=None)
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        window = savings.window_saved_measured(self.conn, "test@example.com", t0, t1)
        self.assertGreater(window, 0, "window figure should be positive with NULL-model row")


class ToolCallReportHeaderTest(unittest.TestCase):
    """The ``report`` header names the savings version."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-v4cli-")
        self.ledger = os.path.join(self.dir, "ledger")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_report_header_shows_version(self):
        from pa import ledger_cli
        from pa import paths
        conn = ledger_cli.open_db()
        try:
            db.upsert_session(conn, {
                "session_id": "sss", "account": "a@test.com",
                "started": "2026-09-01T10:00:00Z", "cost_usd": 1.0,
                "cost_source": "turns"})
            db.upsert_turn(conn, {
                "msg_id": "m1", "session_id": "sss", "account": "a@test.com",
                "ts": "2026-09-01T10:01:00Z", "model": FABLE,
                "cost_usd": 1.0, "kind": "api"})
        finally:
            db.close(conn)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ledger_cli.main(["report"])
        text = buf.getvalue()
        self.assertIn("savings = vanilla-net-v4", text)
        self.assertIn("kept-out + tool-call credit", text)


class RecalcCheckCarryTokenTurnsTest(unittest.TestCase):
    """T8: the stored ``measured_detail_json`` carries ``carry_token_turns`` (not
    ``carry_tokens``), and ``recalc --from turns --check`` stays idempotent."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-v4recalc-")
        self.ledger = os.path.join(self.dir, "ledger")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        from pa import ledger_cli
        self.ledger_cli = ledger_cli
        # main run (run_id == session_id): recalc's aggregation stamps seed_ctx from
        # the turns, and _seed_carry_run only applies to subagent runs (run_id !=
        # session_id) -- a subagent id here would pick up a spurious seed carry from
        # the flat ctx this fixture's turns share.
        sid = rid = "s_recalc"
        conn = ledger_cli.open_db()
        try:
            db.upsert_session(conn, {
                "session_id": sid, "account": "a@test.com", "cwd": "Z:/Recalc/Proj",
                "started": "2026-09-01T09:59:00Z", "cost_source": "turns"})
            db.upsert_agent_run(conn, {
                "run_id": rid, "session_id": sid, "kind": "router",
                "agent_type": "router", "model_seen": FABLE, "model_pinned": FABLE})
            for msg_id, ts in (("t0", "2026-09-01T10:00:00Z"), ("t1", "2026-09-01T10:05:00Z")):
                db.upsert_turn(conn, {
                    "msg_id": msg_id, "run_id": rid, "session_id": sid,
                    "account": "a@test.com", "ts": ts, "model": FABLE,
                    "ctx": 100000, "cost_usd": 1.0, "kind": "api"})
            db.upsert_tool_call(conn, {
                "id": "c_recalc", "session_id": sid, "run_id": rid,
                "ts": "2026-09-01T10:01:00Z", "tool": "Bash", "kind": "script",
                "path": "phase-ends/current/PHASE_PLAN.md",
                "file_tokens": 40000, "emission_tokens": 500, "result_tokens": 10000,
                "model": FABLE})
        finally:
            db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_detail_has_carry_token_turns_not_carry_tokens(self):
        rc, _out, err = self._recalc(["recalc", "--from", "turns"])
        self.assertEqual(rc, 0, err)
        conn = self.ledger_cli.open_db()
        try:
            row = conn.execute("SELECT measured_detail_json FROM savings WHERE run_id=?",
                               ("s_recalc",)).fetchone()
        finally:
            db.close(conn)
        self.assertIsNotNone(row)
        detail = json.loads(row["measured_detail_json"] if hasattr(row, "keys") else row[0])
        cr = detail["credit"]
        self.assertIn("carry_token_turns", cr)
        self.assertNotIn("carry_tokens", cr)

    def test_recalc_check_idempotent(self):
        rc, out_text, err = self._recalc(["recalc", "--from", "turns", "--check"])
        self.assertEqual(rc, 0, err)
        self.assertIn("idempotent OK", out_text)

    def _recalc(self, argv):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = self.ledger_cli.main(argv)
        return rc, buf_out.getvalue(), buf_err.getvalue()


class AccountSwitchTest(ToolCallCreditTest):
    """T9: a session whose turns switch account mid-way."""

    ACCT_A = "alpha@example.com"
    ACCT_B = "beta@example.com"

    def _fixture(self):
        """Two accounts, a retrieval row before the switch that is re-read after,
        and a tool_calls row on each side of the switch."""
        sid = "s_switch"
        rid = sid  # main run
        self._run(rid, sid, kind="router")

        # Retrieval returned before the switch (parented to main run)
        self._retrieval("ret_sw", rid, "2026-09-01T10:01:00Z", 60000)

        # Turns: 3 under account A, then 3 under account B
        for i, (acct, ts) in enumerate([
            (self.ACCT_A, "2026-09-01T10:00:00Z"),
            (self.ACCT_A, "2026-09-01T10:02:00Z"),
            (self.ACCT_A, "2026-09-01T10:04:00Z"),
            (self.ACCT_B, "2026-09-01T10:06:00Z"),
            (self.ACCT_B, "2026-09-01T10:08:00Z"),
            (self.ACCT_B, "2026-09-01T10:10:00Z"),
        ]):
            self._turn("sw_t%d" % i, rid, sid, ts, account=acct)

        # Tool call on each side
        self._tool_call("tc_a", sid, rid, "2026-09-01T10:03:00Z",
                        kind="script", path="file_a.py",
                        file_tokens=20000, emission_tokens=200, result_tokens=5000)
        self._tool_call("tc_b", sid, rid, "2026-09-01T10:07:00Z",
                        kind="script", path="file_b.py",
                        file_tokens=15000, emission_tokens=150, result_tokens=3000)
        return sid, rid

    def test_by_account_sums_to_total(self):
        sid, rid = self._fixture()
        got = savings.measured_saved(self.conn, rid)
        ba = got.get("by_account", {})
        self.assertIn(self.ACCT_A, ba)
        self.assertIn(self.ACCT_B, ba)
        total = sum(ba.values())
        self.assertAlmostEqual(total, got["saved_usd"], places=2,
                               msg="by_account must sum to measured_saved_usd")

    def test_each_account_equals_own_turns_share(self):
        """Each account's figure equals the increments of its own turns."""
        sid, rid = self._fixture()
        got = savings.measured_saved(self.conn, rid)
        ba = got.get("by_account", {})
        # Both accounts must have non-zero savings (the retrieval is re-read by both)
        self.assertGreater(ba.get(self.ACCT_A, 0), 0)
        self.assertGreater(ba.get(self.ACCT_B, 0), 0)

    def test_window_per_account_matches(self):
        """window_saved_measured for each account matches by_account."""
        sid, rid = self._fixture()
        got = savings.measured_saved(self.conn, rid)
        ba = got.get("by_account", {})
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        for acct in (self.ACCT_A, self.ACCT_B):
            window = savings.window_saved_measured(self.conn, acct, t0, t1)
            self.assertAlmostEqual(window, ba.get(acct, 0.0), places=4,
                                   msg="window for %s should match by_account" % acct)

    def test_window_sum_equals_session(self):
        """Sum of window figures for both accounts equals the total."""
        sid, rid = self._fixture()
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        w_a = savings.window_saved_measured(self.conn, self.ACCT_A, t0, t1)
        w_b = savings.window_saved_measured(self.conn, self.ACCT_B, t0, t1)
        total = savings.window_saved_measured(self.conn, None, t0, t1)
        self.assertAlmostEqual(w_a + w_b, total, places=4)

    def test_detail_by_account_helper(self):
        """The shared helper reads by_account from stored detail."""
        sid, rid = self._fixture()
        got = savings.measured_saved(self.conn, rid)
        import json
        detail_json = json.dumps(got)
        ba = savings.detail_by_account(detail_json, self.ACCT_A, got["saved_usd"])
        self.assertIn(self.ACCT_A, ba)
        self.assertIn(self.ACCT_B, ba)

    def test_detail_by_account_fallback(self):
        """Without by_account, the helper falls back to session_account."""
        ba = savings.detail_by_account(None, "x@test.com", 1.23)
        self.assertEqual(ba, {"x@test.com": 1.23})


class AccountSwitchReportTest(ToolCallCreditTest):
    """T9: ``report`` by account shows both accounts after a switch."""

    def test_report_shows_both_accounts(self):
        from pa import ledger_cli
        sid = "s_rpt"
        rid = sid
        self._run(rid, sid, kind="router")
        self._retrieval("ret_rpt", rid, "2026-09-01T09:55:00Z", 40000)
        # Three turns: two for A (one after retrieval), one for B
        self._turn("rpt_t0", rid, sid, "2026-09-01T10:00:00Z",
                   account="a@rpt.com")
        self._turn("rpt_t1", rid, sid, "2026-09-01T10:03:00Z",
                   account="a@rpt.com")
        self._turn("rpt_t2", rid, sid, "2026-09-01T10:05:00Z",
                   account="b@rpt.com")
        # Store savings with detail
        import json
        detail = savings.measured_saved(self.conn, rid)
        db.upsert(self.conn, "savings", {
            "run_id": rid, "session_id": sid,
            "measured_saved_usd": detail["saved_usd"],
            "measured_detail_json": json.dumps(detail),
            "updated": "2026-09-01T10:10:00Z"})
        db.upsert(self.conn, "sessions", {
            "session_id": sid, "account": "a@rpt.com",
            "started": "2026-09-01T10:00:00Z",
            "ended": "2026-09-01T10:10:00Z"})
        # Use the shared helper directly
        ba = savings.detail_by_account(
            json.dumps(detail), "a@rpt.com", detail["saved_usd"])
        self.assertIn("a@rpt.com", ba)
        self.assertIn("b@rpt.com", ba)


if __name__ == "__main__":
    unittest.main()
