"""PostToolUse credit integration: Bash notes -> tool_calls + live items,
Read voiding, seed carry growth."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import accounts, config, db, notify, paths, running  # noqa: E402
from pa import hooks  # noqa: E402
from pa.hooks import post_tool_use  # noqa: E402

SID = "credit-test-sid-0000"
AGENT_ID = "aaaa1111000000014"
ACCOUNT = "credit@example.com"


def _usage_line(msg_id, model="claude-opus-4-6[1m]", inp=10000, write=5000,
                read=0, out=500, ts="2026-09-22T00:00:00.000Z"):
    """Minimal assistant transcript record."""
    return json.dumps({
        "type": "assistant", "uuid": "u-" + msg_id, "requestId": "req_" + msg_id,
        "timestamp": ts, "sessionId": SID,
        "message": {"id": msg_id, "role": "assistant", "model": model,
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": inp,
                              "cache_creation_input_tokens": write,
                              "cache_read_input_tokens": read,
                              "output_tokens": out,
                              "cache_creation": {
                                  "ephemeral_5m_input_tokens": write,
                                  "ephemeral_1h_input_tokens": 0},
                              "output_tokens_details": {"thinking_tokens": 0}}},
    })


class CreditCase(unittest.TestCase):
    """Temp ledger + project with .run/credit/ for PostToolUse credit tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ptu-")
        self.ledger = os.path.join(self.dir, "usage-ledger")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, ".run", "credit"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"pa_version": "3.0.0", "project": "credit-test"}')
        os.environ["PA_LEDGER_DIR"] = self.ledger
        paths.ensure_ledger_tree()
        hooks._PROJECT_ROOTS.clear()
        self.cfg = config.defaults()
        self.toasts = []
        notify.set_spawner(lambda cmd, env: self.toasts.append((cmd, env)))
        self._auth = accounts.auth_status
        accounts.auth_status = lambda *a, **k: {"email": ACCOUNT, "source": "auth",
                                                "org_name": "test-org"}
        # write the main transcript line (a session-level jsonl at the projects dir)
        self.projects_dir = os.path.join(self.dir, "projects")
        self.transcript_path = os.path.join(self.projects_dir, "test.jsonl")
        os.makedirs(os.path.dirname(self.transcript_path), exist_ok=True)
        with open(self.transcript_path, "w", encoding="utf-8") as fh:
            fh.write('{"type":"system","sessionId":"%s"}\n' % SID)
        # write the agent transcript
        self.agent_transcript = os.path.join(
            self.projects_dir, SID, "subagents", "agent-%s.jsonl" % AGENT_ID)
        os.makedirs(os.path.dirname(self.agent_transcript), exist_ok=True)
        with open(self.agent_transcript, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('{"type":"meta","parentAgentId":"%s","sessionId":"%s"}\n' % (SID, SID))
            fh.write(_usage_line("msg1") + "\n")
        # initialise the agent's running.json entry (mimics subagent_start)
        running.set_agent(SID, AGENT_ID, {
            "seed_ctx": 5000, "seed_carry_usd": 0.0,
            "agent_type": "coder-opus46", "role": "coder",
            "parent": SID, "model_pinned": "claude-opus-4-6",
        })

    def tearDown(self):
        accounts.auth_status = self._auth
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        hooks._PROJECT_ROOTS.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_note(self, name, note):
        p = os.path.join(self.root, ".run", "credit", name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(note, fh)

    def _inp(self, tool="Bash", tool_response="output", tool_input=None):
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "agent_id": AGENT_ID,
            "agent_type": "coder-opus46",
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-1",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }

    def conn(self):
        return db.connect(paths.db_path())


class BashCreditTest(CreditCase):
    """A Bash PostToolUse with two note files yields two tool_calls rows and live items."""

    def test_two_notes_yield_two_rows(self):
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
            "path": "pa/foo.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        self._write_note("2-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "commit_task", "kind": "script",
            "path": "pa/bar.py", "file_chars": 2000, "removed_chars": 20,
            "added_chars": 40, "anchor_chars": 60, "whole": False,
        })
        post_tool_use.run(self._inp(tool_response="x" * 200), self.cfg)
        # check DB rows
        c = self.conn()
        try:
            rows = c.execute("SELECT id, kind, path, emission_tokens FROM tool_calls "
                             "WHERE session_id=? ORDER BY path", (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 2)
        paths_found = {r["path"] for r in rows}
        self.assertIn("pa/foo.py", paths_found)
        self.assertIn("pa/bar.py", paths_found)
        # check live items
        entry = running.agent(SID, AGENT_ID)
        rets = entry.get("retrievals") or {}
        # should have items for carry > 0 paths
        carry_items = {k: v for k, v in rets.items() if isinstance(v, dict) and v.get("R", 0) > 0}
        self.assertGreater(len(carry_items), 0)


class ReadVoidsTest(CreditCase):
    """A Read of a credited path voids its live carry item."""

    def test_read_voids_carry(self):
        # first: a Bash with one note
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
            "path": "pa/foo.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        post_tool_use.run(self._inp(tool_response="short"), self.cfg)
        # confirm the live item exists and is not void
        entry = running.agent(SID, AGENT_ID)
        rets = entry.get("retrievals") or {}
        carry_keys = [k for k, v in rets.items()
                      if isinstance(v, dict) and v.get("path") == "pa/foo.py" and not v.get("void")]
        self.assertEqual(len(carry_keys), 1, "expected one non-void carry item for pa/foo.py")

        # refresh transcript (new usage line for the Read call)
        with open(self.agent_transcript, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("msg2", ts="2026-09-22T00:00:01.000Z") + "\n")
        # now: a Read of the same path
        fp = os.path.join(self.root, "pa", "foo.py").replace("\\", "/")
        read_inp = self._inp(tool="Read", tool_response="file content",
                             tool_input={"file_path": fp})
        post_tool_use.run(read_inp, self.cfg)
        # the item should now be void
        entry = running.agent(SID, AGENT_ID)
        rets = entry.get("retrievals") or {}
        for k, v in rets.items():
            if isinstance(v, dict) and v.get("path") == "pa/foo.py" and v.get("kind") != "read":
                self.assertTrue(v.get("void"), "carry item for pa/foo.py should be voided after Read")


class MainBashCreditTest(CreditCase):
    """A main-thread Bash PostToolUse with two notes -> two rows + live items."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None, model=None):
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            # no agent_id -> main thread
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "model": model,
        }

    def test_two_notes_main_thread(self):
        from pa import credit
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
            "path": "pa/foo.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        self._write_note("2-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "task_log", "kind": "script",
            "path": "pa/bar.py", "file_chars": 2000, "removed_chars": 20,
            "added_chars": 40, "anchor_chars": 60, "whole": False,
        })
        stdout = "some output text"
        stderr = "err"
        resp = {"stdout": stdout, "stderr": stderr, "interrupted": False, "exitCode": 0}
        post_tool_use.run(
            self._main_inp(tool_response=resp, model="claude-opus-4-6[1m]"),
            self.cfg)
        # DB rows: run_id = session id, result_tokens from stdout+stderr
        c = self.conn()
        try:
            rows = c.execute("SELECT id, run_id, result_tokens FROM tool_calls "
                             "WHERE session_id=? ORDER BY path", (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 2)
        expected_rt = credit.tokens(len(stdout) + len(stderr))
        for r in rows:
            self.assertEqual(r["run_id"], SID, "main thread run_id should be session id")
            self.assertEqual(r["result_tokens"], expected_rt)
        # live items on session's main entry (agents[sid])
        entry = running.agent(SID, SID)
        rets = entry.get("retrievals") or {}
        carry_items = {k: v for k, v in rets.items()
                       if isinstance(v, dict) and v.get("R", 0) > 0}
        self.assertGreater(len(carry_items), 0, "main entry should have carry items")
        # saved figure moved (emission_live_usd > 0 drives saved_live_usd)
        self.assertGreater(float(entry.get("saved_live_usd") or 0), 0,
                           "saved_live_usd should be positive")


class MainReadVoidsTest(CreditCase):
    """A main-thread Read of a credited path voids its live carry item."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None, model=None):
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "model": model,
        }

    def test_main_read_voids(self):
        # Bash with a note on the main thread
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
            "path": "pa/foo.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        resp = {"stdout": "out", "stderr": "", "interrupted": False, "exitCode": 0}
        post_tool_use.run(
            self._main_inp(tool_response=resp, model="claude-opus-4-6[1m]"),
            self.cfg)
        # confirm the live item exists and is not void
        entry = running.agent(SID, SID)
        rets = entry.get("retrievals") or {}
        carry_keys = [k for k, v in rets.items()
                      if isinstance(v, dict) and v.get("path") == "pa/foo.py"
                      and not v.get("void") and v.get("kind") != "read"]
        self.assertEqual(len(carry_keys), 1, "expected one non-void carry item")

        # Read of the same path on the main thread
        fp = os.path.join(self.root, "pa", "foo.py").replace("\\", "/")
        read_resp = {"type": "text", "file": {"filePath": fp, "content": "x",
                                              "numLines": 1, "startLine": 1, "totalLines": 1}}
        post_tool_use.run(
            self._main_inp(tool="Read", tool_response=read_resp,
                           tool_input={"file_path": fp}, model="claude-opus-4-6[1m]"),
            self.cfg)
        # the carry item should now be void
        entry = running.agent(SID, SID)
        rets = entry.get("retrievals") or {}
        for k, v in rets.items():
            if isinstance(v, dict) and v.get("path") == "pa/foo.py" and v.get("kind") != "read":
                self.assertTrue(v.get("void"),
                                "carry item for pa/foo.py should be voided after Read")


