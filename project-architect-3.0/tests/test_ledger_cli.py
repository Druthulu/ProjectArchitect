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

    def test_since_goes_by_session_start_not_restamp(self):
        """3.28 T3: a recalc restamps sv.updated; an older session stays out of --since."""
        import argparse
        conn = db.connect(paths.db_path())
        for sid in ("s_pre", "s_t"):
            db.upsert_savings(conn, {"run_id": "sv_" + sid, "session_id": sid,
                                     "measured_saved_usd": 1.0, "modeled_saved_usd": 2.0,
                                     "updated": "2026-09-05T00:00:00Z"})
        conn.commit()
        args = argparse.Namespace(session=None, phase=None, account=None)
        got = {r["session_id"] for r in ledger_cli._saved_rows(conn, args, "2026-09-01")}
        db.close(conn)
        self.assertEqual(got, {"s_t"})


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


class DoctorModTest(unittest.TestCase):
    """3.24 T4: doctor's root manifest, registration, matcher, floor, release and 3.2 project rows."""

    def setUp(self):
        from unittest import mock
        self.tmp = tempfile.mkdtemp(prefix="pa3-dmod-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pa3 = os.path.join(self.tmp, "pa3")
        self.clone = os.path.join(self.tmp, "clone")
        os.makedirs(self.pa3)
        for name, value in (("install_dir", lambda: self.pa3),
                            ("package_clone_dir", lambda config_dir=None: self.clone)):
            patcher = mock.patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _rows(self, fn, *args):
        doc = ledger_cli._Doctor()
        fn(doc, *args)
        return doc.lines

    def _write(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    def test_root_manifest(self):
        from pa.install import root
        self.assertTrue(self._rows(ledger_cli._doctor_root_manifest)[0].startswith("WARN"))
        src = self._write("pa3/agents/a.md", "a\n")
        ver = "version: 3.24\n"
        self._write("pa3/MANIFEST.json", root.manifest_text([("agents/a.md", src)], ver))
        self._write("pa3/VERSION", ver)
        rows = self._rows(ledger_cli._doctor_root_manifest)
        self.assertTrue(rows[0].startswith("OK"), rows)
        self._write("pa3/agents/a.md", "changed\n")
        rows = self._rows(ledger_cli._doctor_root_manifest)
        self.assertTrue(rows[0].startswith("FAIL") and "1 of 1 differ" in rows[0], rows)
        self._write("pa3/VERSION", "version: 3.23\n")              # a half copy: VERSION not the manifest's
        rows = self._rows(ledger_cli._doctor_root_manifest)
        self.assertIn("half copy", rows[0])

    def test_registration_and_matcher(self):
        hook = {"type": "command", "command": "py C:/pa3/pa_hook.py pre_tool_use"}
        doc = {"extraKnownMarketplaces": {"pa3": {"source": {"source": "directory",
                                                              "path": os.path.join(self.pa3, "mod")}}},
               "enabledPlugins": {"pa@pa3": True, "pa-watch@pa3": True},
               "hooks": {"PreToolUse": [{"matcher": "Read|Edit|Bash", "hooks": [hook]}]}}
        path = self._write("cfg/settings.json", json.dumps(doc))
        rows = self._rows(ledger_cli._doctor_registration, "user", path)
        self.assertEqual([r.split()[0] for r in rows], ["OK", "OK"], rows)
        doc["extraKnownMarketplaces"]["pa3"]["source"]["path"] = "C:/elsewhere/mod"
        doc["hooks"]["PreToolUse"][0]["matcher"] = "Edit|Bash"
        self._write("cfg/settings.json", json.dumps(doc))
        rows = self._rows(ledger_cli._doctor_registration, "user", path)
        self.assertEqual([r.split()[0] for r in rows], ["FAIL", "FAIL"], rows)
        self.assertIn("not %s" % ledger_cli._fwd_norm(os.path.join(self.pa3, "mod")), rows[0])
        self.assertIn("Read is not in the PreToolUse matcher", rows[1])

    def test_floor(self):
        from unittest import mock
        from pa.install import root
        floor = root.mod_floor(root.package_root())
        with mock.patch.object(root, "claude_version", lambda: "2.0.0"):
            rows = self._rows(ledger_cli._doctor_floor)
        self.assertTrue(rows[0].startswith("FAIL") and "below the mod's floor %s" % floor in rows[0], rows)
        with mock.patch.object(root, "claude_version", lambda: floor):
            self.assertTrue(self._rows(ledger_cli._doctor_floor)[0].startswith("OK"))

    @unittest.skipUnless(shutil.which("git"), "git not on PATH")
    def test_released_tree_warns_when_it_differs(self):
        import subprocess

        def git(*args):
            return subprocess.run(["git"] + list(args), capture_output=True, text=True, check=True).stdout.strip()

        origin = os.path.join(self.tmp, "origin.git")
        git("init", "--bare", "-q", origin)
        git("init", "-q", self.clone)
        git("-C", self.clone, "config", "user.email", "t@t")
        git("-C", self.clone, "config", "user.name", "T")
        self._write("clone/project-architect-3.0/VERSION", "3.24\n")
        git("-C", self.clone, "add", "-A")
        git("-C", self.clone, "commit", "-q", "-m", "release")
        git("-C", self.clone, "remote", "add", "origin", origin)
        git("-C", self.clone, "push", "-q", "-u", "origin", "HEAD")
        tree = git("-C", self.clone, "rev-parse", "HEAD:project-architect-3.0")
        self._write("pa3/VERSION", "version: 3.24\ntree: %s\n" % tree)
        self.assertTrue(self._rows(ledger_cli._doctor_released)[0].startswith("OK"))
        self._write("pa3/VERSION", "version: 3.24\ntree: %s\n" % ("0" * 40))
        rows = self._rows(ledger_cli._doctor_released)
        self.assertTrue(rows[0].startswith("WARN") and "release candidate" in rows[0], rows)

    def test_project_32(self):
        from pa.install import ladder
        proj = os.path.join(self.tmp, "proj")
        self._write("proj/.claude/pa.json", json.dumps({"mod": "on", "preset": "max5"}))
        src = os.path.join(ROOT, "agents")
        for name in sorted(os.listdir(src)):
            agent = name[:-3]
            if name.endswith(".md") and (agent not in ladder.MANAGED or ladder.row(agent, "max5") is not None):
                self._write("proj/.claude/agents/" + name, "x\n")
        self._write("proj/.claude/settings.json", json.dumps({"model": "claude-opus-5-5[1m]"}))
        self._write("cfg/.credentials.json", json.dumps(          # 3.24 T17: a fixed live tier
            {"claudeAiOauth": {"subscriptionType": "max", "rateLimitTier": "default_claude_max_20x"}}))
        from unittest import mock
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cfg")})
        env.start()
        self.addCleanup(env.stop)
        rows = self._rows(ledger_cli._doctor_project_mod, proj)
        self.assertEqual([r.split()[0] for r in rows], ["OK"] * 6, rows)
        self.assertIn("pa-planner runs claude-opus-5-5[1m] at max (tier max20, credentials)", rows[4])
        self._write("proj/.claude-state/journal/2026-10-11/120000-001w-mod-start.md",
                    "---\nagentType: pa-planner\nmodel: claude-opus-5-5\neffort: max\n---\n\nspawn\n")
        self.assertTrue(self._rows(ledger_cli._doctor_project_mod, proj)[5].startswith("OK"))
        self._write("proj/.claude-state/journal/2026-10-11.md", "## 110000-001w-mod-start\n---\n"
                    "agentType: auditor\nmodel: claude-opus-5-5[1m]\neffort: high\n---\n\nspawn\n")
        row = self._rows(ledger_cli._doctor_project_mod, proj)[5]
        self.assertTrue(row.startswith("WARN") and "auditor got claude-opus-5-5[1m]/high" in row, row)
        self.assertNotIn("pa-planner got", row)
        self._write("proj/.claude/pa.json", json.dumps({"preset": "max5"}))
        self._write("proj/.claude/agents/critic.md", "x\n")
        self._write("proj/.claude/commands/proceed.md", "x\n")
        os.remove(os.path.join(proj, ".claude", "agents", "pa-claude.md"))
        self._write("proj/.claude/settings.json", json.dumps(
            {"agent": "pa-session", "modelSettings": {"claude-sonnet-5-5": {"effortLevel": "medium"}}}))
        self._write("proj/.claude/settings.local.json", json.dumps({"model": "claude-sonnet-5-5[1m]"}))
        rows = self._rows(ledger_cli._doctor_project_mod, proj)
        self.assertEqual([r.split()[0] for r in rows], ["FAIL", "FAIL", "FAIL", "FAIL", "WARN", "OK", "WARN"],
                         rows)
        self.assertIn("pa-claude", rows[1])
        self.assertIn(".claude/agents/critic.md, .claude/commands/proceed.md", rows[2])
        self.assertIn("agent, modelSettings.claude-sonnet-5-5", rows[3])
        self.assertEqual(self._rows(ledger_cli._doctor_project_mod, os.path.join(self.tmp, "none")), [])


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

    def test_user_rows_follow_claude_config_dir(self):
        """3.24 T12e: with $CLAUDE_CONFIG_DIR set, the user settings rows read it, not ~/.claude."""
        from unittest import mock
        cfg = os.path.join(self.dir, "cfg-vantage")
        os.makedirs(cfg)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            _rc, text, _e = run_cli("doctor", "--skip-hooks", "--project", self.project)
        self.assertIn("user: no settings.json at %s" % os.path.join(cfg, "settings.json"), text)
        self.assertNotIn(os.path.join(paths.claude_dir(), "settings.json"), text)

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
        self.assertEqual(recalcs, [[self.SID]])    # 3.24 T10: --session repeats (a list)
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


class ModCommandsTest(unittest.TestCase):
    """3.22: ``prices --json``, ``event`` and ``toast``: one JSON line, rc 0; bad input rc 2."""

    def setUp(self):
        from pa import notify

        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-mod-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        paths.ensure_ledger_tree()
        self.spawned = []
        notify.set_spawner(lambda cmd, env: self.spawned.append(cmd))

    def tearDown(self):
        from pa import notify

        notify.set_spawner(None)
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_prices_json(self):
        from pa import prices

        rc, text, errs = run_cli("prices", "--json")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(len(text.strip().splitlines()), 1)
        doc = json.loads(text)
        self.assertEqual(doc["prices"]["opus-5-5"],
                         {"read": 0.2, "write_5m": 5.0, "write_1h": 8.0, "input": 4.0, "output": 20.0})
        rc, text, errs = run_cli("prices", "--json", "--model", "claude-opus-5-5[1m]")
        self.assertEqual(rc, 0, errs)
        self.assertEqual(json.loads(text)["price"]["write_1h"], 8.0)
        self.assertEqual(run_cli("prices", "--json", "--model", "gpt-9")[0], 2)
        tier = {"over": 100000, "input": 0.5, "write_5m": 0.625, "write_1h": 1.0, "read": 0.05,
                "output": 2.5}
        self.assertEqual(doc["prices"]["haiku-5-5"]["prompt_tier"], tier)
        self.assertEqual(doc["prices"]["sonnet-5-5"]["read"], 0.1)
        rc, text, errs = run_cli("prices", "--json", "--model", "claude-haiku-5-5")
        self.assertEqual(json.loads(text)["price"]["prompt_tier"], tier)

    def test_event_spools_a_warm_ping(self):
        detail = {"run_id": "a1", "session_id": "s1", "ttl": "1h", "idle_s": 3300, "n": 1,
                  "via": "mod", "cap": None, "cap_inputs": {}}
        rc, text, errs = run_cli("event", "warm_ping", "--session", "s1", "--run", "a1",
                                 "--detail", json.dumps(detail))
        self.assertEqual(rc, 0, errs)
        row = json.loads(text)
        self.assertEqual((row["kind"], row["run_id"], row["detail"]), ("warm_ping", "a1", detail))
        with open(paths.spool_path("s1"), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline())["detail"]["via"], "mod")
        self.assertEqual(run_cli("event", "toast", "--session", "s1")[0], 2)
        self.assertEqual(run_cli("event", "warm_ping", "--session", "s1", "--detail", "[1]")[0], 2)

    def test_toast_waiting(self):
        proj = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(proj, ".claude"))
        with open(os.path.join(proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"mod": "on", "toast": "waiting"}, fh)
        rc, text, errs = run_cli("toast", "--session", "s1", "--cause", "question", "--title", "PA3",
                                 "--body", "Which one?", "--project", proj)
        self.assertEqual(rc, 0, errs)
        self.assertTrue(json.loads(text)["sent"])
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(run_cli("toast", "--session", "s1", "--cause", "stop", "--title", "t",
                                 "--body", "b")[0], 2)


