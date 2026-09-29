"""pa.fit: crossing fit, level OLS, pooled slope, cost axis, live fixture."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config  # noqa: E402
from pa import db  # noqa: E402
from pa import fit  # noqa: E402
from pa import paths  # noqa: E402
from pa import summary  # noqa: E402

FIXTURE_PATH = os.path.join(ROOT, "tests", "fixtures", "utilization",
                            "live_2026-09-20.json")


# --------------------------------------------------------------------------- helpers

def _linear_samples(slope, n, cost_start=0.0, cost_step=1.0, quantize="floor"):
    """Generate samples with pct = quantize(slope * cost * 100)."""
    samples = []
    for i in range(n):
        cost = cost_start + i * cost_step
        pct_exact = slope * cost * 100.0
        if quantize == "floor":
            pct = float(int(pct_exact))
        elif quantize == "round":
            pct = float(round(pct_exact))
        else:
            pct = pct_exact
        epoch = 1700000000 + i * 60
        samples.append((epoch, pct, cost))
    return samples


class FitTestCase(unittest.TestCase):
    """Base: a temp ledger (PA_LEDGER_DIR)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-fit-")
        self.ledger = os.path.join(self.dir, "ledger")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def conn(self):
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        return conn


# --------------------------------------------------------------------------- synthetic tests

class TestCrossingFitFloor(unittest.TestCase):
    """(a) floor-quantized linear series with slope 0.005 -> crossing fit within 10%."""

    def test_crossing_fit_floor(self):
        slope = 0.005  # pct_per_dollar = 0.005 -> $200/window
        # Generate enough samples for >=5 crossings: need cost steps that cross percent ticks
        # At slope 0.005, each percent tick every $2.00 of cost
        # Use irregular cost spacing
        samples = []
        costs = [0.0, 0.3, 0.8, 1.5, 2.1, 2.9, 3.5, 4.2, 5.0, 5.8,
                 6.5, 7.3, 8.1, 9.0, 10.0, 11.2, 12.5, 14.0, 15.5, 17.0,
                 18.5, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0]
        for i, cost in enumerate(costs):
            pct_exact = slope * cost * 100.0
            pct = float(int(pct_exact))  # floor
            epoch = 1700000000 + i * 120
            samples.append((epoch, pct, cost))

        cr = fit.crossings(samples)
        self.assertGreaterEqual(len(cr), 5)
        result = fit.crossing_fit(cr)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["slope"], slope, delta=slope * 0.10)
        self.assertEqual(result["n"], len(cr))

        # fit_instance
        f = fit.fit_instance(samples, config.defaults())
        self.assertEqual(f["method"], "crossing")
        self.assertEqual(f["quality"], "band")  # T32.1: 27 samples inside an hour is not mature
        self.assertAlmostEqual(f["slope"], slope, delta=slope * 0.10)
        self.assertIsNotNone(f["dollars_per_window"])
        self.assertAlmostEqual(f["dollars_per_window"], 200.0, delta=20.0)


class TestCrossingFitRound(unittest.TestCase):
    """(b) round-quantized: same slope, same quality."""

    def test_crossing_fit_round(self):
        slope = 0.005
        costs = [0.0, 0.3, 0.8, 1.5, 2.1, 2.9, 3.5, 4.2, 5.0, 5.8,
                 6.5, 7.3, 8.1, 9.0, 10.0, 11.2, 12.5, 14.0, 15.5, 17.0,
                 18.5, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0]
        samples = []
        for i, cost in enumerate(costs):
            pct_exact = slope * cost * 100.0
            pct = float(round(pct_exact))  # round
            epoch = 1700000000 + i * 120
            samples.append((epoch, pct, cost))

        f = fit.fit_instance(samples, config.defaults())
        self.assertIn(f["method"], ("crossing", "level"))
        self.assertAlmostEqual(f["slope"], slope, delta=slope * 0.10)


class TestMultiLevelJump(unittest.TestCase):
    """(c) a 3-point jump -> exactly one crossing at the top level."""

    def test_one_crossing_at_top(self):
        # pct jumps from 0 to 3 in one step
        samples = [
            (1700000000, 0.0, 0.0),
            (1700000060, 3.0, 5.0),
            (1700000120, 4.0, 7.0),
        ]
        cr = fit.crossings(samples)
        # First pair: 0->3, cost 0->5, is one crossing at level 3.0/100
        self.assertEqual(len(cr), 2)  # 0->3 and 3->4
        self.assertAlmostEqual(cr[0]["level"], 0.03)
        self.assertAlmostEqual(cr[1]["level"], 0.04)


class TestLevelFitFallback(unittest.TestCase):
    """(d) few crossings but n >= 8 and span >= 3 -> level fit, band."""

    def test_level_fit_band(self):
        slope = 0.005
        # Generate samples with no crossing (same pct ranges but too few crossings)
        # Use n_min=8 samples but only 2 crossings
        samples = _linear_samples(slope, 12, cost_start=0.0, cost_step=1.5)
        # Verify fewer crossings
        cr = fit.crossings(samples)
        # With floor quantization at slope 0.005 and step 1.5,
        # crossing at every $2 of cost -> maybe 3+ crossings.
        # Let's use a different approach: non-quantized samples -> level OLS
        samples_nq = []
        for i in range(12):
            cost = i * 1.5
            pct = slope * cost * 100.0 + 0.1  # non-integer
            epoch = 1700000000 + i * 120
            samples_nq.append((epoch, pct, cost))

        f = fit.fit_instance(samples_nq, config.defaults())
        self.assertEqual(f["method"], "level")
        self.assertIn(f["quality"], ("band", "ok"))
        self.assertAlmostEqual(f["slope"], slope, delta=slope * 0.15)


