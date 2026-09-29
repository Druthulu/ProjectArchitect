"""Tests for tools/analysis/acceptance.py — the spec S12 acceptance runner."""

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
# 3.11 T27: acceptance.py is a dev-repo tool (the repo's tools/analysis), absent from the package and a clone
_ANALYSIS = os.path.join(os.path.dirname(_PKG), "tools", "analysis")
if not os.path.isfile(os.path.join(_ANALYSIS, "acceptance.py")):
    raise unittest.SkipTest("acceptance.py is a dev-repo tool; not in the package")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
if _ANALYSIS not in sys.path:
    sys.path.insert(0, _ANALYSIS)

from pa import db as pa_db          # noqa: E402
from pa import paths as pa_paths    # noqa: E402
import acceptance                    # noqa: E402

# Session and run IDs for the test fixture
_BH_SID = "test-bh-00000000-0000-4000-8000-000000000001"
_RT_SID = "test-rt-00000000-0000-4000-8000-000000000002"
_RT_EXPERT = "test-expert-0000-0000-0000-000000000003"
_RT_RET = "test-ret-00000000-0000-0000-000000000004"
_PROJECT_DIR = "Z:/test/project"


def _create_ledger(path):
    """Build a temp ledger with pa.db: 2 sessions, 4 runs, turns, savings, events."""
    conn = pa_db.connect(path)
    pa_db.init_schema(conn)

    # Sessions
    conn.execute(
        "INSERT INTO sessions (session_id, project, kind, agent_name,"
        " started, ended, cost_usd) VALUES (?,?,?,?,?,?,?)",
        (_BH_SID, _PROJECT_DIR, "other", None,
         "2026-09-20T00:00:00Z", "2026-09-20T00:10:00Z", 0.80))
    conn.execute(
        "INSERT INTO sessions (session_id, project, kind, agent_name,"
        " started, ended, cost_usd) VALUES (?,?,?,?,?,?,?)",
        (_RT_SID, _PROJECT_DIR, "router", "pa-session",
         "2026-09-20T01:00:00Z", "2026-09-20T01:10:00Z", 0.38))

    # Agent runs — three kinds: other, router, expert (+ retriever = 4 runs).
    # _BH_SID's seed_ctx is NULL: a main-thread run never records a seed, so
    # A2/A3 fall back to its first turn's ctx (m1 -> 100000).
    for run_id, sid, kind, seed, ctx_end, turns, atype in [
        (_BH_SID,     _BH_SID, "other",     None,  500000, 2, None),
        (_RT_SID,     _RT_SID, "router",    15000,  30000, 2, "pa-session"),
        (_RT_EXPERT,  _RT_SID, "expert",    13000,  50000, 3, "expert-fable"),
        (_RT_RET,     _RT_SID, "retriever",  5000,  20000, 1, "retriever-code"),
    ]:
        conn.execute(
            "INSERT INTO agent_runs (run_id, session_id, kind, seed_ctx,"
            " ctx_at_end, turns, agent_type) VALUES (?,?,?,?,?,?,?)",
            (run_id, sid, kind, seed, ctx_end, turns, atype))

    # Turns — with cold flags and rewrite values
    for msg_id, run_id, sid, ts, ctx, cold, rewrite, thinking, output, cost in [
        ("m1", _BH_SID,    _BH_SID, "2026-09-20T00:00:00Z", 100000, 1, 0, 50000, 100000, 0.50),
        ("m2", _BH_SID,    _BH_SID, "2026-09-20T00:01:00Z", 200000, 0, 0, 30000,  80000, 0.30),
        ("m3", _RT_SID,    _RT_SID, "2026-09-20T01:00:00Z",  50000, 0, 0, 20000,  40000, 0.10),
        ("m4", _RT_SID,    _RT_SID, "2026-09-20T01:01:00Z",  60000, 0, 0, 15000,  30000, 0.08),
        ("m5", _RT_EXPERT, _RT_SID, "2026-09-20T01:02:00Z",  30000, 0, 0, 10000,  20000, 0.05),
        ("m6", _RT_EXPERT, _RT_SID, "2026-09-20T01:03:00Z",  40000, 1, 1, 15000,  25000, 0.07),
        ("m7", _RT_EXPERT, _RT_SID, "2026-09-20T01:04:00Z",  45000, 0, 0, 12000,  18000, 0.06),
        ("m8", _RT_RET,    _RT_SID, "2026-09-20T01:05:00Z",  15000, 0, 0,  5000,  10000, 0.02),
    ]:
        conn.execute(
            "INSERT INTO turns (msg_id, run_id, session_id, ts, ctx,"
            " cold, rewrite, thinking, output, cost_usd) VALUES"
            " (?,?,?,?,?,?,?,?,?,?)",
            (msg_id, run_id, sid, ts, ctx, cold, rewrite,
             thinking, output, cost))

    # Savings — one unlocked session-level, one locked run-level. The
    # unlocked row's per_retrieval usd for _RT_EXPERT (8.0) is >= its lock
    # (6.0): normal post-lock accrual, not shrinkage.
    detail = json.dumps({"per_retrieval": [
        {"run_id": _RT_EXPERT, "usd": 8.0},
        {"run_id": _RT_RET, "usd": 3.0},
    ]})
    conn.execute(
        "INSERT INTO savings (run_id, session_id, measured_saved_usd,"
        " locked, measured_detail_json) VALUES (?,?,?,?,?)",
        (_BH_SID, _BH_SID, 0.0, 0, None))
    conn.execute(
        "INSERT INTO savings (run_id, session_id, measured_saved_usd,"
        " locked, measured_detail_json) VALUES (?,?,?,?,?)",
        (_RT_SID, _RT_SID, 10.0, 0, detail))
    conn.execute(
        "INSERT INTO savings (run_id, session_id, measured_saved_usd,"
        " locked, measured_detail_json) VALUES (?,?,?,?,?)",
        (_RT_EXPERT, _RT_SID, 6.0, 1, None))

    # Retrievals — _RT_EXPERT kept context out and is locked (satisfies
    # A12's "every kept-out retrieval has a locked savings row"); _RT_RET
    # kept nothing out.
    conn.execute(
        "INSERT INTO retrievals (run_id, parent_run_id, agent_type,"
        " kept_out_tokens) VALUES (?,?,?,?)",
        (_RT_EXPERT, _RT_SID, "expert-fable", 500))
    conn.execute(
        "INSERT INTO retrievals (run_id, parent_run_id, agent_type,"
        " kept_out_tokens) VALUES (?,?,?,?)",
        (_RT_RET, _RT_SID, "retriever-code", 0))

    # Events — toast and deny (deny on PHASE_PLAN.md for by_hand)
    deny_plan = json.dumps({"tool": "Bash", "target": "cat > PHASE_PLAN.md"})
    deny_other = json.dumps({"tool": "Bash", "target": "rm temp.txt"})
    conn.execute(
        "INSERT INTO events (ts, session_id, run_id, kind, detail_json)"
        " VALUES (?,?,?,?,?)",
        ("2026-09-20T00:05:00Z", _BH_SID, _BH_SID, "toast", None))
    conn.execute(
        "INSERT INTO events (ts, session_id, run_id, kind, detail_json)"
        " VALUES (?,?,?,?,?)",
        ("2026-09-20T00:06:00Z", _BH_SID, _BH_SID, "deny", deny_plan))
    conn.execute(
        "INSERT INTO events (ts, session_id, run_id, kind, detail_json)"
        " VALUES (?,?,?,?,?)",
        ("2026-09-20T01:05:00Z", _RT_SID, _RT_SID, "toast", None))
    conn.execute(
        "INSERT INTO events (ts, session_id, run_id, kind, detail_json)"
        " VALUES (?,?,?,?,?)",
        ("2026-09-20T01:06:00Z", _RT_SID, _RT_SID, "deny", deny_other))

    conn.commit()
    conn.close()


