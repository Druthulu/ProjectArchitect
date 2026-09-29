"""pa.hooks end to end: the probe fixtures (T6), the handoff (T7) and latency (T12).

Everything runs against a ``PA_LEDGER_DIR`` temp directory, a temp copy of the
transcript tree and a fake home (``USERPROFILE``, ``HOME`` and ``CLAUDE_CONFIG_DIR``
point under the temp dir, so the SessionStart update check never sees the real
``~/.claude/pa3`` or ``pa3-src``): no test touches ``~/.claude``, the real ledger, the network or
a real notifier (``notify.set_spawner`` records instead of spawning, and
``accounts.auth_status`` is replaced by a fixed email so no ``claude auth status``
subprocess runs).

The probe logs are the verified hook inputs of Claude Code 2.1.270 -- every
event, including the parent-side ``Agent`` ``tool_response`` (recon V4),
PostToolUse inside an Explore subagent, and SubagentStop with
``agent_transcript_path``.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import accounts, config, db, notify, paths, running, summary  # noqa: E402
from pa import hooks  # noqa: E402
from pa.hooks import post_tool_use, pre_tool_use  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "hooks")
EVENT_MAP = {
    "SessionStart": "session_start", "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use", "PostToolUse": "post_tool_use",
    "SubagentStart": "subagent_start", "SubagentStop": "subagent_stop",
    "Stop": "stop", "StopFailure": "stop_failure", "Notification": "notification",
    "PostModelSwitch": "model_switch", "SessionEnd": "session_end",
}
PROBE1_SID = "a1b2c3d4-0000-4000-8000-00000000c0de"
PROBE1_AGENT = "a5cf611700000000b"
ACCOUNT = "probe@example.com"
_PATH_KEYS = ("transcript_path", "agent_transcript_path")


def read_blocks(name):
    """``[(event, input), …]`` for one probe log."""
    out = []
    path = os.path.join(FIXTURES, name)
    current = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("=== "):
                current = line.split()[1]
                continue
            line = line.strip()
            if not line.startswith("{"):
                continue
            inp = json.loads(line)
            out.append((EVENT_MAP[inp.get("hook_event_name") or current], inp))
    return out


def usage_line(msg_id, model="claude-fable-5-1[1m]", inp=1000, write=0, read=0, out=500,
               ts="2026-09-12T20:00:00.000Z", ttl="1h"):
    """One assistant jsonl record shaped like a real transcript line."""
    return json.dumps({
        "type": "assistant", "uuid": "u-" + msg_id, "requestId": "req_" + msg_id,
        "timestamp": ts, "sessionId": "synthetic",
        "message": {"id": msg_id, "role": "assistant", "model": model,
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": inp, "cache_creation_input_tokens": write,
                              "cache_read_input_tokens": read, "output_tokens": out,
                              "cache_creation": {"ephemeral_5m_input_tokens": write if ttl == "5m" else 0,
                                                 "ephemeral_1h_input_tokens": write if ttl == "1h" else 0},
                              "output_tokens_details": {"thinking_tokens": 0}}},
    })


def tool_result_line(uuid, text, ts="2026-09-12T20:00:01.000Z"):
    return json.dumps({"type": "user", "uuid": uuid, "timestamp": ts,
                       "message": {"role": "user",
                                   "content": [{"type": "tool_result", "content": text}]}})


class LedgerCase(unittest.TestCase):
    """Temp ledger + temp transcript tree + stubbed auth/notifier."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-hooks-")
        self.ledger = os.path.join(self.dir, "usage-ledger")
        self.projects = os.path.join(self.dir, "projects")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "hooks-test"}')
        os.environ["PA_LEDGER_DIR"] = self.ledger
        # T24: a fake home, so paths.install_dir()/package_clone_dir() never reach the real ~/.claude
        self.fake_home = os.path.join(self.dir, "home")
        os.makedirs(self.fake_home)
        self._old_env = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["USERPROFILE"] = self.fake_home
        os.environ["HOME"] = self.fake_home
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.fake_home, ".claude")
        paths.ensure_ledger_tree()
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()
        self.cfg["accounts"] = {ACCOUNT: {"renewal_day": 21}}   # 3.11 T9: a known day, so no hook asks for it
        self.toasts = []
        self.warmers = []                  # 3.9.5 T4: the warmer daemon's spawns, kept apart from toasts
        self.rebuilds = []                 # 3.9.6 T5: the detached summary rebuilds, run inline here
        self.run_rebuilds = True
        notify.set_spawner(self._spawn)
        self._auth = accounts.auth_status
        accounts.auth_status = lambda *a, **k: {"email": ACCOUNT, "source": "auth",
                                                "org_name": "probe-org"}

    def tearDown(self):
        accounts.auth_status = self._auth
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        for key, old in self._old_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- helpers

    def _spawn(self, cmd, env):
        """The test spawner: never a real child; a summary rebuild runs inline (synchronously)."""
        if "pa.warmer" in cmd:
            self.warmers.append((cmd, env))
        elif "pa.summary" in cmd:
            self.rebuilds.append((cmd, env))
            if self.run_rebuilds:
                summary.main(cmd[cmd.index("pa.summary") + 1:])
        else:
            self.toasts.append((cmd, env))

    def rewrite(self, inp):
        """Point a fixture's paths at the temp tree (never the real ~/.claude)."""
        out = dict(inp)
        for key in _PATH_KEYS:
            val = out.get(key)
            if not val:
                continue
            norm = str(val).replace("\\", "/")
            m = re.search(r"/projects/(.+)$", norm)
            tail = m.group(1) if m else os.path.basename(norm)
            out[key] = os.path.join(self.projects, *tail.split("/"))
        if out.get("cwd"):
            out["cwd"] = self.root
        return out

    def run_hook(self, event, inp):
        module = __import__("pa.hooks." + event, fromlist=["run"])
        return module.run(self.rewrite(inp), self.cfg)

    def conn(self):
        return db.connect(paths.db_path())

    def rows(self, sql, args=()):
        conn = self.conn()
        try:
            return [tuple(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            db.close(conn)

    def set_toast(self, value):
        """pa.json ``toast`` for this project (3.9.5 T2: missing means ``waiting``)."""
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "hooks-test", "toast": value}, fh)

    def toast_rows(self):
        """The ``events`` rows kind ``toast`` as detail dicts, oldest first."""
        return [json.loads(raw) for (raw,) in
                self.rows("SELECT detail_json FROM events WHERE kind='toast' ORDER BY rowid")]

    def write_transcript(self, path, lines):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write(line + "\n")
        return path


class ProbeReplayTest(LedgerCase):
    """T6: every block of every probe log through its hook."""

    def replay(self, name, synthesize=True):
        blocks = read_blocks(name)
        if synthesize:
            for event, inp in blocks:
                if event == "subagent_stop":
                    target = self.rewrite(inp).get("agent_transcript_path")
                    if target and not os.path.exists(target):
                        self.write_transcript(target, [
                            usage_line("msg_%s_1" % inp["agent_id"], model="claude-haiku-4-5",
                                       inp=1200, write=800, read=0, out=40, ttl="5m"),
                            tool_result_line("tr-1", "x" * 4000),
                            usage_line("msg_%s_2" % inp["agent_id"], model="claude-haiku-4-5",
                                       inp=10, write=0, read=2200, out=64, ttl="5m",
                                       ts="2026-09-12T20:00:05.000Z")])
        results = []
        for event, inp in blocks:
            results.append((event, self.run_hook(event, inp)))
        return results

    def test_probe1_every_block_runs(self):
        results = self.replay("probe1.log")
        self.assertEqual(len(results), 10)
        for event, out in results:
            self.assertIn(out, (None,), "%s returned %r" % (event, out))

    def test_probe2_and_probe3_every_block_runs(self):
        for name, count in (("probe2.log", 11), ("probe3.log", 10)):
            with self.subTest(probe=name):
                results = self.replay(name)
                self.assertEqual(len(results), count)
                for event, out in results:
                    self.assertIsNone(out, "%s returned %r" % (event, out))

    def test_session_row_created_with_fixture_session_id(self):
        self.replay("probe1.log")
        rows = self.rows("SELECT session_id, account, account_source, kind, machine, "
                         "end_reason FROM sessions WHERE session_id=?", (PROBE1_SID,))
        self.assertEqual(len(rows), 1)
        sid, account, source, kind, machine, end_reason = rows[0]
        self.assertEqual(sid, PROBE1_SID)
        self.assertEqual(account, ACCOUNT)
        self.assertEqual(source, "auth")
        self.assertEqual(kind, "probe-main")
        self.assertTrue(machine)
        self.assertEqual(end_reason, "other")             # the SessionEnd block closed it
        self.assertEqual(self.rows("SELECT email FROM accounts"), [(ACCOUNT,)])

    def test_account_stamp_is_reusable_without_a_subprocess(self):
        self.replay("probe1.log")
        self.assertEqual(accounts.account_for(PROBE1_SID), (ACCOUNT, "auth"))

    def test_agent_tool_post_tool_use_links_child_to_parent(self):
        self.replay("probe1.log")
        rows = self.rows("SELECT parent_run_id, parent_source, agent_type, session_id "
                         "FROM agent_runs WHERE run_id=?", (PROBE1_AGENT,))
        self.assertEqual(len(rows), 1)
        parent, source, agent_type, sid = rows[0]
        self.assertEqual(parent, PROBE1_SID)
        self.assertEqual(source, "agent_tool")
        self.assertEqual(agent_type, "probe-leaf")
        self.assertEqual(sid, PROBE1_SID)

    def test_subagent_stop_locks_the_run_from_its_transcript(self):
        self.replay("probe1.log")
        rows = self.rows("SELECT locked, lock_source, turns, status, ctx_at_end, "
                         "transcript_bytes FROM agent_runs WHERE run_id=?", (PROBE1_AGENT,))
        self.assertEqual(len(rows), 1)
        locked, source, turns, status, ctx_at_end, nbytes = rows[0]
        self.assertEqual(locked, 1)
        self.assertEqual(source, "transcript")
        self.assertEqual(turns, 2)
        self.assertEqual(status, "completed")
        self.assertEqual(ctx_at_end, 2210)
        self.assertTrue(nbytes and nbytes > 0)
        msg_ids = self.rows("SELECT msg_id FROM turns WHERE run_id=? ORDER BY msg_id",
                            (PROBE1_AGENT,))
        self.assertEqual(msg_ids, [("msg_%s_1" % PROBE1_AGENT,), ("msg_%s_2" % PROBE1_AGENT,)])

    def test_task_notification_lands_in_running_json(self):
        self.replay("probe1.log")
        with open(os.path.join(self.ledger, "running.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        # the SessionEnd block removes the session again, so read what it logged
        self.assertEqual(doc.get("schema"), 1)
        self.assertNotIn(PROBE1_SID, doc.get("sessions", {}))

    def test_probe2_explore_subagent_is_recorded(self):
        self.replay("probe2.log")
        runs = dict((r[0], r[1]) for r in
                    self.rows("SELECT run_id, agent_type FROM agent_runs"))
        self.assertIn("abc0135b000000015", runs)
        self.assertEqual(runs["abc0135b000000015"], "Explore")

    def test_summary_json_is_written(self):
        self.replay("probe1.log")
        with open(os.path.join(self.ledger, "summary.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["schema"], 1)
        self.assertIn(ACCOUNT, doc["accounts"])
        self.assertIn(PROBE1_SID, doc["sessions"])
        self.assertEqual(doc["sessions"][PROBE1_SID]["account"], ACCOUNT)

    def test_no_stray_output_and_no_real_paths_touched(self):
        self.replay("probe1.log")
        self.assertTrue(os.path.exists(os.path.join(self.ledger, "ledger.sqlite")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".claude")))

    def test_replay_is_idempotent(self):
        self.replay("probe1.log")
        first = self.rows("SELECT COUNT(*) FROM turns")
        self.replay("probe1.log", synthesize=False)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM turns"), first)
        self.assertEqual(len(self.rows("SELECT session_id FROM sessions")), 1)


class HandoffTest(LedgerCase):
    """T7: the 350k crossing fires exactly once."""

    SID = "handoff-session"
    AGENT = "a1111111000000002"

    def _input(self):
        return {"session_id": self.SID, "agent_id": self.AGENT, "agent_type": "expert-fable",
                "cwd": self.root, "hook_event_name": "PostToolUse", "tool_name": "Read",
                "tool_input": {"file_path": "x.py"},
                "transcript_path": os.path.join(self.projects, "slug", self.SID + ".jsonl")}

    def _transcript(self, ctx_total, msg_id="msg_big"):
        inp = self._input()
        path = hooks.agent_transcript(inp)
        read = ctx_total - 3000 - 2000
        self.write_transcript(path, [
            usage_line("msg_seed", inp=2000, write=1000, read=0, out=100),
            usage_line(msg_id, inp=1000, write=2000, read=read, out=2000,
                       ts="2026-09-12T21:00:00.000Z")])
        return path

    def test_subagent_start_stamps_task_and_phase_from_status(self):
        """3.1 T10/T11: agent_runs rows carry the task and phase the router stamped in status.json."""
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T3", "phase": "24", "generation": "3"}, fh)
        self.run_hook("subagent_start", self._input())
        self.assertEqual(self.rows("SELECT task_id, phase, kind FROM agent_runs WHERE run_id=?",
                                   (self.AGENT,)), [("T3", "24", "expert")])

    def test_subagent_start_task_id_from_agent_description(self):
        """T7.c3: an Agent description starting with a task id ('T5.c1 guard
        rules') overrides the running task from status.json, so a coder
        spawned mid-wave for a different task lands under its own id."""
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T1", "phase": "24", "generation": "3"}, fh)
        path = hooks.agent_transcript(self._input())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path[:-6] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "coder-opus46", "description": "T5.c1 guard rules"}, fh)
        self.run_hook("subagent_start", self._input())
        self.assertEqual(self.rows("SELECT task_id FROM agent_runs WHERE run_id=?",
                                   (self.AGENT,)), [("T5",)])

    def test_subagent_start_task_id_falls_back_without_prefix(self):
        """A description without a task-id prefix keeps status.json's task."""
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T1", "phase": "24", "generation": "3"}, fh)
        path = hooks.agent_transcript(self._input())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path[:-6] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "coder-opus46", "description": "guard rules"}, fh)
        self.run_hook("subagent_start", self._input())
        self.assertEqual(self.rows("SELECT task_id FROM agent_runs WHERE run_id=?",
                                   (self.AGENT,)), [("T1",)])

    def test_subagent_start_phase_from_plan_when_status_empty(self):
        """3.6 T4: phase falls back to plan_phase_id when status.phase is null."""
        pe = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(pe, exist_ok=True)
        with open(os.path.join(pe, "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Phase 3.6 -- test\nApproved: 2026-09-22\n")
        # status with no phase
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T4"}, fh)
        self.run_hook("subagent_start", self._input())
        self.assertEqual(self.rows("SELECT phase FROM agent_runs WHERE run_id=?",
                                   (self.AGENT,)), [("3.6",)])

    def test_subagent_start_in_beside_session_tags_beside(self):
        """T28: a session other than router_session stamps ``beside <phase>`` on its runs."""
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T3", "phase": "24", "router_session": "owner-sid"}, fh)
        self.run_hook("subagent_start", self._input())
        self.assertEqual(self.rows("SELECT phase FROM agent_runs WHERE run_id=?",
                                   (self.AGENT,)), [("beside 24",)])

    def test_effort_seen_is_sampled_from_the_transcript(self):
        """3.1 T10: the record-level ``effort`` field lands in running.json as effort_seen."""
        inp = self._input()
        path = hooks.agent_transcript(inp)
        rec = json.loads(usage_line("msg_eff", inp=2000, write=1000, read=0, out=100))
        rec["effort"] = "medium"
        self.write_transcript(path, [json.dumps(rec)])
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["effort_seen"], "medium")
        self.assertEqual(entry["model_seen"], "claude-fable-5-1[1m]")

    def test_below_threshold_returns_none(self):
        self._transcript(120_000)
        self.assertIsNone(post_tool_use.run(self._input(), self.cfg))
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["ctx"], 120_000)
        self.assertFalse(entry["handoff_fired"])
        self.assertEqual(entry["n_requests"], 1)

    def test_crossing_fires_exactly_once(self):
        self._transcript(355_000)
        first = post_tool_use.run(self._input(), self.cfg)
        second = post_tool_use.run(self._input(), self.cfg)
        self.assertIsNotNone(first)
        hso = first["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        self.assertIn("HANDOFF", hso["additionalContext"])
        self.assertIn("TASK_PROGRESS.md", hso["additionalContext"])
        self.assertIsNone(second)

    def test_crossing_sets_handoff_fired_everywhere(self):
        self._transcript(355_000)
        post_tool_use.run(self._input(), self.cfg)
        entry = running.agent(self.SID, self.AGENT)
        self.assertTrue(entry["handoff_fired"])
        self.assertTrue(entry["handoff_at"])
        rows = self.rows("SELECT handoff_fired, status FROM agent_runs WHERE run_id=?",
                         (self.AGENT,))
        self.assertEqual(rows, [(1, "handoff")])
        self.assertTrue(os.path.exists(os.path.join(self.ledger, "state", "handoff", self.AGENT)))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events WHERE kind='handoff'"), [(1,)])

    def test_threshold_is_capped_at_80_percent_of_a_200k_window(self):
        from pa import handoff

        self.assertEqual(handoff.threshold(self.cfg, "expert",
                                           model="claude-fable-5-1[1m]"), 350_000)
        self.assertEqual(handoff.threshold(self.cfg, "expert",
                                           model="claude-sonnet-5"), 160_000)
        pcfg = {"handoff_ctx": {"expert": 60000}}
        self.assertEqual(handoff.threshold(self.cfg, "expert", project_cfg=pcfg,
                                           model="claude-fable-5-1[1m]"), 60000)

    def test_no_handoff_outside_a_pa3_project(self):
        outside = os.path.join(self.dir, "elsewhere")
        os.makedirs(outside)
        hooks._PROJECT_ROOTS.clear()
        self._transcript(355_000)
        inp = self._input()
        inp["cwd"] = outside
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        self.assertEqual(running.agent(self.SID, self.AGENT)["ctx"], 355_000)

    def test_prior_kept_is_set_at_start_and_charged_as_reads(self):
        """What the session already kept out is charged to the new expert per request;
        T11: always at the read price (prior_cold no longer charges a write)."""
        conn = hooks.open_db()
        try:
            db.upsert_retrieval(conn, {"run_id": "ret-0", "parent_run_id": self.SID,
                                       "kept_out_tokens": 30000, "void": 0,
                                       "returned_at": "2026-09-12T19:00:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        inp = self._input()
        inp["hook_event_name"] = "SubagentStart"
        self.run_hook("subagent_start", inp)
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["prior_kept"], 30000)
        self.assertTrue(entry["prior_cold"])
        self._transcript(120_000)
        post_tool_use.run(self._input(), self.cfg)
        entry = running.agent(self.SID, self.AGENT)
        # vanilla-net-v3: prior_kept is no longer credited in live_saved (attributed to its parent)
        self.assertAlmostEqual(entry["saved_live_usd"], 0.0, places=6)

    def test_meta_parent_is_read_on_the_first_sample(self):
        """The harness writes parentAgentId into meta.json; the child's first sample adopts it."""
        path = self._transcript(120_000)
        with open(path[:-6] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "expert-fable", "parentAgentId": "a2222222000000006",
                       "spawnDepth": 2}, fh)
        post_tool_use.run(self._input(), self.cfg)
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["parent"], "a2222222000000006")
        self.assertEqual(entry["parent_source"], "meta")

    def test_planner_is_sampled_live_but_never_handed_off(self):
        """Planners and the critic get ctx, effort and savings live; only handoff roles hand off."""
        inp = dict(self._input(), agent_type="planner-phase")
        self._transcript(400_000)
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["ctx"], 400_000)
        self.assertEqual(entry["role"], "planner")
        self.assertFalse(entry.get("handoff_fired"))

    def test_agent_tool_acknowledgment_links_the_child_to_its_parent(self):
        """A background Agent launch answers with text; the child id in it still links the parent."""
        inp = {"session_id": self.SID, "agent_id": self.AGENT, "agent_type": "expert-fable",
               "cwd": self.root, "hook_event_name": "PostToolUse", "tool_name": "Agent",
               "tool_use_id": "toolu_ack",
               "tool_input": {"subagent_type": "retriever-code", "description": "Interfaces"},
               "tool_response": {"content": [{"type": "text", "text":
                   "Async agent launched successfully. (internal metadata)\n"
                   "agentId: a9999999000000010 (internal ID - do not mention to user)"}]}}
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        child = running.agent(self.SID, "a9999999000000010")
        self.assertEqual(child["parent"], self.AGENT)
        self.assertEqual(child["parent_source"], "agent_tool")
        self.assertEqual(child["agent_type"], "retriever-code")
        self.assertEqual(self.rows("SELECT parent_run_id, parent_source, agent_type FROM agent_runs "
                                   "WHERE run_id=?", ("a9999999000000010",)),
                         [(self.AGENT, "agent_tool", "retriever-code")])

    def test_retriever_savings_flow_into_live_saved(self):
        """A returned retriever's R is charged per later expert request (design E.1)."""
        running.set_agent(self.SID, self.AGENT, {"agent_type": "expert-fable",
                                                 "role": "expert"})
        running.add_retrieval(self.SID, self.AGENT, "ret-1",
                              {"R": 40000, "returned_at": "2026-09-12T20:30:00Z",
                               "n_after": 0, "void": False})
        self._transcript(120_000)
        post_tool_use.run(self._input(), self.cfg)
        entry = running.agent(self.SID, self.AGENT)
        self.assertEqual(entry["retrievals"]["ret-1"]["n_after"], 1)
        # vanilla-net-v3: live_saved uses read only ($0.25/MTok)
        self.assertAlmostEqual(entry["saved_live_usd"], 40000 * 0.25 / 1e6, places=6)
        self.assertGreater(entry["cost_live_usd"], 0.0)