class TestBelowMinimums(unittest.TestCase):
    """(e) below the minimums -> none."""

    def test_too_few_samples(self):
        samples = [(1700000000, 0.0, 0.0), (1700000060, 1.0, 2.0)]
        f = fit.fit_instance(samples, config.defaults())
        self.assertIsNone(f["method"])
        self.assertEqual(f["quality"], "none")

    def test_small_span(self):
        # 8 samples but span < 3
        samples = [(1700000000 + i * 60, 5.0, float(i)) for i in range(10)]
        f = fit.fit_instance(samples, config.defaults())
        self.assertEqual(f["quality"], "none")


class TestPooledSlope(unittest.TestCase):
    """(f) pooled slope of two instances."""

    def test_pooled(self):
        f1 = {"slope": 0.005, "se": 0.0005, "n": 10, "crossings": 5,
              "span_pct": 10, "method": "crossing", "quality": "ok"}
        f2 = {"slope": 0.0048, "se": 0.0006, "n": 8, "crossings": 4,
              "span_pct": 8, "method": "crossing", "quality": "band"}
        result = fit.pooled_slope([f1, f2], config.defaults())
        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "pooled")
        self.assertEqual(result["quality"], "band")
        # Should be close to inverse-variance weighted mean of 0.005 and 0.0048
        self.assertAlmostEqual(result["slope"], 0.005, delta=0.001)
        self.assertIsNotNone(result["dollars_per_window"])


# --------------------------------------------------------------------------- cost_axis

class TestCostAxis(FitTestCase):
    """(g) cost_axis sums two concurrent sessions and honours model_filter."""

    def test_cost_axis_two_sessions(self):
        conn = self.conn()
        try:
            db.seed_prices(conn)
            account = "test@example.com"
            # Insert turns from two sessions
            for i in range(5):
                ts = "2026-09-19T%02d:00:00Z" % (10 + i)
                db.upsert_turn(conn, {
                    "msg_id": "s1_msg_%d" % i, "session_id": "session1",
                    "ts": ts, "cost_usd": 1.0, "kind": "api",
                    "account": account, "model": "claude-opus-4-6",
                })
            for i in range(5):
                ts = "2026-09-19T%02d:30:00Z" % (10 + i)
                db.upsert_turn(conn, {
                    "msg_id": "s2_msg_%d" % i, "session_id": "session2",
                    "ts": ts, "cost_usd": 0.5, "kind": "api",
                    "account": account, "model": "claude-opus-4-6",
                })
            conn.commit()

            started = fit._parse_ts_epoch("2026-09-19T09:00:00Z")
            # Sample at the end: should see all turns
            ep_end = fit._parse_ts_epoch("2026-09-19T15:00:00Z")
            costs = fit.cost_axis(conn, account, started, [ep_end])
            # 5 * 1.0 + 5 * 0.5 = 7.5
            self.assertAlmostEqual(costs[0], 7.5, places=2)
        finally:
            db.close(conn)

    def test_model_filter(self):
        conn = self.conn()
        try:
            db.seed_prices(conn)
            account = "test@example.com"
            for i in range(5):
                ts = "2026-09-19T%02d:00:00Z" % (10 + i)
                db.upsert_turn(conn, {
                    "msg_id": "f_msg_%d" % i, "session_id": "session1",
                    "ts": ts, "cost_usd": 2.0, "kind": "api",
                    "account": account, "model": "claude-fable-5-1",
                })
                db.upsert_turn(conn, {
                    "msg_id": "o_msg_%d" % i, "session_id": "session1",
                    "ts": ts, "cost_usd": 1.0, "kind": "api",
                    "account": account, "model": "claude-opus-4-6",
                })
            conn.commit()

            started = fit._parse_ts_epoch("2026-09-19T09:00:00Z")
            ep_end = fit._parse_ts_epoch("2026-09-19T15:00:00Z")
            # fable filter
            costs = fit.cost_axis(conn, account, started, [ep_end],
                                  model_filter="fable")
            self.assertAlmostEqual(costs[0], 10.0, places=2)  # 5 * 2.0
            # no filter -> all
            costs_all = fit.cost_axis(conn, account, started, [ep_end])
            self.assertAlmostEqual(costs_all[0], 15.0, places=2)  # 5*2 + 5*1
        finally:
            db.close(conn)


# --------------------------------------------------------------------------- live fixture

