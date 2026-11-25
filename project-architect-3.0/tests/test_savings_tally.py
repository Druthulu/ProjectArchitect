"""3.33.1 (GitHub #19): the savings tally gives window_saved_measured's numbers without walking every session per call.

Every check compares the tally with ``savings._window_saved_reference`` (the full walk) on generated ledgers: in
memory before any sync, from the store after one, inside a pass, after more writes, on a read-only copy.
"""

import os
import random
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import db       # noqa: E402
from pa import savings  # noqa: E402

import test_savings      # noqa: E402
import test_savings_v4   # noqa: E402

ACCTS = ("a@x.io", "b@x.io", None)
MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-haiku-4-5", "claude-sonnet-5-5", None, "mystery-1")
PROJECTS = ("Z:/work/one", "Z:/work/two", None)


def _ts(minute, frac=None):
    """Minutes after 2026-03-01T00:00 as the ledger writes it (``.fffZ``), or second-precision when ``frac`` is
    None and the minute is even (both shapes occur; ``'.' < 'Z'`` matters at window bounds)."""
    h, m = divmod(minute, 60)
    d, h = divmod(h, 24)
    base = "2026-03-%02dT%02d:%02d:%02d" % (1 + d, h, m, (minute * 7) % 60)
    if frac is None:
        return base + ("Z" if minute % 2 == 0 else ".%03dZ" % ((minute * 37) % 1000))
    return base + frac


class Ledger(object):
    """A generated ledger: sessions with main and subagent runs over several accounts, retrievals on all three
    session mappings, tool calls of every kind, equal timestamps, NULL models and accounts, negative costs."""

    def __init__(self, conn, seed):
        self.conn, self.rng, self.n = conn, random.Random(seed), 0

    def _id(self, prefix):
        self.n += 1
        return "%s%d" % (prefix, self.n)

    def session(self, start, turns=30):
        rng, c = self.rng, self.conn
        sid = self._id("s")
        acct = rng.choice(ACCTS[:2])
        db.upsert_session(c, {"session_id": sid, "project": rng.choice(PROJECTS), "cwd": "Z:/work/cwd",
                              "account": acct})
        db.upsert_agent_run(c, {"run_id": sid, "session_id": sid, "kind": "partner",
                                "model_seen": rng.choice(MODELS[:4])})
        runs = [sid]
        for _ in range(rng.randint(0, 3)):
            rid = self._id("r")
            db.upsert_agent_run(c, {"run_id": rid, "session_id": sid, "parent_run_id": sid, "kind": "coder",
                                    "model_seen": rng.choice(MODELS), "seed_ctx": rng.choice((0, 0, 8000, 40000))})
            runs.append(rid)
        minute = start
        for i in range(turns):
            minute += rng.choice((0, 1, 2, 5, 90))           # equal minutes, short gaps, cold gaps
            if rng.random() < 0.15:
                acct = rng.choice(ACCTS)                      # account switches, NULL accounts
            db.upsert_turn(c, {"msg_id": self._id("m"), "run_id": rng.choice(runs), "session_id": sid,
                               "account": acct, "ts": _ts(minute), "model": rng.choice(MODELS),
                               "ctx": rng.choice((0, 50000, 300000, 700000, 950000)),
                               "cost_usd": rng.choice((0.5, 1.25, 3.0, -0.02)), "kind": "api"})
            if rng.random() < 0.3:
                self.retrieval(sid, runs, minute)
            if rng.random() < 0.35:
                self.tool_call(sid, runs, minute)
        return sid

    def retrieval(self, sid, runs, minute):
        rng = self.rng
        how = rng.random()
        if how < 0.5:
            rid = rng.choice(runs[1:] or runs)                # through its own run
            parent = sid
        elif how < 0.8:
            rid, parent = self._id("x"), rng.choice(runs)    # through its parent's run
        else:
            rid, parent = self._id("x"), sid                 # the parent id itself
        db.upsert_retrieval(self.conn, {"run_id": rid, "parent_run_id": parent, "returned_at": _ts(minute),
                                        "kept_out_tokens": rng.choice((0, 20000, 90000, 400000)),
                                        "void": 1 if rng.random() < 0.1 else 0})

    def tool_call(self, sid, runs, minute):
        rng = self.rng
        kind = rng.choice(("script", "script", "read", "outline", "outline-read", "runsh"))
        db.upsert_tool_call(self.conn, {"id": self._id("c"), "session_id": sid, "run_id": rng.choice(runs),
                                        "ts": _ts(minute), "tool": "Bash", "kind": kind,
                                        "path": rng.choice(("a.py", "b.md", "c.txt")),
                                        "file_tokens": rng.choice((0, 3000, 30000)),
                                        "emission_tokens": rng.choice((0, 150, 900)),
                                        "result_tokens": rng.choice((0, 800, 5000)),
                                        "kept_tokens": rng.choice((0, 2000)),
                                        "model": rng.choice(MODELS)})


