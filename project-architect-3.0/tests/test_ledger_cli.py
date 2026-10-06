"""pa.ledger_cli cmd_report --by project: usage share from summary.json's
``by_account`` slice (T8), not the largest-cost account's stopgap (3.6 T10).
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
from pa import summary as summary_mod  # noqa: E402

CWD = "Z:/Same/Proj"
ACCT_A = "dev-a@example.com"
ACCT_B = "dev-b@example.com"


def run_cli(*argv):
    """Run the CLI in-process; returns ``(rc, stdout, stderr)``."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = ledger_cli.main(list(argv))
    return rc, buf_out.getvalue(), buf_err.getvalue()


class ReportByProjectAccountShareTest(unittest.TestCase):
    """One project, two accounts: the report's usage share is each row's own
    account slice, the money columns stay the project's all-accounts totals."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-cli-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        for sid, acct, cost in (("s_a", ACCT_A, 3.0), ("s_b", ACCT_B, 2.0)):
            db.upsert_session(conn, {"session_id": sid, "account": acct, "cwd": CWD,
                                      "started": "2026-09-01T09:00:00Z"})
            db.upsert_turn(conn, {"msg_id": sid + "_m0", "run_id": sid, "session_id": sid,
                                   "account": acct, "ts": "2026-09-01T10:00:00Z",
                                   "model": "claude-fable-5-1", "cost_usd": cost, "kind": "api"})
        db.close(conn)

        # summary.json: this project's by_account slices differ from each other and
        # from the old top-level stopgap (largest-cost account's share, kept for
        # backward-compat callers without by_account)
        pkey = summary_mod._project_key(CWD)
        doc = {"projects": {pkey: {
            "label": CWD,
            "pct_used": {"five_hour": 99, "seven_day": 99},   # old stopgap: neither account's own share
            "by_account": {
                ACCT_A: {"pct_used": {"five_hour": 12, "seven_day": 3}},
                ACCT_B: {"pct_used": {"five_hour": 65, "seven_day": 20}},
            },
            "lifetime": {"cost_used": 5.0, "cost_saved_measured": 2.5, "ratio": 0.5},
        }}}
        os.makedirs(os.path.dirname(paths.summary_path()), exist_ok=True)
        with open(paths.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_usage_share_is_the_row_account_slice(self):
        rc, text, errs = run_cli("report", "--by", "project", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        accounts = {a["account"]: a for a in doc["accounts"]}
        self.assertIn(ACCT_A, accounts)
        self.assertIn(ACCT_B, accounts)
        rows_a = {r["key"]: r for r in accounts[ACCT_A]["rows"]}
        rows_b = {r["key"]: r for r in accounts[ACCT_B]["rows"]}
        self.assertIn(CWD, rows_a)
        self.assertIn(CWD, rows_b)
        # each account's own window share, not the top-level stopgap (99/99)
        self.assertEqual(rows_a[CWD]["pct_used_five_hour"], 12)
        self.assertEqual(rows_a[CWD]["pct_used_seven_day"], 3)
        self.assertEqual(rows_b[CWD]["pct_used_five_hour"], 65)
        self.assertEqual(rows_b[CWD]["pct_used_seven_day"], 20)
        # the money column: the project's all-accounts total, same for both rows
        self.assertEqual(rows_a[CWD]["lifetime_saved"], 2.5)
        self.assertEqual(rows_b[CWD]["lifetime_saved"], 2.5)


class ReportSavingsTiersTest(unittest.TestCase):
    """fix-10: the report names counted and modeled savings per account."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-tiers-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        sid, rid = "s_t", "exp_t"
        db.upsert_session(conn, {"session_id": sid, "account": ACCT_A, "cwd": CWD,
                                  "started": "2026-09-01T09:00:00Z"})
        db.upsert_agent_run(conn, {"run_id": rid, "session_id": sid, "kind": "expert",
                                   "agent_type": "expert"})
        for i in range(4):
            db.upsert_turn(conn, {"msg_id": "m%d" % i, "run_id": rid, "session_id": sid,
                                  "account": ACCT_A, "ts": "2026-09-01T10:%02d:00Z" % (5 * i),
                                  "model": "claude-fable-5-1", "ctx": 100000,
                                  "cost_usd": 1.0, "kind": "api"})
        # counted: 5000 + 2000 (retrievals in window) + 300 (credited call); void, early
        # and uncredited rows are left out
        for run_id, ts, kept, void in (("r1", "2026-09-01T10:02:00Z", 5000, 0),
                                       ("r2", "2026-09-01T10:07:00Z", 2000, 0),
                                       ("r_void", "2026-09-01T10:08:00Z", 9000, 1),
                                       ("r_old", "2026-08-20T10:00:00Z", 4000, 0)):
            db.upsert_retrieval(conn, {"run_id": run_id, "parent_run_id": rid,
                                       "returned_at": ts, "kept_out_tokens": kept,
                                       "void": void})
        for cid, ts, kept in (("c1", "2026-09-01T10:03:00Z", 300),
                              ("c0", "2026-09-01T10:04:00Z", 0),
                              ("c_old", "2026-08-20T10:00:00Z", 800)):
            db.upsert(conn, "tool_calls", {"id": cid, "session_id": sid, "run_id": rid,
                                           "ts": ts, "tool": "Bash", "kind": "runsh",
                                           "kept_tokens": kept})
        # fix-10.c2: the ledger era, and one pre-era session with seed cost and no helper rows
        db.set_meta(conn, "created", "2026-09-01T00:00:00Z")
        db.upsert_session(conn, {"session_id": "s_pre", "account": ACCT_A, "cwd": CWD,
                                  "started": "2026-08-10T09:00:00Z"})
        db.upsert_agent_run(conn, {"run_id": "s_pre", "session_id": "s_pre", "kind": "router"})
        db.upsert_agent_run(conn, {"run_id": "exp_pre", "session_id": "s_pre", "kind": "expert",
                                   "agent_type": "expert", "seed_ctx": 50000})
        db.upsert_turn(conn, {"msg_id": "m_pre", "run_id": "exp_pre", "session_id": "s_pre",
                              "account": ACCT_A, "ts": "2026-08-10T10:00:00Z",
                              "model": "claude-fable-5-1", "ctx": 60000, "cost_usd": 5.0,
                              "kind": "api"})
        db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_block_prints_three_labels(self):
        rc, text, errs = run_cli("report", "--since", "2026-09-01")
        self.assertEqual(rc, 0, errs)
        self.assertIn("savings, since 2026-09-01, %s" % ACCT_A, text)
        self.assertIn("counted   kept out of the parent context: 7,300 tokens over 2 helper"
                      " runs and 1 credited tool calls", text)
        self.assertIn("modeled   carry avoided at a 1,000k window:", text)
        self.assertIn("(450k: ", text)
        self.assertIn("modeled   whole account replayed as one session:", text)
        self.assertIn("never summed, D39", text)

    def test_json_carries_tiers(self):
        rc, text, errs = run_cli("report", "--since", "2026-09-01", "--json")
        self.assertEqual(rc, 0, errs)
        acct = {a["account"]: a for a in json.loads(text)["accounts"]}[ACCT_A]
        tiers = acct["tiers"]
        self.assertEqual(tiers["counted"], {"tokens": 7300, "runs": 2, "calls": 1})
        self.assertEqual(tiers["carry"]["window"], 1000000)
        self.assertEqual(set(tiers["carry"]["sensitivity"]), {"450000", "200000"})
        self.assertIn("replay_usd", tiers)


    def test_pool_line_names_borrowed_and_missing_rates(self):
        """fix-20: a borrowed weekly rate is marked; a family with no rate is named."""
        conn = db.connect(paths.db_path())
        for win, fam, rate in (("five_hour", "fable", 0.0045), ("five_hour", "opus", 0.0028),
                               ("five_hour", "sonnet", 0.0011), ("seven_day", "fable", 0.0011),
                               ("seven_day", "opus", 0.0007)):
            db.upsert_fit_pool(conn, {"tier": "max", "window": win, "family": fam, "rate": rate,
                                      "se": 0.0001, "n": 10, "n_instances": 3,
                                      "regime_since": "2026-09-01T00:00:00Z",
                                      "built_at": "2026-09-02T00:00:00Z"})
        conn.commit()
        db.close(conn)
        rc, text, errs = run_cli("report", "--since", "2026-09-01")
        self.assertEqual(rc, 0, errs)
        line = [ln for ln in text.splitlines() if ln.startswith("pool max seven_day:")][0]
        self.assertIn("sonnet 0.00027/$ (borrowed from 5h)", line)
        self.assertIn("· no rate: haiku", line)
        line5 = [ln for ln in text.splitlines() if ln.startswith("pool max five_hour:")][0]
        self.assertNotIn("borrowed", line5)
        self.assertIn("· no rate: haiku", line5)

    def test_all_starts_at_the_era(self):
        rc, text, errs = run_cli("report", "--window", "all", "--json")
        self.assertEqual(rc, 0, errs)
        acct = {a["account"]: a for a in json.loads(text)["accounts"]}[ACCT_A]
        self.assertGreaterEqual(acct["tiers"]["carry"]["usd"], 0.0)   # pre-era seed left out
        self.assertEqual(acct["tiers"]["counted"]["tokens"], 7300)
        rc, text, errs = run_cli("report", "--window", "all")
        self.assertEqual(rc, 0, errs)
        self.assertIn("savings, since 2026-09-01, %s" % ACCT_A, text)
        self.assertIn("the all-time block starts at the ledger era", text)