class RetrievalTest(LedgerCase):
    """SubagentStop of a retriever: kept_out_tokens and the parent's live entry."""

    SID = "retr-session"
    PARENT = "a2222222000000006"
    RETR = "a3333333000000008"

    def test_kept_out_tokens_formula(self):
        from pa.hooks.subagent_stop import kept_out_tokens

        # growth 100000, results 3/4 of the visible output, answer 500 tokens
        self.assertEqual(kept_out_tokens(100_000, 30_000, 10_000, 500), 74_500)
        self.assertEqual(kept_out_tokens(100_000, 30_000, 10_000, 500, "all_growth"), 99_500)
        self.assertEqual(kept_out_tokens(100_000, 30_000, 10_000, 500, "results_est"), 30_000)
        self.assertEqual(kept_out_tokens(100, 0, 0, 500), 0)          # never negative

    def test_retriever_stop_books_a_retrieval_and_primes_the_parent(self):
        running.set_agent(self.SID, self.PARENT, {"agent_type": "expert-fable", "role": "expert"})
        running.set_expert(self.SID, self.PARENT)
        running.set_agent(self.SID, self.RETR, {"agent_type": "retriever-code",
                                                "role": "retriever", "parent": self.PARENT})
        inp = {"session_id": self.SID, "agent_id": self.RETR, "agent_type": "retriever-code",
               "cwd": self.root, "last_assistant_message": "answer " * 50,
               "agent_transcript_path": os.path.join(
                   self.projects, "slug", self.SID, "subagents", "agent-%s.jsonl" % self.RETR)}
        self.write_transcript(inp["agent_transcript_path"], [
            usage_line("msg_r1", model="claude-haiku-4-5", inp=8000, write=0, read=0,
                       out=100, ttl="5m"),
            tool_result_line("tr-1", "z" * 200_000),
            usage_line("msg_r2", model="claude-haiku-4-5", inp=100, write=0, read=58000,
                       out=200, ttl="5m", ts="2026-09-12T20:10:00.000Z")])
        from pa.hooks import subagent_stop

        self.assertIsNone(subagent_stop.run(inp, self.cfg))
        rows = self.rows("SELECT parent_run_id, growth_tokens, kept_out_tokens, kept_out_mode, "
                         "void, answer_tokens FROM retrievals WHERE run_id=?", (self.RETR,))
        self.assertEqual(len(rows), 1)
        parent, growth, kept, mode, void, answer = rows[0]
        self.assertEqual(parent, self.PARENT)
        self.assertEqual(growth, 50_100)
        self.assertEqual(mode, "results_share")
        self.assertEqual(void, 0)
        self.assertGreater(kept, 40_000)
        self.assertGreater(answer, 0)
        live = running.agent(self.SID, self.PARENT)["retrievals"][self.RETR]
        self.assertEqual(live["R"], kept)
        self.assertEqual(live["n_after"], 0)
        self.assertFalse(live["void"])
        self.assertEqual(running.agent(self.SID, self.RETR), {})      # entry removed

    def test_meta_parent_wins_over_the_session_default(self):
        """A retriever booked against the session at start is re-parented from meta.json at stop,
        so its kept-out tokens reach the planner that spawned it."""
        from pa.hooks import subagent_stop

        running.set_agent(self.SID, self.PARENT, {"agent_type": "planner-phase", "role": "planner"})
        running.set_agent(self.SID, self.RETR, {"agent_type": "retriever-digest", "role": "retriever",
                                                "parent": self.SID, "parent_source": "main"})
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.RETR)
        inp = {"session_id": self.SID, "agent_id": self.RETR, "agent_type": "retriever-digest",
               "cwd": self.root, "last_assistant_message": "answer " * 50,
               "agent_transcript_path": path}
        self.write_transcript(path, [
            usage_line("msg_d1", model="claude-sonnet-5", inp=8000, write=0, read=0, out=100, ttl="5m"),
            tool_result_line("tr-1", "z" * 100_000),
            usage_line("msg_d2", model="claude-sonnet-5", inp=100, write=0, read=30000, out=200,
                       ttl="5m", ts="2026-09-12T20:10:00.000Z")])
        with open(path[:-6] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "retriever-digest", "parentAgentId": self.PARENT,
                       "spawnDepth": 2}, fh)
        self.assertIsNone(subagent_stop.run(inp, self.cfg))
        self.assertEqual(self.rows("SELECT parent_run_id FROM retrievals WHERE run_id=?",
                                   (self.RETR,)), [(self.PARENT,)])
        self.assertEqual(self.rows("SELECT parent_run_id, parent_source, spawn_depth FROM agent_runs "
                                   "WHERE run_id=?", (self.RETR,)), [(self.PARENT, "meta", 2)])
        self.assertIn(self.RETR, running.agent(self.SID, self.PARENT)["retrievals"])

    def test_phantom_stop_of_the_main_thread_writes_nothing(self):
        """The harness fires SubagentStop for the router's own turns: no start, no transcript."""
        from pa.hooks import subagent_stop

        inp = {"session_id": self.SID, "agent_id": "a9f9f9f9000000011", "agent_type": "pa-session",
               "cwd": self.root, "last_assistant_message": "T3 done."}
        self.assertIsNone(subagent_stop.run(inp, self.cfg))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_runs WHERE run_id=?",
                                   ("a9f9f9f9000000011",)), [(0,)])

    def test_parent_stop_orphans_its_running_children(self):
        """A retriever still marked running when its expert returns is dropped and marked orphaned."""
        from pa.hooks import subagent_stop

        running.set_agent(self.SID, self.PARENT, {"agent_type": "expert-fable", "role": "expert"})
        running.set_expert(self.SID, self.PARENT)
        running.set_agent(self.SID, self.RETR, {"agent_type": "retriever-code", "role": "retriever",
                                                "parent": self.PARENT, "status": "running"})
        running.set_agent(self.SID, "a4444444000000009", {"agent_type": "retriever-web",
                                                            "role": "retriever", "parent": self.SID,
                                                            "status": "running"})
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.PARENT)
        self.write_transcript(path, [usage_line("msg_p1", inp=2000, write=1000, read=0, out=100)])
        inp = {"session_id": self.SID, "agent_id": self.PARENT, "agent_type": "expert-fable",
               "cwd": self.root, "last_assistant_message": "STATUS: done",
               "agent_transcript_path": path}
        self.assertIsNone(subagent_stop.run(inp, self.cfg))
        self.assertEqual(running.agent(self.SID, self.RETR), {})
        self.assertEqual(self.rows("SELECT status FROM agent_runs WHERE run_id=?", (self.RETR,)),
                         [("orphaned",)])
        self.assertEqual(running.agent(self.SID, "a4444444000000009")["status"], "running")   # not its child

    def test_expert_stop_locks_measured_savings(self):
        """SubagentStop of the expert prices every avoided re-send (spec 3.9.2)."""
        from pa.hooks import subagent_stop

        conn = hooks.open_db()
        try:
            db.upsert_agent_run(conn, {"run_id": self.PARENT, "session_id": self.SID,
                                       "kind": "expert", "agent_type": "expert-fable"})
            db.upsert_retrieval(conn, {"run_id": self.RETR, "parent_run_id": self.PARENT,
                                       "kept_out_tokens": 40000, "void": 0,
                                       "returned_at": "2026-09-12T20:00:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.PARENT)
        self.write_transcript(path, [
            usage_line("msg_x1", inp=1000, write=1000, read=50_000, out=500,
                       ts="2026-09-12T20:05:00.000Z"),
            usage_line("msg_x2", inp=1000, write=1000, read=90_000, out=500,
                       ts="2026-09-12T20:10:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": self.PARENT,
                           "agent_type": "expert-fable", "cwd": self.root,
                           "last_assistant_message": "STATUS: done",
                           "agent_transcript_path": path}, self.cfg)
        rows = self.rows("SELECT measured_saved_usd, locked, measured_detail_json "
                         "FROM savings WHERE run_id=?", (self.PARENT,))
        self.assertEqual(len(rows), 1)
        usd, locked, detail_json = rows[0]
        detail = json.loads(detail_json)
        # vanilla-net-v3: gross = 40000 × write_1h + 40000 × read, minus seed carry
        self.assertAlmostEqual(detail["gross_usd"], 40000 * 20.0 / 1e6 + 40000 * 0.25 / 1e6, places=9)
        self.assertGreater(detail["seed_usd"], 0)         # the expert's own seed carry
        self.assertAlmostEqual(usd, detail["gross_usd"] - detail["seed_usd"], places=9)
        own = self.rows("SELECT kept_out_mode, void FROM retrievals WHERE run_id=?", (self.PARENT,))
        self.assertEqual(own, [("all_growth", 0)])        # the expert's own kept-out row
        self.assertEqual(locked, 1)
        self.assertEqual(detail["per_retrieval"][0]["N"], 2)
        self.assertEqual(detail["source"], "measured")

    def test_ungoverned_session_voids_retrieval_and_skips_savings(self):
        """T11: no .claude/pa.json at cwd -> retrieval void='ungoverned', no savings row."""
        from pa.hooks import subagent_stop

        ungov = os.path.join(self.dir, "ungov")
        os.makedirs(os.path.join(ungov, ".claude"))          # no pa.json
        hooks._PROJECT_ROOTS.clear()

        conn = hooks.open_db()
        try:
            db.upsert_agent_run(conn, {"run_id": self.PARENT, "session_id": self.SID,
                                       "kind": "expert", "agent_type": "expert-fable"})
            db.upsert_retrieval(conn, {"run_id": self.RETR, "parent_run_id": self.PARENT,
                                       "kept_out_tokens": 40000, "void": 0,
                                       "returned_at": "2026-09-12T20:00:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.PARENT)
        self.write_transcript(path, [
            usage_line("msg_u1", inp=1000, write=1000, read=50_000, out=500,
                       ts="2026-09-12T20:05:00.000Z"),
            usage_line("msg_u2", inp=1000, write=1000, read=90_000, out=500,
                       ts="2026-09-12T20:10:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": self.PARENT,
                           "agent_type": "expert-fable", "cwd": ungov,
                           "last_assistant_message": "STATUS: done",
                           "agent_transcript_path": path}, self.cfg)
        own = self.rows("SELECT void, kept_out_mode FROM retrievals WHERE run_id=?", (self.PARENT,))
        self.assertEqual(own, [(1, "ungoverned")])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM savings WHERE run_id=?",
                                   (self.PARENT,)), [(0,)])
        hooks._PROJECT_ROOTS.clear()

    def test_pre_install_session_voids_retrieval_and_skips_savings(self):
        """T10.1: the project's own installed_at, later than the session, voids it live."""
        from pa.hooks import subagent_stop

        proj = os.path.join(self.dir, "later_install")
        os.makedirs(os.path.join(proj, ".claude"))
        with open(os.path.join(proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "installed_at": "2026-09-20T00:00:00Z"}')
        hooks._PROJECT_ROOTS.clear()
        running.ensure_session(self.SID, started="2026-09-12T20:00:00Z")

        conn = hooks.open_db()
        try:
            db.upsert_agent_run(conn, {"run_id": self.PARENT, "session_id": self.SID,
                                       "kind": "expert", "agent_type": "expert-fable"})
            db.upsert_retrieval(conn, {"run_id": self.RETR, "parent_run_id": self.PARENT,
                                       "kept_out_tokens": 40000, "void": 0,
                                       "returned_at": "2026-09-12T20:00:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.PARENT)
        self.write_transcript(path, [
            usage_line("msg_p1", inp=1000, write=1000, read=50_000, out=500,
                       ts="2026-09-12T20:05:00.000Z"),
            usage_line("msg_p2", inp=1000, write=1000, read=90_000, out=500,
                       ts="2026-09-12T20:10:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": self.PARENT,
                           "agent_type": "expert-fable", "cwd": proj,
                           "last_assistant_message": "STATUS: done",
                           "agent_transcript_path": path}, self.cfg)
        own = self.rows("SELECT void, kept_out_mode FROM retrievals WHERE run_id=?", (self.PARENT,))
        self.assertEqual(own, [(1, "pre-install")])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM savings WHERE run_id=?",
                                   (self.PARENT,)), [(0,)])
        hooks._PROJECT_ROOTS.clear()

    def test_oversize_transcript_is_deferred(self):
        cfg = config.defaults()
        cfg["ledger"]["lock_inline_max_bytes"] = 10
        inp = {"session_id": self.SID, "agent_id": self.RETR, "agent_type": "retriever-code",
               "cwd": self.root, "last_assistant_message": "answer",
               "agent_transcript_path": os.path.join(
                   self.projects, "slug", self.SID, "subagents", "agent-%s.jsonl" % self.RETR)}
        self.write_transcript(inp["agent_transcript_path"], [usage_line("msg_r1")])
        from pa.hooks import subagent_stop

        subagent_stop.run(inp, cfg)
        self.assertEqual(self.rows("SELECT status FROM agent_runs WHERE run_id=?",
                                   (self.RETR,)), [("pending_lock",)])
        from pa import runs as runs_mod

        pending = runs_mod.pending_locks()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["run_id"], self.RETR)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM turns"), [(0,)])

    def test_nonstandard_kind_with_kept_out_tokens_gets_locked_savings(self):
        """A12: a memory-curator (or planner-gen) with kept_out_tokens > 0 gets a savings row."""
        from pa.hooks import subagent_stop

        CURATOR = "a777777700000000d"
        running.set_agent(self.SID, CURATOR, {"agent_type": "memory-curator",
                                              "role": "other"})
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % CURATOR)
        self.write_transcript(path, [
            usage_line("msg_c1", inp=3000, write=1000, read=0, out=500,
                       ts="2026-09-12T20:05:00.000Z"),
            usage_line("msg_c2", inp=500, write=500, read=20000, out=300,
                       ts="2026-09-12T20:10:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": CURATOR,
                           "agent_type": "memory-curator", "cwd": self.root,
                           "last_assistant_message": "done",
                           "agent_transcript_path": path}, self.cfg)
        # the run's own retrieval row has kept_out_tokens > 0 (all_growth mode)
        own_ret = self.rows("SELECT kept_out_tokens FROM retrievals WHERE run_id=?", (CURATOR,))
        self.assertEqual(len(own_ret), 1)
        self.assertGreater(own_ret[0][0], 0)
        # A12: a savings row must be locked
        sav = self.rows("SELECT locked FROM savings WHERE run_id=?", (CURATOR,))
        self.assertEqual(len(sav), 1)
        self.assertEqual(sav[0][0], 1)


class FollowTabTest(LedgerCase):
    """3.1 T12.1.1.1: follow_agents on -> a focused wt tab per planner/expert/critic; stop closes it."""

    SID = "follow-s"

    def setUp(self):
        super().setUp()
        os.environ["PA_WT_EXE"] = "C:/fake/wt.exe"

    def tearDown(self):
        os.environ.pop("PA_WT_EXE", None)
        super().tearDown()

    def _pa_json(self, follow):
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "hooks-test", "follow_agents": follow}, fh)

    def _start(self, agent_type, agent_id="f1111111111111111"):
        return {"session_id": self.SID, "agent_id": agent_id, "agent_type": agent_type,
                "cwd": self.root, "hook_event_name": "SubagentStart",
                "transcript_path": os.path.join(self.projects, "slug", self.SID + ".jsonl")}

    def test_off_by_default_opens_nothing(self):
        self.run_hook("subagent_start", self._start("expert-fable"))
        self.assertEqual(self.toasts, [])
        self.assertFalse(os.path.exists(paths.follow_path("f1111111111111111")))

    def test_on_opens_a_focused_tab_for_experts_only_and_stop_closes_it(self):
        self._pa_json("on")
        self.run_hook("subagent_start", self._start("retriever-code", "f2222222222222222"))
        self.run_hook("subagent_start", self._start("coder-opus46", "f3333333333333333"))
        self.assertEqual(self.toasts, [])                                  # never a leaf or a coder
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T3", "phase": "24"}, fh)
        self.run_hook("subagent_start", self._start("expert-fable"))
        self.assertEqual(len(self.toasts), 1)
        cmd, _env = self.toasts[0]
        self.assertEqual(cmd[:4], ["C:/fake/wt.exe", "-w", "0", "nt"])   # current window, new focused tab
        self.assertEqual(cmd[cmd.index("--title") + 1], "expert-fable T3")
        self.assertIn("follow", cmd)
        self.assertEqual(cmd[cmd.index("follow") + 1], "f1111111111111111")
        self.assertTrue(cmd[cmd.index("follow") - 1].endswith("pa_ledger.py"))
        self.assertTrue(os.path.exists(paths.follow_path("f1111111111111111")))
        inp = self._start("expert-fable")
        inp["hook_event_name"] = "SubagentStop"
        inp["agent_transcript_path"] = self.write_transcript(
            hooks.agent_transcript(inp), [usage_line("msg_f", inp=2000, write=1000, read=0, out=100)])
        self.run_hook("subagent_stop", inp)
        self.assertTrue(os.path.exists(paths.follow_path("f1111111111111111", "stop")))
        self.run_hook("subagent_start", self._start("critic", "f4444444444444444"))
        self.run_hook("subagent_start", self._start("planner-phase", "f5555555555555555"))
        self.assertEqual(len(self.toasts), 3)

    def test_follow_renders_like_the_subagent_view_then_exits_on_the_marker(self):
        from contextlib import redirect_stdout
        import io

        from pa import ledger_cli

        path = os.path.join(self.projects, "slug", "agent-f9.jsonl")
        self.write_transcript(path, [
            json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": "TASK: T3 seam report, per biome"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu0", "name": "Read", "input": {"file_path": "cookbook/INDEX.md"}},
                {"type": "tool_use", "id": "tu9", "name": "Agent",
                 "input": {"subagent_type": "retriever-code", "description": "Interfaces for stdin"}}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu0", "content": "# Cookbook"},
                {"type": "tool_result", "tool_use_id": "tu9", "content": [{"type": "text", "text":
                 "Async agent launched successfully. agentId: a1 (internal ID)"}]}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text":
                "<task-notification><task-id>a1</task-id><summary>Agent \"Interfaces for stdin\" finished"
                "</summary></task-notification>"}]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "The seed names Generation 2 as closed, so this is Gen 3."},
                {"type": "text", "text": "Reading the plan."},
                {"type": "tool_use", "id": "tu1", "name": "Read",
                 "input": {"file_path": "phase-ends/current/PHASE_PLAN.md"}}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "# Phase 24"}]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu2", "name": "Agent",
                 "input": {"subagent_type": "retriever-digest", "description": "Digest GenerationEnd 2"}},
                {"type": "tool_use", "id": "tu3", "name": "Bash",
                 "input": {"command": "python   tools/launch.py --dry-run"}}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu3", "is_error": True,
                 "content": "bash: python: command not found"},
                {"type": "tool_result", "tool_use_id": "tu2",
                 "content": [{"type": "text", "text": "REPORT: R3-001"}]}]}}),
        ])
        os.makedirs(os.path.dirname(paths.follow_path("f9")), exist_ok=True)
        with open(paths.follow_path("f9"), "w", encoding="utf-8") as fh:
            json.dump({"title": "planner-gen", "agent": "planner-gen"}, fh)
        with open(paths.follow_path("f9", "stop"), "w", encoding="utf-8") as fh:
            fh.write("now")
        args = ledger_cli.build_parser().parse_args(
            ["follow", "f9", "--transcript", path, "--rounds", "3", "--interval", "0.05"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = args.func(args)
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(text.startswith("planner-gen"), text)
        self.assertIn("~ The seed names Generation 2 as closed", text)
        self.assertIn("Reading the plan.", text)
        self.assertIn("  read current/PHASE_PLAN.md", text)                  # the plan keeps its dir too
        self.assertNotIn("result (", text)                          # a good result is not noise
        self.assertIn("\u25cf retriever-digest: Digest GenerationEnd 2", text)
        self.assertIn("  bash python tools/launch.py --dry-run", text)
        self.assertIn("\u2717 Bash failed: bash: python: command not found", text)
        self.assertIn("\u25cf retriever-digest returned (", text)
        self.assertIn("  \u00bb TASK: T3 seam report, per biome", text)          # the brief
        self.assertIn("  read cookbook/INDEX.md", text)                          # generic names keep their dir
        self.assertIn("\u25cf retriever-code running in the background", text)  # a launch is not a return
        self.assertNotIn("retriever-code returned", text)
        self.assertIn("\u25cf Agent \"Interfaces for stdin\" finished", text)   # the notification is
        self.assertIn("agent finished", text)
        self.assertFalse(os.path.exists(paths.follow_path("f9", "stop")))    # cleaned up

    def test_follow_never_skips_a_record_across_reads(self):
        """A chunk ending on a newline must not push the offset one byte into the next record."""
        from contextlib import redirect_stdout
        import io

        from pa import ledger_cli

        def line(i):
            return json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "line %d" % i}]}})

        path = os.path.join(self.projects, "slug", "agent-f8.jsonl")
        self.write_transcript(path, [line(1), line(2)])            # ends with a newline
        buf, tools = io.StringIO(), {}
        with redirect_stdout(buf):
            off = ledger_cli._follow_render(path, 0, tools, color=False)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line(3) + "\n" + line(4)[:10])            # a partial fourth line
            off = ledger_cli._follow_render(path, off, tools, color=False)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line(4)[10:] + "\n")
            ledger_cli._follow_render(path, off, tools, color=False)
        text = buf.getvalue()
        for i in (1, 2, 3, 4):
            self.assertIn("line %d" % i, text)
        self.assertEqual(text.count("line "), 4)


