"""pa.ledger_cli bench: import (idempotent), report (paired flips, clean-stop), export round-trip (T3).

Fixture results JSONs follow docs/pa3-build/design/bench.md "Results format": two runs of one arm key with
overlapping but unequal task ids (tiers 1 and 2), and a stopped arm in the second run.
3.9.7 T5: a format 2 run with repeat 2 (noise, rubric, deltas, per-attempt rows) and a later repeat-1 run.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import db  # noqa: E402
from pa import ledger_cli  # noqa: E402
from pa import paths  # noqa: E402

CODER = {"role": "coder", "agent": "coder-opus55", "model": "claude-opus-5-5", "effort": "medium",
         "body_hash": "abcdef0123456789", "version": 1}
EXPERT = {"role": "expert", "agent": "expert-opus55", "model": "claude-opus-5-5", "effort": "medium",
          "body_hash": "99887766554433", "version": 2}


def _task(tid, tier, passed, cost, sid=None):
    t = {"id": tid, "tier": tier, "passed": passed, "cost_usd": cost, "secs": 12.5, "turns": 4}
    if sid:
        t.update(session_id=sid, turns_source="transcript")
    return t


def _doc(run_id, date, arms):
    return {"format": 1, "run_id": run_id, "date": date, "suite": "light", "harness_version": "3.0.1",
            "price_version": "2026-09", "fixture_hash": "fx01", "claude_code": "2.1.282", "arms": arms}


RUN_A = _doc("run-a", "2026-09-01", [dict(CODER, stopped=False, tasks=[
    _task("a", 1, True, 0.25), _task("b", 1, False, 0.25),
    _task("c", 2, True, 0.25), _task("d", 2, False, 0.25)])])

RUN_B = _doc("run-b", "2026-09-08T10:00:00Z", [
    dict(CODER, stopped=False, w5h_before=10.0, w5h_after=14.5, tasks=[
        _task("a", 1, False, 0.10, "s1"), _task("b", 1, True, 0.20, "s2"),
        _task("c", 2, True, 0.30, "s3"), _task("e", 2, True, 0.40, "s4")]),
    dict(EXPERT, stopped=True, tasks=[
        _task("x", 1, True, 0.50), _task("y", 1, None, None), _task("z", 2, None, None)])])


def _att(tid, tier, attempt, passed, cost, turns, **extra):
    t = {"id": tid, "tier": tier, "attempt": attempt, "passed": passed, "cost_usd": cost, "secs": 3.0,
         "turns": turns}
    t.update(extra)
    return t


# 3.9.7 T5: format 2, repeat 2; b and c disagree across attempts, f's second attempt never ran
RUN_C = dict(_doc("run-c", "2026-09-15", [dict(CODER, stopped=False, tasks=[
    _att("a", 1, 1, True, 0.10, 2), _att("a", 1, 2, True, 0.10, 4),
    _att("b", 1, 1, True, 0.20, 4), _att("b", 1, 2, False, 0.20, 4, failure_kind="agent"),
    _att("c", 2, 1, True, 0.30, 6, rubric={"score": 3, "max": 4, "model": "m"},
         consequence={"passed": True, "cost_usd": 0.05}),
    _att("c", 2, 2, False, 0.30, 6, rubric={"score": 1, "max": 4, "model": "m"},
         consequence={"passed": False, "cost_usd": 0.05}, failure_kind="grader"),
    _att("f", 2, 1, True, 0.40, 4), _att("f", 2, 2, None, None, None)])]), format=2, repeat=2,
             preset="max5")   # 3.10.6 T6: the tier the run rendered at, kept in detail

# a later repeat-1 run: its noise comes from run-c
RUN_D = _doc("run-d", "2026-09-20", [dict(CODER, stopped=False, tasks=[
    _task("a", 1, True, 0.10), _task("b", 1, True, 0.20)])])


def run_cli(*argv):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = ledger_cli.main(list(argv))
    return rc, buf_out.getvalue(), buf_err.getvalue()


class BenchLedgerTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-bench-")
        self._old = {k: os.environ.get(k) for k in ("PA_LEDGER_DIR", "PA_HOOKS_OFF")}
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        os.environ["PA_HOOKS_OFF"] = "1"
        self.files = {}
        for name, doc in (("a", RUN_A), ("b", RUN_B), ("c", RUN_C), ("d", RUN_D)):
            p = os.path.join(self.dir, "%s.json" % name)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh)
            self.files[name] = p

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def rows(self, run_id=None):
        conn = db.connect(paths.db_path())
        try:
            sql = "SELECT * FROM bench" + (" WHERE run_id=?" if run_id else "")
            got = conn.execute(sql + " ORDER BY run_id, arm, task_id", (run_id,) if run_id else ()).fetchall()
            return [tuple(r) for r in got]
        finally:
            db.close(conn)

    def import_both(self):
        for name in ("a", "b"):
            rc, _, err = run_cli("bench", "import", self.files[name])
            self.assertEqual(rc, 0, err)

    def test_empty_report(self):
        rc, out, _ = run_cli("bench", "report")
        self.assertEqual((rc, out.strip()), (0, "no bench runs"))

    def test_import_is_idempotent(self):
        self.import_both()
        first = self.rows()
        self.assertEqual(len(first), 11)
        self.import_both()
        self.assertEqual(self.rows(), first)
        conn = db.connect(paths.db_path())
        try:
            r = conn.execute("SELECT * FROM bench WHERE run_id='run-b' AND task_id='y'").fetchone()
            self.assertIsNone(r["passed"])                                  # not run stays NULL
            self.assertEqual((r["stopped"], r["source"]), (1, "local"))
            self.assertEqual(r["arm"], "expert|expert-opus55|claude-opus-5-5|medium|99887766554433")
            r = conn.execute("SELECT * FROM bench WHERE run_id='run-b' AND task_id='a'").fetchone()
            self.assertEqual((r["passed"], r["tier"], r["w5h_after"]), (0, 1, 14.5))
            self.assertEqual(json.loads(r["detail"]), {"version": 1, "claude_code": "2.1.282",
                                                       "session_id": "s1", "turns_source": "transcript"})
        finally:
            db.close(conn)

    def test_report_pairs_shared_ids_and_clean_stop(self):
        self.import_both()
        rc, out, _ = run_cli("bench", "report")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines, [
            "coder coder-opus55 claude-opus-5-5/medium abcdef01",
            "  2026-09-08 coder-opus55 claude-opus-5-5/medium pass 3/4 (t1 1/2 · t2 2/2) · noise unknown"
            " · rubric — · $1.00 (Δ−0.15) · turns 4.0 (Δ+0.0) vs previous: +1 −1 flips (paired 3)",
            "  2026-09-01 coder-opus55 claude-opus-5-5/medium pass 2/4 (t1 1/2 · t2 1/2) · noise unknown"
            " · rubric — · $1.00 (Δ —) · turns 4.0 (Δ —) vs previous: —",
            "expert expert-opus55 claude-opus-5-5/medium 99887766",
            "  2026-09-08 expert-opus55 claude-opus-5-5/medium clean-stop 1/3 $0.50",
        ])
        rc, out, _ = run_cli("bench", "report", "--arm", "coder-opus55", "--last", "1")
        self.assertEqual(out.splitlines()[1:], [lines[1]])                 # previous lies outside --last
        rc, out, _ = run_cli("bench", "report", "--role", "expert")
        self.assertEqual(out.splitlines(), lines[3:])

    def test_export_round_trip(self):
        self.import_both()
        before = self.rows("run-b")
        rc, out, err = run_cli("bench", "export", "--run", "run-b")
        self.assertEqual(rc, 0, err)
        want = json.loads(json.dumps(RUN_B))                                # format 2, repeat 1, attempt 1
        want.update(format=2, repeat=1)
        for arm in want["arms"]:
            for t in arm["tasks"]:
                t["attempt"] = 1
        self.assertEqual(json.loads(out), want)
        dest = os.path.join(self.dir, "out.json")
        self.assertEqual(run_cli("bench", "export", "--run", "run-b", "--out", dest)[0], 0)
        conn = db.connect(paths.db_path())
        try:
            conn.execute("DELETE FROM bench WHERE run_id='run-b'")
        finally:
            db.close(conn)
        self.assertEqual(self.rows("run-b"), [])
        self.assertEqual(run_cli("bench", "import", dest)[0], 0)
        self.assertEqual(self.rows("run-b"), before)
        self.assertEqual(run_cli("bench", "export", "--run", "nope")[0], 1)

    def import_all(self):
        for name in ("a", "b", "c", "d"):
            rc, _, err = run_cli("bench", "import", self.files[name])
            self.assertEqual(rc, 0, err)

    def test_format2_import_is_idempotent_per_attempt(self):
        self.import_all()
        first = self.rows()
        self.assertEqual((len(self.rows("run-a")), len(self.rows("run-c"))), (4, 8))  # 1 / 2 per task
        self.import_all()
        self.assertEqual(self.rows(), first)
        conn = db.connect(paths.db_path())
        try:
            got = [tuple(r) for r in conn.execute(
                "SELECT task_id, attempt, passed, rubric, failure_kind, consequence_cost FROM bench"
                " WHERE run_id='run-c' AND task_id IN ('b', 'c') ORDER BY task_id, attempt")]
            self.assertEqual(got, [("b", 1, 1, None, None, None), ("b", 2, 0, None, "agent", None),
                                   ("c", 1, 1, 0.75, None, 0.05), ("c", 2, 0, 0.25, "grader", 0.05)])
            presets = {json.loads(r[0] or "{}").get("preset")
                       for r in conn.execute("SELECT detail FROM bench WHERE run_id='run-c'")}
            self.assertEqual(presets, {"max5"})   # 3.10.6 T6: detail carries the run's preset
            self.assertEqual({r[0] for r in conn.execute("SELECT attempt FROM bench WHERE run_id='run-a'")}, {1})
        finally:
            db.close(conn)

    def test_report_noise_rubric_and_deltas(self):
        self.import_all()
        rc, out, _ = run_cli("bench", "report", "--arm", "coder-opus55", "--last", "3")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[1:], [
            "  2026-09-20 coder-opus55 claude-opus-5-5/medium pass 2/2 (t1 2/2) · noise ±2/3 (2026-09-15)"
            " · rubric — · $0.30 (Δ+0.00) · turns 4.0 (Δ+0.5) vs previous: +1 −0 flips (paired 2)",
            "  2026-09-15 coder-opus55 claude-opus-5-5/medium pass 2/4 (t1 1/2 · t2 1/2) · noise 2/3"
            " · rubric 0.50 · $1.70 (Δ+0.05) · turns 4.3 (Δ+0.3) vs previous: +1 −2 flips (paired 3)",
            "  2026-09-08 coder-opus55 claude-opus-5-5/medium pass 3/4 (t1 1/2 · t2 2/2) · noise unknown"
            " · rubric — · $1.00 (Δ−0.15) · turns 4.0 (Δ+0.0) vs previous: +1 −1 flips (paired 3)",
        ])

    def test_report_pairs_across_bodies(self):
        """3.9.7 T8.1: a key with no earlier run pairs with the newest earlier run of another body."""
        old = dict(EXPERT, body_hash="1111222233334444", stopped=False,
                   tasks=[_task("t%d" % i, 1, i != 8, 0.10) for i in range(1, 9)])
        new = dict(EXPERT, stopped=False, tasks=[_task("t%d" % i, 1, True, 0.10) for i in range(1, 9)])
        for name, doc in (("x", _doc("run-x", "2026-09-10", [old])), ("y", _doc("run-y", "2026-09-12", [new]))):
            p = os.path.join(self.dir, "%s.json" % name)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh)
            self.assertEqual(run_cli("bench", "import", p)[0], 0)
        rc, out, _ = run_cli("bench", "report", "--arm", "expert-opus55")
        runs = [ln for ln in out.splitlines() if ln.startswith("  ")]
        self.assertEqual(rc, 0)
        self.assertTrue(runs[0].endswith("vs previous body 11112222: +1 −0 flips (paired 8)"), runs)
        self.assertTrue(runs[1].endswith("vs previous: —"), runs)

    def test_export_round_trip_format2(self):
        self.import_all()
        before = self.rows("run-c")
        dest = os.path.join(self.dir, "c-out.json")
        rc, _, err = run_cli("bench", "export", "--run", "run-c", "--out", dest)
        self.assertEqual(rc, 0, err)
        with open(dest, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["format"], doc["repeat"], doc["preset"]), (2, 2, "max5"))
        tasks = doc["arms"][0]["tasks"]
        self.assertEqual([(t["id"], t["attempt"]) for t in tasks],
                         [(t["id"], t["attempt"]) for t in RUN_C["arms"][0]["tasks"]])
        self.assertEqual((tasks[3]["failure_kind"], tasks[5]["consequence"]), ("agent", {"cost_usd": 0.05}))
        conn = db.connect(paths.db_path())
        try:
            conn.execute("DELETE FROM bench WHERE run_id='run-c'")
        finally:
            db.close(conn)
        self.assertEqual(run_cli("bench", "import", dest)[0], 0)
        self.assertEqual(self.rows("run-c"), before)


if __name__ == "__main__":
    unittest.main()