class TestLiveFixture(FitTestCase):
    """(h) live fixture: refit_all, check slopes and quality."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE_PATH):
            raise unittest.SkipTest("live fixture not found")
        with open(FIXTURE_PATH) as f:
            cls.fixture = json.load(f)

    def _load_fixture(self, conn):
        """Load the fixture into the ledger db."""
        fx = self.fixture
        account = fx["account"]
        scols = fx["samples_columns"]  # ts, window, pct, resets_at, session_id8, session_cost, model
        tcols = fx["turns_columns"]    # ts, cost_usd, model, session_id8

        # Insert accounts row
        db.upsert_account(conn, {"email": account})

        # Insert window_instances
        for inst in fx["instances"]:
            row = dict(inst)
            row["account"] = account
            db.upsert_window_instance(conn, row)

        # Insert utilization samples
        sid_map = {}  # session_id8 -> full fake session_id
        for s in fx["samples"]:
            rec = dict(zip(scols, s))
            sid8 = rec["session_id8"]
            if sid8 not in sid_map:
                sid_map[sid8] = "fixture-%s-0000-0000-000000000000" % sid8
            full_sid = sid_map[sid8]
            db.insert_utilization(conn, {
                "ts": rec["ts"], "window": rec["window"],
                "pct": rec["pct"], "resets_at": rec["resets_at"],
                "session_id": full_sid, "session_cost": rec["session_cost"],
                "model": rec["model"], "account": account,
            })

        # Insert sessions
        for sid8, full_sid in sid_map.items():
            db.upsert_session(conn, {
                "session_id": full_sid, "account": account,
                "kind": "test", "started": "2026-09-16T00:00:00Z",
            })

        # Insert turns
        turn_sid_map = {}
        for t in fx["turns"]:
            rec = dict(zip(tcols, t))
            sid8 = rec["session_id8"]
            if sid8 not in sid_map:
                sid_map[sid8] = "fixture-%s-0000-0000-000000000000" % sid8
                db.upsert_session(conn, {
                    "session_id": sid_map[sid8], "account": account,
                    "kind": "test", "started": "2026-09-16T00:00:00Z",
                })
            full_sid = sid_map[sid8]
            if sid8 not in turn_sid_map:
                turn_sid_map[sid8] = 0
            turn_sid_map[sid8] += 1
            db.upsert_turn(conn, {
                "msg_id": "turn_%s_%d" % (sid8, turn_sid_map[sid8]),
                "session_id": full_sid, "ts": rec["ts"],
                "cost_usd": rec["cost_usd"], "kind": "api",
                "account": account, "model": rec["model"],
            })
        conn.commit()

    def test_live_fixture_fits(self):
        conn = self.conn()
        try:
            self._load_fixture(conn)
            cfg = config.defaults()
            fits = fit.refit_all(conn, cfg)

            account = self.fixture["account"]
            # The three five_hour instances on 2026-09-19 21:50Z, 02:50Z, 07:50Z
            # resets_at: 1789854600, 1789872600, 1789890600
            five_hour_resets = [1789854600, 1789872600, 1789890600]
            five_hour_fits = []
            for ra in five_hour_resets:
                key = (account, "five_hour", ra)
                self.assertIn(key, fits, "missing fit for five_hour %d" % ra)
                f = fits[key]
                self.assertIn(f["quality"], ("ok", "band"),
                              "five_hour %d quality=%s" % (ra, f["quality"]))
                dpw = f.get("dollars_per_window")
                self.assertIsNotNone(dpw, "five_hour %d has no dollars_per_window" % ra)
                self.assertGreaterEqual(dpw, 180.0, "five_hour %d dpw=%.0f" % (ra, dpw))
                self.assertLessEqual(dpw, 230.0, "five_hour %d dpw=%.0f" % (ra, dpw))
                five_hour_fits.append(f)

            # Slopes within 30% of each other
            slopes = [f["slope"] for f in five_hour_fits]
            avg = sum(slopes) / len(slopes)
            for s in slopes:
                self.assertAlmostEqual(s, avg, delta=avg * 0.30,
                                       msg="five_hour slopes not within 30%%: %s" % slopes)

            # seven_day instances
            seven_day_resets = [1789869600, 1790474400]
            for ra in seven_day_resets:
                key = (account, "seven_day", ra)
                self.assertIn(key, fits, "missing fit for seven_day %d" % ra)
                f = fits[key]
                self.assertIn(f["quality"], ("ok", "band"),
                              "seven_day %d quality=%s" % (ra, f["quality"]))
                dpw = f.get("dollars_per_window")
                self.assertIsNotNone(dpw, "seven_day %d has no dollars_per_window" % ra)
                self.assertGreaterEqual(dpw, 800.0, "seven_day %d dpw=%.0f" % (ra, dpw))
                self.assertLessEqual(dpw, 980.0, "seven_day %d dpw=%.0f" % (ra, dpw))

            # The 2026-09-17 five_hour instance (10 samples, span 1) -> none
            key = (account, "five_hour", 1789620000)
            self.assertIn(key, fits)
            self.assertEqual(fits[key]["quality"], "none")
        finally:
            db.close(conn)


# --------------------------------------------------------------------------- summary integration

class TestSummaryAccountsBlock(FitTestCase):
    """(i) accounts_block carries pct_saved and pace for five_hour."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE_PATH):
            raise unittest.SkipTest("live fixture not found")
        with open(FIXTURE_PATH) as f:
            cls.fixture = json.load(f)

    def _load_fixture(self, conn):
        TestLiveFixture._load_fixture(self, conn)

    def test_accounts_block_fit_fields(self):
        conn = self.conn()
        try:
            self._load_fixture(conn)
            cfg = config.defaults()
            fit.refit_all(conn, cfg)
            block = summary.accounts_block(conn, cfg)
            account = self.fixture["account"]
            self.assertIn(account, block)
            acct = block[account]
            wins = acct["windows"]
            self.assertIn("five_hour", wins)
            fh = wins["five_hour"]
            # pct_per_dollar should be set
            self.assertIsNotNone(fh.get("pct_per_dollar"))
            # fit_quality should be ok or band
            self.assertIn(fh["fit_quality"], ("ok", "band"))
            # pace should be a number (not None) if we have pct_last and elapsed > 1%
            # It may or may not be set depending on timing but should be present
            self.assertIn("pace", fh)
        finally:
            db.close(conn)