class ModelMismatchTest(LedgerCase):
    """D48: one toast per (session, agent, seen model)."""

    def test_agent_tool_mismatch_toasts_once(self):
        self.set_toast("all")                                   # model is not a waiting cause
        inp = {"session_id": "mm-1", "cwd": self.root, "tool_name": "Agent",
               "tool_use_id": "toolu_1",
               "tool_input": {"subagent_type": "expert-fable"},
               "tool_response": {"agentId": "a9999999000000010", "agentType": "expert-fable",
                                 "resolvedModel": "claude-opus-5", "status": "completed"}}
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events WHERE kind='model_mismatch'"),
                         [(1,)])
        self.assertEqual(len(self.toasts), 1)
        body = self.toasts[0][1]["PA_TOAST_BODY"]
        self.assertIn("claude-opus-5", body)
        self.assertIn("claude-fable-5-1", body)

    def test_matching_model_is_silent(self):
        inp = {"session_id": "mm-2", "cwd": self.root, "tool_name": "Agent",
               "tool_input": {"subagent_type": "retriever-code"},
               "tool_response": {"agentId": "a888888800000000e", "agentType": "retriever-code",
                                 "resolvedModel": "claude-sonnet-5-5",
                                 "status": "completed"}}
        post_tool_use.run(inp, self.cfg)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events WHERE kind='model_mismatch'"),
                         [(0,)])
        self.assertEqual(self.toasts, [])

    def test_lost_long_context_pin_counts_as_a_mismatch(self):
        self.assertTrue(post_tool_use.mismatch("claude-fable-5-1[1m]", "claude-fable-5-1"))
        self.assertFalse(post_tool_use.mismatch("claude-fable-5-1", "claude-fable-5-1[1m]"))
        self.assertFalse(post_tool_use.mismatch(None, "claude-opus-5"))