def _cases(sids):
    """The argument matrix window_saved_measured is called with (summary, report)."""
    bounds = ((None, None), (_ts(300), None), (_ts(300, ".500Z"), None), (_ts(301), _ts(2000)),
              ("2026-03-01T05:00:00Z", "2026-03-02T00:00:00Z"))
    for t0, t1 in bounds:
        for account in ("a@x.io", "b@x.io", None, "nobody@x.io"):
            yield account, t0, t1, {}
            yield account, t0, t1, {"by_family": True}
            yield account, t0, t1, {"detail": True}
            yield account, t0, t1, {"project": os.path.normcase(os.path.normpath("Z:/work/one"))}
            yield account, t0, t1, {"project": os.path.normcase(os.path.normpath("Z:/work/cwd")), "by_family": True}
            for sid in sids[:3]:
                yield account, t0, t1, {"session": sid, "detail": True}
                yield account, t0, t1, {"session": sid, "by_family": True}


class TallyCase(unittest.TestCase):

    SEED = 19

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-tally-")
        self.path = os.path.join(self.dir, "ledger.sqlite")
        self.conn = db.connect(self.path)
        db.init_schema(self.conn)
        self.gen = Ledger(self.conn, self.SEED)
        self.sids = [self.gen.session(start) for start in (0, 200, 400, 1500)]

    def tearDown(self):
        db.close(self.conn)
        shutil.rmtree(self.dir, ignore_errors=True)

    def assertSame(self, got, want, msg):
        if isinstance(want, dict):
            self.assertIsInstance(got, dict, msg)
            self.assertEqual(sorted(got), sorted(want), msg)
            for k in want:
                self.assertAlmostEqual(got[k], want[k], places=9, msg="%s [%s]" % (msg, k))
        else:
            self.assertAlmostEqual(got, want, places=9, msg=msg)

    def assertMatchesReference(self, conn=None, label=""):
        conn = conn or self.conn
        n = 0
        for account, t0, t1, kw in _cases(self.sids):
            want = savings._window_saved_reference(conn, account, t0, t1, **kw)
            got = savings.window_saved_measured(conn, account, t0, t1, **kw)
            self.assertSame(got, want, "%s %r %r %r %r" % (label, account, t0, t1, kw))
            n += 1
        return n

    def dirty(self):
        return self.conn.execute("SELECT COUNT(*) FROM tally_dirty").fetchone()[0]


