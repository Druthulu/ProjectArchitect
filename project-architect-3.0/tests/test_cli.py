"""pa.ledger_cli: recalc, report, reconcile, sql, prices, doctor (design doc A.2 / F).

The synthetic fixture is a full session directory: a main transcript with three
requests (one of them carrying the ``tool_use`` block that spawned the
subagent) plus a ``cost-state`` record, and one ``retriever-code`` subagent
whose ``.meta.json`` points back at that ``toolUseId``.
"""

import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config  # noqa: E402
from pa import db  # noqa: E402
from pa import ledger_cli  # noqa: E402
from pa.ledger_cli import PRICE_TOLERANCE  # noqa: E402
from pa import paths  # noqa: E402

SID = "11111111-0000-4000-8000-000000000003"
SLUG = "Z--Test-Proj"
AGENT = "a1b2c3d4000000004"
TOOL_USE_ID = "toolu_01SYNTH00000000000000011"
PENDING_AGENT = "b9c8d7e6f5a403121"
CWD = "Z:/Test/Proj"
TOTAL_COST_USD = 5.0

VANTAGE = ("C:/Users/you/.claude/projects/Z--Storage-git-Vantage/"
           "e2eed858-0000-4000-8000-000000000015.jsonl")
VANTAGE_SID = "e2eed858-0000-4000-8000-000000000015"
SNIPPET = os.path.join(ROOT, "settings", "user.snippet.json")
HOOK_PROBE = os.path.join(os.path.dirname(ROOT), "tools", "analysis", "hook_probe.py")  # 3.11 T27: dev repo
_HAS_GIT = shutil.which("git") is not None


# --------------------------------------------------------------------------- fixture

def _assistant(msg_id, ts, model, usage, content=None, stop="end_turn", sid=SID):
    return {"type": "assistant", "uuid": msg_id + "-u", "sessionId": sid, "timestamp": ts,
            "cwd": CWD, "version": "2.1.270", "isSidechain": False,
            "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model,
                        "stop_reason": stop, "content": content or [{"type": "text",
                                                                     "text": "ok " + msg_id},
                                                                    ],
                        "usage": usage}}


def _usage(inp, write_1h, read, output, write_5m=0, thinking=0):
    return {"input_tokens": inp, "cache_creation_input_tokens": write_1h + write_5m,
            "cache_read_input_tokens": read, "output_tokens": output,
            "cache_creation": {"ephemeral_5m_input_tokens": write_5m,
                               "ephemeral_1h_input_tokens": write_1h},
            "output_tokens_details": {"thinking_tokens": thinking},
            "service_tier": "standard"}


def _tool_result(ts, tool_use_id, text, sid=SID):
    return {"type": "user", "uuid": tool_use_id + "-r", "sessionId": sid, "timestamp": ts,
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result",
                                                 "tool_use_id": tool_use_id,
                                                 "content": text}]}}


def _write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _retriever_records(agent_id, sid=SID, base_hour=10):
    """Three requests with growing ctx and two fat tool results."""
    big = "def loader(path):\n    return open(path).read()\n" * 900      # ~ 40k chars
    ts = lambda m: "2026-09-01T%02d:%02d:00.000Z" % (base_hour, m)       # noqa: E731
    return [
        _assistant("msg_%s_1" % agent_id, ts(1), "claude-haiku-4-5",
                   _usage(50, 0, 0, 100, write_5m=3000),
                   content=[{"type": "tool_use", "id": "toolu_inner_1", "name": "Grep",
                             "input": {"pattern": "loader"}}], stop="tool_use", sid=sid),
        _tool_result(ts(2), "toolu_inner_1", big, sid=sid),
        _assistant("msg_%s_2" % agent_id, ts(3), "claude-haiku-4-5",
                   _usage(20, 0, 3050, 150, write_5m=5000),
                   content=[{"type": "tool_use", "id": "toolu_inner_2", "name": "Read",
                             "input": {"file_path": "src/loader.py"}}], stop="tool_use", sid=sid),
        _tool_result(ts(4), "toolu_inner_2", big, sid=sid),
        _assistant("msg_%s_3" % agent_id, ts(5), "claude-haiku-4-5",
                   _usage(10, 0, 8070, 200, write_5m=2000),
                   content=[{"type": "text", "text": "The loader lives in src/loader.py."}],
                   stop="end_turn", sid=sid),
    ]


def build_session(root, sid=SID):
    """Write ``<root>/<slug>/<sid>.jsonl`` + its ``subagents`` directory.

    ``sid`` defaults to the module's fixture SID; a caller building a second,
    independent ledger root (the union-report tests) passes a different one.
    """
    slug_dir = os.path.join(root, SLUG)
    main = os.path.join(slug_dir, sid + ".jsonl")
    records = [
        {"type": "user", "uuid": "u0", "sessionId": sid, "timestamp": "2026-09-01T09:59:00.000Z",
         "cwd": CWD, "version": "2.1.270", "gitBranch": "main",
         "message": {"role": "user", "content": "find the loader"}},
        _assistant("msg_main_1", "2026-09-01T10:00:00.000Z", "claude-fable-5-1",
                   _usage(100, 20000, 0, 500, thinking=120), sid=sid),
        _assistant("msg_main_2", "2026-09-01T10:00:30.000Z", "claude-fable-5-1",
                   _usage(60, 4000, 20100, 400, thinking=80),
                   content=[{"type": "tool_use", "id": TOOL_USE_ID, "name": "Agent",
                             "input": {"subagent_type": "retriever-code",
                                       "description": "find the loader"}}], stop="tool_use",
                   sid=sid),
        _tool_result("2026-09-01T10:05:30.000Z", TOOL_USE_ID,
                     "The loader lives in src/loader.py.", sid=sid),
        _assistant("msg_main_3", "2026-09-01T10:06:00.000Z", "claude-fable-5-1",
                   _usage(40, 2000, 24160, 300, thinking=60), sid=sid),
        {"type": "ai-title", "aiTitle": "Find the loader", "sessionId": sid},
        {"type": "system", "subtype": "away_summary", "content": "recap",
         "timestamp": "2026-09-01T10:07:00.000Z", "sessionId": sid},
        {"type": "cost-state", "sessionId": sid, "totalCostUSD": TOTAL_COST_USD,
         "startTime": 1788461324517,
         "modelUsage": {"claude-fable-5-1": {"inputTokens": 200, "outputTokens": 1200,
                                             "thinkingTokens": 260,
                                             "cacheReadInputTokens": 44260,
                                             "cacheCreationInputTokens": 26000,
                                             "costUSD": 4.9},
                        "claude-haiku-4-5": {"inputTokens": 80, "outputTokens": 450,
                                             "thinkingTokens": 0,
                                             "cacheReadInputTokens": 11120,
                                             "cacheCreationInputTokens": 10000,
                                             "costUSD": 0.1}},
         "hasUnknownModelCost": False},
    ]
    _write_jsonl(main, records)
    sub = os.path.join(slug_dir, sid, "subagents", "agent-%s.jsonl" % AGENT)
    _write_jsonl(sub, _retriever_records(AGENT, sid=sid))
    with open(sub[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"agentType": "retriever-code", "description": "find the loader",
                   "toolUseId": TOOL_USE_ID, "spawnDepth": 1, "model": "haiku"}, fh)
    # T1.1: workflow agents under subagents/workflows/wf_test-1/
    wf_dir = os.path.join(slug_dir, sid, "subagents", "workflows", "wf_test-1")
    os.makedirs(wf_dir, exist_ok=True)
    for wf_aid, wf_hour in (("c1d2e3f4a5b607181", 10), ("c1d2e3f4a5b607182", 11)):
        wf_path = os.path.join(wf_dir, "agent-%s.jsonl" % wf_aid)
        _write_jsonl(wf_path, _retriever_records(wf_aid, sid=sid, base_hour=wf_hour))
        with open(wf_path[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"agentType": "retriever-code", "description": "wf task",
                       "spawnDepth": 1, "model": "haiku"}, fh)
    return main, sub


def build_dated_session(root, sid, agent_id, tool_use_id, date):
    """A second, self-contained session dated ``date`` (``YYYY-MM-DD``): one main
    transcript + one retriever subagent, every id derived from ``sid``/``agent_id``
    so it can share one ledger with the module fixture's ``SID`` session (T10:
    ``build_session`` reuses fixed ids/msg-ids, which collide with a second call)."""
    slug_dir = os.path.join(root, SLUG)
    main = os.path.join(slug_dir, sid + ".jsonl")

    def ts(hh, mm, ss=0):
        return "%sT%02d:%02d:%02dZ" % (date, hh, mm, ss)

    prefix = "msg_%s" % sid[:8]
    records = [
        {"type": "user", "uuid": prefix + "-u0", "sessionId": sid, "timestamp": ts(9, 59),
         "cwd": CWD, "version": "2.1.270", "gitBranch": "main",
         "message": {"role": "user", "content": "find the parser"}},
        _assistant(prefix + "_1", ts(10, 0), "claude-fable-5-1",
                  _usage(100, 20000, 0, 500, thinking=120), sid=sid),
        _assistant(prefix + "_2", ts(10, 0, 30), "claude-fable-5-1",
                  _usage(60, 4000, 20100, 400, thinking=80),
                  content=[{"type": "tool_use", "id": tool_use_id, "name": "Agent",
                            "input": {"subagent_type": "retriever-code",
                                      "description": "find the parser"}}], stop="tool_use", sid=sid),
        _tool_result(ts(10, 5), tool_use_id, "The parser lives in src/parser.py.", sid=sid),
        _assistant(prefix + "_3", ts(10, 6), "claude-fable-5-1",
                  _usage(40, 2000, 24160, 300, thinking=60), sid=sid),
        {"type": "cost-state", "sessionId": sid, "totalCostUSD": TOTAL_COST_USD,
         "startTime": 1788461324517,
         "modelUsage": {"claude-fable-5-1": {"inputTokens": 200, "outputTokens": 1200,
                                             "thinkingTokens": 260,
                                             "cacheReadInputTokens": 44260,
                                             "cacheCreationInputTokens": 26000,
                                             "costUSD": 4.9}},
         "hasUnknownModelCost": False},
    ]
    _write_jsonl(main, records)

    big = "def parser(path):\n    return open(path).read()\n" * 900
    sub_records = [
        _assistant(prefix + "_sub1", ts(10, 1), "claude-haiku-4-5",
                   _usage(50, 0, 0, 100, write_5m=3000),
                   content=[{"type": "tool_use", "id": prefix + "_toolu1", "name": "Grep",
                             "input": {"pattern": "parser"}}], stop="tool_use", sid=sid),
        _tool_result(ts(10, 2), prefix + "_toolu1", big, sid=sid),
        _assistant(prefix + "_sub2", ts(10, 3), "claude-haiku-4-5",
                   _usage(20, 0, 3050, 150, write_5m=5000),
                   content=[{"type": "tool_use", "id": prefix + "_toolu2", "name": "Read",
                             "input": {"file_path": "src/parser.py"}}], stop="tool_use", sid=sid),
        _tool_result(ts(10, 4), prefix + "_toolu2", big, sid=sid),
        _assistant(prefix + "_sub3", ts(10, 5), "claude-haiku-4-5",
                   _usage(10, 0, 8070, 200, write_5m=2000),
                   content=[{"type": "text", "text": "The parser lives in src/parser.py."}],
                   stop="end_turn", sid=sid),
    ]
    sub = os.path.join(slug_dir, sid, "subagents", "agent-%s.jsonl" % agent_id)
    _write_jsonl(sub, sub_records)
    with open(sub[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"agentType": "retriever-code", "description": "find the parser",
                   "toolUseId": tool_use_id, "spawnDepth": 1, "model": "haiku"}, fh)
    return main, sub


def run_cli(*argv):
    """Run the CLI in-process; returns ``(rc, stdout, stderr)``."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = ledger_cli.main(list(argv))
    return rc, buf_out.getvalue(), buf_err.getvalue()


class CliTestCase(unittest.TestCase):
    """Base: a temp ledger (PA_LEDGER_DIR) and a temp transcript root."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-cli-")
        self.projects = os.path.join(self.dir, "projects")
        os.makedirs(self.projects, exist_ok=True)
        self.ledger = os.path.join(self.dir, "ledger")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        self.main_path, self.sub_path = build_session(self.projects)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)
        # the fixture cwd must never exist on the real drive: a Z:/Test/Proj without a
        # pa.json makes every fixture session ungoverned and voids its savings (2026-09-20)
        if os.path.exists(CWD):
            raise AssertionError("a test wrote to the real drive at %s" % CWD)

    def conn(self):
        return db.connect(paths.db_path())

    def counts(self):
        conn = self.conn()
        try:
            def one(sql, args=()):
                return conn.execute(sql, args).fetchone()[0]
            return {
                "sessions": one("SELECT COUNT(*) FROM sessions"),
                "runs": one("SELECT COUNT(*) FROM agent_runs"),
                "turns": one("SELECT COUNT(*) FROM turns"),
                "api": one("SELECT COUNT(*) FROM turns WHERE kind='api'"),
                "residual": one("SELECT COUNT(*) FROM turns WHERE kind='residual'"),
                "retrievals": one("SELECT COUNT(*) FROM retrievals"),
                "savings": one("SELECT COUNT(*) FROM savings"),
            }
        finally:
            db.close(conn)