class StopToastTest(LedgerCase):
    """Design A C.2: when the Stop hook is allowed to interrupt the developer."""

    def _stop(self, message, background=None, stop_hook_active=False, cfg=None):
        from pa.hooks import stop

        inp = {"session_id": "stop-1", "cwd": self.root, "agent_type": "router",
               "last_assistant_message": message, "background_tasks": background or [],
               "stop_hook_active": stop_hook_active,
               "transcript_path": os.path.join(self.projects, "slug", "stop-1.jsonl")}
        return stop.run(inp, cfg or self.cfg)

    def test_plain_stop_toasts(self):
        self.set_toast("all")                                   # stop is not a waiting cause
        self._stop("Task T3 done, committed.")
        self.assertEqual(len(self.toasts), 1)
        self.assertIn("stop-1", "stop-1")
        self.assertIn("PA3", self.toasts[0][1]["PA_TOAST_TITLE"])

    def test_running_background_subagent_suppresses_the_toast(self):
        self._stop("Waiting for the expert...",
                   background=[{"id": "a1", "type": "subagent", "status": "running"}])
        self.assertEqual(self.toasts, [])

    def test_background_toast_can_be_configured_on(self):
        cfg = config.defaults()
        cfg["notify"]["toast_on_stop_with_background"] = True
        self.set_toast("all")
        self._stop("Waiting...", background=[{"id": "a1", "status": "running"}], cfg=cfg)
        self.assertEqual(len(self.toasts), 1)

    def test_question_always_toasts_and_writes_the_waiting_marker(self):
        self._stop("Which engine should the closer pin for Phase 36?",
                   background=[{"id": "a1", "status": "running"}])
        self.assertEqual(len(self.toasts), 1)
        marker = os.path.join(self.ledger, "state", "waiting", "stop-1.json")
        self.assertTrue(os.path.exists(marker))
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"], "question")

    def test_missing_last_message_falls_back_to_the_transcript(self):
        """A question asked while a background agent runs still counts when the field is empty."""
        path = os.path.join(self.projects, "slug", "stop-1.jsonl")
        self.write_transcript(path, [
            usage_line("msg_q0", model="claude-sonnet-5", inp=2000, write=1000, read=0, out=100),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "What's your favorite color?"}]}})])
        self._stop("", background=[{"id": "a1", "status": "running"}])
        marker = os.path.join(self.ledger, "state", "waiting", "stop-1.json")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"], "question")
        self.assertEqual(len(self.toasts), 1)

    def test_recommended_ending_counts_as_waiting(self):
        self._stop("Everything builds. Recommended: bump the cap to 12 and rerun.")
        marker = os.path.join(self.ledger, "state", "waiting", "stop-1.json")
        self.assertTrue(os.path.exists(marker))

    def test_stop_hook_active_never_toasts(self):
        self._stop("done?", stop_hook_active=True)
        self.assertEqual(self.toasts, [])

    def _project_webhook(self, url):
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "hooks-test",
                       "discord_webhook": url}, fh)

    def test_question_with_a_project_webhook_fires_toast_and_discord_together(self):
        """3.1 T4: pa.json discord_webhook set -> one detached python child toasts, then posts."""
        self._project_webhook("https://discord.example/api/webhooks/1/x")
        self._stop("Which engine should the closer pin for Phase 36?")
        self.assertEqual(len(self.toasts), 1)
        cmd, env = self.toasts[0]
        self.assertEqual(env["PA_DISCORD_WEBHOOK"], "https://discord.example/api/webhooks/1/x")
        self.assertIn("-c", cmd)                                    # the notifier child
        self.assertIn("PA_DISCORD_WEBHOOK", cmd[-1])                # which posts the webhook
        self.assertIsInstance(json.loads(env["PA_TOAST_CMD"]), list)    # and runs the toast
        self.assertIn("Which engine", env["PA_TOAST_BODY"])
        marker = os.path.join(self.ledger, "state", "waiting", "stop-1.json")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"], "question")

    def test_question_without_a_webhook_toasts_only(self):
        """The installed default: pa.json discord_webhook null (deferred, Phase 3.1)."""
        self._stop("Which engine should the closer pin for Phase 36?")
        self.assertEqual(len(self.toasts), 1)
        cmd, env = self.toasts[0]
        self.assertNotIn("PA_DISCORD_WEBHOOK", env)
        self.assertNotIn("-c", cmd)

    def test_replan_newer_than_the_session_counts_as_waiting(self):
        running.ensure_session("stop-1", started="2000-01-01T00:00:00Z")
        cur = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(cur, exist_ok=True)
        with open(os.path.join(cur, "REPLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# replan\n")
        self._stop("Phase blocked, wrote REPLAN.")
        marker = os.path.join(self.ledger, "state", "waiting", "stop-1.json")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"], "REPLAN.md")

    def test_main_run_books_its_savings_at_stop(self):
        """The router's own requests avoid every finished agent's tokens (the rule of 3.1)."""
        conn = hooks.open_db()
        try:
            db.upsert_retrieval(conn, {"run_id": "ret-m", "parent_run_id": "stop-1",
                                       "kept_out_tokens": 50000, "void": 0,
                                       "returned_at": "2026-09-12T19:30:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        self.write_transcript(os.path.join(self.projects, "slug", "stop-1.jsonl"),
                              [usage_line("msg_m1", inp=3000, write=1000, read=0, out=100,
                                          ts="2026-09-12T20:00:00.000Z")])
        self._stop("T3 done.")
        rows = self.rows("SELECT measured_saved_usd, locked FROM savings WHERE run_id='stop-1'")
        self.assertEqual(len(rows), 1)
        usd, locked = rows[0]
        self.assertAlmostEqual(usd, 50000 * 20.0 / 1e6, places=9)            # first request: cold; was 12.5 (5m): ttl-cold-v2, a 1h turn
        self.assertEqual(locked, 0)                                          # grows at every Stop

    def test_main_run_pre_install_skips_savings(self):
        """T10.1: the project's own installed_at, later than the session, skips the booking."""
        from pa.hooks import stop

        proj = os.path.join(self.dir, "later_install_main")
        os.makedirs(os.path.join(proj, ".claude"))
        with open(os.path.join(proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "installed_at": "2026-09-20T00:00:00Z"}')
        hooks._PROJECT_ROOTS.clear()
        running.ensure_session("stop-2", started="2026-09-12T20:00:00Z")

        conn = hooks.open_db()
        try:
            db.upsert_retrieval(conn, {"run_id": "ret-p", "parent_run_id": "stop-2",
                                       "kept_out_tokens": 50000, "void": 0,
                                       "returned_at": "2026-09-12T19:30:00Z",
                                       "status": "completed"})
        finally:
            db.close(conn)
        self.write_transcript(os.path.join(self.projects, "slug", "stop-2.jsonl"),
                              [usage_line("msg_m2", inp=3000, write=1000, read=0, out=100,
                                          ts="2026-09-12T20:00:00.000Z")])
        inp = {"session_id": "stop-2", "cwd": proj, "agent_type": "router",
               "last_assistant_message": "T3 done.", "background_tasks": [],
               "stop_hook_active": False,
               "transcript_path": os.path.join(self.projects, "slug", "stop-2.jsonl")}
        stop.run(inp, self.cfg)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM savings WHERE run_id='stop-2'"), [(0,)])
        hooks._PROJECT_ROOTS.clear()

    def test_session_cost_is_stamped_from_the_spool_sample(self):
        from pa import fsutil

        fsutil.append_line(paths.spool_path("stop-1"), json.dumps(
            {"t": "sample", "ts": "2026-09-12T20:00:00Z", "session_id": "stop-1",
             "account": ACCOUNT, "session_cost": 4.25,
             "windows": {"five_hour": {"pct": 12.0, "resets_at": 1789000000}}}))
        self._stop("done")
        # T30: cost_usd is the turns sum; the statusline figure is the harness cross-check
        turns_sum = self.rows("SELECT COALESCE(SUM(cost_usd),0) FROM turns "
                              "WHERE kind='api' AND session_id='stop-1'")[0][0]
        self.assertEqual(self.rows("SELECT cost_usd, cost_source, harness_cost_usd FROM sessions "
                                   "WHERE session_id='stop-1'"), [(turns_sum, "turns", 4.25)])
        self.assertEqual(self.rows("SELECT window, pct FROM utilization"),
                         [("five_hour", 12.0)])
        self.assertFalse(os.path.exists(paths.spool_path("stop-1")))


class ToastPolicyTest(LedgerCase):
    """3.9.5 T2: pa.json ``toast: waiting|all|off``, one cause per site, one events row per decision."""

    SID = "toast-1"

    def _base(self, event):
        return {"session_id": self.SID, "cwd": self.root, "prompt_id": "p-1",
                "permission_mode": "acceptEdits", "hook_event_name": event,
                "transcript_path": os.path.join(self.projects, "slug", self.SID + ".jsonl")}

    def _stop(self, message):
        inp = self._base("Stop")
        inp.update({"agent_type": "router", "stop_hook_active": False,
                    "last_assistant_message": message, "background_tasks": [],
                    "session_crons": []})
        return self.run_hook("stop", inp)

    def _notification(self, ntype, message="Claude is waiting for your input"):
        inp = self._base("Notification")
        inp.update({"notification_type": ntype, "title": "Claude Code", "message": message})
        return self.run_hook("notification", inp)

    def _subagent_stop(self, agent_type, agent_id, message):
        inp = self._base("SubagentStop")
        inp.update({"agent_id": agent_id, "agent_type": agent_type, "stop_hook_active": False,
                    "last_assistant_message": message, "background_tasks": [],
                    "session_crons": []})
        inp["agent_transcript_path"] = self.write_transcript(
            hooks.agent_transcript(inp, agent_id), [usage_line("msg_" + agent_id, out=100)])
        return self.run_hook("subagent_stop", inp)

    def _last(self):
        rows = self.toast_rows()
        self.assertTrue(rows, "every decision writes a toast row")
        return rows[-1]

    def test_plain_stop_under_waiting_is_recorded_not_sent(self):
        self._stop("Task T3 done, committed.")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("stop", "waiting", 0))
        self.assertEqual(self.toasts, [])

    def test_question_toast_and_marker_land_before_the_ledger_block(self):
        """3.9.5 T6: the watchdog ate a close toast; the ledger block now runs after the toast."""
        from pa.hooks import stop

        def boom(*a, **k):
            raise RuntimeError("ledger past the budget")
        with mock.patch.object(stop, "_ingest", boom):
            with self.assertRaises(RuntimeError):
                self._stop("Which engine should the closer pin for Phase 36?")
        self.assertEqual(self._last()["cause"], "question")
        marker = os.path.join(self.ledger, "state", "waiting", self.SID + ".json")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["reason"], "question")

    def test_question_under_waiting_is_sent(self):
        self._stop("Which engine should the closer pin for Phase 36?")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("question", "waiting", 1))
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(self.rows("SELECT session_id, run_id FROM events WHERE kind='toast'"),
                         [(self.SID, self.SID)])

    def test_review_file_is_cause_review(self):
        cur = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(cur, exist_ok=True)
        with open(os.path.join(cur, "REVIEW.md"), "w", encoding="utf-8") as fh:
            fh.write("# review\n")
        self._stop("T4 paused for review.")
        row = self._last()
        self.assertEqual((row["cause"], row["sent"]), ("review", 1))

    def test_empty_and_dot_never_send_at_any_level(self):
        for level in ("off", "waiting", "all"):
            for message in ("", "   ", "."):
                with self.subTest(level=level, message=message):
                    self.set_toast(level)
                    self._stop(message)
                    row = self._last()
                    self.assertEqual((row["cause"], row["level"], row["sent"]),
                                     ("empty", level, 0))
        self.assertEqual(self.toasts, [])

    def test_idle_prompt_waiting_not_sent_all_sent(self):
        self._notification("idle_prompt")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("idle", "waiting", 0))
        self.assertEqual(row["notification_type"], "idle_prompt")
        self.set_toast("all")
        self._notification("idle_prompt")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("idle", "all", 1))
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(len(self.toast_rows()), 2)             # the only events row per call

    def test_notification_causes(self):
        for ntype, cause in (("permission_prompt", "permission"), ("agent_needs_input", "input"),
                             ("elicitation_dialog", "input"), ("auth_success", "other")):
            with self.subTest(ntype=ntype):
                self._notification(ntype)
                row = self._last()
                self.assertEqual(row["cause"], cause)
                self.assertEqual(row["sent"], 0 if cause == "other" else 1)

    def test_idle_prompt_during_a_discussion_is_cause_discussion(self):
        with open(os.path.join(self.root, ".run", "DISCUSSION"), "w", encoding="utf-8") as fh:
            fh.write("on\n")
        self._notification("idle_prompt")
        row = self._last()
        self.assertEqual((row["cause"], row["sent"]), ("discussion", 1))

    def test_stop_failure_under_waiting_is_crash_sent(self):
        inp = self._base("StopFailure")
        inp.update({"error": "overloaded", "error_details": "529",
                    "last_assistant_message": "working"})
        self.run_hook("stop_failure", inp)
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("crash", "waiting", 1))
        self.assertEqual(len(self.toasts), 1)

    def test_model_under_waiting_is_not_sent(self):
        inp = self._base("PostModelSwitch")
        inp.update({"agent_type": "expert-fable", "from_model": "claude-fable-5-1",
                    "to_model": "claude-opus-5", "requested_model": "claude-fable-5-1",
                    "source": "fallback"})
        self.run_hook("model_switch", inp)
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("model", "waiting", 0))
        self.assertEqual(self.toasts, [])

    def test_true_is_all_and_false_is_off(self):
        self.set_toast(True)
        self._stop("Task T3 done, committed.")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("stop", "all", 1))
        self.set_toast(False)
        self._stop("Which engine should the closer pin?")
        row = self._last()
        self.assertEqual((row["cause"], row["level"], row["sent"]), ("question", "off", 0))
        self.assertEqual(len(self.toasts), 1)

    def test_discuss_subagent_stop_toasts_discussion(self):
        self._subagent_stop("discuss", "d1111111111111111", "Here is the trade-off. Proceed?")
        row = self._last()
        self.assertEqual((row["cause"], row["sent"]), ("discussion", 1))
        self.assertIn("discussion turn ended", row["title"])
        self.assertIn("Proceed?", self.toasts[0][1]["PA_TOAST_BODY"])

    def test_discuss_max_subagent_stop_toasts_discussion(self):
        self._subagent_stop("discuss-max", "d2222222222222222", "Max-effort trade-off. Proceed?")
        row = self._last()
        self.assertEqual((row["cause"], row["sent"]), ("discussion", 1))

    def test_coder_subagent_stop_writes_no_toast_event(self):
        self._subagent_stop("coder-opus55", "c1111111111111111", "STATUS: done")
        self.assertEqual(self.toast_rows(), [])
        self.assertEqual(self.toasts, [])