class ExternalCashRowTest(unittest.TestCase):
    """3.3 T8: an external coder's ext:<config> turns show as their own row marked cash."""

    EXT = "ext:openrouter-deepseek"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-ext-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.dir
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        for sid, acct, cost in (("s_c", ACCT_A, 3.0), ("s_x", self.EXT, 0.5)):
            db.upsert_session(conn, {"session_id": sid, "account": acct, "cwd": CWD,
                                      "started": "2026-09-01T09:00:00Z"})
            db.upsert_turn(conn, {"msg_id": sid + "_m0", "run_id": sid, "session_id": sid,
                                   "account": acct, "ts": "2026-09-01T10:00:00Z",
                                   "model": "claude-fable-5-1", "cost_usd": cost, "kind": "api"})
        db.close(conn)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_report_marks_the_ext_row_cash(self):
        rc, text, errs = run_cli("report", "--since", "2026-09-01", "--json")
        self.assertEqual(rc, 0, errs)
        accounts = {a["account"]: a for a in json.loads(text)["accounts"]}
        self.assertFalse(accounts[ACCT_A]["cash"])
        self.assertAlmostEqual(accounts[ACCT_A]["cost_used"], 3.0)
        self.assertTrue(accounts[self.EXT]["cash"])
        rc, text, errs = run_cli("report", "--since", "2026-09-01")
        self.assertIn(self.EXT + " (cash)", text)

    def test_minimal_summary_leaves_ext_out(self):
        conn = db.connect(paths.db_path())
        try:
            doc = ledger_cli.minimal_summary(conn, {})
        finally:
            db.close(conn)
        self.assertIn(ACCT_A, doc["accounts"])
        self.assertNotIn(self.EXT, doc["accounts"])


