"""pa.warmer (3.9.5 T4): the timer, the ping count, the state file, the event row, start-once, stop.

The decision logic runs without the loop: :func:`pa.warmer.apply_records` and :func:`pa.warmer.due`
over fixture records with a fixed ``now``; :class:`pa.warmer.Warmer` ticks by :meth:`poll` against
a temp project root, a temp transcript tree and a temp ``PA_LEDGER_DIR``.  No process is started:
``notify.set_spawner`` records the spawn.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import config, notify, paths, warmer  # noqa: E402

T0 = 1790000000.0
SID = "w0000000-0000-4000-8000-00000000beef"
AID = "a1111111000000002"
S = warmer.settings(config.defaults())


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def user(t, text=None, tool_result=False):
    content = ([{"type": "tool_result", "tool_use_id": "tu", "content": "ok"}] if tool_result
               else text)
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": content}}


def assistant(t, tool_use=False):
    block = ({"type": "tool_use", "id": "tu", "name": "Bash", "input": {}} if tool_use
             else {"type": "text", "text": "done"})
    return {"type": "assistant", "timestamp": iso(t),
            "message": {"role": "assistant", "content": [block]}}


HB = "toolu_01SYNTH00000000000000001"


def handback(t):
    """C0025: the real records of a delivered report (agent-a8cfdc7600000000f, 2026-09-24)."""
    return [{"type": "assistant", "timestamp": iso(t),
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": HB, "name": "SubagentHandback",
                  "input": {"message": "STATUS: done"}, "caller": {"type": "direct"}}]}},
            {"type": "user", "timestamp": iso(t + 1),
             "message": {"role": "user", "content": [
                 {"tool_use_id": HB, "type": "tool_result", "content": [
                     {"type": "text", "text": "{\"success\":true,\"message\":\"Report delivered "
                                              "to your caller.\"}"}]}]},
             "toolUseResult": {"success": True, "message": "Report delivered to your caller."}},
            {"type": "assistant", "timestamp": iso(t + 3),
             "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "Reported done via SubagentHandback."}]}}]


def run_with(records, **fields):
    run = warmer.new_run(AID, "coder-opus55", "coder", "5m")
    run.update(fields)
    return warmer.apply_records(run, records)


class TimerTest(unittest.TestCase):
    """The binding timer rule over fixture records."""

    def test_base_is_the_user_record_never_the_assistant_record(self):
        run = run_with([user(T0, "go"), assistant(T0 + 200)])       # a 200 s request lag
        self.assertEqual(run["base"], T0)
        self.assertTrue(run["idle"])
        self.assertIsNone(warmer.due(run, T0 + 259, S))
        self.assertEqual(warmer.due(run, T0 + 260, S), 260)          # 285 - 25

    def test_other_record_types_are_ignored(self):
        run = run_with([user(T0, "go"), assistant(T0 + 5), {"type": "system", "timestamp": iso(T0 + 9)},
                        {"type": "queue-operation"}, {"type": "summary"}])
        self.assertTrue(run["idle"])
        self.assertEqual(run["base"], T0)

    def test_an_assistant_record_with_tool_use_is_not_idle(self):
        run = run_with([user(T0, "go"), assistant(T0 + 3, tool_use=True)])
        self.assertFalse(run["idle"])
        self.assertIsNone(warmer.due(run, T0 + 280, S))

    def test_the_ping_landing_rebases_and_keeps_the_count(self):
        for text in ("The coordinator sent a message while you were working: .", "."):
            with self.subTest(text=text):
                run = run_with([user(T0 + 270, text), assistant(T0 + 271)],
                               base=T0, pings=1, last_ping=T0 + 260)
                self.assertEqual(run["pings"], 1)
                self.assertEqual(run["base"], T0 + 270)
                self.assertIsNone(warmer.due(run, T0 + 270 + 259, S))
                self.assertEqual(warmer.due(run, T0 + 270 + 260, S), 260)

    def test_a_real_request_resets_the_count(self):
        for rec in (user(T0 + 400, tool_result=True), user(T0 + 400, "next step")):
            with self.subTest(rec=rec["message"]["content"]):
                run = run_with([rec], base=T0, pings=2, last_ping=T0 + 300)
                self.assertEqual(run["pings"], 0)
                self.assertEqual(run["base"], T0 + 400)

    def test_the_cap_makes_the_run_cold(self):
        run = run_with([user(T0, "."), assistant(T0 + 1)], pings=12, last_ping=T0 - 10)  # was 3 (T8)
        self.assertIsNone(warmer.due(run, T0 + 270, S))

    def test_the_cap_per_ttl_12_on_5m_3_on_1h(self):
        """3.9.5 T8: a 5m run gets pings 1..12 then cold; a 1h run 1..3 then cold."""
        for ttl, fire, cap in (("5m", 260, 12), ("1h", 3275, 3)):
            with self.subTest(ttl=ttl):
                run = run_with([user(T0, "go"), assistant(T0 + 30)], ttl=ttl)
                base = T0
                for n in range(1, cap + 1):
                    now = base + fire
                    self.assertEqual(warmer.due(run, now, S), fire, n)
                    run["pings"], run["last_ping"] = run["pings"] + 1, now      # what _fire does
                    base = now + 10                                              # the ping lands
                    warmer.apply_records(run, [user(base, "."), assistant(base + 1)])
                    self.assertEqual(run["pings"], n)
                self.assertIsNone(warmer.due(run, base + fire, S))              # cold

    def test_once_per_base_until_the_ping_lands(self):
        run = run_with([user(T0, "go"), assistant(T0 + 1)], pings=1, last_ping=T0 + 260)
        self.assertIsNone(warmer.due(run, T0 + 280, S))

    def test_one_hour_ttl_and_an_expired_cache(self):
        run = run_with([user(T0, "go"), assistant(T0 + 1)])
        self.assertIsNone(warmer.due(run, T0 + 301, S))              # 5m cache already gone
        run["ttl"] = "1h"
        self.assertIsNone(warmer.due(run, T0 + 3274, S))
        self.assertEqual(warmer.due(run, T0 + 3275, S), 3275)         # 3300 - 25


class DaemonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-warmer-")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "warmer-test"}, fh)
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "usage-ledger")
        paths.ensure_ledger_tree()
        self.spawned = []
        notify.set_spawner(lambda cmd, env: self.spawned.append((cmd, env)))

    def tearDown(self):
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def wfile(self, ext):
        return warmer.path(self.root, SID, ext)

    def write(self, path, lines, mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write((line if isinstance(line, str) else json.dumps(line)) + "\n")

    def daemon(self, agents=None):
        with open(paths.running_path(), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "sessions": {SID: {"agents": agents or {}}}}, fh)
        w = warmer.Warmer(self.root, SID, None, cfg=config.defaults())
        w.session_dir = os.path.join(self.dir, "projects", "slug", SID)
        w.start()
        return w

    def transcript(self, aid=AID):
        return os.path.join(self.dir, "projects", "slug", SID, "subagents", "agent-%s.jsonl" % aid)


class DaemonTest(DaemonCase):
    """One tick at a time: adoption, the runs file, the wake line, warmer.json, the spool row."""

    def test_adopt_poll_fire_state_and_event(self):
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 30)])
        w = self.daemon({SID: {"agent_type": None, "role": "router"},
                         AID: {"agent_type": "coder-opus55", "role": "coder"}})
        self.assertEqual(sorted(w.runs), [AID])                      # the main session is never warmed
        w.poll(T0 + 100)
        with open(warmer.state_path(self.root), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["session"], SID)
        self.assertEqual(doc["updated"], T0 + 100)
        self.assertEqual(doc["runs"][AID], {"last_request": T0, "ttl": "5m", "pings": 0,
                                            "last_ping": None, "via": "monitor",
                                            "agent_type": "coder-opus55", "idle": True})
        self.assertFalse(os.path.exists(self.wfile("wake")))
        w.poll(T0 + 262)
        with open(self.wfile("wake"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "warm %s 262\n" % AID)
        w.poll(T0 + 270)                                              # once per base
        with open(self.wfile("wake"), encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 1)
        with open(warmer.state_path(self.root), encoding="utf-8") as fh:
            run = json.load(fh)["runs"][AID]
        self.assertEqual((run["pings"], run["last_ping"]), (1, T0 + 262))
        with open(paths.spool_path(SID), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["t"], rows[0]["kind"], rows[0]["run_id"]), ("event", "warm_ping", AID))
        self.assertEqual(rows[0]["detail"], {"run_id": AID, "session_id": SID, "ttl": "5m",
                                             "idle_s": 262, "n": 1, "via": "monitor", "cap": 12})
        # the ping lands, the run answers '.', the next line comes 260 s after the landing
        self.write(self.transcript(), [user(T0 + 280, "The coordinator sent a message while you "
                                                      "were working: ."), assistant(T0 + 281)], "a")
        w.poll(T0 + 280 + 261)
        self.assertEqual(w.runs[AID]["pings"], 2)
        with open(self.wfile("log"), encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("adopted 1", log)
        self.assertIn("warm %s 262" % AID, log)

    def test_the_relay_wrapper_counts_as_a_ping_a_different_body_resets(self):
        """C0025: the harness's real relay wrapper (2026-09-24), not just the inline ': .' shape."""
        wrapper = ("The coordinator sent a message while you were working:\n.\n\n"
                   "Address this before completing your current task.")
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 30)])
        w = self.daemon({AID: {"agent_type": "coder-opus55", "role": "coder"}})
        w.poll(T0 + 262)                                              # first fire: pings 1
        self.assertEqual(w.runs[AID]["pings"], 1)
        # the relayed wrapper lands: the count is kept, the base moves to its own timestamp
        self.write(self.transcript(), [user(T0 + 280, wrapper), assistant(T0 + 281)], "a")
        w.poll(T0 + 280 + 1)
        self.assertEqual((w.runs[AID]["pings"], w.runs[AID]["base"]), (1, T0 + 280))
        # the next fire counts it
        w.poll(T0 + 280 + 261)
        with open(self.wfile("log"), encoding="utf-8") as fh:
            self.assertIn("n 2", fh.read())
        self.assertEqual(w.runs[AID]["pings"], 2)
        # a wrapper whose body is not "." is a real request: it resets the count
        other = "The coordinator sent a message while you were working:\ncontinue\n"
        t = T0 + 280 + 261 + 5
        self.write(self.transcript(), [user(t, other), assistant(t + 1)], "a")
        w.poll(t + 2)
        self.assertEqual(w.runs[AID]["pings"], 0)

    def test_runs_file_adds_and_ends_and_a_vanished_transcript_ends_the_watch(self):
        w = self.daemon()
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 1, tool_use=True)])
        self.write(self.wfile("runs"), [{"add": AID, "agent_type": "expert-fable", "role": "expert"},
                                        {"add": "a2", "agent_type": "coder-opus55", "role": "coder"}])
        w.poll(T0 + 5)
        with open(warmer.state_path(self.root), encoding="utf-8") as fh:
            runs = json.load(fh)["runs"]
        self.assertEqual(sorted(runs), sorted(["a2", AID]))
        self.assertFalse(runs[AID]["idle"])
        self.write(self.wfile("runs"), [{"end": "a2"}], "a")
        w.poll(T0 + 10)
        self.assertEqual(w.runs["a2"]["ended"], T0 + 10)             # advisory: still watched
        os.remove(self.transcript())
        w.poll(T0 + 15)
        self.assertNotIn(AID, w.runs)

    def test_a_finished_run_is_never_pinged_until_a_real_request(self):
        wrapper = ("The coordinator sent a message while you were working:\n.\n\n"
                   "Address this before completing your current task.")
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 1, tool_use=True),
                                       user(T0 + 5, tool_result=True)] + handback(T0 + 10))
        w = self.daemon({AID: {"agent_type": "coder-opus55", "role": "coder"}})
        w.poll(T0 + 11 + 262)
        self.assertFalse(os.path.exists(self.wfile("wake")))
        with open(warmer.state_path(self.root), encoding="utf-8") as fh:
            self.assertIs(json.load(fh)["runs"][AID]["finished"], True)
        t = T0 + 11 + 270                                              # a ping stays finished
        self.write(self.transcript(), [user(t, wrapper), assistant(t + 1)], "a")
        w.poll(t + 262)
        self.assertFalse(os.path.exists(self.wfile("wake")))
        t += 300                                                       # a resume clears it
        self.write(self.transcript(), [user(t, "one more thing"), assistant(t + 2)], "a")
        w.poll(t + 262)
        self.assertNotIn("finished", w.state()[AID])
        with open(self.wfile("wake"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "warm %s 262\n" % AID)

    def test_a_harness_nudge_keeps_a_finished_run_finished(self):
        # trial aa2461ba000000012 (3.9.5 T6): handback, nudge 1 s later, text, pinged at 263 s idle
        nudge = ("[Your previous response had no visible output. Please continue and produce a "
                 "user-visible response.]")
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 1, tool_use=True),
                                       user(T0 + 5, tool_result=True)] + handback(T0 + 10)
                   + [user(T0 + 12, nudge), assistant(T0 + 20)])
        w = self.daemon({AID: {"agent_type": "coder-opus55", "role": "coder"}})
        for dt in (262, 300, 600):
            w.poll(T0 + 12 + dt)
        self.assertFalse(os.path.exists(self.wfile("wake")))
        self.assertIs(w.runs[AID]["finished"], True)
        self.assertEqual(w.runs[AID]["base"], T0 + 12)                 # the base still moves
        t = T0 + 700                                                   # a real request un-finishes
        self.write(self.transcript(), [user(t, "one more thing"), assistant(t + 2)], "a")
        w.poll(t + 262)
        self.assertIs(w.runs[AID]["finished"], False)
        with open(self.wfile("wake"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "warm %s 262\n" % AID)

    def test_the_end_line_is_advisory_and_add_clears_it(self):
        self.write(self.transcript(), [user(T0, "go"), assistant(T0 + 30)])
        w = self.daemon()
        self.write(self.wfile("runs"), [{"add": AID, "agent_type": "expert-opus55", "role": "expert"},
                                        {"end": AID}])
        w.poll(T0 + 40)
        self.assertEqual(w.runs[AID]["ended"], T0 + 40)
        w.poll(T0 + 300)                                              # 300 s of idleness
        with open(self.wfile("wake"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "warm %s 300\n" % AID)
        with open(warmer.state_path(self.root), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["runs"][AID]["ended"], T0 + 40)
        self.write(self.wfile("runs"), [{"add": AID, "agent_type": "expert-opus55", "role": "expert"}],
                   "a")
        w.poll(T0 + 305)
        self.assertIsNone(w.runs[AID]["ended"])
        self.assertNotIn("ended", w.state()[AID])

    def test_read_new_keeps_a_partial_line_for_the_next_read(self):
        p = self.transcript()
        self.write(p, [user(T0, "go")])
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"type": "assist')
        recs, off = warmer.read_new(p, None)
        self.assertEqual(len(recs), 1)
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('ant"}\n')
        recs, off2 = warmer.read_new(p, off)
        self.assertEqual(recs, [{"type": "assistant"}])
        self.assertEqual(off2, os.path.getsize(p))


class LifecycleTest(DaemonCase):
    """Start-once, stop, and every exit reason."""

    def _reap(self):
        """The pid of a process that has exited."""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def test_pid_alive(self):
        self.assertTrue(warmer.pid_alive(os.getpid()))
        self.assertFalse(warmer.pid_alive(0))
        self.assertFalse(warmer.pid_alive(self._reap()))

    def test_start_once_and_the_spawn_command(self):
        self.write(self.wfile("pid"), [str(os.getpid())])
        self.assertFalse(warmer.start_if_needed(self.root, SID, None, "77"))
        self.assertEqual(self.spawned, [])
        self.write(self.wfile("pid"), [str(self._reap())])
        self.write(self.wfile("stop"), ["1"])
        self.assertTrue(warmer.start_if_needed(self.root, SID, None, "77"))
        self.assertFalse(os.path.exists(self.wfile("stop")))          # a resumed session starts clean
        cmd, env = self.spawned[0]
        self.assertEqual(cmd[1:7], ["-s", "-P", "-X", "utf8", "-m", "pa.warmer"])
        self.assertEqual(cmd[7:], ["--project", self.root, "--session", SID, "--pid", "77"])
        self.assertTrue(os.path.isfile(os.path.join(env["PYTHONPATH"], "pa", "warmer.py")))

    def test_warmer_off_never_spawns(self):
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"warmer": "off"}, fh)
        self.assertFalse(warmer.start_if_needed(self.root, SID))
        self.assertEqual(self.spawned, [])

    def test_stop_writes_the_stop_file_and_removes_the_pid(self):
        self.write(self.wfile("pid"), [str(os.getpid())])             # never terminates itself
        warmer.stop(self.root, SID)
        self.assertTrue(os.path.exists(self.wfile("stop")))
        self.assertFalse(os.path.exists(self.wfile("pid")))

    def test_the_loop_exits_on_the_stop_file_and_removes_its_pid(self):
        self.write(self.wfile("stop"), ["1"])
        w = warmer.Warmer(self.root, SID, None, cfg=config.defaults())
        self.assertEqual(w.run(), 0)
        self.assertFalse(os.path.exists(self.wfile("pid")))
        with open(self.wfile("log"), encoding="utf-8") as fh:
            self.assertIn("exit stop file", fh.read())

    def test_exit_reasons(self):
        w = self.daemon()
        self.assertIsNone(w.exit_reason())
        self.write(self.wfile("pid"), ["12345"])
        self.assertIn("names 12345", w.exit_reason())
        w.finish("x")
        self.assertTrue(os.path.exists(self.wfile("pid")))            # not ours: left alone
        w = self.daemon()
        main = w.session_dir + ".jsonl"
        self.write(main, [user(T0, "go")])
        self.assertIsNone(w.exit_reason())
        os.remove(main)
        self.assertEqual(w.exit_reason(), "main transcript gone")
        w = self.daemon()
        w.registry = os.path.join(self.dir, "sessions", "4242.json")
        w.claude_pid = 4242
        self.write(w.registry, ["{}"])
        self.assertIsNone(w.exit_reason())
        os.remove(w.registry)
        self.assertEqual(w.exit_reason(), "session pid 4242 gone")

    def test_an_unregistered_session_pid_exits_within_a_poll(self):
        """3.9.5 T8: no ``sessions/<pid>.json`` ever seen -> exit after one ``poll_s``."""
        cfg = config.defaults()
        cfg["warmer"] = dict(cfg["warmer"], poll_s=0.2)
        with mock.patch.object(paths, "claude_dir", lambda: os.path.join(self.dir, "claude")):
            w = warmer.Warmer(self.root, SID, "4243", cfg=cfg)
        self.assertEqual(w.registry, os.path.join(self.dir, "claude", "sessions", "4243.json"))
        t = time.time()
        self.assertEqual(w.run(), 0)
        self.assertLess(time.time() - t, 0.4 + 0.3)
        self.assertFalse(os.path.exists(self.wfile("pid")))
        with open(self.wfile("log"), encoding="utf-8") as fh:
            self.assertIn("exit session pid 4243 not registered", fh.read())


if __name__ == "__main__":
    unittest.main()