class StopAccountRefreshTest(LedgerCase):
    """T8.c3: the Stop hook re-checks the credentials key each turn."""

    def _stamp(self, sid, email, key="100:200"):
        from pa import fsutil
        stamp = {"session_id": sid, "account": email, "source": "auth",
                 "cred_key": key, "ts": "2026-09-12T20:00:00Z"}
        p = os.path.join(self.ledger, "state", "accounts", "%s.json" % sid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fsutil.atomic_write_json(p, stamp, indent=1)

    def _stop_with_transcript(self, sid, email_after, key_after):
        """Run a Stop whose credentials key has changed."""
        from pa.hooks import stop

        path = os.path.join(self.projects, "slug", "%s.jsonl" % sid)
        self.write_transcript(path, [
            usage_line("msg_a1", model="claude-sonnet-5", inp=1000, write=0,
                       read=0, out=100)])
        # monkeypatch credentials_key and auth_status for the switch
        accounts.credentials_key = lambda: key_after
        accounts.auth_status = lambda *a, **k: {"email": email_after, "source": "auth"}
        inp = {"session_id": sid, "cwd": self.root, "agent_type": "router",
               "last_assistant_message": "done", "background_tasks": [],
               "stop_hook_active": False, "transcript_path": path}
        stop.run(inp, self.cfg)

    def setUp(self):
        super().setUp()
        self._orig_cred_key = accounts.credentials_key

    def tearDown(self):
        accounts.credentials_key = self._orig_cred_key
        super().tearDown()

    def test_stop_after_account_switch_books_to_new_account(self):
        sid = "switch-1"
        old_email = "old@example.com"
        new_email = "new@example.com"
        running.ensure_session(sid, account=old_email)
        self._stamp(sid, old_email, key="100:200")
        self._stop_with_transcript(sid, new_email, key_after="999:200")
        # turns booked to the new account
        turns = self.rows("SELECT account FROM turns WHERE session_id=?", (sid,))
        self.assertTrue(turns)
        self.assertEqual(turns[0][0], new_email)
        # sessions.account updated
        sess = self.rows("SELECT account FROM sessions WHERE session_id=?", (sid,))
        self.assertEqual(sess[0][0], new_email)
        # one account_change event
        evts = self.rows("SELECT kind FROM events WHERE kind='account_change' "
                         "AND session_id=?", (sid,))
        self.assertEqual(len(evts), 1)

    def test_unchanged_key_calls_no_subprocess(self):
        sid = "switch-2"
        email = "same@example.com"
        running.ensure_session(sid, account=email)
        self._stamp(sid, email, key="100:200")
        # credentials_key returns the same key as the stamp
        accounts.credentials_key = lambda: "100:200"
        # auth_status must NOT be called -- track with a flag
        auth_called = []
        def _spy(*a, **k):
            auth_called.append(True)
            return {"email": email, "source": "auth"}
        accounts.auth_status = _spy
        from pa.hooks import stop

        path = os.path.join(self.projects, "slug", "%s.jsonl" % sid)
        self.write_transcript(path, [
            usage_line("msg_b1", model="claude-sonnet-5", inp=1000, write=0,
                       read=0, out=100)])
        inp = {"session_id": sid, "cwd": self.root, "agent_type": "router",
               "last_assistant_message": "ok", "background_tasks": [],
               "stop_hook_active": False, "transcript_path": path}
        stop.run(inp, self.cfg)
        self.assertFalse(auth_called, "auth_status should not be called when key unchanged")
        turns = self.rows("SELECT account FROM turns WHERE session_id=?", (sid,))
        self.assertTrue(turns)
        self.assertEqual(turns[0][0], email)


class SessionEndTest(LedgerCase):
    """SessionEnd: the turns sum is the cost, cost-state the harness cross-check (T30); the gap
    becomes one residual turn (recon V2)."""

    SID = "end-1"

    def _transcript(self):
        path = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        return self.write_transcript(path, [
            usage_line("msg_e1", model="claude-sonnet-5", inp=1000, write=2000, read=0,
                       out=500, ttl="1h"),
            json.dumps({"type": "cost-state", "timestamp": "2026-09-12T20:05:00.000Z",
                        "totalCostUSD": 2.5,
                        "modelUsage": {"claude-sonnet-5": {"costUSD": 2.5}}}),
        ])

    def test_cost_state_total_and_residual(self):
        from pa.hooks import session_end

        path = self._transcript()
        self.assertIsNone(session_end.run(
            {"session_id": self.SID, "cwd": self.root, "reason": "clear",
             "transcript_path": path}, self.cfg))
        api = self.rows("SELECT COALESCE(SUM(cost_usd),0) FROM turns "
                        "WHERE kind='api' AND session_id=?", (self.SID,))[0][0]
        rows = self.rows("SELECT cost_usd, cost_source, harness_cost_usd, end_reason FROM sessions "
                         "WHERE session_id=?", (self.SID,))
        self.assertEqual(rows, [(api, "turns", 2.5, "clear")])
        residual = self.rows("SELECT cost_usd FROM turns WHERE kind='residual' "
                             "AND session_id=?", (self.SID,))
        self.assertEqual(len(residual), 1)
        self.assertAlmostEqual(residual[0][0] + api, 2.5, places=6)
        self.assertEqual(self.rows("SELECT locked FROM agent_runs WHERE run_id=?",
                                   (self.SID,)), [(1,)])
        self.assertNotIn(self.SID, running.load().get("sessions", {}))

    def test_pending_lock_is_finished_when_it_fits(self):
        from pa import runs as runs_mod
        from pa.hooks import session_end

        agent_path = self.write_transcript(
            os.path.join(self.projects, "slug", self.SID, "subagents", "agent-a44.jsonl"),
            [usage_line("msg_p1", model="claude-haiku-4-5", inp=100, write=0, read=0,
                        out=10, ttl="5m")])
        runs_mod.defer_lock({"run_id": "a44", "session_id": self.SID, "path": agent_path,
                             "role": "retriever"})
        session_end.run({"session_id": self.SID, "cwd": self.root, "reason": "other",
                         "transcript_path": self._transcript()}, self.cfg)
        self.assertEqual(runs_mod.pending_locks(), [])
        self.assertEqual(self.rows("SELECT locked, lock_source FROM agent_runs "
                                   "WHERE run_id='a44'"), [(1, "pending")])

    def test_keeps_run_id_and_stamps_killed_at(self):
        """T7.1: the harness kills every subagent with the process -- keep the run's id so the
        seed can offer a resume-by-id instead of a respawn."""
        from pa.hooks import session_end

        status_path = os.path.join(self.root, ".run", "status.json")
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump({"task": "T3.1", "run_id": "a1b2c3d4000000005", "attempt": 1,
                      "expert_agent_type": "expert-fable", "kind": "task"}, fh)
        session_end.run({"session_id": self.SID, "cwd": self.root,
                         "reason": "prompt_input_exit"}, self.cfg)
        with open(status_path, encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(st["run_id"], "a1b2c3d4000000005")     # not nulled
        self.assertEqual(st["task"], "T3.1")
        self.assertEqual(st["attempt"], 1)
        self.assertEqual(st["expert_agent_type"], "expert-fable")
        self.assertEqual(st["killed_reason"], "prompt_input_exit")
        self.assertTrue(st.get("killed_at"))


class SessionStartResumeTest(LedgerCase):
    """T7.1: a resume clears the previous session's end so its cost keeps accumulating."""

    SID = "resume-1"

    def test_resume_clears_ended_and_end_reason_and_logs_event(self):
        from pa.hooks import session_start

        conn = hooks.open_db()
        try:
            db.upsert_session(conn, {"session_id": self.SID, "account": ACCOUNT,
                                     "started": hooks.now_iso(), "ended": hooks.now_iso(),
                                     "end_reason": "prompt_input_exit"})
        finally:
            db.close(conn)
        session_start.run({"session_id": self.SID, "cwd": self.root, "source": "resume",
                           "transcript_path": os.path.join(self.projects, "slug",
                                                           self.SID + ".jsonl")}, self.cfg)
        rows = self.rows("SELECT ended, end_reason FROM sessions WHERE session_id=?", (self.SID,))
        self.assertEqual(rows, [(None, None)])
        events = self.rows("SELECT detail_json FROM events WHERE session_id=? AND kind='resumed'",
                           (self.SID,))
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0][0])["end_reason"], "prompt_input_exit")