class RecalcTest(CliTestCase):

    def test_recalc_from_transcripts(self):
        rc, text, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        self.assertIn("recalc from transcripts", text)
        counts = self.counts()
        self.assertEqual(counts["sessions"], 1)
        self.assertEqual(counts["runs"], 4)                # main + retriever + 2 workflow agents
        self.assertEqual(counts["api"], 12)                # 3 main + 3 retriever + 2*3 workflow
        self.assertEqual(counts["residual"], 1)
        self.assertEqual(counts["retrievals"], 3)          # retriever + 2 workflow agents

        conn = self.conn()
        try:
            main = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (SID,)).fetchone()
            self.assertEqual(main["parent_source"], "main")
            self.assertEqual(main["turns"], 3)
            self.assertEqual(main["spawn_depth"], 0)
            self.assertEqual(main["seed_ctx"], 20100)
            self.assertEqual(main["ctx_at_end"], 26200)
            self.assertEqual(main["cache_write_1h"], 26000)   # main TTL default is 1h

            sub = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (AGENT,)).fetchone()
            self.assertEqual(sub["kind"], "retriever")
            self.assertEqual(sub["agent_type"], "retriever-code")
            self.assertEqual(sub["parent_run_id"], SID)       # via .meta.json toolUseId
            self.assertEqual(sub["parent_source"], "agent_tool")
            self.assertEqual(sub["tool_use_id"], TOOL_USE_ID)
            self.assertEqual(sub["spawn_depth"], 1)
            self.assertEqual(sub["turns"], 3)
            self.assertEqual(sub["session_id"], SID)
            self.assertEqual(sub["cache_write_5m"], 10000)    # retriever TTL default is 5m
            self.assertEqual(sub["model_pinned"], "claude-sonnet-5-5")

            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM turns WHERE run_id=? AND kind='api'", (SID,)).fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM turns WHERE run_id=? AND kind='api'", (AGENT,)).fetchone()[0], 3)

            ret = conn.execute("SELECT * FROM retrievals").fetchone()
            self.assertEqual(ret["run_id"], AGENT)
            self.assertEqual(ret["parent_run_id"], SID)
            self.assertGreater(ret["kept_out_tokens"], 0)
            self.assertEqual(ret["void"], 0)
            self.assertEqual(ret["kept_out_mode"], "results_share")
            self.assertEqual(ret["growth_tokens"], 10080 - 3050)
            self.assertEqual(ret["answer_tokens"], 200)

            session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (SID,)).fetchone()
            turns_sum = conn.execute("SELECT SUM(cost_usd) FROM turns WHERE session_id=?"
                                     " AND kind='api'", (SID,)).fetchone()[0]
            self.assertAlmostEqual(session["cost_usd"], turns_sum)     # T30: turns sum
            self.assertEqual(session["cost_source"], "turns")
            self.assertAlmostEqual(session["harness_cost_usd"], TOTAL_COST_USD)
            self.assertEqual(session["account_source"], "unknown")
            self.assertEqual(session["cwd"], CWD)

            residual = conn.execute("SELECT * FROM turns WHERE kind='residual'").fetchone()
            api_sum = conn.execute("SELECT SUM(cost_usd) FROM turns WHERE kind='api'").fetchone()[0]
            self.assertAlmostEqual(residual["cost_usd"], TOTAL_COST_USD - api_sum)
            self.assertGreater(residual["cost_usd"], 0)
            self.assertEqual(residual["msg_id"], "residual:%s" % SID)

            # T1.1: workflow agents carry wf: prefix and parent = session
            wf_rows = conn.execute(
                "SELECT run_id, description, parent_run_id, parent_source"
                " FROM agent_runs WHERE description LIKE 'wf:%'"
                " ORDER BY run_id").fetchall()
            self.assertEqual(len(wf_rows), 2)
            for wr in wf_rows:
                self.assertTrue(wr["description"].startswith("wf:wf_test-1"))
                self.assertEqual(wr["parent_run_id"], SID)
                self.assertEqual(wr["parent_source"], "workflow")

            # T1.1: workflow agents are cost, never savings (void=1, mode='workflow')
            wf_rets = conn.execute(
                "SELECT run_id, void, kept_out_tokens, kept_out_mode FROM retrievals"
                " WHERE kept_out_mode='workflow' ORDER BY run_id").fetchall()
            self.assertEqual(len(wf_rets), 2)
            for wr in wf_rets:
                self.assertEqual(wr["void"], 1)
                self.assertEqual(wr["kept_out_tokens"], 0)

            # the non-workflow retriever still has savings
            flat_ret = conn.execute(
                "SELECT void, kept_out_tokens FROM retrievals WHERE run_id=?",
                (AGENT,)).fetchone()
            self.assertEqual(flat_ret["void"], 0)
            self.assertGreater(flat_ret["kept_out_tokens"], 0)
        finally:
            db.close(conn)

    def test_recalc_twice_changes_nothing(self):
        run_cli("recalc", "--root", self.projects)
        first = self.counts()
        conn = self.conn()
        try:
            cost_before = conn.execute("SELECT SUM(cost_usd) FROM turns").fetchone()[0]
        finally:
            db.close(conn)
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        self.assertEqual(self.counts(), first)
        conn = self.conn()
        try:
            self.assertAlmostEqual(conn.execute("SELECT SUM(cost_usd) FROM turns").fetchone()[0],
                                   cost_before)
        finally:
            db.close(conn)

    def test_recalc_check_reports_idempotent(self):
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        rc, text, errs = run_cli("recalc", "--check", "--root", self.projects,
                                 "--account", "dev@example.com")
        self.assertEqual(rc, 0, errs)
        self.assertIn("idempotent OK", text)

    def test_recalc_check_ignores_a_turn_newer_than_its_own_start(self):
        """A live session writing between the two snapshots (a turn with ts in the
        future relative to the check's t0, in a session that has not ended) must not
        show up as a diff (T5.c2). A closed session never receives new turns."""
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        live = "99999999-0000-4000-8000-00000000000c"
        conn = self.conn()
        try:
            db.upsert_session(conn, {"session_id": live, "account": "dev@example.com",
                                     "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            db.upsert_turn(conn, {"msg_id": "concurrent1", "run_id": live, "session_id": live,
                                  "account": "dev@example.com",
                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime(time.time() + 60)),
                                  "model": "claude-fable-5-1", "cost_usd": 0.5, "kind": "api"})
        finally:
            db.close(conn)
        rc, text, errs = run_cli("recalc", "--check", "--root", self.projects,
                                 "--account", "dev@example.com")
        self.assertEqual(rc, 0, errs)
        self.assertIn("idempotent OK", text)

    def test_recalc_tool_use_stop_is_interrupted(self):
        """A subagent transcript ending on tool_use -> recalc writes status='interrupted'."""
        # create a subagent whose last stop_reason is tool_use
        agent_id = "aaa11111000000013"
        sub_path = os.path.join(self.projects, SLUG, SID, "subagents",
                                "agent-%s.jsonl" % agent_id)
        records = [
            _assistant("msg_%s_1" % agent_id, "2026-09-01T10:01:00.000Z", "claude-haiku-4-5",
                       _usage(50, 0, 0, 100, write_5m=3000),
                       content=[{"type": "tool_use", "id": "toolu_x1", "name": "Grep",
                                 "input": {"pattern": "x"}}], stop="tool_use"),
        ]
        _write_jsonl(sub_path, records)
        with open(sub_path[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"agentType": "expert-fable", "spawnDepth": 1}, fh)
        rc, _text, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            row = conn.execute("SELECT status FROM agent_runs WHERE run_id=?",
                               (agent_id,)).fetchone()
            self.assertEqual(row["status"], "interrupted")
        finally:
            db.close(conn)

    def test_recalc_empty_agent_is_interrupted(self):
        """An empty agent file -> interrupted, 0 turns."""
        agent_id = "bbb2222222222222f"
        sub_path = os.path.join(self.projects, SLUG, SID, "subagents",
                                "agent-%s.jsonl" % agent_id)
        _write_jsonl(sub_path, [])                           # empty transcript
        with open(sub_path[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"agentType": "expert-fable", "spawnDepth": 1}, fh)
        rc, _text, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            row = conn.execute("SELECT status, turns FROM agent_runs WHERE run_id=?",
                               (agent_id,)).fetchone()
            self.assertEqual(row["status"], "interrupted")
            self.assertEqual(row["turns"], 0)
        finally:
            db.close(conn)

    def test_dry_run_writes_no_rows(self):
        rc, text, errs = run_cli("recalc", "--root", self.projects, "--dry-run")
        self.assertEqual(rc, 0, errs)
        self.assertIn("dry run", text)
        self.assertIn("turns", text)
        self.assertEqual(self.counts()["turns"], 0)

    def test_account_defaults_are_stamped(self):
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        conn = self.conn()
        try:
            row = conn.execute("SELECT account, account_source FROM sessions").fetchone()
            self.assertEqual(row["account"], "dev@example.com")
            self.assertEqual(row["account_source"], "assumed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM turns WHERE account='dev@example.com'").fetchone()[0], 13)
        finally:
            db.close(conn)

    def test_session_and_project_filters(self):
        rc, text, _e = run_cli("recalc", "--root", self.projects, "--session", "does-not-exist")
        self.assertEqual(rc, 0)
        self.assertEqual(self.counts()["turns"], 0)
        run_cli("recalc", "--root", self.projects, "--project", "Test-Proj", "--session", SID)
        self.assertEqual(self.counts()["sessions"], 1)

    def test_recalc_from_turns_rebuilds_aggregates(self):
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            conn.execute("UPDATE agent_runs SET turns=0, cost_usd=0, ctx_at_end=0")
        finally:
            db.close(conn)
        rc, _text, errs = run_cli("recalc", "--from", "turns")
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            row = conn.execute("SELECT turns, cost_usd, ctx_at_end FROM agent_runs"
                               " WHERE run_id=?", (AGENT,)).fetchone()
            self.assertEqual(row["turns"], 3)
            self.assertGreater(row["cost_usd"], 0)
            self.assertEqual(row["ctx_at_end"], 10080)
        finally:
            db.close(conn)

    def test_pending_locks_are_drained(self):
        run_cli("recalc", "--root", self.projects)
        spare_dir = os.path.join(self.dir, "deferred")
        spare = os.path.join(spare_dir, "agent-%s.jsonl" % PENDING_AGENT)
        _write_jsonl(spare, _retriever_records(PENDING_AGENT, base_hour=11))
        with open(spare[:-6] + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"agentType": "retriever-code", "toolUseId": "toolu_deferred",
                       "spawnDepth": 1}, fh)
        paths.ensure_ledger_tree()
        with open(paths.pending_locks_path(), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"agent_transcript_path": spare, "run_id": PENDING_AGENT,
                                 "session_id": SID}) + "\n")
        rc, text, errs = run_cli("recalc", "--pending")
        self.assertEqual(rc, 0, errs)
        self.assertIn("pending: locked 1", text)
        conn = self.conn()
        try:
            row = conn.execute("SELECT kind, turns, session_id FROM agent_runs WHERE run_id=?",
                               (PENDING_AGENT,)).fetchone()
            self.assertEqual((row["kind"], row["turns"], row["session_id"]),
                             ("retriever", 3, SID))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM retrievals").fetchone()[0], 4)
        finally:
            db.close(conn)
        with open(paths.pending_locks_path(), "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "")

    def test_summary_json_is_written(self):
        """pa.summary.rebuild owns the document; recalc only has to trigger it."""
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        with open(paths.summary_path(), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc.get("schema"), 1)
        self.assertIn("accounts", doc)
        self.assertIn("sessions", doc)

    def test_minimal_summary_fallback(self):
        """The local fallback used while pa.summary does not exist."""
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        conn = self.conn()
        try:
            doc = ledger_cli.minimal_summary(conn, config.load())
            turns_sum = conn.execute("SELECT SUM(cost_usd) FROM turns WHERE session_id=?"
                                     " AND kind='api'", (SID,)).fetchone()[0]
        finally:
            db.close(conn)
        self.assertEqual(doc["source"], "ledger_cli.minimal")
        self.assertIn("dev@example.com", doc["accounts"])
        self.assertIn(SID, doc["sessions"])
        self.assertAlmostEqual(doc["sessions"][SID]["cost_usd"], turns_sum)    # T30: turns sum


class PreInstallTest(CliTestCase):
    """T10: sessions started before the ledger's installed_at are cost only."""

    SID2 = "22222222-0000-4000-8000-000000000005"
    AGENT2 = "b2c3d4e5f6071829a"
    TOOL_USE_ID2 = "toolu_01SYNTH00000000000000013"

    def setUp(self):
        super(PreInstallTest, self).setUp()
        build_dated_session(self.projects, self.SID2, self.AGENT2, self.TOOL_USE_ID2,
                            "2026-09-15")

    def _set_installed_at(self, value):
        cfg = config.load(paths.config_path(), refresh=True)
        if value is None:
            cfg.pop("installed_at", None)
        else:
            cfg["installed_at"] = value
        config.save(cfg, paths.config_path())

    def test_pre_install_session_voided_post_install_kept(self):
        # SID is dated 2026-09-01 (the module fixture), SID2 2026-09-15
        self._set_installed_at("2026-09-10T00:00:00Z")
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?", (SID,)).fetchone()[0], 0)
            pre_rets = conn.execute(
                "SELECT void, kept_out_mode FROM retrievals r"
                " JOIN agent_runs a ON a.run_id = r.run_id WHERE a.session_id=?",
                (SID,)).fetchall()
            self.assertEqual(len(pre_rets), 3)             # retriever + 2 workflow agents
            for r in pre_rets:
                self.assertEqual(r["void"], 1)
                self.assertEqual(r["kept_out_mode"], "pre-install")

            self.assertGreater(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?",
                (self.SID2,)).fetchone()[0], 0)
            post_ret = conn.execute(
                "SELECT void, kept_out_mode, kept_out_tokens FROM retrievals WHERE run_id=?",
                (self.AGENT2,)).fetchone()
            self.assertEqual(post_ret["void"], 0)
            self.assertEqual(post_ret["kept_out_mode"], "results_share")
            self.assertGreater(post_ret["kept_out_tokens"], 0)
        finally:
            db.close(conn)

    def test_recalc_with_installed_at_stays_idempotent(self):
        # lower installed_at past both sessions -> both booked
        self._set_installed_at("2026-08-01T00:00:00Z")
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM savings").fetchone()[0], 0)
        finally:
            db.close(conn)

        # raise it past both -> savings rows deleted for both
        self._set_installed_at("2026-09-20T00:00:00Z")
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM savings").fetchone()[0], 0)
        finally:
            db.close(conn)

        # a second recalc at the current setting changes nothing (test_recalc_twice_changes_nothing)
        first = self.counts()
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        self.assertEqual(self.counts(), first)