class ExportBesideTest(unittest.TestCase):
    """T28: export --phase lists a ``beside <phase>`` session in the header, not its rows."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-export-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        self.proj = os.path.join(self.dir, "proj")
        os.makedirs(self.proj)
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        for sid, tag, cost in (("s_own", "9.9", 1.0), ("s_bes", "beside 9.9", 0.75)):
            db.upsert_session(conn, {"session_id": sid, "account": ACCT_A, "cwd": self.proj,
                                      "started": "2026-09-01T09:00:00Z"})
            conn.execute("UPDATE sessions SET phase=? WHERE session_id=?", (tag, sid))
            for i in range(2):
                db.upsert_turn(conn, {"msg_id": "%s_m%d" % (sid, i), "run_id": sid,
                                       "session_id": sid, "account": ACCT_A,
                                       "ts": "2026-09-01T10:0%d:00Z" % i,
                                       "model": "claude-fable-5-1", "cost_usd": cost / 2,
                                       "kind": "api"})
        conn.commit()
        db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_beside_in_header_rows_absent(self):
        import gzip
        out_path = os.path.join(self.dir, "out.jsonl.gz")
        rc, text, errs = run_cli("export", "--project", self.proj, "--phase", "9.9",
                                 "--out", out_path)
        self.assertEqual(rc, 0, errs)
        self.assertIn("beside: 1 sessions $0.75 (own rows, not in the phase)", text)
        with gzip.open(out_path, "rt", encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
        header = lines[0]
        self.assertEqual(header["beside"],
                         [{"session_id": "s_bes", "cost_usd": 0.75, "n_turns": 2}])
        body = json.dumps(lines[1:])
        self.assertIn("s_own", body)
        self.assertNotIn("s_bes", body)


class RepairPhaseTest(unittest.TestCase):
    """T31: a beside session's runs stamped the owner phase: report excludes them, the heal restamps."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-heal-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        for sid, tag in (("s_own", "24"), ("s_bes", "beside 24")):
            db.upsert_session(conn, {"session_id": sid, "account": ACCT_A, "cwd": CWD,
                                      "started": "2026-09-01T09:00:00Z"})
            conn.execute("UPDATE sessions SET phase=? WHERE session_id=?", (tag, sid))
            run = sid + "_r1"
            db.upsert_agent_run(conn, {"run_id": run, "session_id": sid, "kind": "subagent",
                                       "agent_type": "retriever-code", "phase": "24",
                                       "started": "2026-09-01T10:00:00Z", "seed_ctx": 1000})
            db.upsert_turn(conn, {"msg_id": run + "_m0", "run_id": run, "session_id": sid,
                                   "account": ACCT_A, "ts": "2026-09-01T10:00:00Z",
                                   "model": "claude-fable-5-1", "cost_usd": 1.0, "kind": "api"})
        conn.commit()
        db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def _report(self):
        rc, text, errs = run_cli("report", "--phase", "24", "--seeds", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        seeds = sorted(s["run_id"] for s in doc["seeds"])
        rows = sorted(r["key"] for a in doc["accounts"] for r in a["rows"])
        return seeds, rows

    def test_report_excludes_beside_then_heal(self):
        self.assertEqual(self._report(), (["s_own_r1"], ["s_own"]))
        rc, text, errs = run_cli("doctor", "--repair", "phase")
        self.assertEqual(rc, 0, errs)
        self.assertIn("repair phase: 1 rows restamped", text)
        conn = db.connect(paths.db_path())
        phase = conn.execute("SELECT phase FROM agent_runs WHERE run_id='s_bes_r1'").fetchone()[0]
        db.close(conn)
        self.assertEqual(phase, "beside 24")
        rc, text, errs = run_cli("doctor", "--repair", "phase")
        self.assertIn("repair phase: 0 rows restamped", text)
        self.assertEqual(self._report(), (["s_own_r1"], ["s_own"]))


class ReportByProjectFallbackTest(unittest.TestCase):
    """No ``by_account`` in the project's summary entry (old data): the top-level
    ``pct_used`` is used, unchanged."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-cli-fb-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": "s_fb", "account": ACCT_A, "cwd": CWD,
                                  "started": "2026-09-01T09:00:00Z"})
        db.upsert_turn(conn, {"msg_id": "s_fb_m0", "run_id": "s_fb", "session_id": "s_fb",
                               "account": ACCT_A, "ts": "2026-09-01T10:00:00Z",
                               "model": "claude-fable-5-1", "cost_usd": 1.0, "kind": "api"})
        db.close(conn)
        pkey = summary_mod._project_key(CWD)
        doc = {"projects": {pkey: {
            "label": CWD,
            "pct_used": {"five_hour": 30, "seven_day": 8},
            "lifetime": {"cost_used": 1.0, "cost_saved_measured": 0.4, "ratio": 0.4},
        }}}
        os.makedirs(os.path.dirname(paths.summary_path()), exist_ok=True)
        with open(paths.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_falls_back_to_top_level_pct_used(self):
        rc, text, errs = run_cli("report", "--by", "project", "--json")
        self.assertEqual(rc, 0, errs)
        doc = json.loads(text)
        entry = {a["account"]: a for a in doc["accounts"]}[ACCT_A]
        row = {r["key"]: r for r in entry["rows"]}[CWD]
        self.assertEqual(row["pct_used_five_hour"], 30)
        self.assertEqual(row["pct_used_seven_day"], 8)
        self.assertEqual(row["lifetime_saved"], 0.4)


class RoundFloatsTest(unittest.TestCase):
    """``_round_floats`` (used by ``recalc --check`` before diffing) rounds every
    float to 6 decimals, so summation-order noise below 1e-6 isn't a diff."""

    def _dump(self, doc):
        return json.dumps(ledger_cli._round_floats(doc), sort_keys=True)

    def test_twelfth_decimal_difference_compares_equal(self):
        before = {"saved_modeled": 9168.417875000001}
        after = {"saved_modeled": 9168.417875000003}
        self.assertEqual(self._dump(before), self._dump(after))

    def test_hundredths_difference_still_differs(self):
        before = {"saved_modeled": 9168.41}
        after = {"saved_modeled": 9168.42}
        self.assertNotEqual(self._dump(before), self._dump(after))


class DoctorUpdateRulesTest(unittest.TestCase):
    """T20: doctor rows for the tools/pa3_update.py (user) and tools/* (project) allow rules."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-dur-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.user = os.path.join(self.tmp, "user-settings.json")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".claude"))

    def _write(self, path, allow):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"permissions": {"allow": allow}}, fh)

    def _run(self, project):
        doc = ledger_cli._Doctor()
        ledger_cli._doctor_update_rules(doc, self.user, project)
        return doc.lines

    def test_both_present_ok(self):
        self._write(self.user, ["Bash(C:/Py/python.exe tools/pa3_update.py)"])
        self._write(os.path.join(self.proj, ".claude", "settings.json"),
                    ["Bash(C:/Py/python.exe tools/*)"])
        lines = self._run(self.proj)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(ln.startswith("OK") for ln in lines), lines)

    def test_the_old_prefix_form_warns_with_its_replacement(self):
        """3.11 T15: `tools/*:*` reads its * literally in current Claude Code and matches nothing."""
        self._write(self.user, ["Bash(C:/Py/python.exe tools/pa3_update.py)"])
        self._write(os.path.join(self.proj, ".claude", "settings.json"),
                    ["Bash(C:/Py/python.exe tools/*:*)"])
        lines = self._run(self.proj)
        self.assertTrue(lines[1].startswith("WARN"), lines)
        self.assertIn("matches nothing", lines[1])
        self.assertIn("tools/*)`", lines[1])

    def test_both_missing_warn(self):
        self._write(self.user, ["Bash(git status)"])
        self._write(os.path.join(self.proj, ".claude", "settings.json"), [])
        lines = self._run(self.proj)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(ln.startswith("WARN") for ln in lines), lines)
        self.assertIn("tools/pa3_update.py", lines[0])
        self.assertIn("tools/*", lines[1])

    def test_no_project_one_row(self):
        self.assertEqual(len(self._run(None)), 1)


class SavingsDisplayDoctorTest(unittest.TestCase):
    """T11.1: doctor's ``savings display`` row FAILs for a >= $1 session with no
    ``windows`` block; ``--repair savings`` rebuilds summary.json and clears the flag."""

    SID = "s_sd"

    def setUp(self):
        import time
        from unittest import mock

        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-cli-sd-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        self.project = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.project, ".run"))
        os.makedirs(os.path.join(self.dir, "projects"))
        self._p = mock.patch.object(ledger_cli.paths, "projects_dir",
                                    lambda: os.path.join(self.dir, "projects"))
        self._p.start()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        db.upsert_session(conn, {"session_id": self.SID, "account": ACCT_A, "cwd": CWD,
                                  "started": now, "cost_usd": 2.5})
        db.upsert_agent_run(conn, {"run_id": "helper1", "session_id": self.SID,
                                    "kind": "coder", "started": now})
        db.close(conn)
        with open(paths.summary_path(), "w", encoding="utf-8") as fh:   # patch-only row
            json.dump({"sessions": {self.SID: {"cost_usd": 2.5, "saved_measured": None}}}, fh)
        self.health = os.path.join(self.project, ".run", "health.json")
        with open(self.health, "w", encoding="utf-8") as fh:
            json.dump({"other": 1, "savings_missing": {"session": self.SID, "since": 1.0,
                       "detail": "-", "hook_repair": None, "row": None}}, fh)

    def tearDown(self):
        self._p.stop()
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def _row(self, text):
        return [ln for ln in text.splitlines() if "savings display" in ln]

    def test_fail_then_repair_clears_row_and_flag(self):
        _rc, text, _e = run_cli("doctor", "--skip-hooks", "--project", self.project)
        row = self._row(text)
        self.assertEqual(len(row), 1, text)
        self.assertIn("FAIL", row[0])
        self.assertIn(self.SID, row[0])
        self.assertIn("no windows block", row[0])

        rc, text, errs = run_cli("doctor", "--repair", "savings", "--project", self.project)
        self.assertEqual(rc, 0, text + errs)
        self.assertTrue(text.startswith("OK"), text)
        with open(self.health, encoding="utf-8") as fh:
            health = json.load(fh)
        self.assertNotIn("savings_missing", health)
        self.assertEqual(health.get("other"), 1)
        _rc, text, _e = run_cli("doctor", "--skip-hooks", "--project", self.project)
        self.assertIn("OK", self._row(text)[0])

        ok, row = ledger_cli.repair_savings(project=self.project)      # idempotent
        self.assertTrue(ok, row)

    def _session(self, sid, started, cost, turn_ts=None, ended=None):
        conn = db.connect(paths.db_path())
        db.upsert_session(conn, {"session_id": sid, "account": ACCT_A, "cwd": CWD,
                                  "started": started, "ended": ended, "cost_usd": cost})
        if turn_ts:
            conn.execute("INSERT INTO turns(msg_id, run_id, session_id, account, ts, cost_usd)"
                         " VALUES(?, ?, ?, ?, ?, ?)", ("m-" + sid, sid, sid, ACCT_A, turn_ts, cost))
            conn.commit()
        db.close(conn)

    @staticmethod
    def _ago(seconds):
        import time

        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))

    def test_open_session_counts_by_its_last_turn(self):
        """3.15.3: an open session started three days ago with a turn an hour ago is checked
        and repaired; by its start it was out of the check and of summary.json."""
        from unittest import mock

        self._session("s_long", self._ago(3 * 86400), 9.0, turn_ts=self._ago(3600))
        conn = db.connect(paths.db_path())
        try:
            fails, _n = ledger_cli._savings_display_fails(conn)
        finally:
            db.close(conn)
        self.assertIn(("s_long", "no windows block"), fails)
        recalcs = []
        with mock.patch.object(ledger_cli, "_cmd_recalc_run", lambda a: recalcs.append(a)):
            ok, row = ledger_cli.repair_savings("s_long", self.project)
        self.assertTrue(ok, row)
        self.assertEqual(recalcs, [])          # the rebuild alone brought the block back
        with open(paths.summary_path(), encoding="utf-8") as fh:
            self.assertIn("windows", json.load(fh)["sessions"]["s_long"])

    def test_flagged_session_is_checked_at_any_cost(self):
        """3.15.3: the flagged session is checked under $1 and past 24 h while active within
        the summary's 48 h; a session quiet for three days is not."""
        self._session("s_small", self._ago(30 * 3600), 0.4, turn_ts=self._ago(30 * 3600))
        self._session("s_quiet", self._ago(4 * 86400), 5.0, turn_ts=self._ago(3 * 86400))
        conn = db.connect(paths.db_path())
        try:
            general = [f[0] for f in ledger_cli._savings_display_fails(conn)[0]]
            small = [f[0] for f in ledger_cli._savings_display_fails(conn, sid="s_small")[0]]
            quiet = [f[0] for f in ledger_cli._savings_display_fails(conn, sid="s_quiet")[0]]
        finally:
            db.close(conn)
        self.assertNotIn("s_small", general)
        self.assertIn("s_small", small)
        self.assertNotIn("s_quiet", quiet)

    def test_recalc_only_what_the_rebuild_leaves_failing(self):
        """3.15.3: the rebuild runs first; a session still FAIL after it is recalculated, and
        a row still FAIL keeps the flag."""
        from unittest import mock

        recalcs = []
        with mock.patch.object(ledger_cli, "rebuild_summary", lambda *a, **k: None), \
                mock.patch.object(ledger_cli, "_cmd_recalc_run",
                                  lambda a: recalcs.append(a.session)):
            ok, row = ledger_cli.repair_savings(self.SID, self.project)
        self.assertFalse(ok, row)
        self.assertEqual(recalcs, [self.SID])
        with open(self.health, encoding="utf-8") as fh:
            self.assertIn("savings_missing", json.load(fh))


class AccountsTierTest(unittest.TestCase):
    """``accounts tier EMAIL max20|max5|pro|clear``: the override, and the list's tier column."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-cli-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        db.upsert_account(conn, {"email": ACCT_A, "tier": "max5", "tier_source": "credentials"})
        db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def _tier(self):
        conn = db.connect(paths.db_path())
        try:
            return tuple(conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                                      (ACCT_A,)).fetchone())
        finally:
            db.close(conn)

    def test_override_list_and_clear(self):
        from pa import accounts
        rc, text, errs = run_cli("accounts", "tier", ACCT_A, "max20")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(self._tier(), ("max20", "override"))
        conn = db.connect(paths.db_path())
        try:
            accounts.stamp_tier(conn, ACCT_A, "pro")        # a credentials restamp loses
        finally:
            db.close(conn)
        self.assertEqual(self._tier(), ("max20", "override"))
        rc, text, errs = run_cli("accounts", "list")
        self.assertEqual(rc, 0, errs)
        self.assertIn("tier", text.splitlines()[0])
        self.assertIn("max20", text)
        rc, text, errs = run_cli("accounts", "tier", ACCT_A, "clear")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(self._tier(), (None, None))

    def test_bad_value(self):
        rc, _text, _errs = run_cli("accounts", "tier", ACCT_A, "max")
        self.assertEqual(rc, 2)
        self.assertEqual(self._tier(), ("max5", "credentials"))


if __name__ == "__main__":
    unittest.main()