class WarmerHooksTest(LedgerCase):
    """3.9.5 T4: the four hooks drive the warmer, fed the harness's real payloads (probe1, C0025)."""

    def _wfile(self, ext):
        return os.path.join(self.root, ".run", "warmer", "%s.%s" % (PROBE1_SID, ext))

    def _runs(self):
        with open(self._wfile("runs"), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _replay(self):
        for event, inp in read_blocks("probe1.log"):
            if event == "subagent_stop":
                target = self.rewrite(inp).get("agent_transcript_path")
                if target and not os.path.exists(target):
                    self.write_transcript(target, [usage_line("msg_w1", model="claude-haiku-4-5",
                                                              inp=100, out=4, ttl="5m")])
            self.run_hook(event, inp)

    def test_probe_session_spawns_adds_ends_and_stops(self):
        os.environ["CLAUDE_PID"] = "4242"
        try:
            self._replay()
        finally:
            os.environ.pop("CLAUDE_PID", None)
        self.assertEqual(len(self.warmers), 2)             # SessionStart, SubagentStart: no pid file yet
        self.assertEqual(self.toasts, [])
        cmd, env = self.warmers[0]
        self.assertEqual(cmd[cmd.index("-m") + 1], "pa.warmer")
        self.assertNotIn("-I", cmd)                         # -I would drop PYTHONPATH
        self.assertEqual(cmd[cmd.index("--project") + 1], self.root)
        self.assertEqual(cmd[cmd.index("--session") + 1], PROBE1_SID)
        self.assertEqual(cmd[cmd.index("--pid") + 1], "4242")
        self.assertTrue(os.path.isdir(os.path.join(env["PYTHONPATH"], "pa")))
        runs = self._runs()
        self.assertEqual(runs[0]["add"], PROBE1_AGENT)
        self.assertEqual(runs[0]["agent_type"], "probe-leaf")
        self.assertIn("role", runs[0])
        self.assertEqual(runs[-1], {"end": PROBE1_AGENT})
        self.assertTrue(os.path.exists(self._wfile("stop")))   # SessionEnd
        self.assertFalse(os.path.exists(self._wfile("pid")))

    def test_a_live_pid_file_means_no_second_daemon(self):
        start = [inp for ev, inp in read_blocks("probe1.log") if ev == "session_start"][0]
        os.makedirs(os.path.dirname(self._wfile("pid")))
        with open(self._wfile("pid"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        self.run_hook("session_start", start)
        self.assertEqual(self.warmers, [])

    def test_warmer_off_in_pa_json_spawns_nothing(self):
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "hooks-test", "warmer": "off"}, fh)
        start = [inp for ev, inp in read_blocks("probe1.log") if ev == "session_start"][0]
        self.run_hook("session_start", start)
        self.assertEqual(self.warmers, [])

    def test_pa_warmer_off_env_never_reaches_spawn_detached(self):
        """3.9.5 T8: ``PA_WARMER_OFF=1`` (the tests' switch) stops SessionStart and SubagentStart."""
        os.environ["PA_WARMER_OFF"] = "1"
        try:
            with mock.patch.object(notify, "spawn_detached") as spawn:
                for event, inp in read_blocks("probe1.log"):
                    if event in ("session_start", "subagent_start"):
                        self.run_hook(event, inp)
        finally:
            os.environ.pop("PA_WARMER_OFF", None)
        spawn.assert_not_called()
        self.assertEqual(self.warmers, [])
        self.assertEqual(self._runs()[0]["add"], PROBE1_AGENT)     # the runs line is still written


class SummaryTest(LedgerCase):
    """summary.json: the v0 window block the statusline reads (design B.3)."""

    def test_windows_block_shape(self):
        from pa import summary

        conn = hooks.open_db()
        try:
            db.upsert_account(conn, {"email": ACCOUNT, "label": "win-main"})
            for ts, pct, cost in (("2026-09-12T18:00:00Z", 5.0, 10.0),
                                  ("2026-09-12T19:00:00Z", 9.0, 30.0)):
                db.insert_utilization(conn, {"ts": ts, "account": ACCOUNT, "machine": "win",
                                             "session_id": "s-win", "window": "five_hour",
                                             "pct": pct, "resets_at": 1789000000,
                                             "session_cost": cost, "source": "statusline",
                                             "reason": "change"})
            summary.rebuild(conn, self.cfg)
        finally:
            db.close(conn)
        with open(os.path.join(self.ledger, "summary.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        win = doc["accounts"][ACCOUNT]["windows"]["five_hour"]
        self.assertEqual(win["pct_last"], 9.0)
        self.assertEqual(win["pct_ts"], "2026-09-12T19:00:00Z")
        self.assertEqual(win["resets_at"], 1789000000)
        self.assertEqual(win["started_at"], 1789000000 - 18000)
        self.assertEqual(win["cost_in_window"], 30.0)
        self.assertIsNone(win["pct_per_dollar"])
        self.assertEqual(win["fit_quality"], "none")
        self.assertEqual(doc["accounts"][ACCOUNT]["label"], "win-main")
        self.assertIn("start", doc["accounts"][ACCOUNT]["period"])

    def test_sessions_only_rebuild_keeps_the_windows_block(self):
        from pa import summary

        conn = hooks.open_db()
        try:
            db.upsert_session(conn, {"session_id": "s1", "account": ACCOUNT,
                                     "started": hooks.now_iso(), "cost_usd": 1.0})
            summary.rebuild(conn, self.cfg)
            summary.write(dict(summary.read(), accounts={"marker": {"windows": {}}}))
            summary.rebuild(conn, self.cfg, sessions_only=True)
        finally:
            db.close(conn)
        doc = summary.read()
        self.assertIn("marker", doc["accounts"])
        self.assertIn("s1", doc["sessions"])


class DetachedRebuildTest(LedgerCase):
    """3.9.6 T5: the three closing hooks request one detached single-flight rebuild."""

    EVENTS = ("subagent_stop", "stop", "session_end")

    def setUp(self):
        super().setUp()
        ProbeReplayTest.replay(self, "probe1.log")           # a populated ledger (rebuilds inline)
        self.blocks = dict((e, inp) for e, inp in read_blocks("probe1.log") if e in self.EVENTS)
        self.lock, self.dirty = summary._rebuild_files()

    def _clear(self):
        for p in (self.lock, self.dirty):
            if os.path.exists(p):
                os.remove(p)
        del self.rebuilds[:]

    def test_each_hook_spawns_one_rebuild_then_marks_dirty_while_held(self):
        self.run_rebuilds = False                            # the stub records; the lock stays held
        for event in self.EVENTS:
            with self.subTest(hook=event):
                self._clear()
                self.run_hook(event, self.blocks[event])
                self.assertEqual(len(self.rebuilds), 1)
                cmd = self.rebuilds[0][0]
                self.assertEqual(cmd[cmd.index("pa.summary") + 1:][0], "--rebuild")
                self.assertEqual("--sessions-only" in cmd, event == "stop")
                self.assertTrue(os.path.exists(self.lock))
                self.assertFalse(os.path.exists(self.dirty))
                self.run_hook(event, self.blocks[event])
                self.assertEqual(len(self.rebuilds), 1)          # held: none spawned
                self.assertTrue(os.path.exists(self.dirty))
        self.assertFalse(any("pa.summary" in c for c, _e in self.toasts))

    def test_full_request_wins_the_dirty_stamp(self):
        self.run_rebuilds = False
        self._clear()
        self.assertTrue(summary.request_rebuild(self.cfg, sessions_only=True))
        self.assertFalse(summary.request_rebuild(self.cfg))
        self.assertFalse(summary.request_rebuild(self.cfg, sessions_only=True))
        with open(self.dirty, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "full")

    def test_stale_lock_is_taken_over(self):
        self.run_rebuilds = False
        dead = 999999
        from pa import warmer

        self.assertFalse(warmer.pid_alive(dead))
        for content, age in (("%d rebuild\n" % dead, 0), ("%d rebuild\n" % os.getpid(), 700)):
            with self.subTest(lock=content.strip(), age=age):
                self._clear()
                with open(self.lock, "w", encoding="utf-8") as fh:
                    fh.write(content)
                if age:
                    old = time.time() - age
                    os.utime(self.lock, (old, old))
                self.assertTrue(summary.request_rebuild(self.cfg))
                self.assertEqual(len(self.rebuilds), 1)
                self.assertFalse(os.path.exists(self.dirty))

    def test_child_reruns_while_dirty_and_releases_the_lock(self):
        self._clear()
        summary.request_rebuild(self.cfg, sessions_only=True)   # inline: runs and releases
        self.assertEqual(len(self.rebuilds), 1)
        self.assertFalse(os.path.exists(self.lock))
        with open(self.dirty, "w", encoding="utf-8") as fh:
            fh.write("full\n")
        summary.run_rebuild(sessions_only=True)
        self.assertFalse(os.path.exists(self.dirty))
        self.assertFalse(os.path.exists(self.lock))

    def test_rebuild_entry_matches_inline_rebuild(self):
        self._clear()
        cfg = config.load()
        conn = hooks.open_db()
        try:
            summary.rebuild(conn, cfg)
        finally:
            db.close(conn)
        inline = summary.read()
        os.remove(paths.summary_path())
        summary.main(["--rebuild"])
        entry = summary.read()
        for doc in (inline, entry):
            doc.pop("updated", None)                         # the one volatile field (_now_iso)
        self.assertTrue(entry.get("sessions"))
        self.assertEqual(entry, inline)
        self.assertFalse(os.path.exists(self.lock))

    def test_hot_path_module_imports(self):
        import ast

        allowed = {"json", "os", "sys", "time"}
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("pa/summary.py", "pa/hooks/stop.py", "pa/hooks/subagent_stop.py",
                    "pa/hooks/session_end.py"):
            with open(os.path.join(pkg, *rel.split("/")), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            found, todo = set(), list(tree.body)
            while todo:                                      # module level, incl. if/try, never defs
                node = todo.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(node, ast.Import):
                    found.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    found.add((node.module or "").split(".")[0])
                todo.extend(ast.iter_child_nodes(node))
            with self.subTest(module=rel):
                self.assertLessEqual(found, allowed, rel)


class DoctorHookWarnTest(unittest.TestCase):
    """3.9.6 T5: doctor WARNs when a hook's run() on the fixture exceeds ``doctor.hook_warn_ms``."""

    def test_slow_hook_warns_with_event_and_ms(self):
        from pa import ledger_cli
        from pa.hooks import stop as stop_hook

        def slow(inp, cfg):
            time.sleep(0.12)

        doc = ledger_cli._Doctor()
        fixture = os.path.join(FIXTURES, "probe1.log")
        with mock.patch.object(ledger_cli, "HOOK_EVENTS", (("Stop", "stop"),)), \
                mock.patch.object(stop_hook, "run", slow):
            ledger_cli._doctor_hooks_latency(doc, fixture, {"doctor": {"hook_warn_ms": 50}})
        warns = [ln for ln in doc.lines if ln.startswith("WARN")]
        self.assertEqual(len(warns), 1, doc.lines)
        self.assertRegex(warns[0], r"^WARN hook Stop: run\(\) \d+ ms > 50 ms$")
        self.assertEqual(doc.warns, 1)

    def test_hook_payload_carries_pa_probe(self):
        """T8.c1: the hook sees ``pa_probe`` on a copy; the fixture payload is not mutated."""
        from pa import ledger_cli
        from pa.hooks import stop as stop_hook

        seen, events = [], ledger_cli.probe_events(os.path.join(FIXTURES, "probe1.log"))
        doc = ledger_cli._Doctor()
        with mock.patch.object(ledger_cli, "HOOK_EVENTS", (("Stop", "stop"),)), \
                mock.patch.object(ledger_cli, "probe_events", return_value=events), \
                mock.patch.object(stop_hook, "run", lambda inp, cfg: seen.append(inp)):
            ledger_cli._doctor_hooks_latency(doc, os.path.join(FIXTURES, "probe1.log"))
        self.assertEqual(len(seen), 1, doc.lines)
        self.assertIs(seen[0].get("pa_probe"), True)
        self.assertTrue(all("pa_probe" not in p for _n, p in events if isinstance(p, dict)))


class RefreshGrownRunsTest(LedgerCase):
    """T1.c2: a run's last request is picked up when its transcript grows after lock."""

    SID = "refresh-s"
    AGENT = "a777777700000000d"

    def _lock_and_grow(self):
        """Lock a subagent, then append one more usage line to its transcript."""
        from pa.hooks import subagent_stop

        running.set_agent(self.SID, self.AGENT, {"agent_type": "expert-fable",
                                                  "role": "expert"})
        path = os.path.join(self.projects, "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % self.AGENT)
        self.write_transcript(path, [
            usage_line("msg_r1", inp=1000, write=1000, read=0, out=500,
                       ts="2026-09-12T20:00:00.000Z"),
            usage_line("msg_r2", inp=1000, write=0, read=2000, out=500,
                       ts="2026-09-12T20:01:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": self.AGENT,
                           "agent_type": "expert-fable", "cwd": self.root,
                           "last_assistant_message": "STATUS: done",
                           "agent_transcript_path": path}, self.cfg)
        # check initial state
        row = self.rows("SELECT turns, locked, lock_source, transcript_bytes "
                        "FROM agent_runs WHERE run_id=?", (self.AGENT,))
        self.assertEqual(row[0][0], 2)                       # 2 turns
        self.assertEqual(row[0][1], 1)                       # locked
        old_bytes = row[0][3]
        # now append one more usage line (the one SubagentStop missed)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(usage_line("msg_r3", inp=1000, write=0, read=3000, out=500,
                                ts="2026-09-12T20:02:00.000Z") + "\n")
        return path, old_bytes

    def test_stop_refreshes_a_grown_subagent(self):
        """The parent's Stop picks up the extra request and re-locks."""
        from pa.hooks import stop

        path, old_bytes = self._lock_and_grow()
        # write a main transcript for Stop
        main_path = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        self.write_transcript(main_path, [
            usage_line("msg_m1", inp=2000, write=1000, read=0, out=200)])
        stop.run({"session_id": self.SID, "cwd": self.root, "agent_type": "router",
                  "last_assistant_message": "done",
                  "transcript_path": main_path}, self.cfg)
        row = self.rows("SELECT turns, locked, lock_source, transcript_bytes, cost_usd "
                        "FROM agent_runs WHERE run_id=?", (self.AGENT,))
        self.assertEqual(row[0][0], 3)                       # now 3 turns
        self.assertEqual(row[0][1], 1)                       # still locked
        self.assertEqual(row[0][2], "refresh")               # lock_source updated
        self.assertGreater(row[0][3], old_bytes)              # transcript_bytes grew
        # savings row still locked
        sav = self.rows("SELECT locked FROM savings WHERE run_id=?", (self.AGENT,))
        if sav:
            self.assertEqual(sav[0][0], 1)

    def test_session_end_refreshes_a_grown_subagent(self):
        """SessionEnd also picks up the extra request."""
        from pa.hooks import session_end

        path, old_bytes = self._lock_and_grow()
        main_path = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        self.write_transcript(main_path, [
            usage_line("msg_m1", inp=2000, write=1000, read=0, out=200)])
        session_end.run({"session_id": self.SID, "cwd": self.root, "reason": "other",
                         "transcript_path": main_path}, self.cfg)
        row = self.rows("SELECT turns, locked, lock_source, transcript_bytes "
                        "FROM agent_runs WHERE run_id=?", (self.AGENT,))
        self.assertEqual(row[0][0], 3)                       # 3 turns after refresh
        self.assertEqual(row[0][2], "refresh")

    def test_interrupted_run_booked_at_session_end(self):
        """A run left 'running' with a transcript -> interrupted with its turns booked."""
        from pa.hooks import session_end

        # open a run that never got SubagentStop
        conn = hooks.open_db()
        agent_path = os.path.join(self.projects, "slug", self.SID, "subagents",
                                  "agent-%s.jsonl" % self.AGENT)
        try:
            db.upsert_agent_run(conn, {"run_id": self.AGENT, "session_id": self.SID,
                                       "status": "running", "kind": "expert",
                                       "transcript_path": agent_path})
        finally:
            db.close(conn)
        self.write_transcript(agent_path, [
            usage_line("msg_i1", inp=1000, write=1000, read=0, out=500,
                       ts="2026-09-12T20:00:00.000Z"),
            usage_line("msg_i2", inp=1000, write=0, read=2000, out=500,
                       ts="2026-09-12T20:01:00.000Z")])
        main_path = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        self.write_transcript(main_path, [
            usage_line("msg_m1", inp=2000, write=1000, read=0, out=200)])
        session_end.run({"session_id": self.SID, "cwd": self.root, "reason": "other",
                         "transcript_path": main_path}, self.cfg)
        row = self.rows("SELECT status, turns, locked FROM agent_runs WHERE run_id=?",
                        (self.AGENT,))
        self.assertEqual(row[0][0], "interrupted")
        self.assertEqual(row[0][1], 2)                       # turns booked
        self.assertEqual(row[0][2], 1)                       # locked from the transcript

    def test_interrupted_run_relocked_completed_by_subagent_stop(self):
        """T7.1: a resumed run's real SubagentStop relocks 'interrupted' as 'completed'."""
        from pa.hooks import subagent_stop

        conn = hooks.open_db()
        agent_path = os.path.join(self.projects, "slug", self.SID, "subagents",
                                  "agent-%s.jsonl" % self.AGENT)
        try:
            db.upsert_agent_run(conn, {"run_id": self.AGENT, "session_id": self.SID,
                                       "status": "interrupted", "kind": "expert",
                                       "locked": 1, "transcript_path": agent_path})
        finally:
            db.close(conn)
        self.write_transcript(agent_path, [
            usage_line("msg_c1", inp=1000, write=1000, read=0, out=500,
                       ts="2026-09-12T20:00:00.000Z"),
            usage_line("msg_c2", inp=1000, write=0, read=2000, out=500,
                       ts="2026-09-12T20:01:00.000Z")])
        subagent_stop.run({"session_id": self.SID, "agent_id": self.AGENT,
                           "agent_type": "expert-fable", "cwd": self.root,
                           "last_assistant_message": "STATUS: done",
                           "agent_transcript_path": agent_path}, self.cfg)
        row = self.rows("SELECT status, locked, turns FROM agent_runs WHERE run_id=?",
                        (self.AGENT,))
        self.assertEqual(row[0][0], "completed")
        self.assertEqual(row[0][1], 1)
        self.assertEqual(row[0][2], 2)


class ServedOutlineTest(LedgerCase):
    """3.9.5 T11: a denied whole Read serves the outline; the ranged Read after it is credited."""

    SID = "outline-session"
    AGENT = "a4444444000000009"

    def _big_py(self):
        path = os.path.join(self.root, "pa", "big.py").replace("\\", "/")
        os.makedirs(os.path.dirname(path))
        body = ['"""Big module."""', ""]
        for i in range(30):
            body += ["def g%d():" % i, '    """Does g%d."""' % i] + ["    y = 2  # " + "q" * 70] * 12
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(body) + "\n")
        return path

    def _inp(self, tool_input, **extra):
        return dict({"session_id": self.SID, "agent_id": self.AGENT,
                     "agent_type": "retriever-code", "cwd": self.root,
                     "tool_name": "Read", "tool_input": tool_input}, **extra)

    def test_served_outline_then_ranged_read_credit(self):
        path = self._big_py()
        out = pre_tool_use.run(self._inp({"file_path": path}), self.cfg)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        lines = reason.splitlines()
        self.assertIn("over guard.whole_read_chars", lines[0])
        self.assertGreaterEqual(len([l for l in lines if re.match(r"^\d+:def g", l)]), 10)
        self.assertEqual(lines[-1], "Read the ranges you need with offset and limit")
        with open(path, encoding="utf-8") as fh:
            cap_tokens = len(fh.read()) // 4
        # an unrelated note stays for the next Bash call
        with open(os.path.join(self.root, ".run", "credit", "1-1.json"), "w") as fh:
            json.dump({"kind": "script", "script": "plan_edit.py", "path": "x.md"}, fh)

        post_tool_use.run(self._inp({"file_path": path, "offset": 40, "limit": 30},
                                    tool_response={"file": {"content": "c" * 400}}), self.cfg)
        rows = self.rows("SELECT kind, file_tokens, emission_tokens FROM tool_calls"
                         " WHERE session_id=? AND path='pa/big.py' ORDER BY kind", (self.SID,))
        self.assertEqual(rows, [("outline", cap_tokens, (len(reason) + 1) // 4),
                                ("outline-read", cap_tokens, 0)])
        self.assertEqual(os.listdir(os.path.join(self.root, ".run", "credit")), ["1-1.json"])

    def test_size_denied_read_not_recorded_by_reread(self):
        """fix-17: reread_deny runs last, so a whole Read denied by size never enters the read set."""
        path = self._big_py()
        for _ in range(2):
            out = pre_tool_use.run(self._inp({"file_path": path}), self.cfg)
            self.assertIn("over guard.whole_read_chars",
                          out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".run", "guard")))

    def test_read_branch_order_reread_last(self):
        """fix-17: a small file passes the size guard, is recorded, and the re-Read is denied."""
        path = os.path.join(self.root, "small.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.assertIsNone(pre_tool_use.run(self._inp({"file_path": path}), self.cfg))
        out = pre_tool_use.run(self._inp({"file_path": path}), self.cfg)
        self.assertIn("already in this context",
                      out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".run", "guard",
                                                    "reads-%s.json" % self.AGENT)))


class LatencyTest(LedgerCase):
    """T12: the two hooks that fire on every tool call stay far under budget."""

    BUDGET_MS = 15.0

    def _timed(self, fn, inp, n=20):
        fn(inp, self.cfg)                                   # warm the imports
        start = time.perf_counter()
        for _ in range(n):
            fn(inp, self.cfg)
        return (time.perf_counter() - start) * 1000.0 / n

    def test_pre_tool_use_is_fast(self):
        inp = {"session_id": "lat", "cwd": self.root, "tool_name": "Bash",
               "tool_input": {"command": "grep -rn foo src"}}
        ms = self._timed(pre_tool_use.run, inp)
        self.assertLess(ms, self.BUDGET_MS, "PreToolUse took %.2f ms" % ms)

    def test_post_tool_use_main_thread_is_fast(self):
        blocks = [b for b in read_blocks("probe1.log")
                  if b[0] == "post_tool_use" and b[1].get("tool_name") == "Bash"]
        self.assertTrue(blocks)
        inp = self.rewrite(blocks[0][1])
        ms = self._timed(post_tool_use.run, inp)
        self.assertLess(ms, self.BUDGET_MS, "PostToolUse (main) took %.2f ms" % ms)

    def test_post_tool_use_main_thread_writes_nothing(self):
        inp = {"session_id": "lat", "cwd": self.root, "tool_name": "Read",
               "tool_input": {"file_path": "x"}}
        self.assertIsNone(post_tool_use.run(inp, self.cfg))
        self.assertFalse(os.path.exists(paths.db_path()))
        self.assertFalse(os.path.exists(paths.running_path()))


class StopRepairSavingsTest(LedgerCase):
    """T11.1: a ``savings_missing`` flag in ``<project>/.run/health.json`` makes the Stop
    hook run ``doctor --repair savings`` and toast the row."""

    SID = "stop-sd"

    def setUp(self):
        super().setUp()
        os.makedirs(self.projects, exist_ok=True)
        self._p = mock.patch.object(paths, "projects_dir", lambda: self.projects)
        self._p.start()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self.conn()
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": self.SID, "account": ACCOUNT, "cwd": self.root,
                                  "started": now, "cost_usd": 3.0})
        db.close(conn)
        with open(paths.summary_path(), "w", encoding="utf-8") as fh:
            json.dump({"sessions": {self.SID: {"cost_usd": 3.0}}}, fh)
        self.health = os.path.join(self.root, ".run", "health.json")
        with open(self.health, "w", encoding="utf-8") as fh:
            json.dump({"keep": True, "savings_missing": {"session": self.SID, "since": 1.0,
                       "detail": "-", "hook_repair": None, "row": None}}, fh)

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def _stop(self):
        from pa.hooks import stop

        calls = []
        with mock.patch.object(stop, "toast", lambda *a, **k: calls.append((a, k))):
            stop.run({"session_id": self.SID, "cwd": self.root, "agent_type": "router",
                      "last_assistant_message": "done.", "background_tasks": [],
                      "transcript_path": os.path.join(self.projects, "slug", self.SID + ".jsonl")},
                     self.cfg)
        return [c for c in calls if "savings display" in str(c[0][1])]

    def _health(self):
        with open(self.health, encoding="utf-8") as fh:
            return json.load(fh)

    def test_flag_runs_the_repair_and_toasts_the_row(self):
        toasts = self._stop()
        self.assertEqual(len(toasts), 1)
        self.assertTrue(toasts[0][0][2].startswith("OK"), toasts[0][0][2])
        self.assertEqual(self._health(), {"keep": True})

    def test_failed_repair_stamps_the_flag(self):
        with mock.patch("pa.ledger_cli.repair_savings",
                        lambda sid=None, project=None: (False, "FAIL savings display: x")):
            toasts = self._stop()
        flag = self._health()["savings_missing"]
        self.assertIsNotNone(flag["hook_repair"])
        self.assertEqual(flag["row"], "FAIL savings display: x")
        self.assertEqual(toasts[0][1]["cause"], "crash")

    def test_no_flag_no_repair(self):
        os.remove(self.health)
        self.assertEqual(self._stop(), [])