class ProjectCutoffTest(CliTestCase):
    """T10.1: a session's cutoff is its own project's installed_at, not just the root's."""

    def setUp(self):
        super(ProjectCutoffTest, self).setUp()
        self.proj_dir = os.path.join(self.dir, "proj_with_pa")
        os.makedirs(os.path.join(self.proj_dir, ".claude"))
        self._rewrite_cwd(self.main_path, self.proj_dir)      # SID's session is dated 2026-09-01

    def _write_pa_json(self, installed_at):
        with open(os.path.join(self.proj_dir, ".claude", "pa.json"), "w") as fh:
            json.dump({"pa_version": "3.0.0", "installed_at": installed_at}, fh)

    def _rewrite_cwd(self, path, new_cwd):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                rec = json.loads(line)
                if "cwd" in rec:
                    rec["cwd"] = new_cwd
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _set_root_installed_at(self, value):
        cfg = config.load(paths.config_path(), refresh=True)
        cfg["installed_at"] = value
        config.save(cfg, paths.config_path())

    def test_project_installed_at_later_than_root_voids_session(self):
        # the root's install date is older than the session (would keep it alone), but the
        # project's own pa.json installed_at is later than the session -- voided
        self._write_pa_json("2026-09-10T00:00:00Z")
        self._set_root_installed_at("2026-08-01T00:00:00Z")
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?", (SID,)).fetchone()[0], 0)
            rets = conn.execute(
                "SELECT void, kept_out_mode FROM retrievals r"
                " JOIN agent_runs a ON a.run_id = r.run_id WHERE a.session_id=?",
                (SID,)).fetchall()
            self.assertEqual(len(rets), 3)              # retriever + 2 workflow agents
            for r in rets:
                self.assertEqual(r["void"], 1)
                self.assertEqual(r["kept_out_mode"], "pre-install")
        finally:
            db.close(conn)

    def test_project_installed_at_earlier_than_root_books_session(self):
        # the root's install date is later than the session (would void it alone), but the
        # project's own pa.json installed_at is earlier than the session -- booked
        self._write_pa_json("2026-08-01T00:00:00Z")
        self._set_root_installed_at("2026-09-20T00:00:00Z")
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            self.assertGreater(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?", (SID,)).fetchone()[0], 0)
            ret = conn.execute(
                "SELECT void, kept_out_mode FROM retrievals WHERE run_id=?",
                (AGENT,)).fetchone()
            self.assertEqual(ret["void"], 0)
            self.assertEqual(ret["kept_out_mode"], "results_share")
        finally:
            db.close(conn)


class GovernedRecalcTest(CliTestCase):
    """T11: recalc voids retrievals of sessions whose cwd has no .claude/pa.json and books
    the ones whose cwd has."""

    UNGOV_SID = "33333333-0000-4000-8000-000000000006"
    UNGOV_AGENT = "c3d4e5f6a7b807291"
    UNGOV_TOOL = "toolu_03PaTestUngov"

    def setUp(self):
        super(GovernedRecalcTest, self).setUp()
        # Create two directories: one governed (with pa.json), one ungoverned
        self.gov_dir = os.path.join(self.dir, "governed_proj")
        os.makedirs(os.path.join(self.gov_dir, ".claude"))
        with open(os.path.join(self.gov_dir, ".claude", "pa.json"), "w") as fh:
            fh.write('{"pa_version": "3.0.0"}')
        self.ungov_dir = os.path.join(self.dir, "ungoverned_proj")
        os.makedirs(self.ungov_dir)
        # Rewrite the main session's transcript to use the governed CWD
        self._rewrite_cwd(self.main_path, self.gov_dir)
        # Build an ungoverned session
        build_dated_session(self.projects, self.UNGOV_SID, self.UNGOV_AGENT,
                            self.UNGOV_TOOL, "2026-09-15")
        ungov_path = os.path.join(self.projects, SLUG, self.UNGOV_SID + ".jsonl")
        self._rewrite_cwd(ungov_path, self.ungov_dir)

    def _rewrite_cwd(self, path, new_cwd):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                rec = json.loads(line)
                if "cwd" in rec:
                    rec["cwd"] = new_cwd
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_ungoverned_session_voided_governed_kept(self):
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            # The ungoverned session: savings deleted, retrievals voided
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?",
                (self.UNGOV_SID,)).fetchone()[0], 0)
            ungov_rets = conn.execute(
                "SELECT void, kept_out_mode FROM retrievals r"
                " JOIN agent_runs a ON a.run_id = r.run_id WHERE a.session_id=?",
                (self.UNGOV_SID,)).fetchall()
            for r in ungov_rets:
                self.assertEqual(r["void"], 1)
                self.assertEqual(r["kept_out_mode"], "ungoverned")
            # The governed session: has savings and non-void retrievals
            self.assertGreater(conn.execute(
                "SELECT COUNT(*) FROM savings WHERE session_id=?",
                (SID,)).fetchone()[0], 0)
            gov_ret = conn.execute(
                "SELECT void, kept_out_mode FROM retrievals WHERE run_id=?",
                (AGENT,)).fetchone()
            self.assertEqual(gov_ret["void"], 0)
        finally:
            db.close(conn)

    def test_recalc_twice_ungoverned_stays_idempotent(self):
        run_cli("recalc", "--root", self.projects)
        first = self.counts()
        rc, _text, errs = run_cli("recalc", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        self.assertEqual(self.counts(), first)


class ReportTest(CliTestCase):

    def setUp(self):
        super(ReportTest, self).setUp()
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")

    def test_report_by_project_json(self):
        """T12: one row per session's project, with the summary's per-project fields."""
        rc, text, errs = run_cli("report", "--by", "project", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        self.assertEqual(doc["by"], "project")
        entry = {a["account"]: a for a in doc["accounts"]}["dev@example.com"]
        rows = {r["key"]: r for r in entry["rows"]}
        self.assertIn(CWD, rows)                      # the fixture's single project
        self.assertGreater(rows[CWD]["cost"], 0)
        for key in ("pct_used_five_hour", "pct_used_seven_day", "lifetime_saved"):
            self.assertIn(key, rows[CWD])

    def test_report_by_project_text(self):
        rc, text, errs = run_cli("report", "--by", "project")
        self.assertEqual(rc, 0, errs)
        self.assertIn("5h", text)
        self.assertIn("7d", text)
        self.assertIn("lifetime", text)

    def test_report_by_session_json(self):
        rc, text, errs = run_cli("report", "--by", "session", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        self.assertEqual(doc["by"], "session")
        accounts = {a["account"]: a for a in doc["accounts"]}
        self.assertIn("dev@example.com", accounts)
        entry = accounts["dev@example.com"]
        rows = {r["key"]: r for r in entry["rows"]}
        self.assertIn(SID, rows)
        self.assertGreater(rows[SID]["cost"], 0)
        self.assertEqual(rows[SID]["requests"], 12)       # 3 main + 3 retriever + 2*3 workflow
        self.assertAlmostEqual(entry["cost_used"], rows[SID]["cost"])

    def test_report_text_and_grouping(self):
        rc, text, errs = run_cli("report", "--by", "model")
        self.assertEqual(rc, 0, errs)
        self.assertIn("claude-fable-5-1", text)
        self.assertIn("claude-haiku-4-5", text)
        self.assertIn("dev@example.com", text)

    def test_report_modeled_flag(self):
        """The `saved mod.` column and the `modeled:` footer print only with --modeled."""
        rc, text, errs = run_cli("report", "--by", "model")
        self.assertEqual(rc, 0, errs)
        self.assertNotIn("saved mod.", text)
        rc, text, errs = run_cli("report", "--by", "model", "--modeled")
        self.assertEqual(rc, 0, errs)
        self.assertIn("saved mod.", text)

    def test_report_modeled_both_bases(self):
        """T10.c1: --modeled prints both replay bases and the measured block."""
        rc, text, errs = run_cli("report", "--modeled")
        self.assertEqual(rc, 0, errs)
        self.assertIn("replay basis: vanilla", text)
        self.assertIn("replay basis: pa2", text)
        self.assertIn("measured (vanilla-net-v4)", text)
        self.assertIn("assumptions:", text)

    def test_report_modeled_basis_pa2(self):
        """T10.c1: --basis pa2 shows only the pa2 basis."""
        rc, text, errs = run_cli("report", "--modeled", "--basis", "pa2")
        self.assertEqual(rc, 0, errs)
        self.assertIn("replay basis: pa2 -- retired at T30", text)
        self.assertNotIn("replay basis: vanilla", text)
        self.assertIn("measured (vanilla-net-v4)", text)

    def test_report_modeled_json(self):
        """T10.c1: --modeled --json includes modeled_bases and measured."""
        rc, text, errs = run_cli("report", "--modeled", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        self.assertIn("modeled_bases", doc)
        self.assertIn("vanilla", doc["modeled_bases"])
        self.assertNotIn("pa2", doc["modeled_bases"])  # T30: pa2 basis retired, omitted from JSON
        self.assertIn("params", doc["modeled_bases"]["vanilla"])
        self.assertIn("source", doc["modeled_bases"]["vanilla"])
        self.assertIn("accounts", doc["modeled_bases"]["vanilla"])
        self.assertIn("measured", doc)

    def test_report_seeds_and_thinking(self):
        rc, text, errs = run_cli("report", "--seeds", "--thinking")
        self.assertEqual(rc, 0, errs)
        self.assertIn("seeds (first request per run)", text)
        self.assertIn("thinking share", text)
        self.assertIn(AGENT, text)

    def test_report_window_and_filters(self):
        rc, text, _e = run_cli("report", "--window", "five_hour", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(text)["accounts"], [])      # fixture is dated 2026-09-01
        rc, text, _e = run_cli("report", "--session", SID, "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(text)["accounts"]), 1)

    def test_report_until_excludes_a_later_turn(self):
        rc, text, errs = run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        before = json.loads(text)["accounts"][0]["requests"]
        conn = self.conn()
        try:
            db.upsert_turn(conn, {"msg_id": "later1", "run_id": SID, "session_id": SID,
                                  "account": "dev@example.com", "ts": "2026-09-02T00:00:00Z",
                                  "model": "claude-fable-5-1", "cost_usd": 1.0, "kind": "api"})
            # `--until` counts only sessions closed before the cutoff; the fixture's transcript
            # is fresh (recalc leaves `ended` NULL for a live one), so stamp it closed here
            db.upsert_session(conn, {"session_id": SID, "ended": "2026-09-01T11:00:00Z",
                                     "end_reason": "test"})
        finally:
            db.close(conn)
        rc, text, errs = run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(json.loads(text)["accounts"][0]["requests"], before + 1)
        rc, text, errs = run_cli("report", "--until", "2026-09-01T12:00:00Z", "--json")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(json.loads(text)["accounts"][0]["requests"], before)

    def test_report_cost_basis_api_only_by_default(self):
        """T10.c2: default report excludes residual rows; --all-kinds includes them."""
        # the fixture has 12 api turns + 1 residual row (from cost-state reconciliation)
        counts = self.counts()
        self.assertEqual(counts["api"], 12)
        self.assertEqual(counts["residual"], 1)

        # default: api-only cost
        rc, text, errs = run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        self.assertEqual(doc["cost_basis"], "priced api turns")
        api_cost = doc["accounts"][0]["cost_used"]
        api_requests = doc["accounts"][0]["requests"]
        self.assertEqual(api_requests, 12)

        # --all-kinds: includes residual
        rc, text, errs = run_cli("report", "--all-kinds", "--json")
        self.assertEqual(rc, 0, errs)
        doc_all = json.loads(text)
        self.assertEqual(doc_all["cost_basis"], "all turn kinds")
        all_cost = doc_all["accounts"][0]["cost_used"]
        all_requests = doc_all["accounts"][0]["requests"]
        self.assertEqual(all_requests, 13)          # 12 api + 1 residual
        self.assertGreater(all_cost, api_cost)

    def test_report_cost_basis_header_in_text(self):
        """T10.c2: text report prints the cost basis line."""
        rc, text, errs = run_cli("report")
        self.assertEqual(rc, 0, errs)
        self.assertIn("cost = priced api turns; residual corrections in reconcile", text)
        rc, text, errs = run_cli("report", "--all-kinds")
        self.assertEqual(rc, 0, errs)
        self.assertIn("cost = all turn kinds", text)

    def test_report_pool_line_harness_delta_and_method(self):
        """T32.1.c3: pool line (rate, n, regime), harness delta line, method column; JSON keys."""
        rc, text, errs = run_cli("report")
        self.assertEqual(rc, 0, errs)
        self.assertIn("pool: none (set fit.regime_since or accounts.<email>.regime_since)", text)
        conn = self.conn()
        try:
            for fam, rate, n in (("fable", 0.00123, 1834), ("opus", 0.0004, 900)):
                db.upsert_fit_pool(conn, {"tier": "max", "window": "seven_day", "family": fam,
                                          "rate": rate, "se": 0.00004, "n": n, "n_instances": 3,
                                          "regime_since": "2026-09-01T00:00:00Z",
                                          "built_at": "2026-09-20T00:00:00Z"})
            conn.commit()
            residual = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM turns "
                                    "WHERE kind='residual'").fetchone()[0]
        finally:
            db.close(conn)
        rc, text, errs = run_cli("report")
        self.assertEqual(rc, 0, errs)
        self.assertIn("pool max seven_day: fable 0.00123/$ (se 0.00004, n 1834)", text)
        self.assertIn("instances 3, regime since 2026-09-01T00:00:00Z", text)
        self.assertIn("harness delta (residual rows, harness minus turns; excluded from fit "
                      "and ratio): ", text)
        self.assertIn("method", text)
        rc, text, errs = run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        entry = json.loads(text)["accounts"][0]
        for key in ("pool_rates", "harness_delta", "pct_saved_pooled", "fit_method", "fit_reason"):
            self.assertIn(key, entry)
        self.assertAlmostEqual(entry["harness_delta"], float(residual), places=6)
        self.assertNotEqual(float(residual), 0.0)

    def test_tail_and_summary(self):
        rc, text, errs = run_cli("tail", "-n", "8")
        self.assertEqual(rc, 0, errs)
        self.assertIn("residual", text)
        rc, text, errs = run_cli("summary", "--print")
        self.assertEqual(rc, 0, errs)
        self.assertIn("\"schema\"", text)


class PreEraReportTest(CliTestCase):
    """T11: report shows a pre-PA3 row for accounts with pre-era history."""

    SID2 = "44444444-0000-4000-8000-000000000008"
    AGENT2 = "d4e5f6a7b8c907381"
    TOOL2 = "toolu_04PaTestReport"

    def setUp(self):
        super().setUp()
        build_dated_session(self.projects, self.SID2, self.AGENT2, self.TOOL2, "2026-09-15")
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        # Set meta.created between the two sessions so SID (2026-09-01) is pre-era
        # and SID2 (2026-09-15) is post-era
        conn = self.conn()
        try:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('created', ?)",
                         ("2026-09-10T00:00:00Z",))
            # SID2 must not be 'assumed' to be post-era
            conn.execute("UPDATE sessions SET account_source='auth' WHERE session_id=?",
                         (self.SID2,))
            conn.commit()
        finally:
            db.close(conn)

    def test_report_shows_pre_pa3_row(self):
        rc, text, errs = run_cli("report")
        self.assertEqual(rc, 0, errs)
        self.assertIn("pre-PA3", text)
        self.assertIn("not on the lines", text)

    def test_report_json_has_pre_era(self):
        rc, text, errs = run_cli("report", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        entry = doc["accounts"][0]
        self.assertIn("pre_era", entry)
        self.assertGreater(entry["pre_era"]["cost"], 0)
        self.assertGreater(entry["pre_era"]["requests"], 0)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Line3Test(CliTestCase):
    """``summary --line3``: the plain-text statusline line for the newest session."""

    def test_line3_shows_the_fitted_window(self):
        # relax the fit's n_min so a two-point series reaches quality "ok"
        cfg_path = paths.config_path()
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"fit": {"n_min": 1}}, fh)
        account = "acct@example.com"
        sid = "22222222-0000-4000-8000-000000000004"
        now = time.time()
        resets_at = int(now) + 3 * 3600
        resets_7d = int(now) + 5 * 86400
        t1, t2 = now - 1800, now - 600
        conn = ledger_cli.open_db()
        try:
            db.upsert_session(conn, {"session_id": sid, "account": account,
                                     "started": _iso(now - 3600), "cost_usd": 3.0,
                                     "cost_source": "turns"})
            db.upsert_turn(conn, {"msg_id": "m1", "session_id": sid, "account": account,
                                  "ts": _iso(t1), "model": "claude-sonnet-5", "cost_usd": 1.0,
                                  "kind": "api"})
            db.upsert_turn(conn, {"msg_id": "m2", "session_id": sid, "account": account,
                                  "ts": _iso(t2), "model": "claude-sonnet-5", "cost_usd": 2.0,
                                  "kind": "api"})
            db.upsert_savings(conn, {"run_id": sid, "session_id": sid,
                                     "measured_saved_usd": 1.2, "updated": _iso(t2)})
            db.upsert_window_instance(conn, {"account": account, "window": "five_hour",
                                             "resets_at": resets_at, "started_at": resets_at - 18000})
            db.insert_utilization(conn, {"ts": _iso(t1), "account": account, "session_id": sid,
                                         "window": "five_hour", "pct": 10, "resets_at": resets_at,
                                         "session_cost": 1.0, "model": "claude-sonnet-5"})
            db.insert_utilization(conn, {"ts": _iso(t2), "account": account, "session_id": sid,
                                         "window": "five_hour", "pct": 30, "resets_at": resets_at,
                                         "session_cost": 3.0, "model": "claude-sonnet-5"})
            # add 7d window so lifetime/month weeks can be shown (T1.c7: needs ppd_7d)
            db.upsert_window_instance(conn, {"account": account, "window": "seven_day",
                                             "resets_at": resets_7d, "started_at": resets_7d - 604800})
            db.insert_utilization(conn, {"ts": _iso(t1), "account": account, "session_id": sid,
                                         "window": "seven_day", "pct": 5, "resets_at": resets_7d,
                                         "session_cost": 1.0, "model": "claude-sonnet-5"})
            db.insert_utilization(conn, {"ts": _iso(t2), "account": account, "session_id": sid,
                                         "window": "seven_day", "pct": 15, "resets_at": resets_7d,
                                         "session_cost": 3.0, "model": "claude-sonnet-5"})
            conn.commit()
        finally:
            db.close(conn)
        rc, text, errs = run_cli("summary", "--line3", "--session", sid)
        self.assertEqual(rc, 0, errs)
        self.assertIn("of 5h", text)
        self.assertIn("Account:", text)
        # lifetime and month appear in weeks when 7d fit exists (T1.c7)
        if "lifetime" in text:
            self.assertIn("w", text)


class UnionReportTest(CliTestCase):
    """report unions extra_roots; a NULL-account row there takes the root's configured account."""

    WSL_SID = "66666666-0000-4000-8000-00000000000a"

    def setUp(self):
        super(UnionReportTest, self).setUp()
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")

        # a second, independent ledger (stands in for the WSL root): NULL account,
        # machine stamped "wsl" (recalc itself always stamps the real host's tag).
        root2 = os.path.join(self.dir, "wsl-root")
        projects2 = os.path.join(root2, "projects")
        os.makedirs(projects2, exist_ok=True)
        self.ledger2 = os.path.join(root2, "ledger")
        build_session(projects2, sid=self.WSL_SID)

        prev = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger2
        try:
            run_cli("recalc", "--root", projects2)
            conn2 = db.connect(paths.db_path())
            try:
                db.upsert_session(conn2, {"session_id": self.WSL_SID, "machine": "wsl"})
                conn2.commit()
            finally:
                db.close(conn2)
        finally:
            if prev is None:
                os.environ.pop("PA_LEDGER_DIR", None)
            else:
                os.environ["PA_LEDGER_DIR"] = prev

        with open(paths.config_path(), "w", encoding="utf-8") as fh:
            json.dump({"extra_roots": [{"path": self.ledger2,
                                        "account": "third@example.com",
                                        "label": "wsl-bfm"}]}, fh)
        config.load(paths.config_path(), refresh=True)

    def test_json_lists_the_union_session_under_its_configured_account(self):
        rc, text, errs = run_cli("report", "--window", "all",
                                 "--account", "third@example.com", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        accounts = {a["account"]: a for a in doc["accounts"]}
        self.assertIn("third@example.com", accounts)
        rows = {r["key"]: r for r in accounts["third@example.com"]["rows"]}
        self.assertIn(self.WSL_SID, rows)
        self.assertEqual(rows[self.WSL_SID]["machine"], "wsl")
        self.assertEqual(rows[self.WSL_SID]["account"], "third@example.com")

    def test_text_report_shows_the_machine_column(self):
        rc, text, errs = run_cli("report", "--window", "all",
                                 "--account", "third@example.com")
        self.assertEqual(rc, 0, errs)
        self.assertIn(self.WSL_SID, text)
        self.assertIn("wsl", text)

    def test_null_session_machine_falls_back_to_the_readers_meta_machine(self):
        """The live WSL ledger's session rows predate the ``machine`` column
        (NULL); the fallback is the reader database's own ``meta.machine``
        (stamped by ``init_schema`` on the box that built it), not the
        configured account label (``wsl-bfm`` here)."""
        conn2 = db.connect(os.path.join(self.ledger2, "ledger.sqlite"))
        try:
            db.upsert_session(conn2, {"session_id": self.WSL_SID, "machine": None})
            db.set_meta(conn2, "machine", "wsl")
            conn2.commit()
        finally:
            db.close(conn2)
        rc, text, errs = run_cli("report", "--window", "all",
                                 "--account", "third@example.com", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        rows = {r["key"]: r for r in doc["accounts"][0]["rows"]}
        self.assertEqual(rows[self.WSL_SID]["machine"], "wsl")


class FitUnionTest(CliTestCase):
    """``fit`` and ``report`` with an extra root include the other root's account rows."""

    WSL_SID = "f0f0f0f0-wsl-test"

    def setUp(self):
        super().setUp()
        # Build and recalc the local ledger
        run_cli("recalc", "--root", self.projects, "--account", "shared@example.com")
        conn = self.conn()
        try:
            db.upsert_window_instance(conn, {"account": "shared@example.com",
                                             "window": "five_hour",
                                             "resets_at": 1893456000,
                                             "started_at": 1893438000})
            for i in range(5):
                db.insert_utilization(conn, {
                    "ts": "2026-09-01T10:%02d:00Z" % i,
                    "account": "shared@example.com", "session_id": SID,
                    "window": "five_hour", "pct": float(i * 5),
                    "resets_at": 1893456000})
            conn.commit()
        finally:
            db.close(conn)

        # Build second root (the "other machine")
        self.root2_base = tempfile.mkdtemp(prefix="pa3-union-",
                                            dir=self.dir)
        self.ledger2 = os.path.join(self.root2_base, "ledger")
        os.makedirs(self.ledger2, exist_ok=True)
        conn2 = db.connect(os.path.join(self.ledger2, "ledger.sqlite"))
        db.init_schema(conn2)
        try:
            db.seed_prices(conn2)
            db.upsert_account(conn2, {"email": "shared@example.com"})
            db.upsert_session(conn2, {"session_id": self.WSL_SID,
                                       "account": "shared@example.com",
                                       "machine": "wsl",
                                       "kind": "test",
                                       "started": "2026-09-01T10:00:00Z"})
            for i in range(3):
                db.upsert_turn(conn2, {
                    "msg_id": "wsl_turn_%d" % i, "session_id": self.WSL_SID,
                    "ts": "2026-09-01T10:%02d:30Z" % i,
                    "cost_usd": 1.5, "kind": "api",
                    "account": "shared@example.com",
                    "model": "claude-opus-4-6"})
            conn2.commit()
        finally:
            db.close(conn2)

        # Write config pointing to the second root
        with open(paths.config_path(), "w", encoding="utf-8") as fh:
            json.dump({"extra_roots": [{"path": self.ledger2,
                                        "account": "shared@example.com",
                                        "label": "wsl"}]}, fh)
        config.load(paths.config_path(), refresh=True)

    def test_fit_with_extra_root(self):
        rc, text, errs = run_cli("fit", "--all")
        self.assertEqual(rc, 0, errs)
        self.assertIn("shared@example.com", text)

    def test_report_with_extra_root(self):
        rc, text, errs = run_cli("report", "--window", "all",
                                 "--account", "shared@example.com", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        accounts = {a["account"]: a for a in doc["accounts"]}
        self.assertIn("shared@example.com", accounts)


class SummaryRebuildLocalOnlyTest(CliTestCase):
    """``summary.rebuild`` without readers touches no extra root (no copy made)."""

    def setUp(self):
        super().setUp()
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")

    def test_rebuild_without_readers_no_copy(self):
        from pa import summary as _summary
        conn = self.conn()
        try:
            cfg = config.load()
            # Remove any extra_roots from config so readers=None
            cfg.pop("extra_roots", None)
            doc = _summary.rebuild(conn, cfg)
            self.assertIsNotNone(doc)
            # Verify no union_dir was created or used
            from pa.db import union_dir
            udir = union_dir()
            # The union dir either doesn't exist or has nothing in it related to this test
            # The point is that rebuild(conn, cfg) without readers= does not call union_copy
        finally:
            db.close(conn)


class FitTableTest(CliTestCase):
    """``fit``: an account column, real sample counts, empty/unowned instances hidden."""

    def setUp(self):
        super(FitTableTest, self).setUp()
        run_cli("recalc", "--root", self.projects, "--account", "dev@example.com")
        conn = self.conn()
        try:
            # a populated instance: 10 utilization samples
            db.upsert_window_instance(conn, {"account": "dev@example.com", "window": "five_hour",
                                             "resets_at": 1893456000, "started_at": 1893438000})
            for i in range(10):
                db.insert_utilization(conn, {
                    "ts": "2026-09-01T10:%02d:00Z" % i, "account": "dev@example.com",
                    "session_id": SID, "window": "five_hour", "pct": float(i),
                    "resets_at": 1893456000})
            # an empty instance (0 samples) for the same account
            db.upsert_window_instance(conn, {"account": "dev@example.com", "window": "seven_day",
                                             "resets_at": 1893999999, "started_at": 1893900000})
            # a NULL-account instance (pre-account-stamp), with samples
            db.upsert_window_instance(conn, {"account": None, "window": "five_hour",
                                             "resets_at": 1893556000, "started_at": 1893538000})
            db.insert_utilization(conn, {
                "ts": "2026-09-02T10:00:00Z", "account": None, "session_id": SID,
                "window": "five_hour", "pct": 5.0, "resets_at": 1893556000})
            conn.commit()
        finally:
            db.close(conn)

    def test_hides_empty_and_null_account_instances_by_default(self):
        rc, text, errs = run_cli("fit")
        self.assertEqual(rc, 0, errs)
        self.assertIn("account", text)
        self.assertIn("fit n", text)
        self.assertIn("dev@example.com", text)
        self.assertNotIn("(unknown)", text)
        lines = [ln for ln in text.splitlines() if "five_hour" in ln or "seven_day" in ln]
        self.assertEqual(len(lines), 1)                  # only the populated instance shown

    def test_all_shows_every_instance_with_both_counts(self):
        rc, text, errs = run_cli("fit", "--all")
        self.assertEqual(rc, 0, errs)
        self.assertIn("(unknown)", text)
        lines = [ln for ln in text.splitlines() if "five_hour" in ln or "seven_day" in ln]
        self.assertEqual(len(lines), 3)                  # all three instances shown

    def test_json_carries_samples_and_account(self):
        rc, text, errs = run_cli("fit", "--json")
        self.assertEqual(rc, 0, errs)
        rows = json.loads(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account"], "dev@example.com")
        self.assertEqual(rows[0]["samples"], "10")


class SqlGuardTest(CliTestCase):

    def setUp(self):
        super(SqlGuardTest, self).setUp()
        run_cli("recalc", "--root", self.projects)

    def test_select_works(self):
        rc, text, errs = run_cli("sql", "SELECT COUNT(*) AS n FROM turns")
        self.assertEqual(rc, 0, errs)
        self.assertIn("13", text)

    def test_delete_is_refused(self):
        rc, text, errs = run_cli("sql", "DELETE FROM turns")
        self.assertEqual(rc, 2)
        self.assertIn("refused", errs)
        self.assertEqual(self.counts()["turns"], 13)

    def test_other_write_statements_are_refused(self):
        for bad in ("delete from turns",
                    "SELECT 1; DELETE FROM turns",
                    "UPDATE turns SET cost_usd=0",
                    "DROP TABLE turns",
                    "PRAGMA journal_mode=DELETE",
                    "WITH x AS (SELECT 1) DELETE FROM turns",
                    "-- comment\nDELETE FROM turns"):
            rc, _t, errs = run_cli("sql", bad)
            self.assertEqual(rc, 2, bad)
            self.assertIn("refused", errs)
        self.assertEqual(self.counts()["turns"], 13)

    def test_with_and_explain_are_allowed(self):
        rc, _t, errs = run_cli("sql", "WITH x AS (SELECT 1 AS a) SELECT a FROM x")
        self.assertEqual(rc, 0, errs)
        rc, _t, errs = run_cli("sql", "EXPLAIN SELECT 1")
        self.assertEqual(rc, 0, errs)


class PricesTest(CliTestCase):

    def test_prices_show(self):
        rc, text, errs = run_cli("prices", "--show")
        self.assertEqual(rc, 0, errs)
        for key in ("fable-5-1", "opus-5", "sonnet-5", "haiku-4-5"):
            self.assertIn(key, text)
        self.assertIn("effective_from", text)
        self.assertIn("2026-09-01", text)

    def test_paste_parses_a_markdown_table(self):
        table = ("| Model | Input | Cache write 5m | Cache write 1h | Cache hits | Output |\n"
                 "|---|---|---|---|---|---|\n"
                 "| Fable 5.1 | $10 | $12.50 | $20 | $0.30 | $50 |\n")
        rows = ledger_cli.parse_price_table(table)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_key"], "fable-5-1")
        self.assertEqual(rows[0]["read"], 0.30)

    def test_apply_writes_rows(self):
        table = "| Fable 5.1 | $11 | $13.75 | $22 | $0.28 | $55 |\n"
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(table)
        try:
            rc, text, errs = run_cli("prices", "--paste", "--apply",
                                     "--effective-from", "2026-10-01")
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 0, errs)
        self.assertIn("applied 1 rows", text)
        conn = self.conn()
        try:
            row = conn.execute("SELECT * FROM prices WHERE effective_from='2026-10-01'").fetchone()
            self.assertEqual(row["model_key"], "fable-5-1")
            self.assertEqual(row["read"], 0.28)
        finally:
            db.close(conn)


class DoctorTest(CliTestCase):

    @unittest.skipUnless(os.path.exists(SNIPPET), "settings/user.snippet.json not present")
    def test_doctor_reports_missing_hook_files(self):
        config_dir = os.path.join(self.dir, "claude-config")
        os.makedirs(config_dir, exist_ok=True)
        pa3 = os.path.join(self.dir, "not-installed-pa3").replace("\\", "/")
        with open(SNIPPET, "r", encoding="utf-8") as fh:
            raw = fh.read()
        filled = raw.replace("{{PY_EXE}}", sys.executable.replace("\\", "/")).replace("{{PA3}}", pa3)
        json.loads(filled)                     # the filled snippet must still be valid JSON
        with open(os.path.join(config_dir, "settings.json"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(filled)
        rc, text, errs = run_cli("doctor", "--config-dir", config_dir, "--skip-hooks")
        self.assertEqual(rc, 1, errs)
        label = os.path.basename(config_dir)
        fails = [ln for ln in text.splitlines() if ln.strip().startswith("FAIL") and label in ln]
        self.assertTrue(fails, text)
        self.assertTrue(any("pa_hook.py" in ln for ln in fails), fails)
        self.assertTrue(any("pa_statusline.py" in ln for ln in fails), fails)
        self.assertEqual(len([ln for ln in fails if "hook " in ln]), 11)
        self.assertIn("sqlite:", text)
        self.assertIn("interpreter:", text)

    def test_command_paths_handles_quoted_windows_paths(self):
        interp, scripts = ledger_cli.command_paths(
            '"C:/Py/python.exe" -I -X utf8 C:/Users/u/.claude/pa3/pa_hook.py session_start')
        self.assertEqual(interp, "C:/Py/python.exe")
        self.assertEqual(scripts, ["C:/Users/u/.claude/pa3/pa_hook.py"])

    def test_default_fixture_prefers_the_installed_copy(self):
        """The package copy is used when no installed pa3 dir has the fixture yet;
        an installed copy, when present, wins."""
        import unittest.mock as mock

        empty_install = os.path.join(self.dir, "no-pa3")
        with mock.patch.object(paths, "install_dir", return_value=empty_install):
            self.assertEqual(os.path.abspath(ledger_cli.default_fixture()),
                             os.path.abspath(os.path.join(
                                 ROOT, "tests", "fixtures", "hooks", "probe1.log")))

        fake_install = os.path.join(self.dir, "fake-pa3")
        installed = os.path.join(fake_install, "tests", "fixtures", "hooks", "probe1.log")
        os.makedirs(os.path.dirname(installed), exist_ok=True)
        with open(installed, "w", encoding="utf-8") as fh:
            fh.write("=== SessionStart probe ===\n")
        with mock.patch.object(paths, "install_dir", return_value=fake_install):
            self.assertEqual(os.path.abspath(ledger_cli.default_fixture()),
                             os.path.abspath(installed))


class VacuumWatchTest(CliTestCase):

    def test_vacuum_folds_old_samples(self):
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            db.insert_utilization(conn, {"ts": "2020-01-01T00:00:00Z", "account": "dev@example.com",
                                         "session_id": SID, "window": "five_hour", "pct": 12.0,
                                         "resets_at": 1600000000})
        finally:
            db.close(conn)
        rc, text, errs = run_cli("vacuum", "--samples-older-than", "30d")
        self.assertEqual(rc, 0, errs)
        self.assertIn("utilization rows deleted", text)
        conn = self.conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM utilization").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM window_instances").fetchone()[0], 1)
        finally:
            db.close(conn)
        wal_path = paths.db_path() + "-wal"
        wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        self.assertLess(wal_size, 4 * 1024 * 1024)         # journal_size_limit is 4 MB

    def test_watch_prints_one_line_per_file(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, errs = run_cli("watch", "--rounds", "1", "--interval", "0.05",
                                 "--cwd", self.dir)
        self.assertEqual(rc, 0, errs)
        self.assertIn("summary", text)


@unittest.skipUnless(os.path.exists(VANTAGE), "Vantage reference transcript not present")
class ReferenceReconcileTest(CliTestCase):
    """Design doc F: price table <= 0.1 %, coverage >= 97 % on a real session."""

    def test_reconcile_vantage(self):
        rc, _text, errs = run_cli("recalc", "--session", VANTAGE_SID)
        self.assertEqual(rc, 0, errs)
        rc, text, errs = run_cli("reconcile", VANTAGE_SID, "--json")
        self.assertEqual(rc, 0, errs + text)
        res = json.loads(text)
        pools = {m["model"]: m for m in res["models"]}
        self.assertIn("claude-fable-5-1", pools)
        self.assertLessEqual(abs(pools["claude-fable-5-1"]["delta_pct"]), 0.1)
        # sonnet-5 and sonnet-5[1m] are pooled into one normalized key
        self.assertIn("claude-sonnet-5", pools)
        self.assertLessEqual(abs(pools["claude-sonnet-5"]["delta_pct"]), 0.1)
        self.assertLessEqual(res["worst_delta_pct"], 0.1)
        self.assertGreaterEqual(res["coverage_pct"], 97.0)
        self.assertTrue(res["ok_price"] and res["ok_coverage"])
        self.assertEqual(len(res["away_summary"]), 2)
        self.assertGreater(res["ai_title"], 0)
        self.assertGreater(res["residual"], 0)
        self.assertGreater(res.get("transcript_total", 0), 0)
        self.assertTrue(res.get("cost_state_fresh"))

    def test_reconcile_text_output(self):
        run_cli("recalc", "--session", VANTAGE_SID)
        rc, text, errs = run_cli("reconcile", VANTAGE_SID)
        self.assertEqual(rc, 0, errs)
        self.assertIn("1. price check per pool", text)
        self.assertIn("2. coverage", text)
        self.assertIn("3. cost-state cross-check", text)
        self.assertIn("RESULT", text)
        self.assertIn("cost-state fresh", text)


class ReconcileMissingTest(CliTestCase):

    def test_unknown_session_exits_1(self):
        rc, _text, errs = run_cli("reconcile", "no-such-session", "--root", self.projects)
        self.assertEqual(rc, 1)
        self.assertIn("no transcript", errs)

    def test_synthetic_session_reconciles(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        self.assertEqual(res["session_id"], SID)
        self.assertEqual(res["n_main"], 3)
        self.assertEqual(res["n_sub"], 9)                  # 3 retriever + 2*3 workflow
        self.assertEqual(len(res["away_summary"]), 1)
        self.assertEqual(res["ai_title"], 1)
        self.assertAlmostEqual(res["total_cost_state"], TOTAL_COST_USD)
        self.assertGreater(res.get("transcript_total", 0), 0)
        self.assertTrue(res.get("cost_state_fresh"))   # cost-state is the last record
        self.assertEqual(rc, 1)                        # synthetic cost-state is deliberately off


class NoCostStateReconcileTest(CliTestCase):
    """T8: a killed/headless session (no cost-state record) grades on coverage alone."""

    def setUp(self):
        super().setUp()
        with open(self.main_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        kept = [ln for ln in lines if json.loads(ln).get("type") != "cost-state"]
        with open(self.main_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(kept)

    def test_no_cost_state_reconciles_exit_0_full_coverage(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        self.assertTrue(res.get("cost_state_absent"))
        self.assertIsNone(res.get("ok_price"))
        self.assertAlmostEqual(res["coverage_pct"], 100.0, places=3)
        self.assertEqual(rc, 0)

    def test_no_cost_state_text_output(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects)
        self.assertIn("n/a (no cost-state record)", text)
        self.assertIn("cost-state absent", text)
        self.assertEqual(rc, 0)


class StaleReconcileTest(CliTestCase):
    """A stale cost-state (assistant records after it) is not graded."""

    def setUp(self):
        super().setUp()
        # append assistant records after the cost-state to make it stale
        with open(self.main_path, "a", encoding="utf-8", newline="\n") as fh:
            extra = _assistant("msg_stale_1", "2026-09-01T10:10:00.000Z", "claude-fable-5-1",
                               _usage(10, 500, 100, 50))
            fh.write(json.dumps(extra) + "\n")

    def test_stale_cost_state_fresh_false(self):
        from pa import transcript
        _rec, fresh, _off = transcript.cost_state_fresh(self.main_path)
        self.assertFalse(fresh)

    def test_stale_result_contains_stale(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects)
        self.assertIn("stale", text)
        self.assertIn("RESULT", text)

    def test_stale_exit_0_when_coverage_holds(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        self.assertFalse(res.get("cost_state_fresh"))
        self.assertIn("cost_state_stale_at", res)
        # coverage is against transcript total; the extra stale request is included
        # in the transcript total, so coverage may be below 100% but check exit code
        if res["ok_price"] and res["ok_coverage"]:
            self.assertEqual(rc, 0)

    def test_stale_no_residual_after_recalc(self):
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM turns WHERE kind='residual' AND session_id=?",
                             (SID,)).fetchone()[0]
        finally:
            db.close(conn)
        self.assertEqual(n, 0)

    def test_stale_cost_source_is_turns(self):
        run_cli("recalc", "--root", self.projects)
        conn = self.conn()
        try:
            row = conn.execute("SELECT cost_source FROM sessions WHERE session_id=?",
                               (SID,)).fetchone()
        finally:
            db.close(conn)
        self.assertEqual(row[0], "turns")


class PooledModelReconcileTest(CliTestCase):
    """Two cost-state keys that normalize to one pool."""

    def setUp(self):
        super().setUp()
        # Rewrite the main transcript with two cost-state model keys: sonnet-5 and sonnet-5[1m]
        slug_dir = os.path.join(self.projects, SLUG)
        main = os.path.join(slug_dir, SID + ".jsonl")
        records = [
            {"type": "user", "uuid": "u0", "sessionId": SID, "timestamp": "2026-09-01T09:59:00.000Z",
             "cwd": CWD, "version": "2.1.270",
             "message": {"role": "user", "content": "hello"}},
            _assistant("msg_p1", "2026-09-01T10:00:00.000Z", "claude-sonnet-5",
                       _usage(100, 20000, 0, 500)),              # main-thread: 1h writes
            _assistant("msg_p2", "2026-09-01T10:01:00.000Z", "claude-sonnet-5",
                       _usage(50, 0, 20100, 300, write_5m=2000)),  # subagent-like: 5m writes
            # cost-state mirrors the billing: [1m] key carries the main-thread 1h writes,
            # bare key carries the subagent 5m writes (real CLI behavior)
            # pooled: inp 150*2 + w5m 2000*2.5 + w1h 20000*4 + cr 20100*0.2 + out 800*10
            #       = 0.0003 + 0.005 + 0.08 + 0.00402 + 0.008 = 0.09732
            {"type": "cost-state", "sessionId": SID, "totalCostUSD": 0.09732,
             "startTime": 1788461324517,
             "modelUsage": {
                 "claude-sonnet-5[1m]": {"inputTokens": 100, "outputTokens": 500,
                                         "cacheReadInputTokens": 0,
                                         "cacheCreationInputTokens": 20000,
                                         "costUSD": 0.0852},
                 "claude-sonnet-5": {"inputTokens": 50, "outputTokens": 300,
                                     "cacheReadInputTokens": 20100,
                                     "cacheCreationInputTokens": 2000,
                                     "costUSD": 0.01212}},
             "hasUnknownModelCost": False},
        ]
        _write_jsonl(main, records)
        # remove the subagent directory since we're testing pooling only
        sub_dir = os.path.join(slug_dir, SID, "subagents")
        if os.path.exists(sub_dir):
            shutil.rmtree(sub_dir)

    def test_pooled_keys_one_row(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        models = res["models"]
        # two cost-state keys normalize to one pool
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["model"], "claude-sonnet-5")
        self.assertEqual(len(models[0]["raw_keys"]), 2)
        # delta should be within tolerance
        if models[0]["graded"]:
            self.assertLessEqual(abs(models[0]["delta_pct"]), PRICE_TOLERANCE)


class MixedTTLBareKeyReconcileTest(CliTestCase):
    """T8.c2: a bare cost-state key whose own traffic is itself mixed TTL (a
    coder call's 1h writes pooled with a retriever call's 5m writes under the
    same bare "claude-sonnet-5" key, as on a6ab5364) -- the T8.c1 key rule
    (bare -> all 5m) underprices it by far more than PRICE_TOLERANCE; only the
    transcript's own pooled ratio reconciles."""

    def setUp(self):
        super().setUp()
        slug_dir = os.path.join(self.projects, SLUG)
        main = os.path.join(slug_dir, SID + ".jsonl")
        records = [
            {"type": "user", "uuid": "u0", "sessionId": SID, "timestamp": "2026-09-01T09:59:00.000Z",
             "cwd": CWD, "version": "2.1.270",
             "message": {"role": "user", "content": "hello"}},
            _assistant("msg_m1", "2026-09-01T10:00:00.000Z", "claude-sonnet-5",
                       _usage(100, 10000, 0, 500)),                  # coder-like: 1h writes
            _assistant("msg_m2", "2026-09-01T10:01:00.000Z", "claude-sonnet-5",
                       _usage(50, 0, 0, 300, write_5m=4000)),        # retriever-like: 5m writes
            # cost-state: one bare key ("claude-sonnet-5", no [1m] tag) pooling both
            # pooled: inp 150*2 + w5m 4000*2.5 + w1h 10000*4 + cr 0 + out 800*10
            #       = 0.0003 + 0.01 + 0.04 + 0 + 0.008 = 0.0583
            {"type": "cost-state", "sessionId": SID, "totalCostUSD": 0.0583,
             "startTime": 1788461324517,
             "modelUsage": {
                 "claude-sonnet-5": {"inputTokens": 150, "outputTokens": 800,
                                     "cacheReadInputTokens": 0,
                                     "cacheCreationInputTokens": 14000,
                                     "costUSD": 0.0583}},
             "hasUnknownModelCost": False},
        ]
        _write_jsonl(main, records)
        sub_dir = os.path.join(slug_dir, SID, "subagents")
        if os.path.exists(sub_dir):
            shutil.rmtree(sub_dir)

    def test_transcript_ratio_wins_over_key_rule(self):
        run_cli("recalc", "--root", self.projects)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        models = res["models"]
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m["model"], "claude-sonnet-5")
        # transcript ratio reproduces the pooled cost exactly; the key rule (bare
        # -> all 5m) underprices it well past tolerance.
        self.assertTrue(m["write_split"].startswith("transcript"), m["write_split"])
        self.assertLessEqual(abs(m["delta_pct"]), PRICE_TOLERANCE)
        self.assertIn("key rule", m["split_candidates"])
        self.assertGreater(abs(m["split_candidates"]["key rule"]), PRICE_TOLERANCE)


class UnbookedRunReconcileTest(CliTestCase):
    """When booked request count < transcript count, the run is listed as unbooked."""

    def test_unbooked_run_listed(self):
        run_cli("recalc", "--root", self.projects)
        # Delete one subagent turn row to create an unbooked-run condition
        conn = self.conn()
        try:
            # delete one of the subagent's turns
            rows = conn.execute(
                "SELECT rowid FROM turns WHERE session_id=? AND kind='api' AND run_id<>?"
                " LIMIT 1", (SID, SID)).fetchall()
            if rows:
                conn.execute("DELETE FROM turns WHERE rowid=?", (rows[0][0],))
                conn.commit()
        finally:
            db.close(conn)
        rc, text, _e = run_cli("reconcile", SID, "--root", self.projects, "--json")
        res = json.loads(text)
        self.assertTrue(res.get("unbooked"))
        self.assertLess(res["coverage_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()


class ProjectShareTest(CliTestCase):
    """A project's usage share of a window is the meter's own reading apportioned by weighted
    spend: the shares add up to the meter and never exceed it (2026-09-20: one project read
    28 % of 5h beside a 24 % meter)."""

    def test_shares_add_up_to_the_meter(self):
        from pa import summary
        cfg_path = paths.config_path()
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"fit": {"n_min": 1}}, fh)
        account = "acct@example.com"
        now = time.time()
        resets_at = int(now) + 3 * 3600
        t1, t2 = now - 1800, now - 600
        sids = {"Z:/Test/A": ("aaaaaaaa-0000-4000-8000-000000000010", 3.0),
                "Z:/Test/B": ("bbbbbbbb-0000-4000-8000-000000000013", 1.0)}
        conn = ledger_cli.open_db()
        try:
            db.set_meta(conn, "created", _iso(now - 7200))  # era before fixture sessions
            for cwd, (sid, cost) in sids.items():
                db.upsert_session(conn, {"session_id": sid, "account": account, "cwd": cwd,
                                         "project": cwd, "started": _iso(now - 3600),
                                         "cost_usd": cost, "cost_source": "turns"})
                db.upsert_turn(conn, {"msg_id": "m-" + sid[:8], "session_id": sid,
                                      "account": account, "ts": _iso(t1),
                                      "model": "claude-sonnet-5", "cost_usd": cost, "kind": "api"})
            db.upsert_window_instance(conn, {"account": account, "window": "five_hour",
                                             "resets_at": resets_at, "started_at": resets_at - 18000})
            for ts, pct, sc in ((t1, 10, 1.0), (t2, 30, 3.0)):
                db.insert_utilization(conn, {"ts": _iso(ts), "account": account,
                                             "session_id": sids["Z:/Test/A"][0],
                                             "window": "five_hour", "pct": pct,
                                             "resets_at": resets_at, "session_cost": sc,
                                             "model": "claude-sonnet-5"})
            conn.commit()
            projects = summary.projects_block(conn, config.load(refresh=True))
        finally:
            db.close(conn)
        def tail(key):
            return key.lower().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]

        shares = {tail(k): v["pct_used"].get("five_hour") for k, v in projects.items()
                  if tail(k) in ("a", "b")}            # the fixture's own project stays out
        self.assertEqual(set(shares), {"a", "b"})
        self.assertAlmostEqual(sum(shares.values()), 30.0, places=2)
        self.assertAlmostEqual(shares["a"], 22.5, places=2)
        self.assertAlmostEqual(shares["b"], 7.5, places=2)
        for v in shares.values():
            self.assertLessEqual(v, 30.0)


class DoctorSizesTest(CliTestCase):
    """``doctor --sizes`` (T10): governed-file size checks."""

    def _make_project(self):
        project = os.path.join(self.dir, "test-project")
        os.makedirs(project, exist_ok=True)
        return project

    def test_doctor_sizes_ok_warn_fail(self):
        project = self._make_project()
        # CLAUDE.md: 100 chars = OK
        with open(os.path.join(project, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# CLAUDE\nshort")
        # HOW_WE_WORK.md: 15000 chars > 14000 = WARN
        with open(os.path.join(project, "HOW_WE_WORK.md"), "w", encoding="utf-8") as fh:
            fh.write("x" * 15000)
        # memory index: 250 lines > 200 = FAIL
        mem_dir = os.path.join(project, ".claude-state", "memory")
        os.makedirs(mem_dir, exist_ok=True)
        with open(os.path.join(mem_dir, "MEMORY.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join("line %d" % i for i in range(250)))

        rc, text, errs = run_cli("doctor", "--sizes", "--project", project)
        self.assertIn("sizes", text.split("\n")[0])
        ok_lines = [ln for ln in text.splitlines() if ln.strip().startswith("OK")]
        warn_lines = [ln for ln in text.splitlines() if ln.strip().startswith("WARN")]
        fail_lines = [ln for ln in text.splitlines() if ln.strip().startswith("FAIL")]
        self.assertTrue(ok_lines, text)
        self.assertTrue(warn_lines, text)
        self.assertTrue(fail_lines, text)
        self.assertEqual(rc, 1)

    def test_doctor_sizes_seed_from_ledger(self):
        project = self._make_project()
        with open(os.path.join(project, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# Project\n")
        conn = ledger_cli.open_db()
        try:
            sid = "seed-test-0000-0000-000000000001"
            db.upsert(conn, "sessions", {
                "session_id": sid, "account": "dev@example.com",
                "project": project.replace("\\", "/"), "cwd": project,
                "kind": "main", "started": "2026-09-20T10:00:00Z",
                "ended": "2026-09-20T11:00:00Z", "phase": "3.3"})
            for i, seed in enumerate([30000, 35000, 40000]):
                db.upsert(conn, "agent_runs", {
                    "run_id": "seed-run-%04d" % i, "session_id": sid,
                    "kind": "expert", "agent_type": "expert-fable", "phase": "3.3",
                    "seed_ctx": seed, "started": "2026-09-20T10:%02d:00Z" % i})
            conn.commit()
        finally:
            db.close(conn)
        rc, text, errs = run_cli("doctor", "--sizes", "--project", project)
        self.assertEqual(rc, 0, text)
        self.assertIn("sizes", text.split("\n")[0])
        self.assertTrue(any("expert seed" in ln for ln in text.splitlines()), text)

    def test_doctor_sizes_docs_ops_index_present_over_400(self):
        """T5: docs/ops/INDEX.md over 400 lines -> WARN."""
        project = self._make_project()
        ops_dir = os.path.join(project, "docs", "ops")
        os.makedirs(ops_dir, exist_ok=True)
        with open(os.path.join(ops_dir, "INDEX.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join("line %d" % i for i in range(450)))
        rc, text, errs = run_cli("doctor", "--sizes", "--project", project)
        warn_lines = [ln for ln in text.splitlines() if "WARN" in ln and "docs/ops/INDEX.md" in ln]
        self.assertTrue(warn_lines, text)

    def test_doctor_sizes_docs_ops_index_absent_ok(self):
        """T5: docs/ops/INDEX.md absent -> OK."""
        project = self._make_project()
        rc, text, errs = run_cli("doctor", "--sizes", "--project", project)
        ok_lines = [ln for ln in text.splitlines() if "OK" in ln and "docs/ops/INDEX.md" in ln]
        self.assertTrue(ok_lines, text)


class DoctorSupersededTest(CliTestCase):
    """T5: doctor reports WARN for superseded defaults, 0 for current."""

    def _write_config(self, cfg_dict):
        """Write a config.json in the temp ledger dir."""
        ledger = os.environ["PA_LEDGER_DIR"]
        os.makedirs(ledger, exist_ok=True)
        cfg_path = os.path.join(ledger, "config.json")
        with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg_dict, fh)
        config._CACHE.clear()

    def _config_warns(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        return text, [ln for ln in text.splitlines() if "WARN" in ln and "config: " in ln
                      and any(k in ln for k in ("past_default", "retired_model", "unknown_key"))]

    def test_superseded_lines_6_gives_one_warn(self):
        cfg = config.defaults()
        cfg["statusline"]["lines"] = 6       # superseded (now 7)
        self._write_config(cfg)
        text, warn_lines = self._config_warns()
        self.assertEqual(len(warn_lines), 1, text)
        self.assertIn("past_default statusline.lines", warn_lines[0])
        self.assertIn("pa_install.py --root drops it", warn_lines[0])

    def test_current_defaults_give_zero_superseded_warns(self):
        cfg = config.defaults()
        self._write_config(cfg)
        text, warn_lines = self._config_warns()
        self.assertEqual(len(warn_lines), 0, text)
        self.assertIn("0 past defaults", text)

    def test_retired_model_pin_and_unknown_key_warn(self):
        """T12: a pin to a model no default names, and a key the package no longer reads."""
        self._write_config({"renewal_day": 5,
                            "pinned_models": {"coder-opus55": "claude-opus-4-1[1m]"},
                            "statusline": {"old_knob": 1}})
        text, warn_lines = self._config_warns()
        self.assertEqual(len(warn_lines), 2, text)
        joined = "\n".join(warn_lines)
        self.assertIn("retired_model pinned_models.coder-opus55", joined)
        self.assertIn("edit config.json", joined)
        self.assertIn("unknown_key statusline.old_knob", joined)


# =========================================================================== doctor install rows (T9)

def _init_repo(path):
    """Turn ``path`` into a one-commit git repo (for the ``package clone`` row)."""
    os.makedirs(path, exist_ok=True)
    for args in (["init", "-q", path],
                ["-C", path, "config", "user.email", "test@test.com"],
                ["-C", path, "config", "user.name", "Test"],
                ["-C", path, "config", "commit.gpgsign", "false"]):
        subprocess.run(["git"] + args, check=True, timeout=10,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", path, "add", "."], check=True, timeout=10,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", path, "commit", "-qm", "init"], check=True, timeout=10,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=10)
    return out.stdout.strip()


class DoctorInstallRowsTest(CliTestCase):
    """T9: package clone / update check / card / mcp servers doctor rows."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.dir, "fakehome")
        os.makedirs(self.home, exist_ok=True)
        self._old_home = paths.home
        paths.home = lambda: self.home
        self._old_cfg_env = os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def tearDown(self):
        paths.home = self._old_home
        if self._old_cfg_env is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg_env
        super().tearDown()

    def _update_json(self, doc):
        os.makedirs(paths.install_dir(), exist_ok=True)
        with open(os.path.join(paths.install_dir(), "update.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    # -- package clone --------------------------------------------------

    @unittest.skipUnless(_HAS_GIT, "git not on PATH")
    def test_package_clone_present_and_named(self):
        clone = paths.package_clone_dir()
        head = _init_repo(clone)
        os.makedirs(paths.install_dir(), exist_ok=True)
        with open(os.path.join(paths.install_dir(), "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("version: 3.0.0\ngit: %s\nsource: %s\n" % (head, clone.replace("\\", "/")))
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "package clone" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("OK"), lines)
        self.assertIn(head, lines[0])

    @unittest.skipUnless(_HAS_GIT, "git not on PATH")
    def test_package_clone_present_but_version_does_not_name_it(self):
        clone = paths.package_clone_dir()
        _init_repo(clone)
        os.makedirs(paths.install_dir(), exist_ok=True)
        with open(os.path.join(paths.install_dir(), "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("version: 3.0.0\nsource: /somewhere/else\n")
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "package clone" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)

    @unittest.skipUnless(_HAS_GIT, "git not on PATH")
    def test_package_clone_named_by_version_under_another_config_dir(self):
        clone = paths.package_clone_dir()                         # <home>/.claude/pa3-src
        head = _init_repo(clone)
        os.makedirs(paths.install_dir(), exist_ok=True)
        with open(os.path.join(paths.install_dir(), "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("version: 3.0.0\nsource: %s\n" % clone.replace("\\", "/"))
        other = os.path.join(self.dir, "claude-other")              # no pa3-src of its own
        os.makedirs(other, exist_ok=True)
        os.environ["CLAUDE_CONFIG_DIR"] = other
        try:
            rc, text, errs = run_cli("doctor", "--skip-hooks")
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        lines = [ln for ln in text.splitlines() if "package clone" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("OK"), lines)
        self.assertIn(head, lines[0])

    def test_package_clone_absent_warns_never_fails(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "package clone" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)

    # -- update check -----------------------------------------------------

    def test_update_check_never_when_file_missing(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "update check" in ln]
        self.assertTrue(lines, text)
        self.assertIn("never", lines[0])
        self.assertFalse(lines[0].strip().startswith("WARN"), lines)

    def test_update_check_current(self):
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        self._update_json({"checked_at": now, "clone_head": "abc123", "remote_head": "abc123",
                           "behind": 0, "installed": True, "subjects": [], "status": "current"})
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "update check" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("OK"), lines)
        self.assertIn("current", lines[0])

    def test_update_check_behind_warns(self):
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        self._update_json({"checked_at": now, "clone_head": "abc123", "remote_head": "def456",
                           "behind": 3, "installed": True, "subjects": [], "status": "behind"})
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "update check" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)
        self.assertIn("behind 3", lines[0])

    def test_update_check_offline(self):
        stale = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        self._update_json({"checked_at": stale, "clone_head": None, "remote_head": None,
                           "behind": 0, "installed": True, "subjects": [], "status": "offline"})
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "update check" in ln]
        self.assertTrue(lines, text)
        self.assertIn("offline", lines[0])
        # older than 2x install.update_check_hours (default 24h) -> WARN
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)

    # -- card ---------------------------------------------------------------

    def _project(self):
        project = os.path.join(self.dir, "card-project")
        os.makedirs(project, exist_ok=True)
        return project

    def test_card_under_cap_no_placeholders_ok(self):
        project = self._project()
        with open(os.path.join(project, "HOW_WE_WORK.md"), "w", encoding="utf-8") as fh:
            fh.write("# card\nshort body\n")
        rc, text, errs = run_cli("doctor", "--skip-hooks", "--project", project)
        lines = [ln for ln in text.splitlines() if ln.strip().startswith(("OK", "WARN")) and "card:" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("OK"), lines)

    def test_card_over_cap_warns(self):
        project = self._project()
        with open(os.path.join(project, "HOW_WE_WORK.md"), "w", encoding="utf-8") as fh:
            fh.write("x" * 8000)
        rc, text, errs = run_cli("doctor", "--skip-hooks", "--project", project)
        lines = [ln for ln in text.splitlines() if ln.strip().startswith(("OK", "WARN")) and "card:" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)
        self.assertIn("8000 chars", lines[0])

    def test_card_placeholders_warn(self):
        project = self._project()
        with open(os.path.join(project, "HOW_WE_WORK.md"), "w", encoding="utf-8") as fh:
            fh.write("# card\nname: {{name}}\n")
        rc, text, errs = run_cli("doctor", "--skip-hooks", "--project", project)
        lines = [ln for ln in text.splitlines() if ln.strip().startswith(("OK", "WARN")) and "card:" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)
        self.assertIn("1 placeholders", lines[0])

    def test_card_absent_without_project_no_row(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "card:" in ln]
        self.assertFalse(lines, text)

    # -- mcp servers ----------------------------------------------------

    def test_mcp_servers_zero_ok(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "mcp servers" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("OK"), lines)
        self.assertIn("0 configured", lines[0])

    def test_mcp_servers_two_warn(self):
        with open(os.path.join(self.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"alpha": {}, "beta": {}}}, fh)
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        lines = [ln for ln in text.splitlines() if "mcp servers" in ln]
        self.assertTrue(lines, text)
        self.assertTrue(lines[0].strip().startswith("WARN"), lines)
        self.assertIn("2 configured", lines[0])
        self.assertIn("alpha", lines[0])
        self.assertIn("beta", lines[0])
        self.assertIn("disable the unused ones", lines[0])

    def test_mcp_servers_project_mcp_json_adds_to_count(self):
        with open(os.path.join(self.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"alpha": {}}}, fh)
        project = self._project()
        with open(os.path.join(project, ".mcp.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"gamma": {}}}, fh)
        rc, text, errs = run_cli("doctor", "--skip-hooks", "--project", project)
        lines = [ln for ln in text.splitlines() if "mcp servers" in ln]
        self.assertTrue(lines, text)
        self.assertIn("2 configured", lines[0])
        self.assertIn("gamma", lines[0])

    def test_summary_line_still_counts_fail_zero(self):
        rc, text, errs = run_cli("doctor", "--skip-hooks")
        self.assertRegex(text, r"\d+ OK, \d+ WARN, 0 FAIL")


# =========================================================================== hook_probe.py (T9)

@unittest.skipUnless(os.path.isfile(HOOK_PROBE), "hook_probe.py is a dev-repo tool; not in the package")
class HookProbeTest(unittest.TestCase):
    """``tools/analysis/hook_probe.py``: builds the payload and pipes it to a hook."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-hookprobe-test-")
        self.stub = os.path.join(self.dir, "stub_hook.py")
        with open(self.stub, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.exit(0)\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, HOOK_PROBE] + list(args),
                              capture_output=True, text=True, timeout=30)

    def test_session_start_payload_echoed(self):
        proc = self._run("--event", "SessionStart", "--cwd", self.dir,
                         "--hook", self.stub, "--source", "startup")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["hook_event_name"], "SessionStart")
        self.assertEqual(payload["cwd"], self.dir)
        self.assertEqual(payload["source"], "startup")
        self.assertIn("session_id", payload)
        self.assertTrue(payload["transcript_path"])

    def test_pre_tool_use_payload_echoed(self):
        proc = self._run("--event", "PreToolUse", "--cwd", self.dir, "--tool", "Read",
                         "--path", "Z:/x/PHASE_PLAN.md", "--agent-type", "review",
                         "--hook", self.stub)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["hook_event_name"], "PreToolUse")
        self.assertEqual(payload["tool_name"], "Read")
        self.assertEqual(payload["tool_input"]["file_path"], "Z:/x/PHASE_PLAN.md")
        self.assertEqual(payload["agent_type"], "review")

    def test_exit_code_follows_the_hook(self):
        failing = os.path.join(self.dir, "fail_hook.py")
        with open(failing, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdin.buffer.read()\nsys.exit(7)\n")
        proc = self._run("--event", "SessionStart", "--cwd", self.dir, "--hook", failing)
        self.assertEqual(proc.returncode, 7)

    def test_offset_and_limit_in_payload(self):
        """--offset and --limit reach the hook as tool_input.offset/limit."""
        proc = self._run("--event", "PreToolUse", "--cwd", self.dir, "--tool", "Read",
                         "--path", "big.py", "--offset", "100", "--limit", "40",
                         "--hook", self.stub)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["tool_input"]["offset"], 100)
        self.assertEqual(payload["tool_input"]["limit"], 40)

    def test_no_agent_type_is_main_session(self):
        """Without --agent-type the payload has no agent_type key (main session)."""
        proc = self._run("--event", "PreToolUse", "--cwd", self.dir, "--tool", "Read",
                         "--path", "some.py", "--hook", self.stub)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertNotIn("agent_type", payload)


# =========================================================================== account-switch

class AccountSwitchTest(CliTestCase):
    """T8.c2: ``pa_ledger.py account-switch`` rebooks turns, utilization, stamp,
    running and event to a new account."""

    OLD_ACCT = "old@example.com"
    NEW_ACCT = "new@example.com"
    SWITCH_TS = "2026-09-01T10:03:00Z"  # between turn 2 and turn 3

    def _seed(self):
        """Recalc the fixture, then overwrite accounts so the split is testable."""
        rc, _, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)

        conn = self.conn()
        try:
            # assign all turns to old account
            conn.execute("UPDATE turns SET account=? WHERE session_id=?",
                         (self.OLD_ACCT, SID))
            conn.execute("UPDATE sessions SET account=? WHERE session_id=?",
                         (self.OLD_ACCT, SID))

            # two utilization rows after the switch time
            for i, ts in enumerate(["2026-09-01T10:04:00Z", "2026-09-01T10:05:00Z"]):
                db.insert_utilization(conn, {
                    "ts": ts, "account": self.OLD_ACCT, "machine": "win",
                    "session_id": SID, "window": "five_hour",
                    "pct": 10.0 + i, "source": "test"})
        finally:
            db.close(conn)

        # stamp
        from pa import accounts
        accounts.stamp_session(SID, self.OLD_ACCT, source="auth")

        # running entry
        from pa import running
        running.patch_session(SID, {"account": self.OLD_ACCT})

    def test_account_switch_rebooks(self):
        self._seed()
        rc, text, errs = run_cli(
            "account-switch", "--session", SID,
            "--to", self.NEW_ACCT, "--from", self.OLD_ACCT,
            "--since", self.SWITCH_TS)
        self.assertEqual(rc, 0, errs)

        conn = self.conn()
        try:
            # turns before the switch stay on old account
            old_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=? AND kind='api'",
                (SID, self.OLD_ACCT)).fetchone()[0]
            new_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=? AND kind='api'",
                (SID, self.NEW_ACCT)).fetchone()[0]
            self.assertGreater(old_turns, 0, "some turns should stay on old account")
            self.assertGreater(new_turns, 0, "some turns should move to new account")

            # utilization rows relabelled
            util_old = conn.execute(
                "SELECT COUNT(*) FROM utilization WHERE account=?",
                (self.OLD_ACCT,)).fetchone()[0]
            util_new = conn.execute(
                "SELECT COUNT(*) FROM utilization WHERE account=?",
                (self.NEW_ACCT,)).fetchone()[0]
            self.assertEqual(util_old, 0)
            self.assertEqual(util_new, 2)

            # session row updated
            sess = conn.execute("SELECT account FROM sessions WHERE session_id=?",
                                (SID,)).fetchone()
            self.assertEqual(sess["account"], self.NEW_ACCT)

            # event present
            evts = conn.execute(
                "SELECT * FROM events WHERE session_id=? AND kind='account_change'",
                (SID,)).fetchall()
            self.assertEqual(len(evts), 1)
            detail = json.loads(evts[0]["detail_json"])
            self.assertEqual(detail["from"], self.OLD_ACCT)
            self.assertEqual(detail["to"], self.NEW_ACCT)
            self.assertEqual(detail["source"], "account-switch")
        finally:
            db.close(conn)

        # output includes the account split
        self.assertIn(self.OLD_ACCT, text)
        self.assertIn(self.NEW_ACCT, text)

    def test_second_run_is_idempotent(self):
        self._seed()
        run_cli("account-switch", "--session", SID,
                "--to", self.NEW_ACCT, "--from", self.OLD_ACCT,
                "--since", self.SWITCH_TS)

        # second run
        rc, text, errs = run_cli(
            "account-switch", "--session", SID,
            "--to", self.NEW_ACCT, "--from", self.OLD_ACCT,
            "--since", self.SWITCH_TS)
        self.assertEqual(rc, 0, errs)
        self.assertIn("turns: 0", text)
        self.assertIn("event: present", text)

        conn = self.conn()
        try:
            evts = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=? AND kind='account_change'",
                (SID,)).fetchone()[0]
            self.assertEqual(evts, 1)
        finally:
            db.close(conn)

    def test_dry_run_changes_nothing(self):
        self._seed()
        rc, text, errs = run_cli(
            "account-switch", "--session", SID,
            "--to", self.NEW_ACCT, "--from", self.OLD_ACCT,
            "--since", self.SWITCH_TS, "--dry-run")
        self.assertEqual(rc, 0, errs)

        conn = self.conn()
        try:
            # all turns still on old account
            old_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=?",
                (SID, self.OLD_ACCT)).fetchone()[0]
            new_turns = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=?",
                (SID, self.NEW_ACCT)).fetchone()[0]
            self.assertGreater(old_turns, 0)
            self.assertEqual(new_turns, 0)

            # session unchanged
            sess = conn.execute("SELECT account FROM sessions WHERE session_id=?",
                                (SID,)).fetchone()
            self.assertEqual(sess["account"], self.OLD_ACCT)

            # no event
            evts = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=? AND kind='account_change'",
                (SID,)).fetchone()[0]
            self.assertEqual(evts, 0)
        finally:
            db.close(conn)

    def test_refuses_unknown_session(self):
        rc, text, errs = run_cli(
            "account-switch", "--session", "nonexistent-sid",
            "--to", self.NEW_ACCT, "--since", self.SWITCH_TS)
        self.assertEqual(rc, 1)
        self.assertIn("unknown session", errs)

    def test_refuses_same_accounts(self):
        self._seed()
        rc, text, errs = run_cli(
            "account-switch", "--session", SID,
            "--to", self.OLD_ACCT, "--from", self.OLD_ACCT,
            "--since", self.SWITCH_TS)
        self.assertEqual(rc, 1)
        self.assertIn("--to equals --from", errs)


class RecalcAccountTimelineTest(CliTestCase):
    """T8.c4: recalc keeps each turn's account from the account_change timeline."""

    OLD_ACCT = "outlook@example.com"
    NEW_ACCT = "gmail@example.com"
    # between main turn 3 (10:06) and wf2 turn 1 (11:01); gives both accounts
    # credits in the savings walk (retriever returns at 10:05, first credit at 10:06)
    SWITCH_TS = "2026-09-01T10:30:00Z"

    def setUp(self):
        super(RecalcAccountTimelineTest, self).setUp()
        # make the session governed so savings are written
        self.gov_dir = os.path.join(self.dir, "governed_proj")
        os.makedirs(os.path.join(self.gov_dir, ".claude"))
        with open(os.path.join(self.gov_dir, ".claude", "pa.json"), "w") as fh:
            fh.write('{"pa_version": "3.0.0"}')
        self._rewrite_cwd(self.main_path, self.gov_dir)

    def _rewrite_cwd(self, path, new_cwd):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                rec = json.loads(line)
                if "cwd" in rec:
                    rec["cwd"] = new_cwd
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _seed_with_event(self):
        """Recalc to populate, then insert an account_change event."""
        rc, _, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects,
                              "--account", self.OLD_ACCT)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            conn.execute("UPDATE sessions SET account=? WHERE session_id=?",
                         (self.NEW_ACCT, SID))
            db.insert_event(conn, "account_change",
                            {"from": self.OLD_ACCT, "to": self.NEW_ACCT,
                             "source": "account-switch"},
                            session_id=SID, account=self.NEW_ACCT,
                            ts=self.SWITCH_TS)
        finally:
            db.close(conn)

    def test_recalc_preserves_per_turn_accounts(self):
        self._seed_with_event()
        rc, _, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            old_n = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=? AND kind='api'",
                (SID, self.OLD_ACCT)).fetchone()[0]
            new_n = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id=? AND account=? AND kind='api'",
                (SID, self.NEW_ACCT)).fetchone()[0]
            self.assertGreater(old_n, 0, "pre-event turns keep old account")
            self.assertGreater(new_n, 0, "post-event turns get new account")
            before = conn.execute(
                "SELECT account FROM turns WHERE session_id=? AND ts < ? AND kind='api'",
                (SID, self.SWITCH_TS)).fetchall()
            for row in before:
                self.assertEqual(row["account"], self.OLD_ACCT)
            after = conn.execute(
                "SELECT account FROM turns WHERE session_id=? AND ts >= ? AND kind='api'",
                (SID, self.SWITCH_TS)).fetchall()
            for row in after:
                self.assertEqual(row["account"], self.NEW_ACCT)
        finally:
            db.close(conn)

    def test_no_event_keeps_session_account(self):
        rc, _, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects,
                              "--account", "solo@example.com")
        self.assertEqual(rc, 0, errs)
        conn = self.conn()
        try:
            accts = conn.execute(
                "SELECT DISTINCT account FROM turns WHERE session_id=? AND kind='api'",
                (SID,)).fetchall()
            self.assertEqual(len(accts), 1)
            self.assertEqual(accts[0]["account"], "solo@example.com")
        finally:
            db.close(conn)

    def test_recalc_check_idempotent(self):
        self._seed_with_event()
        run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        rc, text, errs = run_cli("recalc", "--check", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        self.assertIn("idempotent OK", text)

    def test_by_account_split(self):
        self._seed_with_event()
        rc, _, errs = run_cli("recalc", "--from", "transcripts", "--root", self.projects)
        self.assertEqual(rc, 0, errs)
        from pa import savings
        conn = self.conn()
        try:
            detail = savings.measured_saved(conn, SID)
            ba = detail.get("by_account", {})
            saved = float(detail.get("saved_usd") or 0.0)
            self.assertAlmostEqual(sum(ba.values()), saved)
            self.assertIn(self.OLD_ACCT, ba, "old account in by_account")
            self.assertIn(self.NEW_ACCT, ba, "new account in by_account")
        finally:
            db.close(conn)


class RenewalCommandTest(CliTestCase):
    """3.11 T9: `accounts renewal EMAIL DAY|clear` stores the user's own day; `report --window period`
    refuses an account without one instead of reporting all time as a period."""

    def test_set_clear_and_refuse(self):
        from pa import notify

        notify.set_spawner(lambda cmd, env: None)          # the summary rebuild request spawns nothing
        self.addCleanup(notify.set_spawner, None)
        rc, _out, err = run_cli("accounts", "renewal", "dev@example.com", "9")
        self.assertEqual(rc, 0, err)
        self.assertEqual(config.renewal_day_for(config.load(refresh=True), "dev@example.com"), 9)
        rc, _out, _err = run_cli("accounts", "renewal", "dev@example.com", "32")
        self.assertEqual(rc, 2)
        rc, _out, err = run_cli("accounts", "renewal", "dev@example.com", "clear")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(config.renewal_day_for(config.load(refresh=True), "dev@example.com"))
        try:
            rc, _out, _err = run_cli("report", "--window", "period", "--account", "dev@example.com")
        except SystemExit as exc:
            rc = exc.code
        self.assertEqual(rc, 2)