class TallyMatchesReferenceTest(TallyCase):

    def test_the_ledger_is_not_trivial(self):
        total = savings._window_saved_reference(self.conn, None, None, None, detail=True)
        self.assertGreater(total["gross"], 1.0)
        self.assertNotEqual(total["seeds"], 0.0)
        self.assertGreater(total["emission"], 0.0)
        fams = savings._window_saved_reference(self.conn, "a@x.io", None, None, by_family=True)
        self.assertGreater(len(fams), 1)

    def test_in_memory_before_any_sync(self):
        self.assertIsNone(db.get_meta(self.conn, "tally_stamp"))
        self.assertEqual(self.assertMatchesReference(label="memory"), 220)

    def test_from_the_store_after_a_sync(self):
        self.assertGreaterEqual(savings.tally_sync(self.conn), len(self.sids))
        self.assertEqual(self.dirty(), 0)
        self.assertEqual(db.get_meta(self.conn, "tally_stamp"), savings.tally_stamp())
        self.assertMatchesReference(label="stored")
        with savings.tally_pass(self.conn):
            self.assertMatchesReference(label="pass")

    def test_a_pass_makes_no_reference_walk(self):
        with mock.patch.object(savings, "_window_saved_reference", side_effect=AssertionError("walked")):
            with savings.tally_pass(self.conn):
                for account, t0, t1, kw in _cases(self.sids):
                    savings.window_saved_measured(self.conn, account, t0, t1, **kw)

    def test_a_pass_walks_each_session_once(self):
        """The cost #19 was about: one walk per session per pass, never one per call."""
        savings.tally_sync(self.conn)
        self.gen.session(3000)                                # dirty: walked in memory by the view
        with mock.patch.object(savings, "_session_tally", wraps=savings._session_tally) as walks:
            with savings.tally_pass(self.conn, sync=False):
                for account, t0, t1, kw in _cases(self.sids):
                    savings.window_saved_measured(self.conn, account, t0, t1, **kw)
        self.assertEqual(walks.call_count, 1)

    def test_later_writes_mark_and_rewalk_only_their_sessions(self):
        savings.tally_sync(self.conn)
        rng = random.Random(7)
        late = self.gen.session(600)                           # a new session over the old ones' times
        s0 = self.sids[0]
        db.upsert_turn(self.conn, {"msg_id": "late1", "run_id": s0, "session_id": s0, "account": "b@x.io",
                                   "ts": _ts(5), "model": "claude-opus-5-5", "ctx": 120000, "cost_usd": 2.0,
                                   "kind": "api"})            # a subagent lock dated before turns already in
        self.gen.retrieval(self.sids[1], [self.sids[1]], 250)
        self.conn.execute("UPDATE retrievals SET void=1 WHERE rowid=(SELECT MIN(rowid) FROM retrievals)")
        self.conn.execute("UPDATE turns SET account='a@x.io' WHERE msg_id=(SELECT MIN(msg_id) FROM turns "
                          "WHERE session_id=?)", (self.sids[2],))
        self.gen.tool_call(self.sids[3], [self.sids[3]], 1600 + rng.randint(0, 50))
        dirty = set(r[0] for r in self.conn.execute("SELECT session_id FROM tally_dirty"))
        self.assertTrue({late, s0, self.sids[2], self.sids[3]} <= dirty)
        self.sids.append(late)
        self.assertMatchesReference(label="dirty, before the sync")
        self.assertEqual(savings.tally_sync(self.conn), len(dirty))
        self.assertEqual(self.dirty(), 0)
        self.assertMatchesReference(label="after the sync")

    def test_a_read_only_copy_reads_the_store_or_walks(self):
        savings.tally_sync(self.conn)
        ro = db.connect(self.path, readonly=True)
        try:
            with mock.patch.object(savings, "_session_tally", wraps=savings._session_tally) as walks:
                self.assertMatchesReference(conn=ro, label="read-only, our stamp")
            reference_walks = walks.call_count
            self.conn.execute("UPDATE meta SET value='another' WHERE key='tally_stamp'")
            with savings.tally_pass(None):
                self.assertMatchesReference(conn=ro, label="read-only, another stamp")
        finally:
            db.close(ro)
        self.assertEqual(reference_walks, 0)

    def test_a_ledger_without_tally_tables_is_walked(self):
        conn = db.connect(os.path.join(self.dir, "old.sqlite"))
        try:
            conn.executescript(db.SCHEMA_SQL.replace(db.TALLY_SQL, ""))
            Ledger(conn, 5).session(0)
            for account, t0, t1, kw in list(_cases(["s1"]))[:40]:
                self.assertSame(savings.window_saved_measured(conn, account, t0, t1, **kw),
                                savings._window_saved_reference(conn, account, t0, t1, **kw), "old ledger")
        finally:
            db.close(conn)