class StopPhaseRestampTest(LedgerCase):
    """T28: Stop restamps sessions.phase when router_session changes (a takeover)."""

    SID = "stop-phase"

    def _stop(self, status):
        from pa.hooks import stop

        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        with mock.patch.object(stop, "toast", lambda *a, **k: None):
            stop.run({"session_id": self.SID, "cwd": self.root, "agent_type": "router",
                      "last_assistant_message": "done.", "background_tasks": [],
                      "transcript_path": os.path.join(self.projects, "slug", self.SID + ".jsonl")},
                     self.cfg)
        return self.rows("SELECT phase FROM sessions WHERE session_id=?", (self.SID,))

    def test_takeover_restamps_phase(self):
        conn = self.conn()
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": self.SID, "account": ACCOUNT, "cwd": self.root,
                                  "phase": "beside 24"})
        db.close(conn)
        self.assertEqual(self._stop({"phase": "24", "router_session": self.SID}), [("24",)])
        self.assertEqual(self._stop({"phase": "24", "router_session": "other"}),
                         [("beside 24",)])

    def test_beside_session_heals_its_runs(self):
        """T31: a beside session's retriever run stamped the owner phase follows the session."""
        conn = self.conn()
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": self.SID, "account": ACCOUNT, "cwd": self.root,
                                  "phase": "24"})
        db.upsert_agent_run(conn, {"run_id": "ret-1", "session_id": self.SID,
                                   "agent_type": "retriever-code", "phase": "24"})
        db.upsert_agent_run(conn, {"run_id": "old-1", "session_id": self.SID,
                                   "agent_type": "retriever-code", "phase": "23"})
        db.close(conn)
        self.assertEqual(self._stop({"phase": "24", "router_session": "other"}),
                         [("beside 24",)])
        self.assertEqual(self.rows("SELECT run_id, phase FROM agent_runs WHERE session_id=?"
                                   " ORDER BY run_id", (self.SID,)),
                         [("old-1", "23"), ("ret-1", "beside 24")])


class EntryPointTest(unittest.TestCase):
    """pa_hook.py: exit 0 always, JSON only when the hook returns something."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-entry-")
        self.env = dict(os.environ)
        self.env["PA_LEDGER_DIR"] = self.dir
        self.env["PA_WARMER_OFF"] = "1"        # 3.9.5 T8: a real hook never spawns a real daemon here
        home = os.path.join(self.dir, "home")  # T24: a startup SessionStart runs the update check
        os.makedirs(home)
        self.env.update({"USERPROFILE": home, "HOME": home,
                         "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude")})
        self.repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, event, payload, env_extra=None):
        import subprocess

        env = dict(self.env)
        env.update(env_extra or {})
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", os.path.join(self.repo, "pa_hook.py"), event],
            input=payload.encode("utf-8"), capture_output=True, env=env, timeout=60)
        return proc

    def test_main_thread_post_tool_use_is_silent_and_exits_zero(self):
        payload = json.dumps({"session_id": "x", "hook_event_name": "PostToolUse",
                              "tool_name": "Bash", "tool_input": {"command": "ls"},
                              "cwd": self.dir})
        proc = self._run("post_tool_use", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_garbage_stdin_exits_zero_and_logs(self):
        proc = self._run("post_tool_use", "this is not json{{{")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        with open(os.path.join(self.dir, "hooks.log"), encoding="utf-8") as fh:
            self.assertIn("hook_bad_stdin", fh.read())

    def test_kill_switches_exit_immediately(self):
        payload = json.dumps({"session_id": "x", "hook_event_name": "SessionStart",
                              "source": "startup", "cwd": self.dir})
        for var in ("PA_LEDGER_OFF", "PA_HOOKS_OFF"):
            with self.subTest(var=var):
                proc = self._run("session_start", payload, {var: "1"})
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(os.path.exists(os.path.join(self.dir, "ledger.sqlite")))

    def test_hooks_off_leaves_the_savings_flag(self):
        proj = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(proj, ".run"))
        health = os.path.join(proj, ".run", "health.json")
        flag = {"savings_missing": {"session": "x", "since": 1.0, "detail": "-",
                                    "hook_repair": None, "row": None}}
        with open(health, "w", encoding="utf-8") as fh:
            json.dump(flag, fh)
        payload = json.dumps({"session_id": "x", "hook_event_name": "Stop", "cwd": proj})
        proc = self._run("stop", payload, {"PA_HOOKS_OFF": "1"})
        self.assertEqual(proc.returncode, 0)
        with open(health, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), flag)

    def test_config_enabled_false_is_a_kill_switch(self):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "enabled": False}, fh)
        payload = json.dumps({"session_id": "x", "hook_event_name": "SessionStart",
                              "source": "startup", "cwd": self.dir})
        proc = self._run("session_start", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "ledger.sqlite")))

    def test_clear_reseeds_the_router_through_context(self):
        os.makedirs(os.path.join(self.dir, ".claude"), exist_ok=True)
        with open(os.path.join(self.dir, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "demo"}, fh)
        payload = json.dumps({"session_id": "x", "hook_event_name": "SessionStart",
                              "source": "clear", "cwd": self.dir})
        proc = self._run("session_start", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("launch.py --seed-only", proc.stdout.decode("utf-8", "replace"))
        payload = json.dumps({"session_id": "y", "hook_event_name": "SessionStart",
                              "source": "startup", "cwd": self.dir})
        proc = self._run("session_start", payload)
        self.assertNotIn("seed-only", proc.stdout.decode("utf-8", "replace"))   # only a clear needs the nudge

    def test_unknown_event_is_logged_not_crashed(self):
        proc = self._run("not_a_hook", "{}")
        self.assertEqual(proc.returncode, 0)
        with open(os.path.join(self.dir, "hooks.log"), encoding="utf-8") as fh:
            self.assertIn("hook_unknown_event", fh.read())

    def test_pre_tool_use_denial_is_printed_as_json(self):
        root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(root, ".claude"))
        with open(os.path.join(root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        payload = json.dumps({"session_id": "x", "hook_event_name": "PreToolUse",
                              "tool_name": "Edit", "cwd": root,
                              "tool_input": {"file_path": "phase-ends/current/PHASE_PLAN.md"}})
        proc = self._run("pre_tool_use", payload)
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


class WorkflowAgentTest(LedgerCase):
    """T1.1: SubagentStop and PostToolUse resolve a workflow agent's transcript."""

    SID = "wf-session-1"
    WF_AGENT = "b1c2d3e4f5a607181"

    def _setup_workflow(self):
        """Write a workflow agent transcript under subagents/workflows/wf_x/."""
        from pa import transcript as T

        sid_dir = os.path.join(self.projects, "slug", self.SID)
        wf_dir = os.path.join(sid_dir, "subagents", "workflows", "wf_x")
        agent_path = os.path.join(wf_dir, "agent-%s.jsonl" % self.WF_AGENT)
        self.write_transcript(agent_path, [
            usage_line("msg_wf1", model="claude-haiku-4-5", inp=800, write=500, read=0,
                       out=30, ttl="5m"),
            usage_line("msg_wf2", model="claude-haiku-4-5", inp=10, write=0, read=1300,
                       out=50, ttl="5m", ts="2026-09-12T20:00:05.000Z")])
        meta_path = agent_path[:-6] + ".meta.json"
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump({"agentType": "retriever-code", "description": "wf lookup",
                       "spawnDepth": 1}, fh)
        # write the main transcript so hooks can resolve the sid
        main_path = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        self.write_transcript(main_path, [
            usage_line("msg_main1", inp=2000, write=1000, read=0, out=100)])
        return agent_path, main_path

    def test_subagent_stop_resolves_workflow_agent(self):
        """SubagentStop for a workflow agent whose agent_transcript_path is absent."""
        from pa.hooks import subagent_stop

        agent_path, main_path = self._setup_workflow()
        running.set_agent(self.SID, self.WF_AGENT,
                          {"agent_type": "retriever-code", "role": "retriever"})
        # no agent_transcript_path in inp — the hook must resolve it
        subagent_stop.run({"session_id": self.SID, "agent_id": self.WF_AGENT,
                           "agent_type": "retriever-code", "cwd": self.root,
                           "last_assistant_message": "done",
                           "transcript_path": main_path}, self.cfg)
        row = self.rows("SELECT locked, turns, description, parent_source "
                        "FROM agent_runs WHERE run_id=?", (self.WF_AGENT,))
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][0], 1)                        # locked
        self.assertEqual(row[0][1], 2)                        # 2 turns
        self.assertIn("wf:wf_x", row[0][2])                  # description carries wf id
        self.assertEqual(row[0][3], "workflow")               # parent_source

    def test_post_tool_use_reads_workflow_agent_tail(self):
        """PostToolUse inside a workflow agent reads its tail via the cached path."""
        agent_path, main_path = self._setup_workflow()
        running.set_agent(self.SID, self.WF_AGENT,
                          {"agent_type": "retriever-code", "role": "retriever"})
        # simulate PostToolUse inside the workflow agent
        result = post_tool_use.run({
            "session_id": self.SID, "agent_id": self.WF_AGENT,
            "agent_type": "retriever-code", "cwd": self.root,
            "transcript_path": main_path, "tool_name": "Read",
        }, self.cfg)
        # should not error; it updates running.json
        entry = running.agent(self.SID, self.WF_AGENT)
        self.assertIsNotNone(entry.get("ctx"))