class _TempLedger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ledger-cli-hb-")
        self._old_env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        paths.ensure_ledger_tree()
        self.conn = db.connect(paths.db_path())
        db.init_schema(self.conn)

    def tearDown(self):
        db.close(self.conn)
        if self._old_env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)


def _request(msg_id, ts, ctx, content):
    return json.dumps({"type": "assistant", "uuid": "u-" + msg_id, "requestId": "req_" + msg_id,
                       "timestamp": ts,
                       "message": {"id": msg_id, "role": "assistant", "model": "claude-opus-5-5",
                                   "stop_reason": "tool_use", "content": content,
                                   "usage": {"input_tokens": ctx, "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": 0, "output_tokens": 300}}})


class HandbackRebuildTest(_TempLedger):
    """3.30.8: a recalc books a helper that ended at its hand-back as completed with its report as
    the answer (it took the run's closing ``tool_use`` stop for an interruption); a run cut off on
    another tool call stays interrupted and void."""

    SID = "s_hb"

    def setUp(self):
        super().setUp()
        db.upsert_session(self.conn, {"session_id": self.SID, "account": ACCT_A, "cwd": CWD,
                                      "started": "2026-10-29T01:00:00Z"})

    def _lock(self, agent_id, tool, message):
        path = os.path.join(self.dir, "projects", "slug", self.SID, "subagents",
                            "agent-%s.jsonl" % agent_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_request("m1" + agent_id, "2026-10-29T01:00:00.000Z", 20000,
                              [{"type": "text", "text": "Starting."}]) + "\n")
            fh.write(_request("m2" + agent_id, "2026-10-29T01:05:00.000Z", 60000,
                              [{"type": "tool_use", "id": "t-" + agent_id, "name": tool,
                                "input": {"message": message}}]) + "\n")
        with open(path[:-6] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"agentType": "pa-coder", "parentAgentId": "a1", "spawnDepth": 2}, fh)
        return ledger_cli.lock_agent_transcript(self.conn, path, run_id=agent_id, session_id=self.SID,
                                                account=ACCT_A)

    def _retrieval(self, run_id):
        return tuple(self.conn.execute("SELECT status, void, answer_tokens FROM retrievals"
                                       " WHERE run_id=?", (run_id,)).fetchone())

    def test_a_handback_run_is_completed_with_its_report_as_the_answer(self):
        self.assertEqual(self._lock("ahb", "SubagentHandback", "done. " * 200)["status"], "completed")
        self.assertEqual(self._retrieval("ahb"), ("completed", 0, 300))

    def test_a_run_cut_off_on_another_tool_stays_interrupted(self):
        self.assertEqual(self._lock("acut", "Bash", "ls")["status"], "interrupted")
        self.assertEqual(self._retrieval("acut"), ("interrupted", 1, 0))