class SeedCarryTest(CreditCase):
    """seed_carry_usd grows per request."""

    def test_seed_carry_grows(self):
        post_tool_use.run(self._inp(), self.cfg)
        entry = running.agent(SID, AGENT_ID)
        carry1 = float(entry.get("seed_carry_usd") or 0)
        # the seed_ctx=5000 and ctx from the transcript; carry should be > 0
        # (seed / ctx > 0, cost > 0)
        self.assertGreater(carry1, 0, "seed_carry_usd should be positive after a request")

        # add a second request to the transcript
        with open(self.agent_transcript, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("msg3", ts="2026-09-22T00:00:02.000Z") + "\n")
        post_tool_use.run(self._inp(), self.cfg)
        entry = running.agent(SID, AGENT_ID)
        carry2 = float(entry.get("seed_carry_usd") or 0)
        self.assertGreater(carry2, carry1, "seed_carry_usd should grow with each request")


class MainLiveFiguresTest(CreditCase):
    """Main-thread n_requests, cost_live_usd and saved_live_usd advance per request."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None, model=None):
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "model": model,
            "transcript_path": self.transcript_path,
        }

    def test_n_after_and_saved_grow(self):
        """One credit item + transcript gaining requests -> n_after, n_requests, saved grow."""
        # Seed a credit item on the main entry
        def _seed(data):
            entry = running.agent_ref(data, SID, SID)
            entry.setdefault("retrievals", {})["ret-1"] = {
                "R": 6127, "n_after": 0, "kind": "script",
                "returned_at": "2026-09-21T23:59:00Z"}
            return data
        running.update(_seed)

        # First request in transcript
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("main1", ts="2026-09-22T00:00:01.000Z") + "\n")

        post_tool_use.run(
            self._main_inp(tool="Bash", model="claude-opus-4-6[1m]"), self.cfg)

        entry = running.agent(SID, SID)
        self.assertEqual(entry.get("n_requests"), 1)
        rets = entry.get("retrievals") or {}
        self.assertEqual(int(rets.get("ret-1", {}).get("n_after", 0)), 1)
        saved1 = float(entry.get("saved_live_usd") or 0)
        self.assertGreater(saved1, 0, "saved_live_usd should be positive after one request")

        # Second request
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("main2", ts="2026-09-22T00:00:02.000Z") + "\n")

        post_tool_use.run(
            self._main_inp(tool="Bash", model="claude-opus-4-6[1m]"), self.cfg)

        entry = running.agent(SID, SID)
        self.assertEqual(entry.get("n_requests"), 2)
        rets = entry.get("retrievals") or {}
        self.assertEqual(int(rets.get("ret-1", {}).get("n_after", 0)), 2)
        saved2 = float(entry.get("saved_live_usd") or 0)
        self.assertGreater(saved2, saved1, "saved_live_usd should grow with more requests")

    def test_same_request_no_double_count(self):
        """The same msg_id on two consecutive PostToolUse calls does not double-count."""
        def _seed(data):
            entry = running.agent_ref(data, SID, SID)
            entry.setdefault("retrievals", {})["ret-1"] = {
                "R": 6127, "n_after": 0, "kind": "script",
                "returned_at": "2026-09-21T23:59:00Z"}
            return data
        running.update(_seed)

        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("main1", ts="2026-09-22T00:00:01.000Z") + "\n")

        post_tool_use.run(
            self._main_inp(tool="Bash", model="claude-opus-4-6[1m]"), self.cfg)
        # second call, same transcript (no new request)
        post_tool_use.run(
            self._main_inp(tool="Edit", model="claude-opus-4-6[1m]"), self.cfg)

        entry = running.agent(SID, SID)
        self.assertEqual(entry.get("n_requests"), 1, "should not double-count the same msg_id")


class ReadDBRowTest(CreditCase):
    """Read PostToolUse writes a kind='read' row to tool_calls (both paths)."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None):
        """Main-thread inp without a model key (the harness never sends one)."""
        inp = {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }
        return inp

    def test_main_thread_read_row(self):
        """A main-thread Read of a project file writes a kind='read' tool_calls row."""
        # Seed the main transcript so req is non-None
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("main-r1", ts="2026-09-22T00:00:01.000Z") + "\n")

        fp = os.path.join(self.root, "pa", "foo.py").replace("\\", "/")
        resp = {"type": "text", "file": {"filePath": fp, "content": "x = 1\n",
                "numLines": 1, "startLine": 1, "totalLines": 1}}
        post_tool_use.run(
            self._main_inp(tool="Read", tool_response=resp,
                           tool_input={"file_path": fp}),
            self.cfg)

        c = self.conn()
        try:
            rows = c.execute("SELECT kind, path, model FROM tool_calls"
                             " WHERE session_id=? AND kind='read'",
                             (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 1, "expected one read row")
        self.assertEqual(rows[0]["kind"], "read")
        self.assertEqual(rows[0]["path"], "pa/foo.py")
        self.assertIsNotNone(rows[0]["model"], "read row should carry a model")

    def test_worker_read_row(self):
        """A worker Read of a project file writes a kind='read' tool_calls row."""
        fp = os.path.join(self.root, "pa", "bar.py").replace("\\", "/")
        resp = {"type": "text", "file": {"filePath": fp, "content": "y = 2\n",
                "numLines": 1, "startLine": 1, "totalLines": 1}}
        post_tool_use.run(
            self._inp(tool="Read", tool_response=resp,
                      tool_input={"file_path": fp}),
            self.cfg)

        c = self.conn()
        try:
            rows = c.execute("SELECT kind, path, model FROM tool_calls"
                             " WHERE session_id=? AND kind='read'",
                             (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 1, "expected one read row for worker Read")
        self.assertEqual(rows[0]["path"], "pa/bar.py")


class StopSurvivalTest(CreditCase):
    """Live items and emission survive a Stop hook call between PostToolUse calls."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None):
        """Main-thread inp, no model key (real harness shape)."""
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }

    def _stop_inp(self):
        return {
            "hook_event_name": "Stop",
            "session_id": SID,
            "last_assistant_message": "ok",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }

    def test_items_survive_stop(self):
        """Two Bash PostToolUse with notes, a Stop, a third PostToolUse:
        items and emission_live_usd survive and saved_live_usd grows."""
        from pa.hooks import stop as _stop

        # First request in transcript
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("m1", ts="2026-09-22T00:00:01.000Z") + "\n")

        # PostToolUse 1: Bash with a credit note
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:01Z", "script": "plan_edit", "kind": "script",
            "path": "phase-ends/current/PHASE_PLAN.md", "file_chars": 24000,
            "removed_chars": 40, "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        resp = {"stdout": "ok", "stderr": "", "interrupted": False, "exitCode": 0}
        post_tool_use.run(self._main_inp(tool_response=resp), self.cfg)

        entry = running.agent(SID, SID)
        rets1 = entry.get("retrievals") or {}
        carry_keys = [k for k, v in rets1.items()
                      if isinstance(v, dict) and v.get("R", 0) > 0]
        self.assertGreater(len(carry_keys), 0, "carry items after first Bash")
        emission1 = float(entry.get("emission_live_usd") or 0)
        self.assertGreater(emission1, 0, "emission_live_usd after first Bash")

        # PostToolUse 2: Bash with another note (same request, no bump)
        self._write_note("2-100.json", {
            "ts": "2026-09-22T00:00:01Z", "script": "task_log", "kind": "script",
            "path": "logs/T1.md", "file_chars": 8000,
            "removed_chars": 10, "added_chars": 20, "anchor_chars": 30, "whole": False,
        })
        post_tool_use.run(self._main_inp(tool_response=resp), self.cfg)

        entry = running.agent(SID, SID)
        rets2 = entry.get("retrievals") or {}
        emission2 = float(entry.get("emission_live_usd") or 0)
        self.assertGreaterEqual(len(rets2), len(carry_keys),
                                "carry items should not shrink after second Bash")
        self.assertGreaterEqual(emission2, emission1,
                                "emission should grow with second note")

        # Stop hook
        _stop.run(self._stop_inp(), self.cfg)

        entry = running.agent(SID, SID)
        rets_after_stop = entry.get("retrievals") or {}
        self.assertEqual(rets_after_stop, rets2,
                         "retrievals should be unchanged after Stop")
        self.assertAlmostEqual(float(entry.get("emission_live_usd") or 0), emission2,
                               places=6, msg="emission should survive Stop")

        # Second request in transcript
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("m2", ts="2026-09-22T00:00:03.000Z") + "\n")

        # PostToolUse 3: Bash with a third note (new request -> bump)
        self._write_note("3-100.json", {
            "ts": "2026-09-22T00:00:03Z", "script": "set_status", "kind": "script",
            "path": "phase-ends/current/STATUS.md", "file_chars": 4000,
            "removed_chars": 100, "added_chars": 200, "anchor_chars": 300, "whole": False,
        })
        post_tool_use.run(self._main_inp(tool_response=resp), self.cfg)

        entry = running.agent(SID, SID)
        rets3 = entry.get("retrievals") or {}
        self.assertGreaterEqual(len(rets3), len(rets2),
                                "carry items should grow or stay after third Bash")
        saved = float(entry.get("saved_live_usd") or 0)
        self.assertGreater(saved, 0, "saved_live_usd should be positive after bump")


class ModelFromTranscriptTest(CreditCase):
    """Main-thread rows carry a model from the transcript, not from inp."""

    def _main_inp(self, tool="Bash", tool_response="output", tool_input=None):
        """No model key (harness's real shape)."""
        return {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "tool_name": tool,
            "tool_response": tool_response,
            "tool_input": tool_input or {},
            "tool_use_id": "tu-main",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }

    def test_model_from_transcript(self):
        """Credit rows and live emission use the transcript's model, not inp."""
        with open(self.transcript_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(_usage_line("m1", model="claude-opus-4-6[1m]",
                                 ts="2026-09-22T00:00:01.000Z") + "\n")

        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:01Z", "script": "plan_edit", "kind": "script",
            "path": "phase-ends/current/PHASE_PLAN.md", "file_chars": 24000,
            "removed_chars": 40, "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        resp = {"stdout": "ok", "stderr": "", "interrupted": False, "exitCode": 0}
        post_tool_use.run(self._main_inp(tool_response=resp), self.cfg)

        # DB row should carry the transcript's model
        c = self.conn()
        try:
            rows = c.execute("SELECT model FROM tool_calls WHERE session_id=?",
                             (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertGreater(len(rows), 0)
        self.assertEqual(rows[0]["model"], "claude-opus-4-6[1m]")

        # Live emission should be positive (priced at the transcript model)
        entry = running.agent(SID, SID)
        self.assertGreater(float(entry.get("emission_live_usd") or 0), 0,
                           "emission should be priced at the transcript model")


class CoderCreditOnlyTest(CreditCase):
    """Coder not in handoff.roles: credit-only path produces rows and live items."""

    def setUp(self):
        super().setUp()
        self.cfg["handoff"] = {"roles": ["expert"]}

    def test_coder_bash_two_notes(self):
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "run", "kind": "script",
            "path": "pa/alpha.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        self._write_note("2-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "commit_task", "kind": "script",
            "path": "pa/beta.py", "file_chars": 2000, "removed_chars": 20,
            "added_chars": 40, "anchor_chars": 60, "whole": False,
        })
        post_tool_use.run(self._inp(tool_response="x" * 200), self.cfg)
        c = self.conn()
        try:
            rows = c.execute("SELECT run_id, path FROM tool_calls "
                             "WHERE session_id=? ORDER BY path", (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["run_id"], AGENT_ID, "credit-only run_id = agent id")
        entry = running.agent(SID, AGENT_ID)
        rets = entry.get("retrievals") or {}
        carry_items = {k: v for k, v in rets.items()
                       if isinstance(v, dict) and v.get("R", 0) > 0}
        self.assertGreater(len(carry_items), 0, "coder entry should have carry items")


class RetrieverReadCreditTest(CreditCase):
    """Retriever Read via credit-only path produces a read row."""

    RETRIEVER_ID = "rrrr1111bbbb2222c"

    def setUp(self):
        super().setUp()
        running.set_agent(SID, self.RETRIEVER_ID, {
            "seed_ctx": 0, "seed_carry_usd": 0.0,
            "agent_type": "retriever-code", "role": "retriever",
            "parent": SID, "model_pinned": "claude-haiku-4-5",
        })

    def test_retriever_read_row(self):
        fp = os.path.join(self.root, "pa", "baz.py").replace("\\", "/")
        resp = {"type": "text", "file": {"filePath": fp, "content": "z = 3\n",
                "numLines": 1, "startLine": 1, "totalLines": 1}}
        inp = {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "agent_id": self.RETRIEVER_ID,
            "agent_type": "retriever-code",
            "tool_name": "Read",
            "tool_response": resp,
            "tool_input": {"file_path": fp},
            "tool_use_id": "tu-r1",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }
        post_tool_use.run(inp, self.cfg)
        c = self.conn()
        try:
            rows = c.execute("SELECT kind, run_id, path FROM tool_calls "
                             "WHERE session_id=? AND kind='read'",
                             (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 1, "expected one read row for retriever Read")
        self.assertEqual(rows[0]["run_id"], self.RETRIEVER_ID)
        self.assertEqual(rows[0]["path"], "pa/baz.py")


class ExpertNoDuplicateTest(CreditCase):
    """Expert via _inside_worker: credit rows produced once, not doubled."""

    EXPERT_ID = "eeee1111bbbb2222c"

    def setUp(self):
        super().setUp()
        self.expert_transcript = os.path.join(
            self.projects_dir, SID, "subagents", "agent-%s.jsonl" % self.EXPERT_ID)
        os.makedirs(os.path.dirname(self.expert_transcript), exist_ok=True)
        with open(self.expert_transcript, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('{"type":"meta","parentAgentId":"%s","sessionId":"%s"}\n' % (SID, SID))
            fh.write(_usage_line("msg-e1") + "\n")
        running.set_agent(SID, self.EXPERT_ID, {
            "seed_ctx": 5000, "seed_carry_usd": 0.0,
            "agent_type": "expert-fable", "role": "expert",
            "parent": SID, "model_pinned": "claude-fable-5-1",
        })

    def test_expert_produces_rows_once(self):
        self._write_note("1-100.json", {
            "ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
            "path": "pa/gamma.py", "file_chars": 4000, "removed_chars": 40,
            "added_chars": 80, "anchor_chars": 120, "whole": False,
        })
        inp = {
            "hook_event_name": "PostToolUse",
            "session_id": SID,
            "agent_id": self.EXPERT_ID,
            "agent_type": "expert-fable",
            "tool_name": "Bash",
            "tool_response": "x" * 200,
            "tool_input": {},
            "tool_use_id": "tu-e1",
            "cwd": self.root,
            "transcript_path": self.transcript_path,
        }
        post_tool_use.run(inp, self.cfg)
        c = self.conn()
        try:
            rows = c.execute("SELECT run_id, path FROM tool_calls "
                             "WHERE session_id=?", (SID,)).fetchall()
        finally:
            db.close(c)
        self.assertEqual(len(rows), 1, "expert should produce exactly one row")
        self.assertEqual(rows[0]["run_id"], self.EXPERT_ID)


class OutlineCreditTest(CreditCase):
    """An outline then the first ranged Read of the same path: one outline-read row."""

    def _outline(self, rel):
        self._write_note("9-100.json", {
            "ts": 1.0, "script": "outline.py", "kind": "outline", "path": rel,
            "file_chars": 90000, "cap_chars": 80000, "emitted_chars": 400,
            "removed_chars": 0, "added_chars": 0, "anchor_chars": 0, "whole": False})
        post_tool_use.run(self._inp(tool_response="o" * 400), self.cfg)

    def _read(self, rel, **rng):
        fp = os.path.join(self.root, rel).replace("\\", "/")
        resp = {"type": "text", "file": {"filePath": fp, "content": "c" * 3600}}
        post_tool_use.run(self._inp(tool="Read", tool_response=resp,
                                    tool_input=dict(file_path=fp, **rng)), self.cfg)
        return resp

    def _kinds(self, rel):
        c = self.conn()
        try:
            return sorted((r["kind"], r["file_tokens"]) for r in c.execute(
                "SELECT kind, file_tokens FROM tool_calls WHERE session_id=? AND path=?",
                (SID, rel)).fetchall())
        finally:
            db.close(c)

    def test_no_credit_without_ranged_read(self):
        self._outline("pa/big.py")
        self._read("pa/big.py")                            # unranged
        self.assertEqual(self._kinds("pa/big.py"), [("outline", 20000), ("read", 0)])

    def test_one_credit_per_outline(self):
        self._outline("pa/big.py")
        resp = self._read("pa/big.py", offset=100, limit=50)
        self._read("pa/big.py", offset=400)
        self.assertEqual(self._kinds("pa/big.py"),
                         [("outline", 20000), ("outline-read", 20000), ("read", 0)])
        from pa import credit, savings
        c = self.conn()
        try:
            carry = [x[2] for x in savings._tool_calls_carry_rows(c, SID)]
        finally:
            db.close(c)
        self.assertEqual(carry, [20000 - 100 - credit.tokens(credit.result_chars(resp))])

    def test_other_path_plain_read(self):
        self._outline("pa/big.py")
        self._read("pa/other.py", limit=10)
        self.assertEqual(self._kinds("pa/other.py"), [("read", 0)])


class LadderMismatchTest(CreditCase):
    """3.10.6 T3: a ladder agent on its tier's model is no ``model_mismatch``."""

    def setUp(self):
        super().setUp()
        old = config._LADDER_TIER
        self.addCleanup(setattr, config, "_LADDER_TIER", old)

    def _events(self, agent, seen, run_id):
        conn = self.conn()
        db.init_schema(conn)
        try:
            post_tool_use._mismatch_check(conn, self.cfg, self._inp(), SID, run_id, agent, seen)
            return conn.execute("SELECT COUNT(*) FROM events WHERE kind='model_mismatch' "
                                "AND run_id=?", (run_id,)).fetchone()[0]
        finally:
            conn.close()

    def test_pro_and_max5_models_no_mismatch(self):
        config._set_ladder_tier("pro")
        self.assertEqual(self._events("critic", "claude-opus-5-5[1m]", "r-pro"), 0)
        config._set_ladder_tier("max5")
        self.assertEqual(self._events("plain", "claude-fable-5-1[1m]", "r-max5"), 0)
        config._set_ladder_tier("max20")
        self.assertEqual(self._events("critic", "claude-opus-5-5[1m]", "r-max20"), 1)


if __name__ == "__main__":
    unittest.main()
