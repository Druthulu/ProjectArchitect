"""status.py agent-alive: alive / stale / unknown on a temp ledger (T4.c2)."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")


def _tool(*args, env_extra=None):
    cmd = [sys.executable, "-X", "utf8", os.path.join(TOOLS, "status.py")]
    cmd += [str(a) for a in args]
    env = dict(os.environ)
    env["PA_SKIP_PREFLIGHT"] = "1"
    env.update(env_extra or {})
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    p.out = (p.stdout or "") + (p.stderr or "")
    return p


class InboxConsumeTest(unittest.TestCase):
    """inbox-consume prints `none` for an absent or empty INBOX.md, the text otherwise."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-status-inbox-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.root = os.path.join(self.tmpdir, "proj")
        self.cur = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(self.cur)
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"project": "test"}, fh)

    def _env(self):
        return {"PA_PROJECT_ROOT": self.root}

    def test_none_when_absent(self):
        p = _tool("inbox-consume", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "none\n")

    def test_none_when_only_the_template_header(self):
        with open(os.path.join(self.cur, "INBOX.md"), "w") as fh:
            fh.write("# Inbox\n<!-- notes for later, consumed at session start -->\n"
                     "- <what to add>\n")
        p = _tool("inbox-consume", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "none\n")
        sys.path.insert(0, TOOLS)
        import status as ST                    # fix-2: the placeholder is not a pending bullet
        self.assertEqual(ST.inbox_pending("# Inbox\n- <what to add>\n- real one\n"), ["real one"])

    def test_text_when_present(self):
        with open(os.path.join(self.cur, "INBOX.md"), "w") as fh:
            fh.write("- check the seam class on biome 4\n")
        p = _tool("inbox-consume", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("check the seam class", p.stdout)
        self.assertNotEqual(p.stdout, "none\n")

    def test_routes_bullets_to_index(self):
        """Each bullet gets an I<n> row in discussions/INDEX.md and a route: line."""
        with open(os.path.join(self.root, "GENERATION_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Generation 3\n\n## Phases\n"
                     "- 3.9 old | milestone: m | status: done\n"
                     "- 3.10 repairs | milestone: m | status: open\n"
                     "- 3.11 wiki | milestone: m | status: open\n"
                     "- 3.12 publish | milestone: m | status: open\n")
        with open(os.path.join(self.cur, "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Phase 3.10 — repairs        (implements GENERATION_PLAN.md phase 3.10)\n")
        bullets = [("3.11 wiki: move the pages", "3.11"),
                   ("tidy the launcher -> 3.11", "3.11"),
                   ("later: revisit the ops sunset", "3.11"),
                   ("Gen 4: a hosted mode", "gen4"),
                   ("check the bake host after 22:00", "now"),
                   ("see 3.99 for the rest", "now (unknown phase 3.99)"),
                   ("3.11 wiki: e.g. a Gen 5 host could reuse it", "3.11"),
                   ("3.10 launcher tidy, unlike the Gen 4 plan", "now")]
        with open(os.path.join(self.cur, "INBOX.md"), "w", encoding="utf-8") as fh:
            fh.write("# INBOX\n<!-- - not a bullet -> 3.12 -->\n"
                     + "".join("- %s\n" % b for b, _ in bullets))
        p = _tool("inbox-consume", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        routes = [l for l in p.stdout.split("\n") if l.startswith("route: ")]
        self.assertEqual(routes, ["route: I%d -> %s" % (i + 1, r)
                                  for i, (_, r) in enumerate(bullets)])
        with open(os.path.join(self.cur, "discussions", "INDEX.md"), encoding="utf-8") as fh:
            rows = [l for l in fh.read().split("\n") if l.startswith("I")]
        self.assertEqual(len(rows), len(bullets))
        for i, (b, r) in enumerate(bullets):
            parts = [c.strip() for c in rows[i].split("|")]
            self.assertEqual(parts[0], "I%d" % (i + 1))
            self.assertEqual(parts[1], b)
            self.assertEqual(parts[3], "now" if r.startswith("now") else "deferred:" + r)
            self.assertTrue(parts[4].startswith("phase-ends/current/discussions/inbox-"))

    def test_pending_now_rows_and_mark(self):
        """T29: consume flags a `now` row no task or Changes line names; inbox-mark resolves it."""
        os.makedirs(os.path.join(self.cur, "discussions"))
        idx = os.path.join(self.cur, "discussions", "INDEX.md")
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write("# Discussions -- one line per record\n# id | topic | date | status | path\n"
                     "I1 | tidy the launcher | 2026-09-01 | now | x.md\n"
                     "I2 | named by a task | 2026-09-01 | now | x.md\n"
                     "I3 | named by a change | 2026-09-01 | now | x.md\n"
                     "I4 | later work | 2026-09-01 | deferred:3.11 | x.md\n")
        with open(os.path.join(self.cur, "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Phase 3.10 — repairs\n\n## Context\n- mentions I1 here only\n\n"
                     "## Tasks\n- T1 | todo | title: from inbox I2 | files: a\n\n"
                     "## Changes\n- 2026-09-02 add T2 (I3)\n")
        with open(os.path.join(self.cur, "INBOX.md"), "w", encoding="utf-8") as fh:
            fh.write("- a fresh note\n")
        p = _tool("inbox-consume", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        pend = [l for l in p.stdout.split("\n") if l.startswith("pending: ")]
        self.assertEqual(len(pend), 1, p.out)
        self.assertTrue(pend[0].startswith("pending: I1 (now,"), pend[0])
        self.assertIn("-- tidy the launcher", pend[0])
        self.assertIn("route: I5 -> now", p.stdout)
        p = _tool("inbox-mark", "I1", "bogus", env_extra=self._env())
        self.assertNotEqual(p.returncode, 0, p.out)
        p = _tool("inbox-mark", "I9", "dropped", env_extra=self._env())
        self.assertNotEqual(p.returncode, 0, p.out)
        p = _tool("inbox-mark", "I1", "planned:3.10/T3", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        with open(idx, encoding="utf-8") as fh:
            row = [l for l in fh.read().split("\n") if l.startswith("I1 ")][0]
        self.assertEqual(row, "I1 | tidy the launcher | 2026-09-01 | planned:3.10/T3 | x.md")
        p = _tool("inbox-consume", env_extra=self._env())
        pend = [l.split(" ")[1] for l in p.stdout.split("\n") if l.startswith("pending: ")]
        self.assertEqual(pend, ["I5"], p.out)     # the note consumed above, still `now`


class AgentAliveTest(unittest.TestCase):
    """agent-alive on a temp ledger with a transcript file aged by os.utime."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-status-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.ledger_dir = os.path.join(self.tmpdir, "ledger")
        os.makedirs(self.ledger_dir)
        # set up a minimal project tree for find_root
        self.root = os.path.join(self.tmpdir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"project": "test"}, fh)
        # create the ledger db with agent_runs table
        self.db_path = os.path.join(self.ledger_dir, "usage-ledger.db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_runs(
            run_id TEXT PRIMARY KEY, transcript_path TEXT)""")
        conn.commit()
        conn.close()

    def _env(self):
        return {"PA_LEDGER_DIR": self.ledger_dir,
                "PA_PROJECT_ROOT": self.root}

    def _make_transcript(self, run_id, age_seconds=0):
        """Create a transcript file and register it in agent_runs; age its mtime."""
        tp = os.path.join(self.tmpdir, "agent-%s.jsonl" % run_id)
        with open(tp, "w") as fh:
            fh.write("{}\n")
        if age_seconds > 0:
            past = time.time() - age_seconds
            os.utime(tp, (past, past))
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO agent_runs(run_id, transcript_path) VALUES(?, ?)",
            (run_id, tp))
        conn.commit()
        conn.close()
        return tp

    def test_alive_fresh_transcript(self):
        self._make_transcript("run-alive-1", age_seconds=60)  # 1 min ago, < 5 min default
        p = _tool("agent-alive", "run-alive-1", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("alive", p.out)

    def test_stale_old_transcript(self):
        self._make_transcript("run-stale-1", age_seconds=600)  # 10 min ago, > 5 min default
        p = _tool("agent-alive", "run-stale-1", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("stale", p.out)

    def test_unknown_missing_run(self):
        p = _tool("agent-alive", "no-such-run", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("unknown", p.out)

    def test_custom_liveness_min(self):
        """A config with liveness_min = 20 makes a 10-min-old transcript alive."""
        cfg_path = os.path.join(self.ledger_dir, "config.json")
        with open(cfg_path, "w") as fh:
            json.dump({"resume": {"liveness_min": 20}}, fh)
        self._make_transcript("run-custom-1", age_seconds=600)  # 10 min
        p = _tool("agent-alive", "run-custom-1", env_extra=self._env())
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("alive", p.out)


class WindowGateTest(unittest.TestCase):
    """window-gate: exit 0 go / exit 3 pause at five_hour pct >= 90 (T5.c1)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pa3-status-gate-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.root = os.path.join(self.tmpdir, "proj")
        self.ledger = os.path.join(self.tmpdir, "ledger")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(self.ledger)
        with open(os.path.join(self.root, ".claude", "pa.json"), "w") as fh:
            json.dump({"project": "test"}, fh)
        self.env = {"PA_PROJECT_ROOT": self.root, "PA_LEDGER_DIR": self.ledger}

    def _cache(self, pct, resets_at, ts=None):
        with open(os.path.join(self.ledger, "usage_api.json"), "w") as fh:
            json.dump({"ts": time.time() if ts is None else ts, "account": "a",
                       "windows": {"five_hour": {"pct": pct, "resets_at": resets_at}}}, fh)

    def _status(self):
        with open(os.path.join(self.root, ".run", "status.json"), encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _at_minute(minute):  # a future local time with the given minute, as epoch
        import datetime
        dt = datetime.datetime.fromtimestamp(time.time() + 7200).replace(minute=minute, second=20)
        return dt.timestamp(), dt

    def test_below_threshold_goes(self):
        self._cache(89.4, time.time() + 3600)
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "go: five-hour window at 89%\n")

    def test_at_threshold_pauses(self):
        import datetime
        epoch, dt = self._at_minute(10)
        self._cache(90, epoch)
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 3, p.out)
        resume = dt.replace(minute=12, second=0)
        cron = "12 %d %d %d *" % (resume.hour, resume.day, resume.month)
        self.assertEqual(p.stdout, "paused: five-hour window at 90%% until %s; resume scheduled %s; cron: %s\n"
                         % (dt.strftime("%H:%M"), resume.strftime("%H:%M"), cron))
        st = self._status()
        self.assertEqual(st["kind"], "paused")
        self.assertEqual(st["resume_cron"], cron)
        utc = lambda d: datetime.datetime.fromtimestamp(d.timestamp(), datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(st["resume_at"], utc(resume))
        self.assertEqual(st["resets_at"], utc(dt))

    def test_half_hour_edge_adds_three(self):
        for minute, want in ((58, "01"), (28, "31")):
            epoch, dt = self._at_minute(minute)
            self._cache(95.2, epoch)
            p = _tool("window-gate", env_extra=self.env)
            self.assertEqual(p.returncode, 3, p.out)
            import datetime
            resume = datetime.datetime.fromtimestamp(epoch - 20 + 180)
            self.assertEqual(resume.strftime("%M"), want)
            self.assertIn("resume scheduled %s; cron: %d %d %d %d *\n"
                          % (resume.strftime("%H:%M"), resume.minute, resume.hour, resume.day,
                             resume.month), p.stdout)

    def test_missing_cache_goes(self):
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "go: no fresh usage data\n")

    def test_stale_cache_goes(self):
        self._cache(99, time.time() + 3600, ts=time.time() - 31 * 60)
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "go: no fresh usage data\n")

    def test_reset_past_goes(self):
        self._cache(99, time.time() - 5)
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 0, p.out)
        self.assertEqual(p.stdout, "go: no fresh usage data\n")

    def test_go_after_pause_clears(self):
        epoch, _dt = self._at_minute(10)
        self._cache(92, epoch)
        self.assertEqual(_tool("window-gate", env_extra=self.env).returncode, 3)
        self._cache(12, epoch)
        p = _tool("window-gate", env_extra=self.env)
        self.assertEqual(p.returncode, 0, p.out)
        st = self._status()
        for k in ("kind", "resets_at", "resume_at", "resume_cron"):
            self.assertIsNone(st[k], k)


if __name__ == "__main__":
    unittest.main()