class TallySyncTest(TallyCase):

    SEED = 3

    def test_an_older_walk_never_overwrites_a_newer_one(self):
        savings.tally_sync(self.conn)
        sid = self.sids[0]
        sk, walked = self.conn.execute("SELECT sk, walked_seq FROM tally_sessions WHERE session_id=?",
                                       (sid,)).fetchone()
        self.conn.execute("UPDATE tally_credits SET usd=usd+1 WHERE sk=?", (sk,))   # marks what is stored
        self.conn.execute("UPDATE tally_sessions SET walked_seq=? WHERE sk=?", (walked + 10 ** 6, sk))
        self.conn.execute("INSERT INTO tally_dirty(session_id) VALUES (?)", (sid,))  # a walk covering less
        savings._tally_write_session(self.conn, sid, savings.tally_stamp(), 3600, 1000000)
        self.assertEqual(self.dirty(), 0)                                 # its entry is cleared ...
        self.assertTrue(self.conn.execute("SELECT 1 FROM tally_credits WHERE sk=? AND usd>=1 LIMIT 1",
                                          (sk,)).fetchone())               # ... and the newer credits stay

    def test_a_write_during_the_walk_stays_in_the_log(self):
        savings.tally_sync(self.conn)
        sid = self.sids[1]
        real = savings._session_tally

        def walk_then_write(conn, s, vtl, win):
            got = real(conn, s, vtl, win)
            other = db.connect(self.path)              # a hook writing while the walk runs
            try:
                db.upsert_turn(other, {"msg_id": "during", "run_id": s, "session_id": s, "account": "a@x.io",
                                       "ts": _ts(999), "model": "claude-opus-5-5", "ctx": 1000, "cost_usd": 1.0,
                                       "kind": "api"})
            finally:
                db.close(other)
            return got

        self.conn.execute("INSERT INTO tally_dirty(session_id) VALUES (?)", (sid,))
        with mock.patch.object(savings, "_session_tally", side_effect=walk_then_write):
            savings.tally_sync(self.conn, spot_check=False)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT session_id FROM tally_dirty")], [sid])
        self.assertMatchesReference(label="the late write")
        savings.tally_sync(self.conn)
        self.assertEqual(self.dirty(), 0)
        self.assertMatchesReference(label="after the next sync")

    def test_a_new_stamp_rewalks_every_session(self):
        savings.tally_sync(self.conn)
        with mock.patch.object(savings, "TALLY_VERSION", "test"), mock.patch.dict(savings._STAMP_CACHE, clear=True):
            stamp = savings.tally_stamp()
            self.assertNotEqual(stamp, db.get_meta(self.conn, "tally_stamp"))
            self.assertMatchesReference(label="stale store")
            self.assertEqual(savings.tally_sync(self.conn), len(self.sids))
            self.assertEqual(set(r[0] for r in self.conn.execute("SELECT DISTINCT stamp FROM tally_sessions")),
                             {stamp})
            self.assertMatchesReference(label="rewalked")

    def test_the_spot_check_finds_and_heals_a_wrong_store(self):
        from pa import log

        savings.tally_sync(self.conn)
        self.conn.execute("UPDATE tally_credits SET usd=usd+0.5")
        with mock.patch.object(log, "log") as logged:
            bad = savings._tally_spot_check(self.conn, savings.tally_stamp(), 3600, 1000000,
                                            now=1772496000)       # 2026-03-03: the generated week
        self.assertTrue(bad)
        self.assertIn("savings_tally_mismatch", [c.args[0] for c in logged.call_args_list])
        self.assertEqual(self.dirty(), len(bad))
        marks = ",".join("?" * len(bad))                          # the sessions it did not pick, put back
        self.conn.execute("UPDATE tally_credits SET usd=usd-0.5 WHERE sk NOT IN (SELECT sk FROM tally_sessions "
                          "WHERE session_id IN (%s))" % marks, bad)
        savings.tally_sync(self.conn, spot_check=False)
        self.assertEqual(self.dirty(), 0)
        self.assertMatchesReference(label="healed")
        self.assertEqual(savings._tally_spot_check(self.conn, savings.tally_stamp(), 3600, 1000000,
                                                   now=1772496000), [])

    def test_a_negative_credit_sends_its_session_to_the_reference(self):
        sid = self.sids[0]
        for rid, kept in (("pos", 100000), ("neg", -50000)):     # returned together: first mass 50k, one credit < 0
            db.upsert_retrieval(self.conn, {"run_id": rid, "parent_run_id": sid, "returned_at": "2026-02-28T00:00:00Z",
                                            "kept_out_tokens": kept})
        db.upsert_turn(self.conn, {"msg_id": "first", "run_id": sid, "session_id": sid, "account": "a@x.io",
                                   "ts": "2026-02-28T00:01:00Z", "model": "claude-opus-5-5", "ctx": 1000,
                                   "cost_usd": 1.0, "kind": "api"})
        savings.tally_sync(self.conn)
        self.assertEqual(self.conn.execute("SELECT ref_only FROM tally_sessions WHERE session_id=?",
                                           (sid,)).fetchone()[0], 1)
        with mock.patch.object(savings, "_window_saved_reference",
                               wraps=savings._window_saved_reference) as ref:
            savings.window_saved_measured(self.conn, None, None, None)
            savings.window_saved_measured(self.conn, None, None, None, session=self.sids[1])
        self.assertEqual(ref.call_count, 1)

    def test_the_switch_and_other_windows_take_the_reference(self):
        savings.tally_sync(self.conn)
        with mock.patch.object(savings, "_window_saved_reference", return_value=7.0) as ref:
            self.assertEqual(savings.window_saved_measured(self.conn, None, None, None, window=200000), 7.0)
            with mock.patch.object(savings, "_tally_enabled", return_value=False):
                self.assertEqual(savings.window_saved_measured(self.conn, None, None, None), 7.0)
            self.assertNotEqual(savings.window_saved_measured(self.conn, None, None, None, window=1000000), 7.0)
        self.assertEqual(ref.call_count, 2)


# The savings tests' own expectations, rerun on the reference walk (they run on the tally in their own modules).

def _on_reference(cls):
    class OnReference(cls):
        def setUp(self):
            patcher = mock.patch.object(savings, "_tally_enabled", return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
            super(OnReference, self).setUp()
    OnReference.__name__ = OnReference.__qualname__ = cls.__name__ + "OnReference"
    return OnReference


MeasuredSavedOnReference = _on_reference(test_savings.MeasuredSavedTest)
ByFamilyOnReference = _on_reference(test_savings.ByFamilyTest)
VanillaNetV3OnReference = _on_reference(test_savings.VanillaNetV3Test)
ToolCallCreditOnReference = _on_reference(test_savings_v4.ToolCallCreditTest)
NullModelPricingOnReference = _on_reference(test_savings_v4.NullModelPricingTest)
AccountSwitchOnReference = _on_reference(test_savings_v4.AccountSwitchTest)


if __name__ == "__main__":
    unittest.main()
