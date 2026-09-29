"""pa.liveness: sweep finished agents whose SubagentStop never fired (T1.c6).

All tests use temp dirs and PA_LEDGER_DIR; no real transcripts or ledger touched.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import accounts, config, liveness, notify, paths, running  # noqa: E402
from pa import hooks  # noqa: E402


def _write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write_meta(transcript_path, meta):
    if transcript_path.endswith(".jsonl"):
        meta_path = transcript_path[:-6] + ".meta.json"
    else:
        meta_path = transcript_path + ".meta.json"
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _tool_result_record(tool_use_id=None, ts=None):
    """A user/tool_result JSONL record."""
    content = [{"type": "tool_result", "content": "done"}]
    if tool_use_id:
        content[0]["tool_use_id"] = tool_use_id
    rec = {"type": "user", "timestamp": ts or "2026-09-20T01:00:00.000Z",
           "message": {"role": "user", "content": content}}
    return rec


def _tool_use_record(ts=None):
    """An assistant/tool_use JSONL record."""
    return {"type": "assistant", "timestamp": ts or "2026-09-20T01:00:00.000Z",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": "tu_1", "name": "Bash",
                                     "input": {"command": "echo hi"}}]}}


_OLD_TS = "2026-09-20T00:00:00Z"   # well past min_age_s=300
_NOW = 1789905600.0                 # 2026-09-20T12:00:00Z (after _OLD_TS)


class LivenessCase(unittest.TestCase):
    """Temp ledger + temp transcript tree."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-liveness-")
        self.ledger = os.path.join(self.dir, "usage-ledger")
        self.projects = os.path.join(self.dir, "projects")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "liveness-test"}')
        os.environ["PA_LEDGER_DIR"] = self.ledger
        paths.ensure_ledger_tree()
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()
        self.sid = "liveness-sid-0001"
        self.toasts = []
        notify.set_spawner(lambda cmd, env: self.toasts.append((cmd, env)))
        self._auth = accounts.auth_status
        accounts.auth_status = lambda *a, **k: {"email": "test@example.com",
                                                "source": "auth", "org_name": "test-org"}
        # ensure a running.json session exists
        running.ensure_session(self.sid, project=self.root)

    def tearDown(self):
        accounts.auth_status = self._auth
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _agent_transcript(self, agent_id):
        """Build a transcript path under the temp tree."""
        return os.path.join(self.projects, self.sid, "subagents",
                            "agent-%s.jsonl" % agent_id)

    def _parent_transcript(self, parent_id):
        """Build a parent agent transcript path."""
        return os.path.join(self.projects, self.sid, "subagents",
                            "agent-%s.jsonl" % parent_id)

    def _session_transcript(self):
        """Build the main session transcript path."""
        return os.path.join(self.projects, "%s.jsonl" % self.sid)

    def _register_agent(self, agent_id, agent_type="retriever-code", parent=None,
                        started=None, transcript_path=None):
        """Register an agent in running.json."""
        tp = transcript_path or self._agent_transcript(agent_id)
        running.set_agent(self.sid, agent_id, {
            "agent_type": agent_type,
            "role": config.role_from_agent_type(agent_type),
            "parent": parent or self.sid,
            "started": started or _OLD_TS,
            "status": "running",
            "transcript_path": tp,
            "ctx": 0,
        })

    # ------------------------------------------------------------------- tests

    def test_parent_tool_result_sweeps(self):
        """A child with a meta toolUseId whose parent transcript holds the matching
        tool_result is swept and booked."""
        child_id = "child_aaa111"
        parent_id = "parent_bbb222"
        tool_use_id = "toolu_sweep_test_001"

        child_path = self._agent_transcript(child_id)
        parent_path = self._parent_transcript(parent_id)

        # register child with parent pointing to session (will be repaired)
        self._register_agent(child_id, parent=self.sid)

        # write child meta with toolUseId and parentAgentId
        _write_meta(child_path, {"toolUseId": tool_use_id, "parentAgentId": parent_id,
                                 "agentType": "retriever-code"})

        # write child transcript (a tool result, stale)
        _write_jsonl(child_path, [_tool_result_record(ts="2026-09-20T00:10:00.000Z")])

        # register parent agent too
        self._register_agent(parent_id, agent_type="coder-opus46", parent=self.sid)

        # write parent transcript WITH matching tool_result
        _write_jsonl(parent_path, [
            _tool_use_record(ts="2026-09-20T00:09:00.000Z"),
            _tool_result_record(tool_use_id=tool_use_id, ts="2026-09-20T00:10:00.000Z"),
        ])

        swept = liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)
        self.assertIn(child_id, swept)

        # verify it was removed from running.json
        self.assertEqual(running.agent(self.sid, child_id), {})

    def test_no_match_keeps_agent(self):
        """When parent transcript has no matching tool_result, agent is kept."""
        child_id = "child_keep111"
        parent_id = "parent_keep222"
        tool_use_id = "toolu_no_match_001"

        child_path = self._agent_transcript(child_id)
        parent_path = self._parent_transcript(parent_id)

        self._register_agent(child_id, parent=parent_id)
        _write_meta(child_path, {"toolUseId": tool_use_id, "parentAgentId": parent_id})
        _write_jsonl(child_path, [_tool_use_record(ts="2026-09-20T00:10:00.000Z")])

        self._register_agent(parent_id, agent_type="coder-opus46", parent=self.sid)
        # parent transcript without matching tool_result
        _write_jsonl(parent_path, [_tool_use_record(ts="2026-09-20T00:09:00.000Z")])

        swept = liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)
        self.assertNotIn(child_id, swept)

        # still in running.json
        self.assertNotEqual(running.agent(self.sid, child_id), {})

    def test_stale_tool_result_sweeps(self):
        """A child without toolUseId whose transcript ends on a tool_result older
        than dead_min is swept with reason 'stale_tool_result'."""
        child_id = "child_stale_333"
        child_path = self._agent_transcript(child_id)

        self._register_agent(child_id)

        # write child transcript ending on a stale tool_result (20+ min old)
        old_ts = "2026-09-20T00:00:00.000Z"
        _write_jsonl(child_path, [_tool_result_record(ts=old_ts)])
        # no meta (or meta without toolUseId)
        _write_meta(child_path, {"agentType": "retriever-code"})

        swept = liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)
        self.assertIn(child_id, swept)
        self.assertEqual(running.agent(self.sid, child_id), {})

    def test_assistant_tool_use_never_swept(self):
        """An entry whose last record is an assistant tool_use is never swept by age."""
        child_id = "child_tooluse_444"
        child_path = self._agent_transcript(child_id)

        self._register_agent(child_id)
        # write transcript ending on an assistant tool_use (very old)
        _write_jsonl(child_path, [_tool_use_record(ts="2026-09-10T00:00:00.000Z")])
        _write_meta(child_path, {"agentType": "retriever-code"})

        swept = liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)
        self.assertNotIn(child_id, swept)
        self.assertNotEqual(running.agent(self.sid, child_id), {})

    def test_once_per_minute_stamp(self):
        """sweep_if_due respects the 60-second throttle."""
        child_id = "child_stamp_555"
        child_path = self._agent_transcript(child_id)
        self._register_agent(child_id)
        _write_jsonl(child_path, [_tool_result_record(ts="2026-09-20T00:00:00.000Z")])
        _write_meta(child_path, {"agentType": "retriever-code"})

        # first call: should sweep
        swept1 = liveness.sweep_if_due(self.sid, self.cfg, now=_NOW)
        self.assertIn(child_id, swept1)

        # re-register the agent
        self._register_agent(child_id)
        _write_jsonl(child_path, [_tool_result_record(ts="2026-09-20T00:00:00.000Z")])

        # second call 10 seconds later: throttled, no sweep
        swept2 = liveness.sweep_if_due(self.sid, self.cfg, now=_NOW + 10)
        self.assertEqual(swept2, [])

        # third call 61 seconds later: should sweep again
        swept3 = liveness.sweep_if_due(self.sid, self.cfg, now=_NOW + 61)
        self.assertIn(child_id, swept3)

    def test_main_thread_entry_never_swept(self):
        """The main thread's own live entry (agent id == session id) has no
        subagent transcript by construction; the sweep must skip it as an
        orphan and only remove a genuinely orphaned subagent."""
        orphan_id = "child_orphan_999"
        orphan_path = self._agent_transcript(orphan_id)

        # main-thread entry: agent id equals the session id, no transcript on disk
        self._register_agent(self.sid, parent=self.sid)
        running.set_agent(self.sid, self.sid, {"retrievals": {"r1": 3}})

        # a real orphan: no transcript, no toolUseId
        self._register_agent(orphan_id)
        _write_meta(orphan_path, {"agentType": "retriever-code"})

        swept = liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)

        self.assertNotIn(self.sid, swept)
        self.assertIn(orphan_id, swept)

        main_entry = running.agent(self.sid, self.sid)
        self.assertNotEqual(main_entry, {})
        self.assertEqual(main_entry.get("retrievals"), {"r1": 3})
        self.assertEqual(running.agent(self.sid, orphan_id), {})

    def test_parent_repair_from_meta(self):
        """When the running entry's parent is the session but meta names an agent,
        the entry's parent is repaired."""
        child_id = "child_repair_666"
        parent_id = "parent_repair_777"
        child_path = self._agent_transcript(child_id)
        parent_path = self._parent_transcript(parent_id)

        # register with parent = session
        self._register_agent(child_id, parent=self.sid)

        # meta says parentAgentId is the real parent
        _write_meta(child_path, {"parentAgentId": parent_id, "agentType": "retriever-code"})
        _write_jsonl(child_path, [_tool_use_record()])  # ends on tool_use: won't be swept

        self._register_agent(parent_id, agent_type="coder-opus46")
        _write_jsonl(parent_path, [_tool_use_record()])

        liveness.sweep(self.sid, self.cfg, now=_NOW, min_age_s=300)

        # verify parent was repaired in running.json
        entry = running.agent(self.sid, child_id)
        self.assertEqual(entry.get("parent"), parent_id)
        self.assertEqual(entry.get("parent_source"), "meta_repair")

    def test_render_stale_marker(self):
        """A stale child renders with '?' after elapsed time."""
        from pa import statusline

        child_id = "child_render_888"
        child_path = self._agent_transcript(child_id)

        self._register_agent(child_id, started=_OLD_TS, transcript_path=child_path)
        # write transcript and set its mtime to old
        _write_jsonl(child_path, [_tool_result_record(ts="2026-09-20T00:00:00.000Z")])
        old_mtime = _NOW - 1500  # 25 minutes ago, > dead_min(20)
        os.utime(child_path, (old_mtime, old_mtime))

        ctx = {
            "running": running.load(),
            "sid": self.sid,
        }
        status = {}

        children = statusline._task_children(ctx, status)
        stale_children = [c for c in children if c.get("stale")]
        self.assertTrue(len(stale_children) > 0, "Expected at least one stale child")
        self.assertTrue(stale_children[0]["elapsed"].endswith("?"),
                        "Expected elapsed to end with '?'")


class LivenessConfigTest(unittest.TestCase):
    """Config validation for the liveness section."""

    def test_defaults_have_liveness(self):
        cfg = config.defaults()
        self.assertEqual(cfg["liveness"]["dead_min"], 20)
        self.assertEqual(cfg["liveness"]["scan_bytes"], 4194304)

    def test_validate_accepts_defaults(self):
        cfg = config.defaults()
        problems = config.validate(cfg)
        self.assertEqual(problems, [])

    def test_validate_bad_dead_min(self):
        cfg = config.defaults()
        cfg["liveness"]["dead_min"] = -1
        problems = config.validate(cfg)
        self.assertTrue(any("liveness.dead_min" in p for p in problems))

    def test_validate_bad_scan_bytes(self):
        cfg = config.defaults()
        cfg["liveness"]["scan_bytes"] = "not_a_number"
        problems = config.validate(cfg)
        self.assertTrue(any("liveness.scan_bytes" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
