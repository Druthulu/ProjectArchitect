"""pa.savings: kept-out tokens, measured savings, headline ratio (design doc E.1)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import db  # noqa: E402
from pa import savings  # noqa: E402

FABLE = "claude-fable-5-1"          # cache read $0.25/MTok
P_READ = 0.25
P_WRITE = 12.5                       # Fable 5.1 5m cache write, $/MTok
P_WRITE_1H = 20.0                     # Fable 5.1 1h cache write, $/MTok


class KeptOutTest(unittest.TestCase):

    def _turns(self, seed, end):
        return [{"ctx": seed}, {"ctx": 12345}, {"ctx": end}]

    def test_results_share_subtracts_the_answer(self):
        kept, detail = savings.kept_out_tokens(
            self._turns(4000, 40000), result_tokens_est=30000, own_output_est=10000,
            answer_tokens=1000)
        # growth 36000 * 30000/40000 = 27000, minus the 1000-token answer
        self.assertEqual(kept, 26000)
        self.assertEqual(detail["growth_tokens"], 36000)
        self.assertAlmostEqual(detail["results_share"], 0.75)
        self.assertEqual(detail["mode"], "results_share")

    def test_all_growth_and_results_est_modes(self):
        kept, _ = savings.kept_out_tokens(self._turns(4000, 40000), 30000, 10000, 1000,
                                          mode="all_growth")
        self.assertEqual(kept, 35000)
        kept, _ = savings.kept_out_tokens(self._turns(4000, 40000), 30000, 10000, 1000,
                                          mode="results_est")
        self.assertEqual(kept, 30000)

    def test_never_negative_and_empty_is_zero(self):
        kept, _ = savings.kept_out_tokens(self._turns(4000, 4100), 1, 1, 9999)
        self.assertEqual(kept, 0)
        kept, detail = savings.kept_out_tokens([], 0, 0, 0)
        self.assertEqual((kept, detail["growth_tokens"], detail["n_turns"]), (0, 0, 0))

    def test_unknown_mode_falls_back_to_results_share(self):
        kept, detail = savings.kept_out_tokens(self._turns(0, 1000), 1000, 0, 0, mode="nonsense")
        self.assertEqual(detail["mode"], "results_share")
        self.assertEqual(kept, 1000)

    def test_retrieval_row_marks_void(self):
        row = savings.retrieval_row("r1", "expA", "retriever-code", FABLE,
                                    self._turns(1000, 21000), 20000, 5000,
                                    answer_tokens=0, status="completed")
        self.assertEqual(row["void"], 1)            # returned nothing
        self.assertGreater(row["kept_out_tokens"], 0)
        row = savings.retrieval_row("r2", "expA", "retriever-code", FABLE,
                                    self._turns(1000, 21000), 20000, 5000,
                                    answer_tokens=300, status="max_turns")
        self.assertEqual(row["void"], 1)            # did not complete
        row = savings.retrieval_row("r3", "expA", "retriever-code", FABLE,
                                    self._turns(1000, 21000), 20000, 5000,
                                    answer_tokens=300, status="completed")
        self.assertEqual(row["void"], 0)
        self.assertEqual(row["parent_run_id"], "expA")


class MeasuredSavedTest(unittest.TestCase):
    """Hand-built expert with two retrievals, one of them void."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-savings-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)
        db.upsert_agent_run(self.conn, {
            "run_id": "expA", "session_id": "sessA", "kind": "expert", "phase": "36",
            "agent_type": "expert-fable", "model_seen": FABLE, "model_pinned": FABLE})
        for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T10:05:00Z",
                                "2026-09-01T10:10:00Z", "2026-09-01T10:15:00Z")):
            db.upsert_turn(self.conn, {
                "msg_id": "msg_%d" % i, "run_id": "expA", "session_id": "sessA",
                "account": "dev@example.com", "ts": ts, "model": FABLE, "ctx": 100000,
                "cost_usd": 1.0, "kind": "api", "cache_write_5m": 5000})
        db.upsert_retrieval(self.conn, {
            "run_id": "ret1", "parent_run_id": "expA", "agent_type": "retriever-code",
            "model": "claude-haiku-4-5", "returned_at": "2026-09-01T10:02:00Z",
            "kept_out_tokens": 100000, "kept_out_mode": "results_share",
            "answer_tokens": 400, "status": "completed", "void": 0})
        db.upsert_retrieval(self.conn, {
            "run_id": "ret2", "parent_run_id": "expA", "agent_type": "retriever-code",
            "model": "claude-haiku-4-5", "returned_at": "2026-09-01T10:01:00Z",
            "kept_out_tokens": 50000, "kept_out_mode": "results_share",
            "answer_tokens": 0, "status": "error", "void": 1})

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_sum_of_R_times_N_times_price_read(self):
        got = savings.measured_saved(self.conn, "expA")
        # vanilla-net-v4: ret1 R=100000, 3 session turns after return: first at write_1h ($20),
        # next two at read ($0.25).  No seed carry.
        want = 100000 * (P_WRITE_1H + 2 * P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)
        self.assertAlmostEqual(got["gross_usd"], want)
        self.assertAlmostEqual(got["seed_usd"], 0.0)
        self.assertEqual(got["savings_version"], "vanilla-net-v4")
        self.assertEqual(got["price_read"], P_READ)
        self.assertEqual(got["price_write"], P_WRITE)
        self.assertEqual(got["model"], FABLE)
        self.assertEqual(got["n_retrievals"], 2)
        self.assertEqual(got["n_void"], 1)
        by_id = {r["run_id"]: r for r in got["per_retrieval"]}
        self.assertEqual(by_id["ret1"]["N"], 3)
        self.assertAlmostEqual(by_id["ret1"]["usd"], want)
        self.assertEqual(by_id["ret1"]["first_write_ttl"], "1h")
        self.assertEqual(by_id["ret2"]["N"], 0)
        self.assertEqual(by_id["ret2"]["usd"], 0.0)

    def test_until_caps_the_request_count(self):
        got = savings.measured_saved(self.conn, "expA", until="2026-09-01T10:10:00Z")
        # vanilla-net-v4: 2 turns after return (10:05, 10:10) — first at write_1h, second at read
        self.assertAlmostEqual(got["saved_usd"], 100000 * (P_WRITE_1H + P_READ) / 1e6)

    def test_unknown_run_is_zero_not_an_error(self):
        got = savings.measured_saved(self.conn, "nope")
        self.assertEqual(got["saved_usd"], 0.0)
        self.assertEqual(got["per_retrieval"], [])

    def test_window_split_by_turn_timestamp(self):
        want = 100000 * (P_WRITE_1H + 2 * P_READ) / 1e6
        whole = savings.window_saved_measured(self.conn, "dev@example.com",
                                              "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
        self.assertAlmostEqual(whole, want)
        first_half = savings.window_saved_measured(self.conn, "dev@example.com",
                                                   "2026-09-01T00:00:00Z", "2026-09-01T10:11:00Z")
        self.assertAlmostEqual(first_half, 100000 * (P_WRITE_1H + P_READ) / 1e6)
        self.assertAlmostEqual(first_half + savings.window_saved_measured(
            self.conn, "dev@example.com", "2026-09-01T10:11:00Z", "2026-09-02T00:00:00Z"), whole)

    def test_live_saved_is_conservative(self):
        # vanilla-net-v4: live uses read only, locked uses write_1h on first — live ≤ locked
        entry = {"retrievals": {"ret1": {"R": 100000, "n_after": 3, "void": False},
                                "ret2": {"R": 50000, "n_after": 4, "void": True}}}
        live = savings.live_saved(entry, P_READ, P_WRITE)
        locked = savings.measured_saved(self.conn, "expA")["saved_usd"]
        self.assertAlmostEqual(live, 100000 * 3 * P_READ / 1e6)
        self.assertLessEqual(live, locked + 0.001)
        self.assertEqual(savings.live_saved({}, P_READ), 0.0)
        self.assertEqual(savings.kept_out_usd(100000, 0, P_READ, P_WRITE), 0.0)


SONNET = "claude-sonnet-5"


class CrossRunSavedTest(unittest.TestCase):
    """Vanilla-net-v3: the session-wide walk credits each row to its parent_run_id."""

    def tearDown(self):
        MeasuredSavedTest.tearDown(self)

    def setUp(self):
        MeasuredSavedTest.setUp(self)
        # expA finished at 10:20 having kept 90000 tokens out of everyone else
        db.upsert_retrieval(self.conn, {
            "run_id": "expA", "parent_run_id": "sessA", "agent_type": "expert-fable",
            "model": FABLE, "returned_at": "2026-09-01T10:20:00Z", "kept_out_tokens": 90000,
            "kept_out_mode": "all_growth", "answer_tokens": 300, "status": "completed", "void": 0})
        db.upsert_agent_run(self.conn, {
            "run_id": "expB", "session_id": "sessA", "kind": "expert", "phase": "36",
            "agent_type": "expert-fable", "model_seen": FABLE, "model_pinned": FABLE})
        # expB's first request comes 15 min after expA's last: within vanilla_ttl_s (3600s)
        for i, ts in enumerate(("2026-09-01T10:30:00Z", "2026-09-01T10:34:00Z")):
            db.upsert_turn(self.conn, {
                "msg_id": "msgB_%d" % i, "run_id": "expB", "session_id": "sessA",
                "account": "dev@example.com", "ts": ts, "model": FABLE, "ctx": 50000,
                "cost_usd": 1.0, "kind": "api", "cache_write_5m": 5000})
        # the router runs on Sonnet: its request 3 min later
        db.upsert_agent_run(self.conn, {"run_id": "sessA", "session_id": "sessA", "kind": "router",
                                        "model_seen": SONNET})
        db.upsert_turn(self.conn, {
            "msg_id": "msgR_0", "run_id": "sessA", "session_id": "sessA",
            "account": "dev@example.com", "ts": "2026-09-01T10:37:00Z", "model": SONNET,
            "ctx": 20000, "cost_usd": 0.5, "kind": "api", "cache_write_5m": 5000})

    def test_attribution_goes_to_row_parent(self):
        # vanilla-net-v4: ret1 (parent expA) credits go to expA; expA's row (parent sessA)
        # credits go to sessA; expB has no rows → its savings = 0
        got_expA = savings.measured_saved(self.conn, "expA")
        # ret1 (R=100000): first write at 10:05 → write_1h, then 5 reads (10:10, 10:15,
        # 10:30, 10:34 at Fable read, 10:37 at Sonnet read)
        want_ret1 = (100000 * P_WRITE_1H / 1e6
                     + 100000 * 4 * P_READ / 1e6
                     + 100000 * savings.price_read(SONNET) / 1e6)
        self.assertAlmostEqual(got_expA["saved_usd"], want_ret1)

        got_expB = savings.measured_saved(self.conn, "expB")
        self.assertAlmostEqual(got_expB["saved_usd"], 0.0)  # no rows parented to expB

        got_sessA = savings.measured_saved(self.conn, "sessA")
        # expA's row (R=90000, parent sessA): first write at 10:30 → write_1h(Fable),
        # 10:34 read(Fable), 10:37 read(Sonnet)
        want_expA_row = (90000 * P_WRITE_1H / 1e6
                         + 90000 * P_READ / 1e6
                         + 90000 * savings.price_read(SONNET) / 1e6)
        self.assertAlmostEqual(got_sessA["saved_usd"], want_expA_row)

    def test_sum_of_runs_equals_session_net(self):
        """The sum over all runs' saved_usd equals the session-wide net."""
        runs = ["expA", "expB", "sessA"]
        per_run_sum = sum(savings.measured_saved(self.conn, r)["saved_usd"] for r in runs)
        # Session net from window
        whole = savings.window_saved_measured(self.conn, "dev@example.com",
                                              "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
        self.assertAlmostEqual(per_run_sum, whole)

    def test_cold_points_of_the_session(self):
        """T11: session_requests still works (backward compat)."""
        seq = savings.session_requests(self.conn, "sessA")
        colds = [(ts[11:16], cold) for ts, _r, _m, _a, cold in seq]
        self.assertEqual(colds, [("10:00", True), ("10:05", False), ("10:10", False),
                                 ("10:15", False), ("10:30", True), ("10:34", False),
                                 ("10:37", False)])

    def test_prior_kept_unchanged(self):
        """prior_kept helper still works (backward compat for hooks)."""
        self.assertEqual(savings.prior_kept(self.conn, "sessA", "2026-09-01T10:25:00Z"), 190000)
        self.assertEqual(savings.prior_kept(self.conn, "sessA", "2026-09-01T10:25:00Z",
                                            exclude_run="expA"), 100000)

    def test_live_saved_read_only(self):
        """Vanilla-net-v3: live_saved uses read only, no prior_kept contribution."""
        entry = {"retrievals": {"ret1": {"R": 100000, "n_after": 3, "void": False}}}
        live = savings.live_saved(entry, P_READ, P_WRITE)
        self.assertAlmostEqual(live, 100000 * 3 * P_READ / 1e6)
        # prior_kept no longer contributes (attributed to parent of those rows)
        entry2 = {"prior_kept": 190000, "n_requests": 2, "retrievals": {}}
        self.assertAlmostEqual(savings.live_saved(entry2, P_READ, P_WRITE), 0.0)


class TaskBoundaryTest(unittest.TestCase):
    """T11: cold at task boundaries (expert/planner/critic/review/coder returning to the
    main run) and TTL gaps; a coder returning to its expert is not a boundary; a retriever
    return is not a boundary; the live figure is ≤ the locked one."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-boundary-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)
        # Session: router sessB on Fable
        db.upsert_agent_run(self.conn, {
            "run_id": "sessB", "session_id": "sessB", "kind": "router",
            "model_seen": FABLE})
        # Router requests: 10:00, 10:03 (warm), [expert returns at 10:04 to sessB],
        # 10:06 (cold from boundary), [coder returns at 10:07 to expD],
        # 10:08 (warm — coder returned to expert, not router)
        for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T10:03:00Z",
                                "2026-09-01T10:06:00Z", "2026-09-01T10:08:00Z")):
            db.upsert_turn(self.conn, {
                "msg_id": "bnd_%d" % i, "run_id": "sessB", "session_id": "sessB",
                "account": "dev@example.com", "ts": ts, "model": FABLE,
                "ctx": 50000, "cost_usd": 1.0, "kind": "api", "cache_write_5m": 5000})
        # Expert D: kind=expert, returns at 10:04 to the main run (parent_run_id=sessB)
        db.upsert_agent_run(self.conn, {
            "run_id": "expD", "session_id": "sessB", "kind": "expert",
            "agent_type": "expert-fable"})
        db.upsert_retrieval(self.conn, {
            "run_id": "expD", "parent_run_id": "sessB",
            "returned_at": "2026-09-01T10:04:00Z", "kept_out_tokens": 80000,
            "void": 0, "status": "completed"})
        # Coder C: kind=coder, returns at 10:07 to the expert (parent_run_id=expD, NOT sessB)
        db.upsert_agent_run(self.conn, {
            "run_id": "coderC", "session_id": "sessB", "kind": "coder",
            "agent_type": "coder-opus46"})
        db.upsert_retrieval(self.conn, {
            "run_id": "coderC", "parent_run_id": "expD",
            "returned_at": "2026-09-01T10:07:00Z", "kept_out_tokens": 30000,
            "void": 0, "status": "completed"})

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_expert_return_to_router_is_cold(self):
        seq = savings.session_requests(self.conn, "sessB")
        colds = {ts[11:16]: cold for ts, _r, _m, _a, cold in seq}
        self.assertTrue(colds["10:00"])    # first request: cold
        self.assertFalse(colds["10:03"])   # 3 min gap, within 5m TTL: warm
        self.assertTrue(colds["10:06"])    # expert returned at 10:04 to sessB: boundary cold
        self.assertFalse(colds["10:08"])   # coder returned at 10:07 to expD: NOT a boundary

    def test_retriever_return_is_not_a_boundary(self):
        db.upsert_agent_run(self.conn, {
            "run_id": "retR", "session_id": "sessB", "kind": "retriever",
            "agent_type": "retriever-code"})
        db.upsert_retrieval(self.conn, {
            "run_id": "retR", "parent_run_id": "sessB",
            "returned_at": "2026-09-01T10:02:00Z", "kept_out_tokens": 10000,
            "void": 0, "status": "completed"})
        seq = savings.session_requests(self.conn, "sessB")
        colds = {ts[11:16]: cold for ts, _r, _m, _a, cold in seq}
        self.assertFalse(colds["10:03"])   # still warm despite retriever return at 10:02

    def test_ttl_gap_is_still_cold(self):
        db.upsert_turn(self.conn, {
            "msg_id": "bnd_gap", "run_id": "sessB", "session_id": "sessB",
            "account": "dev@example.com", "ts": "2026-09-01T10:20:00Z", "model": FABLE,
            "ctx": 50000, "cost_usd": 1.0, "kind": "api"})
        seq = savings.session_requests(self.conn, "sessB")
        colds = {ts[11:16]: cold for ts, _r, _m, _a, cold in seq}
        self.assertTrue(colds["10:20"])    # 12 min gap past 5m TTL: cold

    def test_live_le_locked(self):
        """D43: the live provisional figure ≤ the locked measured figure."""
        live_entry = {"retrievals": {
            "expD": {"R": 80000, "n_after": 2, "void": False}}}
        live = savings.live_saved(live_entry, P_READ)
        # Under v3, sessB's locked figure comes from expD's row (parent sessB)
        locked = savings.measured_saved(self.conn, "sessB")["saved_usd"]
        self.assertLessEqual(live, locked + 0.001)


class ByFamilyTest(unittest.TestCase):
    """window_saved_measured(by_family=True) splits by model family (T2.1)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-byfam-")
        self.conn = db.connect(os.path.join(self.dir, "ledger.sqlite"))
        db.init_schema(self.conn)
        # Two agents: one Fable expert, one Opus expert
        db.upsert_agent_run(self.conn, {
            "run_id": "expF", "session_id": "sessB", "kind": "expert",
            "agent_type": "expert-fable", "model_seen": FABLE, "model_pinned": FABLE})
        db.upsert_agent_run(self.conn, {
            "run_id": "expO", "session_id": "sessB", "kind": "expert",
            "agent_type": "coder-opus46", "model_seen": "claude-opus-4-6",
            "model_pinned": "claude-opus-4-6"})
        # Retrieval from the first expert (parent_run_id = expF)
        db.upsert_retrieval(self.conn, {
            "run_id": "retF", "parent_run_id": "expF", "agent_type": "retriever-code",
            "model": "claude-haiku-4-5", "returned_at": "2026-09-01T10:02:00Z",
            "kept_out_tokens": 100000, "kept_out_mode": "results_share",
            "answer_tokens": 400, "status": "completed", "void": 0})
        # Turns: fable request, then opus request, both after the retrieval
        db.upsert_turn(self.conn, {
            "msg_id": "msgF1", "run_id": "expF", "session_id": "sessB",
            "account": "test@example.com", "ts": "2026-09-01T10:00:00Z",
            "model": FABLE, "cost_usd": 5.0, "kind": "api", "ctx": 100000})
        db.upsert_turn(self.conn, {
            "msg_id": "msgF2", "run_id": "expF", "session_id": "sessB",
            "account": "test@example.com", "ts": "2026-09-01T10:05:00Z",
            "model": FABLE, "cost_usd": 5.0, "kind": "api", "ctx": 100000})
        db.upsert_turn(self.conn, {
            "msg_id": "msgO1", "run_id": "expO", "session_id": "sessB",
            "account": "test@example.com", "ts": "2026-09-01T10:10:00Z",
            "model": "claude-opus-4-6", "cost_usd": 3.0, "kind": "api", "ctx": 100000})
        self.conn.commit()

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_by_family_splits(self):
        result = savings.window_saved_measured(
            self.conn, "test@example.com",
            "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
            by_family=True)
        self.assertIsInstance(result, dict)
        # retF parent is expF (fable model) → attributed to fable family
        self.assertIn("fable", result)
        self.assertGreater(result["fable"], 0)

    def test_default_returns_float(self):
        result = savings.window_saved_measured(
            self.conn, "test@example.com",
            "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
        self.assertIsInstance(result, float)


class RatioTest(unittest.TestCase):

    def test_headline_ratio(self):
        self.assertAlmostEqual(savings.headline_ratio(9.1, 4.2), 9.1 / 4.2)
        self.assertIsNone(savings.headline_ratio(9.1, 0))
        self.assertIsNone(savings.headline_ratio(9.1, None))
        self.assertEqual(savings.headline_ratio(0, 4.2), 0.0)

    def test_price_read_lookup(self):
        self.assertEqual(savings.price_read(FABLE), P_READ)
        self.assertEqual(savings.price_read("claude-opus-5[1m]"), 0.50)
        self.assertEqual(savings.price_read("something-else"), 0.0)


P_WRITE_5M = 12.5     # Fable write_5m $/MTok
P_INPUT = 10.0         # Fable input $/MTok


class VanillaNetV3Test(unittest.TestCase):
    """Vanilla-net-v3 measured savings (3.5 T9)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ttlv2-")
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

    def test_5m_parent_first_write_at_write_1h(self):
        """(1) 5m parent (its own TTL irrelevant): first write at write_1h."""
        self._run("exp1", "s1")
        self._retrieval("ret1", "exp1", "2026-09-01T10:02:00Z", 100000)
        for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T10:05:00Z",
                                "2026-09-01T10:08:00Z", "2026-09-01T10:11:00Z",
                                "2026-09-01T10:14:00Z", "2026-09-01T10:17:00Z")):
            self._turn("m_%d" % i, "exp1", "s1", ts, cache_write_5m=5000)
        got = savings.measured_saved(self.conn, "exp1")
        # vanilla-net-v4: 5 turns after 10:02 — first at write_1h, four at read
        want = 100000 * (P_WRITE_1H + 4 * P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)

    def test_second_run_no_second_write_reads_only(self):
        """(2) A second run after the row gets reads only (no second first-write)."""
        self._run("exp2a", "s2")
        self._run("exp2b", "s2")
        self._retrieval("ret2", "exp2a", "2026-09-01T10:02:00Z", 100000)
        # First run's turns (first write happens here)
        self._turn("m_0", "exp2a", "s2", "2026-09-01T10:00:00Z", cache_write_1h=5000)
        self._turn("m_1", "exp2a", "s2", "2026-09-01T10:05:00Z", cache_write_1h=5000)
        # Second run's turns — no second write, reads only
        self._turn("m_2", "exp2b", "s2", "2026-09-01T10:10:00Z", cache_write_1h=5000)
        self._turn("m_3", "exp2b", "s2", "2026-09-01T10:15:00Z", cache_write_1h=5000)
        got = savings.measured_saved(self.conn, "exp2a")
        # 3 session turns after 10:02 (10:05, 10:10, 10:15): first at write_1h, two at read
        want = 100000 * (P_WRITE_1H + 2 * P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)

    def test_session_gap_over_one_hour_rewrites(self):
        """(3) A session gap over 1h rewrites carried mass at write_1h."""
        self._run("exp3", "s3")
        self._retrieval("ret3", "exp3", "2026-09-01T10:02:00Z", 100000)
        self._turn("m_0", "exp3", "s3", "2026-09-01T10:00:00Z", cache_write_5m=5000)
        # first write on turn after return
        self._turn("m_1", "exp3", "s3", "2026-09-01T10:05:00Z", cache_write_5m=5000)
        self._turn("m_2", "exp3", "s3", "2026-09-01T10:08:00Z", cache_write_5m=5000)
        # gap of 70 min (> 3600s) -> carried mass rewritten at write_1h
        self._turn("m_3", "exp3", "s3", "2026-09-01T11:18:00Z", cache_write_5m=5000)
        self._turn("m_4", "exp3", "s3", "2026-09-01T11:21:00Z", cache_write_5m=5000)
        got = savings.measured_saved(self.conn, "exp3")
        # turns after 10:02: 10:05 (first write_1h), 10:08 (read),
        # 11:18 (cold rewrite write_1h), 11:21 (read)
        want = 100000 * (P_WRITE_1H + P_READ + P_WRITE_1H + P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)
        by_id = {r["run_id"]: r for r in got["per_retrieval"]}
        self.assertEqual(by_id["ret3"]["n_cold"], 1)

    def test_under_window_credits_full_carry_every_turn(self):
        """(4a) T30: ctx + carried stays under 1M -> full carried mass every turn."""
        self._run("exp4", "s4")
        self._retrieval("ret4a", "exp4", "2026-09-01T10:02:00Z", 100000)
        self._retrieval("ret4b", "exp4", "2026-09-01T10:06:00Z", 100000)
        self._turn("m_0", "exp4", "s4", "2026-09-01T10:00:00Z")
        for i, ts in enumerate(("2026-09-01T10:05:00Z", "2026-09-01T10:08:00Z",
                                "2026-09-01T10:11:00Z")):
            self._turn("m_%d" % (i + 1), "exp4", "s4", ts, ctx=300000)
        got = savings.measured_saved(self.conn, "exp4")
        # 10:05 a written; 10:08 a read + b written (300k+100k+100k <= 1M); 10:11 a, b read
        want = (100000 * P_WRITE_1H + 100000 * P_WRITE_1H + 100000 * P_READ
                + 200000 * P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)

    def test_window_cycle_drops_carry(self):
        """(4b) T30: crossing 1M drops the carry; rows returning after are credited afresh."""
        self._run("exp4", "s4")
        self._retrieval("ret4a", "exp4", "2026-09-01T10:02:00Z", 300000)
        self._retrieval("ret4b", "exp4", "2026-09-01T10:09:00Z", 100000)
        self._turn("m_0", "exp4", "s4", "2026-09-01T10:00:00Z")
        # 10:05: a first write 300k (room 800k)
        self._turn("m_1", "exp4", "s4", "2026-09-01T10:05:00Z", ctx=200000)
        # 10:08: 800k + carried 300k > 1M -> a dropped, nothing credited
        self._turn("m_2", "exp4", "s4", "2026-09-01T10:08:00Z", ctx=800000)
        # 10:11: b first write 100k (room 150k); a never re-written
        self._turn("m_3", "exp4", "s4", "2026-09-01T10:11:00Z", ctx=850000)
        # 10:14: b read (860k + 100k <= 1M); a never read again
        self._turn("m_4", "exp4", "s4", "2026-09-01T10:14:00Z", ctx=860000)
        got = savings.measured_saved(self.conn, "exp4")
        by_id = {r["run_id"]: r for r in got["per_retrieval"]}
        self.assertAlmostEqual(by_id["ret4a"]["usd"], 300000 * P_WRITE_1H / 1e6)
        self.assertEqual(by_id["ret4a"]["N"], 1)
        self.assertAlmostEqual(by_id["ret4b"]["usd"], 100000 * (P_WRITE_1H + P_READ) / 1e6)
        want = (300000 * P_WRITE_1H + 100000 * P_WRITE_1H + 100000 * P_READ) / 1e6
        self.assertAlmostEqual(got["saved_usd"], want)

    def test_seed_carry_exact_share(self):
        """(5) Seed carry: one run with seed 10k over three turns of known cost."""
        self._run("sMain5", "s5", kind="router")
        self._run("exp5", "s5")
        # Set seed_ctx for exp5
        self.conn.execute("UPDATE agent_runs SET seed_ctx=10000 WHERE run_id='exp5'")
        self._retrieval("ret5", "sMain5", "2026-09-01T10:02:00Z", 100000)
        # Main run turns
        self._turn("m_0", "sMain5", "s5", "2026-09-01T10:00:00Z")
        self._turn("m_1", "sMain5", "s5", "2026-09-01T10:05:00Z")
        # exp5 turns: 3 turns, ctx=50000 each, cost $1 each
        for i, ts in enumerate(("2026-09-01T10:10:00Z", "2026-09-01T10:13:00Z",
                                "2026-09-01T10:16:00Z")):
            self._turn("e_%d" % i, "exp5", "s5", ts, ctx=50000, cost_usd=1.0)
        got = savings.measured_saved(self.conn, "exp5")
        # Seed carry: 3 turns × min(1, 10000/50000) × $1 = 3 × 0.2 × 1 = $0.60
        self.assertAlmostEqual(got["seed_usd"], 0.60)
        # exp5 has no rows parented to it → gross=0 → net = -seed
        self.assertAlmostEqual(got["saved_usd"], -0.60)

    def test_governance_run_negative_net(self):
        """(6) A governance run with no rows gets negative net equal to its seed carry."""
        self._run("sMain6", "s6", kind="router")
        self._run("critic6", "s6", kind="critic")
        self.conn.execute("UPDATE agent_runs SET seed_ctx=20000 WHERE run_id='critic6'")
        self._retrieval("ret6", "sMain6", "2026-09-01T10:02:00Z", 50000)
        self._turn("m_0", "sMain6", "s6", "2026-09-01T10:00:00Z")
        self._turn("m_1", "sMain6", "s6", "2026-09-01T10:05:00Z")
        # critic turns: ctx=40000, cost $2.0 each
        self._turn("c_0", "critic6", "s6", "2026-09-01T10:10:00Z", ctx=40000, cost_usd=2.0)
        self._turn("c_1", "critic6", "s6", "2026-09-01T10:13:00Z", ctx=40000, cost_usd=2.0)
        got = savings.measured_saved(self.conn, "critic6")
        # Seed carry: 2 × min(1, 20000/40000) × $2 = 2 × 0.5 × 2 = $2.00
        self.assertAlmostEqual(got["seed_usd"], 2.0)
        self.assertAlmostEqual(got["gross_usd"], 0.0)
        self.assertAlmostEqual(got["saved_usd"], -2.0)

    def test_void_row_contributes_nothing(self):
        """(7) A void row contributes 0 to saved_usd."""
        self._run("exp7v", "s7v")
        self._retrieval("retV", "exp7v", "2026-09-01T10:02:00Z", 100000, void=1)
        for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T10:05:00Z",
                                "2026-09-01T10:08:00Z")):
            self._turn("m_%d" % i, "exp7v", "s7v", ts, cache_write_5m=5000)
        got = savings.measured_saved(self.conn, "exp7v")
        self.assertEqual(got["saved_usd"], 0.0)
        self.assertEqual(got["n_void"], 1)

    def test_returned_dict_carries_version_and_ttl(self):
        """(8) savings_version and per_retrieval first_write_ttl present."""
        self._run("exp8", "s8")
        self._retrieval("ret8", "exp8", "2026-09-01T10:02:00Z", 100000)
        self._turn("m_0", "exp8", "s8", "2026-09-01T10:00:00Z", cache_write_5m=5000)
        self._turn("m_1", "exp8", "s8", "2026-09-01T10:05:00Z", cache_write_5m=5000)
        got = savings.measured_saved(self.conn, "exp8")
        self.assertEqual(got["savings_version"], "vanilla-net-v4")
        self.assertIn("gross_usd", got)
        self.assertIn("seed_usd", got)
        by_id = {r["run_id"]: r for r in got["per_retrieval"]}
        self.assertEqual(by_id["ret8"]["savings_version"], "vanilla-net-v4")
        self.assertEqual(by_id["ret8"]["first_write_ttl"], "1h")

    def test_kept_out_usd_unchanged(self):
        """(9) kept_out_usd formula unchanged for its own callers."""
        self.assertEqual(savings.kept_out_usd(100000, 3, P_READ, P_WRITE),
                         100000 * (P_WRITE + 2 * P_READ) / 1e6)
        self.assertEqual(savings.kept_out_usd(100000, 0, P_READ, P_WRITE), 0.0)
        self.assertEqual(savings.kept_out_usd(0, 5, P_READ, P_WRITE), 0.0)

    # -- window_saved_measured follows vanilla-net-v4 --------------------------

    def test_sum_over_runs_equals_session_net(self):
        """Sum of per-run saved_usd equals the session-wide window net."""
        self._run("sMain_w", "ws", kind="router")
        self._run("exp_a", "ws")
        self._run("exp_b", "ws")
        self._retrieval("ret_a", "exp_a", "2026-09-01T10:02:00Z", 100000)
        self._turn("w0", "exp_a", "ws", "2026-09-01T10:00:00Z")
        self._turn("w1", "exp_a", "ws", "2026-09-01T10:05:00Z")
        self._turn("w2", "exp_a", "ws", "2026-09-01T10:10:00Z")
        self._turn("w3", "exp_b", "ws", "2026-09-01T10:30:00Z")
        whole = savings.window_saved_measured(self.conn, "test@example.com",
                                              "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
        per_run = sum(savings.measured_saved(self.conn, r)["saved_usd"]
                      for r in ("sMain_w", "exp_a", "exp_b"))
        self.assertAlmostEqual(whole, per_run, places=4)

    def test_window_detail_returns_gross_seeds_net(self):
        """detail=True returns {gross, seeds, net}."""
        self._run("sMain_d", "sd", kind="router")
        self._run("exp_d", "sd")
        self.conn.execute("UPDATE agent_runs SET seed_ctx=5000 WHERE run_id='exp_d'")
        self._retrieval("ret_d", "sMain_d", "2026-09-01T10:02:00Z", 80000)
        self._turn("d_0", "sMain_d", "sd", "2026-09-01T10:00:00Z")
        self._turn("d_1", "sMain_d", "sd", "2026-09-01T10:05:00Z")
        # exp_d turns in window
        self._turn("d_2", "exp_d", "sd", "2026-09-01T10:10:00Z", ctx=50000, cost_usd=1.0)
        d = savings.window_saved_measured(self.conn, "test@example.com",
                                          "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                                          detail=True)
        self.assertIsInstance(d, dict)
        self.assertIn("gross", d)
        self.assertIn("seeds", d)
        self.assertIn("net", d)
        self.assertAlmostEqual(d["net"], d["gross"] - d["seeds"])
        self.assertGreater(d["gross"], 0)
        self.assertGreater(d["seeds"], 0)

    def test_window_after_first_turn_at_read(self):
        """Window starting after the first write: remaining turns at read."""
        self._run("exp_w", "sw")
        self._retrieval("ret_w", "exp_w", "2026-09-01T10:02:00Z", 100000)
        self._turn("w0", "exp_w", "sw", "2026-09-01T10:00:00Z")
        self._turn("w1", "exp_w", "sw", "2026-09-01T10:05:00Z")
        self._turn("w2", "exp_w", "sw", "2026-09-01T10:10:00Z")
        self._turn("w3", "exp_w", "sw", "2026-09-01T10:14:00Z")
        self._turn("w4", "exp_w", "sw", "2026-09-01T10:17:00Z")
        # Window starts after the first write (10:05): 10:10, 10:14, 10:17 at read
        got = savings.window_saved_measured(self.conn, "test@example.com",
                                            "2026-09-01T10:07:00Z", "2026-09-02T00:00:00Z")
        want = 100000 * 3 * P_READ / 1e6
        self.assertAlmostEqual(got, want)

    def test_live_saved_starts_at_minus_seed(self):
        """live_saved starts at minus the run's seed carry."""
        entry = {"seed_carry_usd": 0.5,
                 "retrievals": {"ret1": {"R": 80000, "n_after": 2, "void": False}}}
        live = savings.live_saved(entry, P_READ)
        self.assertAlmostEqual(live, -0.5 + 80000 * 2 * P_READ / 1e6)

    def test_session_filter_on_window(self):
        """session= filter: two sessions, filter to one equals that session's net."""
        self._run("sM_a", "ws_a", kind="router")
        self._run("sM_b", "ws_b", kind="router")
        self._retrieval("ret_sa", "sM_a", "2026-09-01T10:02:00Z", 60000)
        self._retrieval("ret_sb", "sM_b", "2026-09-01T10:02:00Z", 40000)
        self._turn("ta1", "sM_a", "ws_a", "2026-09-01T10:00:00Z")
        self._turn("ta2", "sM_a", "ws_a", "2026-09-01T10:05:00Z")
        self._turn("tb1", "sM_b", "ws_b", "2026-09-01T10:00:00Z")
        self._turn("tb2", "sM_b", "ws_b", "2026-09-01T10:05:00Z")
        t0, t1 = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"
        whole = savings.window_saved_measured(self.conn, "test@example.com", t0, t1)
        sa = savings.window_saved_measured(self.conn, "test@example.com", t0, t1, session="ws_a")
        sb = savings.window_saved_measured(self.conn, "test@example.com", t0, t1, session="ws_b")
        self.assertAlmostEqual(sa + sb, whole)
        # Each session's filter equals its own net
        self.assertGreater(sa, 0)
        self.assertGreater(sb, 0)


class PooledPctSavedTest(unittest.TestCase):
    """T32.1.c3: saved $ convert to window percent by model mix through the pool rates."""

    def test_session_and_window_pct_saved_pooled(self):
        import time as _time
        from unittest import mock
        from pa import summary
        d = tempfile.mkdtemp(prefix="pa3-pool-")
        conn = db.connect(os.path.join(d, "ledger.sqlite"))
        try:
            db.init_schema(conn)
            acct = "pool@example.com"
            now = _time.time()
            ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 600))
            db.upsert_session(conn, {"session_id": "sP", "account": acct, "started": ts,
                                     "ended": ts, "cost_usd": 60.0})
            db.insert_utilization(conn, {"ts": ts, "account": acct, "session_id": "sP",
                                         "window": "seven_day", "pct": 2.0,
                                         "resets_at": int(now + 3 * 86400)})
            for fam, rate in (("fable", 0.005), ("opus", 0.0005)):
                db.upsert_fit_pool(conn, {"tier": "max", "window": "seven_day", "family": fam,
                                          "rate": rate, "se": 0.0001, "n": 100, "n_instances": 2,
                                          "regime_since": "2026-09-01T00:00:00Z",
                                          "built_at": ts})
            conn.commit()

            def walk(conn_, account, t0, t1, by_family=False, detail=False, **kw):
                if by_family:
                    return {"fable": 20.0, "opus": 30.0}
                return {"gross": 50.0, "seeds": 0.0, "net": 50.0} if detail else 50.0

            with mock.patch.object(savings, "window_saved_measured", side_effect=walk):
                accts = summary.accounts_block(conn, {})
                sess = summary.sessions_block(conn, {}, accounts=accts)
        finally:
            db.close(conn)
            shutil.rmtree(d, ignore_errors=True)
        w = accts[acct]["windows"]["seven_day"]
        self.assertAlmostEqual(w["pct_saved_pooled"], 11.5, places=6)
        self.assertEqual(sorted(w["pool_rates"]), ["fable", "opus"])
        self.assertEqual(w["pool_meta"]["n_instances"], 2)
        sw = sess["sP"]["windows"]["seven_day"]
        self.assertEqual(sw["net_saved_by_family"], {"fable": 20.0, "opus": 30.0})
        self.assertAlmostEqual(sw["pct_saved_pooled"], 11.5, places=6)


if __name__ == "__main__":
    unittest.main()