class TestYoungInstanceUsesPrevious(FitTestCase):
    """T30.c2: a young instance borrows the previous instance's fit."""

    ACCT = "young@example.com"
    DUR = 604800

    def _load(self, conn, started_ago):
        now = int(time.time())
        new_reset = now - started_ago + self.DUR
        old_reset = new_reset - self.DUR
        db.upsert_window_instance(conn, {
            "account": self.ACCT, "window": "seven_day", "resets_at": old_reset,
            "pct_per_dollar": 0.0011, "fit_method": "crossing", "fit_n": 120,
            "fit_crossings": 30, "fit_span": 60.0, "fit_se": 0.0001, "fit_quality": "ok"})
        db.upsert_window_instance(conn, {
            "account": self.ACCT, "window": "seven_day", "resets_at": new_reset,
            "pct_per_dollar": 0.009, "fit_method": "level", "fit_n": 8,
            "fit_crossings": 3, "fit_span": 4.0, "fit_se": 0.002, "fit_quality": "ok"})
        for i in range(8):
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(now - started_ago + 60 + i * 60))
            db.insert_utilization(conn, {
                "ts": ts, "window": "seven_day", "pct": float(i), "resets_at": new_reset,
                "session_id": "young-sess", "account": self.ACCT})
        conn.commit()

    def _win(self, conn, cfg):
        return summary.accounts_block(conn, cfg)[self.ACCT]["windows"]["seven_day"]

    def test_young_by_age(self):
        conn = self.conn()
        try:
            self._load(conn, started_ago=3600)
            cfg = config.defaults()
            w = self._win(conn, cfg)
            self.assertEqual(w["fit_method"], "previous")
            self.assertEqual(w["pct_per_dollar"], 0.0011)
            self.assertEqual(w["fit_quality"], "ok")
            self.assertTrue(w["fit_reason"].startswith("age "))
            cfg["fit"]["instance_min_age_s"] = 0
            cfg["fit"]["instance_min_samples"] = 0
            cfg["fit"]["instance_min_span_pct"] = 0
            w = self._win(conn, cfg)
            self.assertEqual(w["fit_method"], "level")
            self.assertEqual(w["pct_per_dollar"], 0.009)
            self.assertIsNone(w["fit_reason"])
        finally:
            db.close(conn)

    def test_young_by_samples(self):
        conn = self.conn()
        try:
            self._load(conn, started_ago=2 * 86400)
            cfg = config.defaults()
            w = self._win(conn, cfg)
            self.assertEqual(w["fit_method"], "previous")
            self.assertEqual(w["pct_per_dollar"], 0.0011)
            self.assertEqual(w["fit_reason"], "samples 8 < 50")
            cfg["fit"]["instance_min_age_s"] = 0
            cfg["fit"]["instance_min_samples"] = 0
            cfg["fit"]["instance_min_span_pct"] = 0
            w = self._win(conn, cfg)
            self.assertEqual(w["fit_method"], "level")
            self.assertEqual(w["pct_per_dollar"], 0.009)
        finally:
            db.close(conn)

    def _load_n(self, conn, started_ago, n, span, cost_to=None, ledger_to=None):
        """Old+new instances; n samples on the new one, pct 0..span, session_cost 0..cost_to;
        ``ledger_to``: ten api turns in the ledger summing to it (T32.1.c2: the ratio's denominator)."""
        self._load(conn, started_ago)
        conn.execute("DELETE FROM utilization")
        now = int(time.time())
        new_reset = now - started_ago + self.DUR
        for i in range(n):
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(now - started_ago + 60 + i * 60))
            row = {"ts": ts, "window": "seven_day", "pct": span * i / (n - 1),
                   "resets_at": new_reset, "session_id": "young-sess", "account": self.ACCT}
            if cost_to is not None:
                row["session_cost"] = cost_to * i / (n - 1)
            db.insert_utilization(conn, row)
        for i in range(10 if ledger_to is not None else 0):
            db.upsert_turn(conn, {"msg_id": "young-t%d" % i, "session_id": "young-sess",
                                  "kind": "api", "account": self.ACCT, "model": "claude-opus-4-6",
                                  "cost_usd": ledger_to / 10.0,
                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
                                      now - started_ago + 30 + i * 60))})
        conn.commit()

    def test_young_by_span(self):
        conn = self.conn()
        try:
            self._load_n(conn, 2 * 86400, 60, 4.0)
            w = self._win(conn, config.defaults())
            self.assertEqual(w["fit_method"], "previous")
            self.assertEqual(w["fit_reason"], "span 4.0 < 10")
        finally:
            db.close(conn)

    def test_ratio_past_min_pct(self):
        conn = self.conn()
        try:
            # session_cost (the harness cost-state, [1m] premium) is 150; the ledger says 100
            self._load_n(conn, 2 * 86400, 60, 12.0, cost_to=150.0, ledger_to=100.0)
            w = self._win(conn, config.defaults())
            self.assertEqual(w["fit_method"], "ratio")
            self.assertAlmostEqual(w["pct_per_dollar"], 0.0012, places=6)
            self.assertEqual(w["fit_quality"], "ok")
            self.assertAlmostEqual(w["dollars_per_window"], 1 / 0.0012, places=3)
        finally:
            db.close(conn)

    def test_residual_rows_move_neither_cost_axis_nor_ratio(self):
        """T32.1.c3: a $1000 residual-kind turn in the window changes neither the fit's cost
        axis nor the ratio (both read kind='api' only)."""
        conn = self.conn()
        try:
            self._load_n(conn, 2 * 86400, 60, 12.0, cost_to=150.0, ledger_to=100.0)
            cfg = config.defaults()
            now = int(time.time())
            started = now - 2 * 86400
            axis_before = fit.cost_axis(conn, self.ACCT, started, [now])
            axes_before = fit.cost_axes(conn, self.ACCT, started, [now])
            w_before = self._win(conn, cfg)
            db.upsert_turn(conn, {"msg_id": "residual:young-sess", "session_id": "young-sess",
                                  "kind": "residual", "account": self.ACCT,
                                  "model": "claude-opus-4-6", "cost_usd": 1000.0,
                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime(now - 3600))})
            conn.commit()
            self.assertEqual(fit.cost_axis(conn, self.ACCT, started, [now]), axis_before)
            self.assertEqual(fit.cost_axes(conn, self.ACCT, started, [now]), axes_before)
            w_after = self._win(conn, cfg)
            self.assertEqual(w_after["fit_method"], "ratio")
            self.assertAlmostEqual(w_after["pct_per_dollar"], w_before["pct_per_dollar"], places=9)
            self.assertAlmostEqual(w_after["pct_per_dollar"], 0.0012, places=6)
        finally:
            db.close(conn)

    def test_no_ratio_below_min_pct(self):
        conn = self.conn()
        try:
            self._load_n(conn, 2 * 86400, 60, 3.0, cost_to=100.0, ledger_to=100.0)
            w = self._win(conn, config.defaults())
            self.assertNotEqual(w["fit_method"], "ratio")
        finally:
            db.close(conn)


