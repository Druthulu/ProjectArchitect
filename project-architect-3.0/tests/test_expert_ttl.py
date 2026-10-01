"""3.15 T5: tools/expert_ttl.py prints exactly 1h or 5m from a fixture ledger (temp PA_LEDGER_DIR)."""

import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "expert_ttl.py")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from pa import db  # noqa: E402
import expert_ttl  # noqa: E402

PROJECT = r"Z:\Fixture\Proj"
T0 = dt.datetime(2026, 9, 20, 1, 0, 0, tzinfo=dt.timezone.utc)


def iso(sec):
    return (T0 + dt.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ExpertTtlTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa_expert_ttl_")
        self.conn = sqlite3.connect(os.path.join(self.dir, "ledger.sqlite"))
        self.conn.executescript(db.SCHEMA_SQL)
        self.conn.execute("INSERT INTO sessions(session_id, project) VALUES('s1', ?)", (PROJECT,))
        self.conn.execute("INSERT INTO sessions(session_id, project) VALUES('s2', '/home/x/other')")
        self.n = 0

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_(self, gaps, pings=(), agent="expert-opus55", session="s1"):
        """One finished run: turns at cumulative ``gaps``; a warm_ping 5 s before each turn index in ``pings``."""
        self.n += 1
        rid, t, base = "run%d" % self.n, 0.0, self.n * 100000
        self.conn.execute("INSERT INTO agent_runs(run_id, session_id, agent_type, started, ended)"
                          " VALUES(?, ?, ?, ?, ?)", (rid, session, agent, iso(base), iso(base + 1)))
        for i, gap in enumerate(gaps):
            t += gap
            self.conn.execute("INSERT INTO turns(msg_id, run_id, session_id, ts, gap_s)"
                              " VALUES(?, ?, ?, ?, ?)", ("%s-%d" % (rid, i), rid, session,
                                                         iso(base + t), gap))
            if i in pings:
                self.conn.execute("INSERT INTO events(ts, session_id, run_id, kind, detail_json)"
                                  " VALUES(?, ?, ?, 'warm_ping', '{}')", (iso(base + t - 5), session, rid))

    def tool(self, *args):
        self.conn.commit()
        env = dict(os.environ, PA_LEDGER_DIR=self.dir)
        out = subprocess.run([sys.executable, TOOL, "--project", "/mnt/z/fixture/proj/"] + list(args),
                             capture_output=True, env=env, cwd=self.dir, timeout=60)
        self.assertEqual(out.returncode, 0)
        return out.stdout

    def test_no_history_1h(self):
        self.assertEqual(self.tool(), b"1h\n")

    def test_idle_51_min_1h(self):
        for _ in range(5):
            self.run_([5, 10, 3060, 10])
        self.assertEqual(self.tool(), b"1h\n")

    def test_idle_3_min_5m(self):
        for _ in range(5):
            self.run_([5, 10, 180, 10])
        for _ in range(5):                       # another project's long waits do not count
            self.run_([5, 3060], session="s2")
        self.assertEqual(self.tool(), b"5m\n")

    def test_coder_none_5m(self):
        for _ in range(5):
            self.run_([5, 10, 3060, 10])
        self.assertEqual(self.tool("--coder", "none"), b"5m\n")

    def test_pings_merge_one_wait_1h(self):
        # a 50-min wait split by 10 ping turns (each gap < 300 s) is one idle wait
        for _ in range(5):
            self.run_([5, 10] + [280] * 10 + [200, 10], pings=set(range(2, 12)))
        self.assertEqual(self.tool(), b"1h\n")
        self.assertEqual(self.tool("--coder", "opus55"), b"1h\n")

    def test_long_wait_clipped_5m(self):
        # 3.15 T5.1: one 440-min wait clips to 60; mean over 10 runs = 6 min < 15 -> 5m (unclipped 44 -> 1h)
        self.run_([5, 10, 440 * 60, 10])
        for _ in range(9):
            self.run_([5, 10, 60, 10])
        self.assertEqual(self.tool(), b"5m\n")

    def test_norm(self):
        self.assertEqual(expert_ttl.norm(r"Z:\Storage\git\X"), expert_ttl.norm("/mnt/z/storage/git/x/"))
        self.assertNotEqual(expert_ttl.norm("/home/a/B"), expert_ttl.norm("/home/a/b"))


if __name__ == "__main__":
    unittest.main()