class HelperAnswersDoctorTest(_TempLedger):
    """3.30.8: the doctor WARNs when most of the newest helpers came back void with no answer read
    (CC 2.1.285's hand-back voided every helper for 26 days with nothing to say so)."""

    def _helpers(self, n, unread):
        for i in range(n):
            db.upsert_retrieval(self.conn, {
                "run_id": "h%02d" % i, "kept_out_mode": "all_growth",
                "returned_at": "2026-10-29T01:%02d:00Z" % i,
                "void": 1 if i < unread else 0, "answer_tokens": 0 if i < unread else 120})

    def test_warn_when_most_of_the_newest_helpers_are_unread(self):
        self._helpers(20, 15)
        level, text = ledger_cli._helper_answers_row(self.conn)
        self.assertEqual(level, "WARN")
        self.assertIn("15 of the last 20 helpers", text)
        self.assertIn("Claude Code changelog", text)

    def test_ok_when_answers_are_read_or_helpers_are_few(self):
        self._helpers(20, 2)
        self.assertEqual(ledger_cli._helper_answers_row(self.conn),
                         ("OK", "helper answers: 2 of the last 20 helpers voided"))
        self.conn.execute("DELETE FROM retrievals")
        self._helpers(6, 6)
        self.assertEqual(ledger_cli._helper_answers_row(self.conn)[0], "OK")


if __name__ == "__main__":
    unittest.main()