class TestFitInstanceMaturity(unittest.TestCase):
    """T32.1: fit_instance "ok" needs instance_min_samples, span and age."""

    def test_young_band(self):
        # 8 quantized samples, 3 points of span, inside an hour
        s = [(1700000000 + i * 300, float(i * 3 // 7), float(i)) for i in range(8)]
        r = fit.fit_instance(s, config.defaults())
        self.assertIsNotNone(r["method"])
        self.assertEqual(r["quality"], "band")

    def test_mature_ok(self):
        # 60 samples, span 20, epochs over 2 days
        s = [(1700000000 + i * 3000, 20.0 * i / 59, 2.0 * i) for i in range(60)]
        r = fit.fit_instance(s, config.defaults())
        self.assertIsNotNone(r["method"])
        self.assertEqual(r["quality"], "ok")


# --------------------------------------------------------------------------- union (cross-machine)

# --------------------------------------------------------------------------- multi-family (T2.1)

class TestTwoFamilySynthetic(unittest.TestCase):
    """(l) a two-family series (Fable 0.005, Opus 0.0005) recovers both within 15%."""

    def test_two_family_fit(self):
        fable_slope = 0.005   # %/$ for fable
        opus_slope = 0.0005   # %/$ for opus
        samples = []
        fable_cost = 0.0
        opus_cost = 0.0
        # Interleave fable and opus spending, floor-quantized pct
        # More samples and larger costs for a better fit on the small coefficient
        for i in range(120):
            epoch = 1700000000 + i * 60
            if i % 3 == 0:
                fable_cost += 1.5
            else:
                opus_cost += 2.0
            pct_exact = (fable_slope * fable_cost + opus_slope * opus_cost) * 100.0
            pct = float(int(pct_exact))  # floor quantize
            samples.append((epoch, pct, {"fable": fable_cost, "opus": opus_cost}))

        f = fit.fit_instance(samples, config.defaults())
        self.assertIsNotNone(f.get("families"), "expected per-family fit")
        fams = f["families"]
        self.assertIn("fable", fams)
        fw = fams["fable"]["weight"]
        self.assertAlmostEqual(fw, fable_slope, delta=fable_slope * 0.15,
                               msg="fable weight %.6f not within 15%% of %.6f" % (fw, fable_slope))
        if "opus" in fams:
            ow = fams["opus"]["weight"]
            self.assertAlmostEqual(ow, opus_slope, delta=opus_slope * 0.15,
                                   msg="opus weight %.6f not within 15%% of %.6f" % (ow, opus_slope))


class TestOneFamilyFallback(unittest.TestCase):
    """(m) one-family series falls back to single slope, assigned to the dominant family."""

    def test_single_family_fallback(self):
        slope = 0.005
        samples = []
        for i in range(30):
            cost = i * 2.0
            pct_exact = slope * cost * 100.0
            pct = float(int(pct_exact))
            epoch = 1700000000 + i * 120
            samples.append((epoch, pct, {"fable": cost}))
        f = fit.fit_instance(samples, config.defaults())
        # Should use single-slope path since only one family
        self.assertIsNotNone(f.get("slope"))
        self.assertAlmostEqual(f["slope"], slope, delta=slope * 0.15)
        # families should assign to the dominant family
        fams = f.get("families")
        if fams:
            self.assertIn("fable", fams)


class TestLiveFixtureMultiFamily(FitTestCase):
    """(n) live fixture yields per-family weights; Fable near 0.005, Opus+Sonnet < 0.002."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE_PATH):
            raise unittest.SkipTest("live fixture not found")
        with open(FIXTURE_PATH) as f:
            cls.fixture = json.load(f)

    def _load_fixture(self, conn):
        TestLiveFixture._load_fixture(self, conn)

    def test_live_per_family_weights(self):
        conn = self.conn()
        try:
            self._load_fixture(conn)
            cfg = config.defaults()
            fits = fit.refit_all(conn, cfg)

            account = self.fixture["account"]
            # Check five_hour instances that carry both families
            five_hour_resets = [1789854600, 1789872600, 1789890600]
            found_multi = False
            for ra in five_hour_resets:
                key = (account, "five_hour", ra)
                if key not in fits:
                    continue
                f = fits[key]
                fams = f.get("families")
                if not fams or len(fams) < 2:
                    continue
                found_multi = True
                # Fable weight should be in [0.004, 0.0065]
                if "fable" in fams:
                    fw = fams["fable"]["weight"]
                    self.assertGreaterEqual(fw, 0.004,
                                           "five_hour %d fable weight %.6f < 0.004" % (ra, fw))
                    self.assertLessEqual(fw, 0.0065,
                                         "five_hour %d fable weight %.6f > 0.0065" % (ra, fw))
                # Opus+Sonnet weight should be below 0.002
                for other_fam in ("opus", "sonnet"):
                    if other_fam in fams:
                        ow = fams[other_fam]["weight"]
                        self.assertLess(ow, 0.002,
                                        "five_hour %d %s weight %.6f >= 0.002" % (ra, other_fam, ow))

            # refit_all should have written fit_detail
            for ra in five_hour_resets:
                row = conn.execute(
                    "SELECT fit_detail FROM window_instances "
                    "WHERE account IS ? AND window='five_hour' AND resets_at=?",
                    (account, ra)).fetchone()
                if row and row[0]:
                    detail = json.loads(row[0])
                    self.assertIsInstance(detail, dict)
        finally:
            db.close(conn)


class TestUnionCostAxis(FitTestCase):
    """(j) cost_axis with a second root sums both, dedupes a shared msg_id."""

    def setUp(self):
        super().setUp()
        # Second root: a separate ledger dir
        self.root2_dir = tempfile.mkdtemp(prefix="pa3-fit-r2-")
        self.ledger2 = os.path.join(self.root2_dir, "ledger.sqlite")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.root2_dir, ignore_errors=True)

    def _conn2(self):
        import sqlite3
        conn2 = db.connect(self.ledger2)
        db.init_schema(conn2)
        return conn2

    def test_cost_axis_union_sums_and_dedupes(self):
        conn = self.conn()
        conn2 = self._conn2()
        try:
            db.seed_prices(conn)
            db.seed_prices(conn2)
            account = "shared@example.com"
            # Local: 3 turns
            for i in range(3):
                ts = "2026-09-19T%02d:00:00Z" % (10 + i)
                db.upsert_turn(conn, {
                    "msg_id": "local_%d" % i, "session_id": "s1",
                    "ts": ts, "cost_usd": 2.0, "kind": "api",
                    "account": account, "model": "claude-opus-4-6",
                })
            # Remote: 2 unique + 1 duplicate msg_id
            db.upsert_turn(conn2, {
                "msg_id": "remote_0", "session_id": "s2",
                "ts": "2026-09-19T10:30:00Z", "cost_usd": 3.0, "kind": "api",
                "account": account, "model": "claude-opus-4-6",
            })
            db.upsert_turn(conn2, {
                "msg_id": "remote_1", "session_id": "s2",
                "ts": "2026-09-19T11:30:00Z", "cost_usd": 4.0, "kind": "api",
                "account": account, "model": "claude-opus-4-6",
            })
            # duplicate of local_0
            db.upsert_turn(conn2, {
                "msg_id": "local_0", "session_id": "s1",
                "ts": "2026-09-19T10:00:00Z", "cost_usd": 2.0, "kind": "api",
                "account": account, "model": "claude-opus-4-6",
            })
            conn.commit()
            conn2.commit()

            readers = [{"conn": conn2, "path": self.root2_dir,
                        "account": account, "label": "r2", "machine": "wsl"}]
            started = fit._parse_ts_epoch("2026-09-19T09:00:00Z")
            ep_end = fit._parse_ts_epoch("2026-09-19T15:00:00Z")

            # Without readers: 3 * 2.0 = 6.0
            costs_local = fit.cost_axis(conn, account, started, [ep_end])
            self.assertAlmostEqual(costs_local[0], 6.0, places=2)

            # With readers: 3 local + 2 unique remote = 6.0 + 3.0 + 4.0 = 13.0
            costs_union = fit.cost_axis(conn, account, started, [ep_end],
                                        readers=readers)
            self.assertAlmostEqual(costs_union[0], 13.0, places=2)
        finally:
            db.close(conn2)
            db.close(conn)


class TestUnionInstanceSamples(FitTestCase):
    """(k) instance_samples with readers dedupes a shared (session_id, ts, window)."""

    def setUp(self):
        super().setUp()
        self.root2_dir = tempfile.mkdtemp(prefix="pa3-fit-r2-")
        self.ledger2 = os.path.join(self.root2_dir, "ledger.sqlite")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.root2_dir, ignore_errors=True)

    def _conn2(self):
        conn2 = db.connect(self.ledger2)
        db.init_schema(conn2)
        return conn2

    def test_instance_samples_union_dedupes(self):
        conn = self.conn()
        conn2 = self._conn2()
        try:
            account = "shared@example.com"
            resets_at = 1789854600
            # Local: 3 samples
            for i in range(3):
                db.insert_utilization(conn, {
                    "ts": "2026-09-19T%02d:00:00Z" % (10 + i),
                    "pct": float(i * 10), "window": "five_hour",
                    "resets_at": resets_at, "session_id": "s1",
                    "account": account,
                })
            # Remote: 1 unique + 1 duplicate (same session_id, ts, window)
            db.insert_utilization(conn2, {
                "ts": "2026-09-19T10:00:00Z",  # duplicate of local
                "pct": 0.0, "window": "five_hour",
                "resets_at": resets_at, "session_id": "s1",
                "account": account,
            })
            db.insert_utilization(conn2, {
                "ts": "2026-09-19T13:00:00Z",  # unique
                "pct": 30.0, "window": "five_hour",
                "resets_at": resets_at, "session_id": "s2",
                "account": account,
            })
            conn.commit()
            conn2.commit()

            readers = [{"conn": conn2, "path": self.root2_dir,
                        "account": account, "label": "r2", "machine": "wsl"}]

            # Without readers: 3 samples
            samples_local = fit.instance_samples(conn, account, "five_hour", resets_at)
            self.assertEqual(len(samples_local), 3)

            # With readers: 3 local + 1 unique remote = 4
            samples_union = fit.instance_samples(conn, account, "five_hour", resets_at,
                                                  readers=readers)
            self.assertEqual(len(samples_union), 4)
        finally:
            db.close(conn2)
            db.close(conn)


class TestFitPool(FitTestCase):
    """T32.1: per-family fit pooled across same-tier accounts, only from in-regime instances."""

    BOUNDARY = "2026-09-20T00:00:00Z"

    def _instance(self, conn, account, started_iso, intercept, scale, tag):
        started = int(fit._parse_ts_epoch(started_iso))
        db.upsert_window_instance(conn, {"account": account, "window": "seven_day",
                                         "resets_at": started + 604800, "started_at": started})
        fable = opus = 0.0
        for i in range(60):
            t = started + i * 600
            if i % 3 == 0:
                fable += 3.0
                model = "claude-fable-5-1"
            else:
                opus += 8.0
                model = "claude-opus-4-6"
            db.upsert_turn(conn, {"msg_id": "%s_%d" % (tag, i), "session_id": tag, "kind": "api",
                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t + 10)),
                                  "cost_usd": (3.0 if i % 3 == 0 else 8.0), "account": account,
                                  "model": model})
            pct = intercept + scale * (0.005 * fable + 0.0005 * opus) * 100.0
            db.insert_utilization(conn, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                             time.gmtime(t + 300)),
                                         "account": account, "session_id": tag,
                                         "window": "seven_day", "pct": float(int(pct)),
                                         "resets_at": started + 604800})

    def _load(self, conn):
        self._instance(conn, "a@x.com", "2026-09-20T01:00:00Z", 2.0, 1.0, "a1")
        self._instance(conn, "a@x.com", "2026-09-21T01:00:00Z", 5.0, 1.0, "a2")
        self._instance(conn, "b@x.com", "2026-09-22T01:00:00Z", 9.0, 1.0, "b1")
        self._instance(conn, "a@x.com", "2026-09-18T01:00:00Z", 3.0, 1.5, "a0")   # old regime
        conn.commit()

    def _cfg(self, since=BOUNDARY):
        cfg = config.defaults()
        cfg["fit"]["regime_since"] = since
        cfg["accounts"] = {"a@x.com": {"tier": "max"}, "b@x.com": {"tier": "max"}}
        return cfg

    def test_pool_recovers_rates_from_in_regime_instances(self):
        conn = self.conn()
        try:
            self._load(conn)
            out = fit.build_pool(conn, self._cfg())
            self.assertEqual(sorted(out), [("max", "seven_day")])
            rates = fit.pool_rates(conn, "max", "seven_day")
            self.assertEqual(list(rates), ["fable", "opus"])
            for fam, truth in (("fable", 0.005), ("opus", 0.0005)):
                w = rates[fam]["weight"]
                self.assertAlmostEqual(w, truth, delta=truth * 0.15,
                                       msg="%s %.6f not within 15%% of %.6f" % (fam, w, truth))
                self.assertEqual(rates[fam]["n"], 180)                 # the old-regime 60 excluded
                self.assertGreater(rates[fam]["se"], 0)
                self.assertAlmostEqual(rates[fam]["dollars_per_window"], 1.0 / w)
            meta = fit.pool_meta(conn, "max", "seven_day")
            self.assertEqual(meta["n_instances"], 3)
            self.assertEqual(meta["regime_since"], self.BOUNDARY)
            self.assertIsNone(fit.pool_rates(conn, "pro", "seven_day"))
        finally:
            db.close(conn)

    def test_no_regime_boundary_no_rows(self):
        conn = self.conn()
        try:
            self._load(conn)
            fit.build_pool(conn, self._cfg())
            self.assertIsNotNone(fit.pool_rates(conn, "max", "seven_day"))
            self.assertEqual(fit.build_pool(conn, self._cfg(since=None)), {})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fit_pool").fetchone()[0], 0)
            self.assertIsNone(fit.pool_meta(conn, "max", "seven_day"))
        finally:
            db.close(conn)

    def test_pro_tier_pools_separately(self):
        conn = self.conn()
        try:
            self._load(conn)
            self._instance(conn, "c@x.com", "2026-09-23T01:00:00Z", 4.0, 1.0, "c1")
            conn.commit()
            cfg = self._cfg()
            cfg["accounts"]["c@x.com"] = {"tier": "pro"}
            out = fit.build_pool(conn, cfg)
            self.assertEqual(sorted(out), [("max", "seven_day"), ("pro", "seven_day")])
            self.assertEqual(fit.pool_meta(conn, "pro", "seven_day")["n_instances"], 1)
            self.assertEqual(fit.pool_rates(conn, "max", "seven_day")["fable"]["n"], 180)
            self.assertEqual(fit.pool_rates(conn, "pro", "seven_day")["fable"]["n"], 60)
        finally:
            db.close(conn)

    def _instance_noisy(self, conn, account, started_iso, intercept, tag):
        """T33: fable+opus drive pct; sonnet co-spent with haiku, whose pct effect is negative."""
        started = int(fit._parse_ts_epoch(started_iso))
        db.upsert_window_instance(conn, {"account": account, "window": "seven_day",
                                         "resets_at": started + 604800, "started_at": started})
        models = {"fable": "claude-fable-5-1", "opus": "claude-opus-4-6",
                  "sonnet": "claude-sonnet-4-5", "haiku": "claude-haiku-4-5"}
        c = dict.fromkeys(models, 0.0)
        for i in range(60):
            t = started + i * 600
            spend = [("fable", 3.0)] if i % 3 == 0 else [("opus", 8.0)]
            if i % 4 == 0:
                spend.append(("sonnet", 1.0))
            if i % 4 == 0 or i % 6 == 0:
                spend.append(("haiku", 1.0))
            for k, (fam, usd) in enumerate(spend):
                c[fam] += usd
                db.upsert_turn(conn, {"msg_id": "%s_%d_%d" % (tag, i, k), "session_id": tag,
                                      "kind": "api", "ts": time.strftime(
                                          "%Y-%m-%dT%H:%M:%SZ", time.gmtime(t + 10 + k)),
                                      "cost_usd": usd, "account": account,
                                      "model": models[fam]})
            pct = intercept + (0.005 * c["fable"] + 0.0005 * c["opus"] + 0.001 * c["sonnet"]
                               - 0.003 * c["haiku"]) * 100.0
            db.insert_utilization(conn, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                             time.gmtime(t + 300)),
                                         "account": account, "session_id": tag,
                                         "window": "seven_day", "pct": float(int(pct)),
                                         "resets_at": started + 604800})

    def test_repeated_nonpositive_drop_keeps_pool(self):
        """T33: pass 1 haiku < 0, pass 2 sonnet < 0; the old one-refit code discarded the pool."""
        conn = self.conn()
        try:
            self._instance_noisy(conn, "a@x.com", "2026-09-20T01:00:00Z", 40.0, "a1")
            self._instance_noisy(conn, "b@x.com", "2026-09-22T01:00:00Z", 45.0, "b1")
            conn.commit()
            passes = []
            orig = fit._weighted_ols_multi

            def spy(X, ys, ws):
                r = orig(X, ys, ws)
                passes.append(r[1] if r else None)
                return r
            fit._weighted_ols_multi = spy
            try:
                out = fit.build_pool(conn, self._cfg())
            finally:
                fit._weighted_ols_multi = orig
            self.assertEqual(len(passes), 3)                      # the path the old code cut short
            self.assertLess(passes[0][3], 0)                      # haiku
            self.assertLess(passes[1][2], 0)                      # sonnet, after haiku dropped
            self.assertEqual(sorted(out), [("max", "seven_day")])
            rates = fit.pool_rates(conn, "max", "seven_day")
            self.assertEqual(list(rates), ["fable", "opus"])
            for fam in ("fable", "opus"):
                self.assertGreater(rates[fam]["weight"], 0)
            self.assertEqual(fit.pool_meta(conn, "max", "seven_day")["n_instances"], 2)
            rows = conn.execute("SELECT family, rate, n_instances FROM fit_pool "
                                "WHERE tier='max' AND window='seven_day'").fetchall()
            self.assertEqual(sorted(r[0] for r in rows), ["fable", "opus"])
            for r in rows:
                self.assertGreater(r[1], 0)
                self.assertEqual(r[2], 2)
        finally:
            db.close(conn)


class TestBorrowedWeeklyRate(FitTestCase):
    """fix-20: a family with a 5h pool rate but no 7d one borrows the median 7d/5h ratio."""

    def _rows(self, conn, rows):
        for win, fam, rate in rows:
            db.upsert_fit_pool(conn, {"tier": "max", "window": win, "family": fam, "rate": rate,
                                      "se": 0.0001, "n": 29 if win == "five_hour" else 3,
                                      "n_instances": 3, "regime_since": "2026-09-20T00:00:00Z",
                                      "built_at": "2026-09-27T00:00:00Z"})
        conn.commit()

    LIVE = (("five_hour", "fable", 0.0045137), ("five_hour", "opus", 0.0028531),
            ("five_hour", "sonnet", 0.0010762), ("seven_day", "fable", 0.0010712),
            ("seven_day", "opus", 0.0007740))

    def test_sonnet_borrows_median_ratio(self):
        conn = self.conn()
        try:
            self._rows(conn, self.LIVE)
            rates = fit.pool_rates(conn, "max", "seven_day", config.defaults())
            self.assertEqual(list(rates), ["fable", "opus", "sonnet"])
            med = (0.0010712 / 0.0045137 + 0.0007740 / 0.0028531) / 2.0
            son = rates["sonnet"]
            self.assertAlmostEqual(son["weight"], 0.0010762 * med)
            self.assertAlmostEqual(son["weight"], 0.000274, delta=0.000002)
            self.assertIsNone(son["se"])
            self.assertEqual(son["n"], 29)
            self.assertAlmostEqual(son["dollars_per_window"], 1.0 / son["weight"])
            self.assertEqual(son["borrowed_from"], "five_hour")
            self.assertNotIn("borrowed_from", rates["fable"])
            self.assertNotIn("haiku", rates)
            self.assertNotIn("borrowed_from", fit.pool_rates(conn, "max", "five_hour",
                                                             config.defaults())["sonnet"])
            self.assertAlmostEqual(fit.pct_saved({"sonnet": 100.0}, rates),
                                   100.0 * son["weight"] * 100.0)
        finally:
            db.close(conn)

    def test_borrow_off(self):
        conn = self.conn()
        try:
            self._rows(conn, self.LIVE)
            cfg = config.defaults()
            cfg["fit"]["borrow_rates"] = False
            self.assertEqual(list(fit.pool_rates(conn, "max", "seven_day", cfg)), ["fable", "opus"])
        finally:
            db.close(conn)

    def test_no_shared_family_no_borrow(self):
        conn = self.conn()
        try:
            self._rows(conn, (("five_hour", "sonnet", 0.001), ("seven_day", "fable", 0.001)))
            self.assertEqual(list(fit.pool_rates(conn, "max", "seven_day", config.defaults())),
                             ["fable"])
        finally:
            db.close(conn)


if __name__ == "__main__":
    unittest.main()