def _create_transcripts(projects_dir):
    """Two tiny transcripts: Write PHASE_PLAN.md, task_reminder, Bash result,
    AskUserQuestion."""
    slug = pa_paths.project_slug(_PROJECT_DIR)
    base = os.path.join(projects_dir, slug)
    os.makedirs(base, exist_ok=True)

    bash_content = "a" * 100  # 100 chars -> est = 25 tokens

    # Transcript 1 — by-hand main session
    t1 = os.path.join(base, _BH_SID + ".jsonl")
    lines = [
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_w", "name": "Write",
             "input": {"file_path": "phase-ends/current/PHASE_PLAN.md",
                       "content": "x"}}]},
            "timestamp": "2026-09-20T00:00:00Z"}),
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_w",
             "content": "ok"}]},
            "timestamp": "2026-09-20T00:00:01Z"}),
        json.dumps({"attachment": {"type": "task_reminder", "content": []},
                     "type": "attachment",
                     "timestamp": "2026-09-20T00:00:02Z"}),
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_b", "name": "Bash",
             "input": {"command": "echo hi"}}]},
            "timestamp": "2026-09-20T00:00:03Z"}),
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_b",
             "content": bash_content}]},
            "timestamp": "2026-09-20T00:00:04Z"}),
    ]
    with open(t1, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    # Transcript 2 — router main session
    t2 = os.path.join(base, _RT_SID + ".jsonl")
    lines = [
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_a", "name": "AskUserQuestion",
             "input": {"question": "Proceed?"}}]},
            "timestamp": "2026-09-20T01:00:00Z"}),
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_a",
             "content": "yes"}]},
            "timestamp": "2026-09-20T01:00:01Z"}),
    ]
    with open(t2, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


class TestAcceptance(unittest.TestCase):
    """Unit tests for the acceptance runner."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="pa3_acc_")
        cls.ledger_path = os.path.join(cls.tmpdir, "ledger.sqlite")
        cls.projects_dir = os.path.join(cls.tmpdir, "projects")
        cls.out_dir = os.path.join(cls.tmpdir, "out")
        os.makedirs(cls.out_dir, exist_ok=True)

        cls.sessions_path = os.path.join(cls.tmpdir, "sessions.json")
        with open(cls.sessions_path, "w", encoding="utf-8") as f:
            json.dump({
                "test": {
                    "by_hand": [{"sid": _BH_SID,
                                 "project_dir": _PROJECT_DIR}],
                    "router":  [{"sid": _RT_SID,
                                 "project_dir": _PROJECT_DIR}],
                }
            }, f, sort_keys=True, indent=1)

        _create_ledger(cls.ledger_path)
        _create_transcripts(cls.projects_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # -- helpers --

    def _build_scopes(self):
        """Gather scopes from the test fixture using the public API."""
        conn = pa_db.connect(self.ledger_path, readonly=True)
        sessions = acceptance.load_sessions(self.sessions_path)
        phase_data = sessions["test"]
        scopes = {}
        for scope_name, sessions_list in sorted(phase_data.items()):
            sids = [s["sid"] for s in sessions_list]
            ld = acceptance.ledger_rows(conn, sids)
            all_paths = []
            for s in sessions_list:
                all_paths.append(
                    acceptance._transcript_main(
                        self.projects_dir, s["project_dir"], s["sid"]))
                all_paths.extend(
                    acceptance._transcript_subagents(
                        self.projects_dir, s["project_dir"], s["sid"]))
            td = acceptance.transcript_rows(all_paths)
            scopes[scope_name] = {
                "ledger": ld,
                "transcripts": td,
                "reconcile": {},
                "report": None,
                "sessions_list": sessions_list,
                "projects_dir": self.projects_dir,
            }
        conn.close()
        return scopes

    # -- tests --

    def test_load_sessions(self):
        s = acceptance.load_sessions(self.sessions_path)
        self.assertIn("test", s)
        self.assertEqual(len(s["test"]["by_hand"]), 1)
        self.assertEqual(s["test"]["by_hand"][0]["sid"], _BH_SID)

    def test_ledger_rows(self):
        conn = pa_db.connect(self.ledger_path, readonly=True)
        ld = acceptance.ledger_rows(conn, [_BH_SID])
        conn.close()
        self.assertEqual(len(ld["runs"]), 1)
        self.assertEqual(ld["runs"][0]["run_id"], _BH_SID)
        self.assertEqual(len(ld["turns"]), 2)
        self.assertEqual(len(ld["events"]), 2)
        self.assertEqual(ld["retrievals"], [])  # no retrievals for by_hand

    def test_ledger_rows_retrievals(self):
        conn = pa_db.connect(self.ledger_path, readonly=True)
        ld = acceptance.ledger_rows(conn, [_RT_SID])
        conn.close()
        self.assertEqual(len(ld["retrievals"]), 2)
        rids = {r["run_id"] for r in ld["retrievals"]}
        self.assertEqual(rids, {_RT_EXPERT, _RT_RET})

    def test_transcript_rows(self):
        slug = pa_paths.project_slug(_PROJECT_DIR)
        base = os.path.join(self.projects_dir, slug)
        p = os.path.join(base, _BH_SID + ".jsonl")
        td = acceptance.transcript_rows([p])
        self.assertEqual(td["task_reminder"], 1)
        self.assertEqual(td["write_phase_plan"], 1)
        self.assertIn(p, td["per_path"])
        self.assertEqual(td["per_path"][p]["bash_result_tokens"], 25)
        self.assertEqual(td["per_path"][p]["ask_user_question"], 0)

    def test_parse_reconcile_ok(self):
        line = ("RESULT  price OK (worst |delta| 0.0000%)   "
                "coverage OK (100.000%)   cost-state fresh  -> exit 0")
        r = acceptance.parse_reconcile_result(line)
        self.assertEqual(r["price_delta_pct"], 0.0)
        self.assertEqual(r["coverage_pct"], 100.0)
        self.assertTrue(r["ok"])

    def test_parse_reconcile_fail(self):
        line = ("RESULT  price FAIL (worst |delta| 2.5607%)   "
                "coverage OK (100.000%)   cost-state fresh  -> exit 1")
        r = acceptance.parse_reconcile_result(line)
        self.assertAlmostEqual(r["price_delta_pct"], 2.5607)
        self.assertEqual(r["coverage_pct"], 100.0)
        self.assertFalse(r["ok"])

    def test_rows_a1_avg_ctx(self):
        scopes = self._build_scopes()
        r = acceptance.rows("test", scopes)
        a1 = r[0]
        self.assertEqual(a1["id"], "A1")
        # by_hand expert = bh-sid: avg(100000, 200000) = 150000 <= 150000
        self.assertAlmostEqual(a1["values"]["by_hand"], 150000.0)
        # router expert = rt-expert: avg(30000, 40000, 45000) = 38333.33...
        self.assertAlmostEqual(a1["values"]["router"], 38333.333, places=1)
        self.assertTrue(a1["detail"]["by_hand"].startswith("PASS"))
        self.assertTrue(a1["detail"]["router"].startswith("PASS"))
        # target 150000; passed is the router scope's verdict
        self.assertTrue(a1["passed"])

    def test_rows_a2_expert_seed(self):
        scopes = self._build_scopes()
        a2 = acceptance.rows("test", scopes)[1]
        # by_hand: seed_ctx=NULL, fallback first turn ctx 100000 > 40000 -> FAIL
        self.assertEqual(a2["values"]["by_hand"], {"median": 100000, "max": 100000})
        self.assertTrue(a2["detail"]["by_hand"].startswith("FAIL"))
        # router: seed_ctx=13000 <= 40000 -> PASS
        self.assertEqual(a2["values"]["router"], {"median": 13000, "max": 13000})
        self.assertTrue(a2["detail"]["router"].startswith("PASS"))
        # passed is the router scope's verdict
        self.assertTrue(a2["passed"])

    def test_rows_a3_retriever_seed(self):
        scopes = self._build_scopes()
        a3 = acceptance.rows("test", scopes)[2]
        self.assertIsNone(a3["values"]["by_hand"])  # no retriever in by_hand session
        self.assertEqual(a3["values"]["router"], {"median": 5000, "max": 5000})
        self.assertTrue(a3["detail"]["router"].startswith("PASS"))
        self.assertTrue(a3["passed"])

    def test_rows_a4_router_ctx(self):
        scopes = self._build_scopes()
        a4 = acceptance.rows("test", scopes)[3]
        self.assertIsNone(a4["values"]["by_hand"])
        self.assertEqual(a4["detail"]["by_hand"], "INFO n/a (by hand)")
        self.assertEqual(a4["values"]["router"]["max"], 30000)
        self.assertTrue(a4["detail"]["router"].startswith("PASS"))
        self.assertTrue(a4["passed"])

    def test_rows_a5_prefix_rewrites(self):
        scopes = self._build_scopes()
        a5 = acceptance.rows("test", scopes)[4]
        # by_hand: no rewrite=1 turns -> 0 (== 0 -> PASS)
        self.assertEqual(a5["values"]["by_hand"], 0)
        # router: m6 rewrite=1, not first turn of rt-expert -> 1 (FAIL)
        self.assertEqual(a5["values"]["router"], 1)
        self.assertIn("cold_after_first=1", a5["detail"]["router"])
        self.assertFalse(a5["passed"])

    def test_rows_a6_task_reminders(self):
        scopes = self._build_scopes()
        a6 = acceptance.rows("test", scopes)[5]
        self.assertEqual(a6["values"]["by_hand"], 1)
        self.assertEqual(a6["values"]["router"], 0)
        # by_hand: 1 != 0 -> FAIL; router: 0 == 0 -> PASS
        self.assertTrue(a6["detail"]["by_hand"].startswith("FAIL"))
        self.assertTrue(a6["detail"]["router"].startswith("PASS"))
        # passed is the router scope's verdict
        self.assertTrue(a6["passed"])

    def test_rows_a7_bash_results(self):
        scopes = self._build_scopes()
        a7 = acceptance.rows("test", scopes)[6]
        # by_hand: expert = bh-sid, transcript has 100 chars Bash -> 25
        # tokens; kept as session total, own verdict is null (needs task count)
        self.assertEqual(a7["values"]["by_hand"], 25)
        self.assertIn("per-task split needs the task count",
                      a7["detail"]["by_hand"])
        # router: expert subagent transcript doesn't exist in the fixture
        self.assertIsNone(a7["values"]["router"])
        self.assertIsNone(a7["passed"])

    def test_rows_a8_write_phase_plan(self):
        scopes = self._build_scopes()
        a8 = acceptance.rows("test", scopes)[7]
        # by_hand: 1 Write + 1 deny = 2, FAIL; router: 0, PASS
        self.assertEqual(a8["values"]["by_hand"], 2)
        self.assertTrue(a8["detail"]["by_hand"].startswith("FAIL"))
        self.assertEqual(a8["values"]["router"], 0)
        self.assertTrue(a8["detail"]["router"].startswith("PASS"))
        # passed is the router scope's verdict
        self.assertTrue(a8["passed"])

    def test_rows_a9_thinking_share(self):
        scopes = self._build_scopes()
        a9 = acceptance.rows("test", scopes)[8]
        # by_hand: thinking=80000, output=180000
        self.assertAlmostEqual(a9["values"]["by_hand"],
                               80000 / 180000, places=3)
        # router: thinking=77000, output=143000
        self.assertAlmostEqual(a9["values"]["router"],
                               77000 / 143000, places=3)
        self.assertIsNone(a9["passed"])

    def test_rows_a10_cost_per_request(self):
        scopes = self._build_scopes()
        a10 = acceptance.rows("test", scopes)[9]
        # by_hand: 0.40 > 0.10 -> FAIL
        self.assertAlmostEqual(a10["values"]["by_hand"], 0.40, places=3)
        self.assertTrue(a10["detail"]["by_hand"].startswith("FAIL"))
        # router: 0.38/6 <= 0.10 -> PASS; passed is the router verdict
        self.assertAlmostEqual(a10["values"]["router"],
                               0.38 / 6, places=3)
        self.assertTrue(a10["detail"]["router"].startswith("PASS"))
        self.assertTrue(a10["passed"])

    def test_rows_a11_reconcile_no_subprocess(self):
        scopes = self._build_scopes()
        a11 = acceptance.rows("test", scopes)[10]
        self.assertIsNone(a11["values"]["by_hand"])
        self.assertIsNone(a11["values"]["router"])
        self.assertIsNone(a11["passed"])

    def test_rows_a12_live_savings(self):
        scopes = self._build_scopes()
        a12 = acceptance.rows("test", scopes)[11]
        self.assertEqual(a12["values"]["by_hand"]["locked"], 0)
        self.assertEqual(a12["values"]["by_hand"]["negative"], 0)
        self.assertEqual(a12["values"]["router"]["locked"], 1)
        self.assertEqual(a12["values"]["router"]["negative"], 0)
        # _RT_EXPERT's kept-out retrieval is locked -> 0 unlocked
        self.assertEqual(a12["values"]["router"]["unlocked_with_kept_out"], 0)
        # Informational fields present
        self.assertIn("sum_locks_usd", a12["values"]["router"])
        self.assertIn("ratio", a12["values"]["router"])
        self.assertIn("locks_above_session", a12["values"]["router"])
        # Target text
        self.assertEqual(a12["target"], "locked, none negative")
        # Detail starts with PASS for both scopes
        self.assertTrue(a12["detail"]["by_hand"].startswith("PASS"))
        self.assertTrue(a12["detail"]["router"].startswith("PASS"))
        self.assertTrue(a12["passed"])

    def test_rows_a13_events_questions(self):
        scopes = self._build_scopes()
        a13 = acceptance.rows("test", scopes)[12]
        self.assertEqual(a13["values"]["by_hand"]["events"], {"toast": 1, "deny": 1})
        self.assertEqual(a13["values"]["by_hand"]["ask_user_question"], 0)
        self.assertEqual(a13["values"]["router"]["ask_user_question"], 1)
        self.assertIsNone(a13["passed"])

    def test_row_result_cell_per_scope_verdict(self):
        """Router scope passes, by_hand fails -> router cell PASS."""
        scopes = self._build_scopes()
        r = acceptance.rows("test", scopes)
        a2 = r[1]  # A2: by_hand FAIL (100000>40000), router PASS (13000<=40000)
        self.assertEqual(acceptance._row_result_cell(a2, "router"), "PASS")
        self.assertEqual(acceptance._row_result_cell(a2, "by_hand"), "FAIL")

    def test_row_result_cell_a4_by_hand_info(self):
        """A4 by_hand prints INFO (not applicable)."""
        scopes = self._build_scopes()
        r = acceptance.rows("test", scopes)
        a4 = r[3]
        self.assertEqual(acceptance._row_result_cell(a4, "by_hand"), "INFO")

    def test_a7_by_hand_info(self):
        """A7 by_hand prints INFO (session total, no task split)."""
        scopes = self._build_scopes()
        r = acceptance.rows("test", scopes)
        a7 = r[6]
        self.assertEqual(acceptance._row_result_cell(a7, "by_hand"), "INFO")

    def test_a12_unlocked_kept_out_fails(self):
        """A12: unlocked run with kept_out_tokens>0 -> that scope FAIL,
        router scope still PASS -> row passed True."""
        conn = pa_db.connect(self.ledger_path, readonly=True)
        ld_bh = acceptance.ledger_rows(conn, [_BH_SID])
        ld_rt = acceptance.ledger_rows(conn, [_RT_SID])
        conn.close()
        # Inject an unlocked kept-out run into by_hand; _BH_SID savings
        # is locked=0, so this retrieval has no locked savings row.
        ld_bh["retrievals"].append(
            {"run_id": _BH_SID, "parent_run_id": _BH_SID,
             "agent_type": "expert", "kept_out_tokens": 200})
        slug = pa_paths.project_slug(_PROJECT_DIR)
        base = os.path.join(self.projects_dir, slug)
        bh_tp = os.path.join(base, _BH_SID + ".jsonl")
        rt_tp = os.path.join(base, _RT_SID + ".jsonl")
        td_bh = acceptance.transcript_rows([bh_tp])
        td_rt = acceptance.transcript_rows([rt_tp])
        scopes = {
            "by_hand": {
                "ledger": ld_bh, "transcripts": td_bh,
                "reconcile": {}, "report": None,
                "sessions_list": [{"sid": _BH_SID,
                                   "project_dir": _PROJECT_DIR}],
                "projects_dir": self.projects_dir,
            },
            "router": {
                "ledger": ld_rt, "transcripts": td_rt,
                "reconcile": {}, "report": None,
                "sessions_list": [{"sid": _RT_SID,
                                   "project_dir": _PROJECT_DIR}],
                "projects_dir": self.projects_dir,
            },
        }
        r = acceptance.rows("test", scopes)
        a12 = r[11]
        # by_hand FAIL (unlocked kept-out), router PASS
        self.assertTrue(a12["detail"]["by_hand"].startswith("FAIL"))
        self.assertTrue(a12["detail"]["router"].startswith("PASS"))
        self.assertTrue(a12["passed"])
        self.assertEqual(acceptance._row_result_cell(a12, "by_hand"), "FAIL")
        self.assertEqual(acceptance._row_result_cell(a12, "router"), "PASS")

    def test_byte_identical_rerun(self):
        """Two runs on the same data produce byte-identical output."""
        out1 = os.path.join(self.tmpdir, "run1")
        out2 = os.path.join(self.tmpdir, "run2")
        os.makedirs(out1, exist_ok=True)
        os.makedirs(out2, exist_ok=True)

        base_args = ["--phase", "test", "--sessions", self.sessions_path,
                     "--ledger", self.ledger_path,
                     "--projects-dir", self.projects_dir,
                     "--no-subprocess", "--out"]
        acceptance.main(base_args + [out1])
        acceptance.main(base_args + [out2])

        f1 = os.path.join(out1, "phasetest.json")
        f2 = os.path.join(out2, "phasetest.json")
        with open(f1, "rb") as a, open(f2, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_write_json_determinism(self):
        path = os.path.join(self.tmpdir, "det.json")
        doc = {"b": 1, "a": {"x": 1.23456, "y": [2.99999]}}
        acceptance.write_json(path, doc)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(list(data.keys()), ["a", "b"])
        self.assertEqual(data["a"]["x"], 1.235)
        self.assertEqual(data["a"]["y"][0], 3.0)

    def test_thirteen_rows(self):
        """Output JSON has exactly 13 rows."""
        out = os.path.join(self.tmpdir, "count")
        os.makedirs(out, exist_ok=True)
        acceptance.main(["--phase", "test", "--sessions", self.sessions_path,
                         "--ledger", self.ledger_path,
                         "--projects-dir", self.projects_dir,
                         "--no-subprocess", "--out", out])
        with open(os.path.join(out, "phasetest.json")) as f:
            doc = json.load(f)
        self.assertEqual(len(doc["rows"]), 13)


_HASH = "e0a5" + "0" * 60


_NOISE = ("noise coder-opus55 ±0/28 (2026-09-26)\n"
          "noise expert-opus55 ±1/12 (2026-09-26)\n"
          "noise planner-phase ±1/12 (2026-09-26)\n"
          "noise retriever-code ±1/28 (2026-09-26)\n"
          "noise pa-session ±0/12 (2026-09-26)\n"
          "noise critic ±0/12 (2026-09-26)\n"
          "noise review ±0/12 (2026-09-26)\n")


def _fake_runner(fetch_ok=True, check_rc=1, graders_ok=True, plan_full=None,
                 noise=_NOISE):
    """Fake subprocess runner for the bench rows."""
    def runner(cmd):
        if cmd[0] == "git":
            if cmd[1] == "fetch":
                return (0, "") if fetch_ok else (
                    128, "fatal: unable to access remote")
            if cmd[1] == "ls-tree":
                return 0, ("bench/README.md\nbench/results/2026-09-24.json\n"
                           "bench/results/2026-09-25.json\n")
            if cmd[1] == "show":
                return 0, json.dumps({"suite": "canary",
                                      "claude_code": "2.1.281",
                                      "fixture_hash": _HASH})
        args = cmd[2:]
        if args[0] == "plan":
            if args[1] == "full" and plan_full:
                return 0, plan_full + "\n"
            return 0, acceptance._PLAN_WANT[args[1]] + "\n"
        if args[0] == "fixtures":
            return 0, "fixtures OK {}\n".format(_HASH)
        if args[0] == "check":
            graders = ("graders OK (full: 0 grader, 0 harness, 0 unaudited)"
                       if graders_ok else
                       "graders FAIL (full: 1 grader, 0 harness, 0 unaudited)")
            return check_rc, ("budget OK (light 2.0, full 2.0 points)\n"
                              + graders + "\n" + noise +
                              "calibration FAIL coder coder-opus55 t1 10/10 "
                              "(100%)\n")
        return 2, "unexpected"
    return runner


class TestBenchRows(unittest.TestCase):
    """B1-B8 with a fake runner and a temp results dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # older by date, later by name: the date decides
        self._result("zz-old.json", "2026-09-25", work_dir="",
                     tasks=[{"passed": False, "failure_kind": None}])
        self._result("2026-09-26-3.json", "2026-09-26", work_dir="/w/a",
                     tasks=[{"passed": True, "failure_kind": None},
                            {"passed": False, "failure_kind": "agent"}])

    def _result(self, name, date, work_dir, tasks):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
            json.dump({"date": date, "work_dir": work_dir,
                       "arms": [{"agent": "a", "tasks": tasks}]}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_pass(self):
        rs = acceptance.bench_rows(_fake_runner(), self.tmp)
        self.assertEqual([r[0] for r in rs],
                         ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"])
        self.assertEqual({r[2] for r in rs}, {"PASS"})
        self.assertEqual(rs[1][3], _HASH)
        self.assertIn("budget OK", rs[3][3])
        self.assertIn("2026-09-25.json", rs[4][3])
        self.assertIn("suite=canary", rs[4][3])
        self.assertIn("claude_code=2.1.281", rs[4][3])
        self.assertIn("== B2", rs[4][3])
        self.assertEqual(rs[5][3], "2026-09-26-3.json work_dir=/w/a "
                                   "failed=1 unclassified=0")
        self.assertIn("coder 28", rs[6][3])
        self.assertIn("tier-3 tasks", rs[6][3])
        self.assertIn("critic ±0/12", rs[7][3])

    def test_b3_pass_while_check_exits_1(self):
        rs = acceptance.bench_rows(_fake_runner(check_rc=1), self.tmp)
        self.assertEqual(rs[2][1], "grader health")
        self.assertEqual(rs[2][2], "PASS")
        self.assertTrue(rs[2][3].startswith("graders OK"))
        self.assertEqual(rs[3][2], "PASS")

    def test_b3_fail_without_graders_ok(self):
        rs = acceptance.bench_rows(_fake_runner(graders_ok=False), self.tmp)
        self.assertEqual(rs[2][2], "FAIL")
        self.assertEqual(rs[3][2], "PASS")

    def test_b6_fail_unclassified_failure(self):
        self._result("2026-09-27.json", "2026-09-27", work_dir="/w/b",
                     tasks=[{"passed": False, "failure_kind": None}])
        rs = acceptance.bench_rows(_fake_runner(), self.tmp)
        self.assertEqual(rs[5][2], "FAIL")
        self.assertIn("2026-09-27.json", rs[5][3])
        self.assertIn("unclassified=1", rs[5][3])

    def test_b6_fail_no_work_dir(self):
        self._result("2026-09-27.json", "2026-09-27", work_dir=None,
                     tasks=[])
        rs = acceptance.bench_rows(_fake_runner(), self.tmp)
        self.assertEqual(rs[5][2], "FAIL")
        self.assertIn("work_dir=-", rs[5][3])

    def test_b5_fetch_failure(self):
        runner = _fake_runner(fetch_ok=False)
        rs = acceptance.bench_rows(runner, self.tmp)
        self.assertEqual(rs[4][2], "FAIL")
        self.assertIn("fetch failed", rs[4][3])
        self.assertEqual(rs[5][0], "B6")
        self.assertEqual(rs[5][2], "PASS")
        self.assertEqual(acceptance.bench_main(runner=runner,
                                               results_dir=self.tmp), 1)
        self.assertEqual([r[0] for r in rs][-2:], ["B7", "B8"])
        self.assertEqual(rs[6][2], "PASS")

    def test_b7_fail_count_below_floor(self):
        full = acceptance._PLAN_WANT["full"].replace("critic 12", "critic 6")
        rs = acceptance.bench_rows(_fake_runner(plan_full=full), self.tmp)
        self.assertEqual(rs[6][0], "B7")
        self.assertEqual(rs[6][2], "FAIL")
        self.assertIn("low: critic", rs[6][3])

    def test_b8_fail_noise_unknown(self):
        noise = _NOISE.replace("noise critic ±0/12 (2026-09-26)",
                               "noise critic unknown")
        rs = acceptance.bench_rows(_fake_runner(noise=noise), self.tmp)
        self.assertEqual(rs[7][0], "B8")
        self.assertEqual(rs[7][2], "FAIL")
        self.assertIn("unknown: critic", rs[7][3])


_DOCTOR_OK = ("  OK   ledger: schema v9\n"
              "  OK   hook SessionStart: run() 154.2 ms\n"
              "  OK   hook Stop: run() 28.6 ms\n")


class TestStandingRows(unittest.TestCase):
    """A14-A16 on a temp package and a stub doctor runner."""

    def setUp(self):
        self.pkg = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.pkg, "docs"))
        os.makedirs(os.path.join(self.pkg, "agents"))
        self._doc("| E1 | x | RESOLVED | y | — | 3.1 |\n")
        self._agent("coder", "3.10.1")
        self._agent("mine", "3.10.2+u1")
        self.cmds = []

    def tearDown(self):
        shutil.rmtree(self.pkg, ignore_errors=True)

    def _doc(self, text):
        with open(os.path.join(self.pkg, "docs", "pa3-verification.md"),
                  "w", encoding="utf-8") as f:
            f.write("## Spec\n\n" + text)

    def _agent(self, name, version):
        with open(os.path.join(self.pkg, "agents", name + ".md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nname: {}\nversion: {}\n---\nbody\n".format(
                name, version))

    def _runner(self, out, rc=0):
        def runner(cmd):
            self.cmds.append(cmd)
            return rc, out
        return runner

    def _rows(self, out=_DOCTOR_OK, rc=0):
        return acceptance.standing_rows(self._runner(out, rc), self.pkg,
                                        os.path.join(self.pkg, "home"))

    def test_all_pass(self):
        rs = self._rows()
        self.assertEqual([r[0] for r in rs], ["A14", "A15", "A16"])
        self.assertEqual({r[2] for r in rs}, {"PASS"})
        self.assertEqual(rs[0][3], "open rows 0")
        self.assertEqual(rs[2][3], "SessionStart 154.2 ms; Stop 28.6 ms")
        cmd = self.cmds[0]
        self.assertEqual(cmd[-1], "doctor")
        self.assertEqual(os.path.normpath(cmd[1]), os.path.join(
            self.pkg, "home", "pa3", "pa_ledger.py"))

    def test_a14_fail_open_row(self):
        self._doc("| E23 | throttle | " + "OPEN | two weeks | — | — |\n")
        rs = self._rows()
        self.assertEqual(rs[0][2], "FAIL")
        self.assertEqual(rs[0][3], "open rows 1")

    def test_a15_fail_bad_version(self):
        self._agent("old", "3.10")
        rs = self._rows()
        self.assertEqual(rs[1][2], "FAIL")
        self.assertIn("old=3.10", rs[1][3])

    def test_a16_fail_warn(self):
        out = _DOCTOR_OK + "WARN hook SubagentStop: run() 2100 ms > 2000 ms\n"
        rs = self._rows(out)
        self.assertEqual(rs[2][2], "FAIL")
        self.assertIn("SubagentStop 2100 ms > 2000 ms", rs[2][3])

    def test_a16_fail_raised(self):
        out = _DOCTOR_OK + "FAIL hook Stop: run() raised KeyError: 'x'\n"
        self.assertEqual(self._rows(out)[2][2], "FAIL")

    def test_a16_fail_no_hook_lines(self):
        rs = self._rows("can't open file", rc=2)
        self.assertEqual(rs[2][2], "FAIL")
        self.assertEqual(rs[2][3], "no hook lines (rc 2)")

    def test_bench_main_exits_1_on_standing_fail(self):
        results = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, results, True)
        with open(os.path.join(results, "r.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"date": "2026-09-27", "work_dir": "/w",
                       "arms": []}, f)
        bench = _fake_runner()

        def runner(cmd):
            if cmd[-1] == "doctor":
                return 0, _DOCTOR_OK
            return bench(cmd)
        home = os.path.join(self.pkg, "home")
        self.assertEqual(acceptance.bench_main(
            runner=runner, results_dir=results, pkg=self.pkg,
            claude_dir=home), 0)
        self._agent("old", "3.10")
        self.assertEqual(acceptance.bench_main(
            runner=runner, results_dir=results, pkg=self.pkg,
            claude_dir=home), 1)


if __name__ == "__main__":
    unittest.main()