# --------------------------------------------------------------------------- memory link (T5)

class TestMemoryLink(LedgerCase):
    """SessionStart creates a missing memory link for a governed main session."""

    def setUp(self):
        super().setUp()
        # create .claude-state/memory/ in the temp project root
        self.mem_dir = os.path.join(self.root, ".claude-state", "memory")
        os.makedirs(self.mem_dir, exist_ok=True)
        with open(os.path.join(self.mem_dir, "MEMORY.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("# memory index\n")
        # home is redirected by LedgerCase (self.fake_home), so paths.projects_dir() is our temp tree

    def _proj_mem(self):
        slug = paths.project_slug(self.root.replace("\\", "/"))
        return os.path.join(self.fake_home, ".claude", "projects", slug, "memory")

    def _is_link(self, path):
        import stat as _stat
        try:
            st = os.lstat(path)
        except OSError:
            return False
        if _stat.S_ISLNK(st.st_mode):
            return True
        if sys.platform.startswith("win") or os.name == "nt":
            return bool(getattr(st, "st_file_attributes", 0)
                        & _stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return False

    def test_hook_creates_missing_link(self):
        inp = {"session_id": "mem-test-001", "source": "startup",
               "cwd": self.root, "agent_type": "pa-session"}
        self.run_hook("session_start", inp)
        proj_mem = self._proj_mem()
        self.assertTrue(self._is_link(proj_mem),
                        "expected link at %s" % proj_mem)

    def test_subagent_session_does_nothing(self):
        inp = {"session_id": "mem-test-002", "source": "startup",
               "cwd": self.root, "agent_type": "retriever-code",
               "agent_id": "a1234567000000003"}
        self.run_hook("session_start", inp)
        proj_mem = self._proj_mem()
        self.assertFalse(self._is_link(proj_mem))

    def test_old_dir_with_a_subdirectory_is_left_alone(self):
        # T5 expert: never rmtree -- a file is moved, a subdirectory stays, no link is made
        proj_mem = self._proj_mem()
        os.makedirs(os.path.join(proj_mem, "sub"))
        with open(os.path.join(proj_mem, "sub", "keep.md"), "w", encoding="utf-8") as fh:
            fh.write("keep\n")
        with open(os.path.join(proj_mem, "note.md"), "w", encoding="utf-8") as fh:
            fh.write("note\n")
        inp = {"session_id": "mem-test-004", "source": "startup",
               "cwd": self.root, "agent_type": "pa-session"}
        self.run_hook("session_start", inp)
        self.assertFalse(self._is_link(proj_mem))
        self.assertTrue(os.path.isfile(os.path.join(proj_mem, "sub", "keep.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.mem_dir, "note.md")))

    def test_existing_link_does_nothing(self):
        # first run creates the link
        inp = {"session_id": "mem-test-003", "source": "startup",
               "cwd": self.root, "agent_type": "pa-session"}
        self.run_hook("session_start", inp)
        proj_mem = self._proj_mem()
        self.assertTrue(self._is_link(proj_mem))
        # second run should not error
        self.run_hook("session_start", inp)
        self.assertTrue(self._is_link(proj_mem))


class ProgressFromKillTest(LedgerCase):
    """T13: resume hook writes TASK_PROGRESS.md from a killed run's transcript."""

    SID = "progress-session"

    def _make_transcript(self, run_id, tool_calls=None, text_blocks=None):
        """Write a synthetic agent transcript with tool_use and text blocks."""
        tp = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        sub_dir = os.path.join(self.projects, "slug", self.SID, "subagents")
        os.makedirs(sub_dir, exist_ok=True)
        # write the main transcript too (so session_dir resolves)
        if not os.path.exists(tp):
            with open(tp, "w", encoding="utf-8") as fh:
                fh.write("")
        path = os.path.join(sub_dir, "agent-%s.jsonl" % run_id)
        lines = []
        if tool_calls is None:
            tool_calls = [
                ("Read", {"file_path": "/proj/foo.py"}),
                ("Bash", {"command": "python -m unittest discover -s tests"}),
                ("Agent", {"subagent_type": "coder-opus46", "description": "fix the bug"}),
                ("Edit", {"file_path": "/proj/bar.py"}),
            ]
        if text_blocks is None:
            text_blocks = [
                "Analyzing the results from the test run.",
                "The fix needs to handle the edge case in the parser.",
                "I will now commit the changes and verify."
            ]
        # interleave: write tool_use records and text records as assistant messages
        idx = 0
        for tname, tinp in tool_calls:
            content = [{"type": "tool_use", "name": tname, "input": tinp}]
            rec = {"type": "assistant", "timestamp": "2026-09-20T22:00:%02dZ" % idx,
                   "message": {"role": "assistant", "content": content}}
            lines.append(json.dumps(rec))
            idx += 1
        for txt in text_blocks:
            rec = {"type": "assistant", "timestamp": "2026-09-20T22:01:%02dZ" % idx,
                   "message": {"role": "assistant",
                               "content": [{"type": "text", "text": txt}]}}
            lines.append(json.dumps(rec))
            idx += 1
        self.write_transcript(path, lines)
        return tp, path

    def _status(self, run_id, task="T3", attempt=1, killed_at="2026-09-20T22:01:39Z"):
        status_path = os.path.join(self.root, ".run", "status.json")
        st = {"run_id": run_id, "task": task, "attempt": attempt,
              "killed_at": killed_at, "task_title": "the test task",
              "expert_agent_type": "expert-fable"}
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    def _inp(self, tp=None, source="resume"):
        return {"session_id": self.SID, "source": source,
                "cwd": self.root, "agent_type": "pa-session",
                "transcript_path": tp or os.path.join(
                    self.projects, "slug", self.SID + ".jsonl")}

    def _progress_path(self):
        return os.path.join(self.root, "phase-ends", "current", "TASK_PROGRESS.md")

    def test_resume_writes_progress_with_header_and_sections(self):
        run_id = "a1234567000000003"
        tp, _agent_tp = self._make_transcript(run_id)
        self._status(run_id)
        os.makedirs(os.path.join(self.root, "phase-ends", "current"), exist_ok=True)
        self.run_hook("session_start", self._inp(tp))
        progress = self._progress_path()
        self.assertTrue(os.path.isfile(progress))
        with open(progress, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("# TASK_PROGRESS -- T3 attempt 1", text)
        self.assertIn("Task: T3 the test task", text)
        self.assertIn("Agent: expert-fable", text)
        self.assertIn("Run: a1234567000000003", text)
        self.assertIn("## Done so far", text)
        self.assertIn("Read -- /proj/foo.py", text)
        self.assertIn("Bash -- python -m unittest discover -s tests", text)
        self.assertIn("Agent -- coder-opus46 fix the bug", text)
        self.assertIn("coder runs spawned: 1", text)
        self.assertIn("## In flight", text)
        self.assertIn("## Hypotheses rejected", text)
        self.assertIn("## Current hypothesis", text)
        self.assertIn("## Next 5 steps", text)
        self.assertIn("logs/T3.c*.md", text)

    def test_12_call_cap(self):
        """Only the last 12 tool calls appear."""
        run_id = "a2222222000000006"
        calls = [("Read", {"file_path": "/proj/f%d.py" % i}) for i in range(20)]
        tp, _agent_tp = self._make_transcript(run_id, tool_calls=calls,
                                              text_blocks=["last text"])
        self._status(run_id)
        os.makedirs(os.path.join(self.root, "phase-ends", "current"), exist_ok=True)
        self.run_hook("session_start", self._inp(tp))
        with open(self._progress_path(), encoding="utf-8") as fh:
            text = fh.read()
        # f0..f7 should be excluded (20 - 12 = 8)
        self.assertNotIn("f0.py", text)
        self.assertNotIn("f7.py", text)
        # f8..f19 should be present
        self.assertIn("f8.py", text)
        self.assertIn("f19.py", text)

    def test_three_text_blocks_in_flight(self):
        """Only the last 3 text blocks appear in ## In flight."""
        run_id = "a3333333000000008"
        texts = ["text block %d" % i for i in range(6)]
        tp, _agent_tp = self._make_transcript(run_id, tool_calls=[], text_blocks=texts)
        self._status(run_id)
        os.makedirs(os.path.join(self.root, "phase-ends", "current"), exist_ok=True)
        self.run_hook("session_start", self._inp(tp))
        with open(self._progress_path(), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("text block 0", text)
        self.assertNotIn("text block 2", text)
        self.assertIn("text block 3", text)
        self.assertIn("text block 5", text)

    def test_subagent_session_writes_nothing(self):
        """A subagent's SessionStart must not write progress."""
        run_id = "a4444444000000009"
        tp, _agent_tp = self._make_transcript(run_id)
        self._status(run_id)
        os.makedirs(os.path.join(self.root, "phase-ends", "current"), exist_ok=True)
        inp = self._inp(tp)
        inp["agent_id"] = "sub-agent-id"  # marks it as a subagent
        self.run_hook("session_start", inp)
        self.assertFalse(os.path.exists(self._progress_path()))

    def test_existing_progress_is_kept_and_the_digest_appended_once(self):
        """An expert-written TASK_PROGRESS.md is never replaced: the digest is appended, once."""
        run_id = "a555555500000000a"
        killed_at = "2026-09-20T22:01:39Z"
        tp, _agent_tp = self._make_transcript(run_id)
        self._status(run_id, killed_at=killed_at)
        progress = self._progress_path()
        os.makedirs(os.path.dirname(progress), exist_ok=True)
        with open(progress, "w", encoding="utf-8") as fh:
            fh.write("# Expert-written progress\n")
        # set mtime to the future relative to killed_at
        import time as _time
        future = _time.time() + 3600
        os.utime(progress, (future, future))
        self.run_hook("session_start", self._inp(tp))
        self.run_hook("session_start", self._inp(tp))          # a second resume of the same kill
        with open(progress, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Expert-written progress\n"))
        self.assertEqual(text.count("## Resume digest (killed at %s, run %s" % (killed_at, run_id)), 1)
        self.assertIn("## Done so far", text)
        self.assertNotIn("## Next 5 steps", text)                # the expert's own plan stands

    def test_missing_transcript_writes_nothing(self):
        """When the killed transcript is missing, nothing is written."""
        run_id = "a666666600000000c"
        self._status(run_id)
        os.makedirs(os.path.join(self.root, "phase-ends", "current"), exist_ok=True)
        tp = os.path.join(self.projects, "slug", self.SID + ".jsonl")
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with open(tp, "w") as fh:
            fh.write("")
        # do NOT create the agent transcript
        self.run_hook("session_start", self._inp(tp))
        self.assertFalse(os.path.exists(self._progress_path()))


class ManagedEditTest(LedgerCase):
    """3.9.7 T7: an edit of a file listed in .claude/pa3-managed.json spools one managed_edit."""

    SID = "managed-session"
    REL = ".claude/agents/x.md"

    def setUp(self):
        super().setUp()
        self._record({self.REL: {"base": "4", "sha": "0" * 64, "user": None}})
        with open(os.path.join(self.root, ".run", "status.json"), "w", encoding="utf-8") as fh:
            json.dump({"task": "T7"}, fh)

    def _record(self, files, raw=None):
        with open(os.path.join(self.root, ".claude", "pa3-managed.json"), "w",
                  encoding="utf-8") as fh:
            fh.write(raw if raw is not None else json.dumps({"format": 1, "files": files}))

    def _inp(self, rel, tool="Edit"):
        return {"session_id": self.SID, "cwd": self.root, "tool_name": tool,
                "hook_event_name": "PostToolUse",
                "tool_input": {"file_path": os.path.join(self.root, *rel.split("/")),
                               "old_string": "a", "new_string": "b"},
                "tool_response": {"filePath": rel}}

    def _spooled(self):
        path = paths.spool_path(self.SID)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return [r for r in rows if r.get("kind") == "managed_edit"]

    def test_listed_edit_spools_one_event(self):
        self.assertIsNone(post_tool_use.run(self._inp(self.REL), self.cfg))
        rows = self._spooled()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["detail"], {"path": self.REL, "task": "T7", "tool": "Edit"})
        self.assertEqual(rows[0]["session_id"], self.SID)

    def test_write_and_multiedit_spool_too(self):
        post_tool_use.run(self._inp(self.REL, "Write"), self.cfg)
        post_tool_use.run(self._inp(self.REL, "MultiEdit"), self.cfg)
        self.assertEqual([r["detail"]["tool"] for r in self._spooled()], ["Write", "MultiEdit"])

    def test_unlisted_and_other_tools_spool_nothing(self):
        post_tool_use.run(self._inp(".claude/agents/other.md"), self.cfg)
        post_tool_use.run(self._inp(self.REL, "Read"), self.cfg)
        self.assertEqual(self._spooled(), [])

    def test_corrupt_or_missing_manifest_spools_nothing(self):
        self._record(None, raw="{not json")
        self.assertIsNone(post_tool_use.run(self._inp(self.REL), self.cfg))
        os.remove(os.path.join(self.root, ".claude", "pa3-managed.json"))
        self.assertIsNone(post_tool_use.run(self._inp(self.REL), self.cfg))
        self.assertEqual(self._spooled(), [])

    def test_corrupt_manifest_entry_point_exits_zero(self):
        import subprocess

        self._record(None, raw="[1, 2")
        env = dict(os.environ, PA_WARMER_OFF="1")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", os.path.join(repo, "pa_hook.py"), "post_tool_use"],
            input=json.dumps(self._inp(self.REL)).encode("utf-8"), capture_output=True,
            env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(self._spooled(), [])

    def test_module_level_imports(self):
        import ast

        with open(post_tool_use.__file__, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
        self.assertLessEqual(names, {"json", "os", "sys", "time"})
        self.assertNotIn("hashlib", src)


if __name__ == "__main__":
    unittest.main()
