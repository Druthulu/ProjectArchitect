"""test_bench.py -- bench.py plan/run (3.9.6 T6) against a fake `claude` on PATH.

No network (window_pct patched), no live claude; PA_LEDGER_DIR, CLAUDE_CONFIG_DIR, the work dir and --out all
live under <repo>/.run/bench-test (never system temp).
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
TOOLS = PKG / "tools"
BENCH = PKG / "tests" / "fixtures" / "bench"
SCRATCH = PKG.parent / ".run" / "bench-test"   # never system temp
WORKFLOW = PKG.parent / ".github" / "workflows" / "bench.yml"   # dev repo only (3.11 T1)
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(PKG))
import bench  # noqa: E402
from pa import config  # noqa: E402

FAKE = r'''
import json, os, subprocess, sys, time, uuid
from pathlib import Path
if "--version" in sys.argv:
    print("2.1.282 (Claude Code)")
    sys.exit(0)
prompt = sys.stdin.read()
clone = Path.cwd()
arm, tid = clone.parent.name, clone.name
attempt = None
if tid[:1] == "a" and tid[1:].isdigit():   # 3.9.7 T5: repeat clone <arm>/<id>/a<i>
    arm, tid, attempt = clone.parent.parent.name, clone.parent.name, int(tid[1:])
log = os.environ["FAKE_LOG"]
hidden_seen = (clone / "tests" / f"test_hidden_{tid}.py").exists()
t0 = time.time()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0.1")))
fixtures = Path(os.environ["FAKE_FIXTURES"])
answer = "fake answer\nline 2"
if arm == "coder" and os.environ.get("FAKE_PASS"):
    patch = fixtures / "hidden" / tid / "solution.patch"
    subprocess.run(["git", "apply", str(patch)], cwd=clone, capture_output=True)
if arm == "expert" and tid.endswith(".c") and os.environ.get("FAKE_PASS"):   # 3.9.7 T3: the consequence coder
    based_on = json.loads((fixtures / "tasks" / "expert" / f"{tid.split('.')[0]}.json").read_text(encoding="utf-8"))
    patch = fixtures / "hidden" / based_on["expect"]["based_on"] / "solution.patch"
    subprocess.run(["git", "apply", str(patch)], cwd=clone, capture_output=True)
if arm == "expert" and not tid.endswith(".c") and os.environ.get("FAKE_BRIEF"):
    answer = (fixtures / "tasks" / "expert" / f"{tid}.ref.md").read_text(encoding="utf-8")
if arm == "_rubric":   # 3.9.7 T4: the rubric grader call, cwd <work>/_rubric/<arm>-<id>/
    answer = os.environ.get("FAKE_RUBRIC", "S1 1 S2 1 P1 1 | total 11") + "\nstrongest\nworst"
if arm == "planner":
    (clone / ".run").mkdir(exist_ok=True)
    (clone / ".run" / "PHASE_PLAN.draft.md").write_text("# fake draft\n", encoding="utf-8")
sid = str(uuid.uuid4())
proj = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "fake"
proj.mkdir(parents=True, exist_ok=True)
with open(proj / f"{sid}.jsonl", "w", encoding="utf-8") as fh:
    for mid in ("m1", "m1", "m2"):
        fh.write(json.dumps({"type": "assistant", "message": {"id": mid, "content": []}}) + "\n")
# one file per call: concurrent "a"-mode appends from parallel fakes lose rows on Windows (T8.c3)
Path(log).mkdir(parents=True, exist_ok=True)
with open(Path(log) / f"{sid}.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"arm": arm, "id": tid, "attempt": attempt, "start": t0, "end": time.time(), "hidden": hidden_seen,
                         "prompt": bool(prompt), "stdin": prompt, "argv": sys.argv[1:],
                         "api_key": "ANTHROPIC_API_KEY" in os.environ,
                         "hooks_off": os.environ.get("PA_HOOKS_OFF")}) + "\n")
denials = [{"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {}}] if os.environ.get("FAKE_DENY") == tid else []
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "session_id": sid, "num_turns": 5,
                  "total_cost_usd": 0.07 if tid.endswith(".c") else 0.02 if arm == "_rubric" else 0.05,
                  "permission_denials": denials,
                  "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30,
                            "cache_creation_input_tokens": 40},
                  "modelUsage": {"claude-opus-5-5": {"costUSD": 0.05}}}))
'''


class BenchRun(unittest.TestCase):
    def setUp(self):
        if SCRATCH.exists():
            shutil.rmtree(SCRATCH, onexc=bench._force_remove)   # git objects are read-only on Windows
        SCRATCH.mkdir(parents=True)
        fake_dir = SCRATCH / "bin"
        fake_dir.mkdir()
        fake_py = fake_dir / "fake_claude.py"
        fake_py.write_text(FAKE, encoding="utf-8")
        if os.name == "nt":
            (fake_dir / "claude.cmd").write_text(f'@"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
        else:
            sh = fake_dir / "claude"
            sh.write_text(f"#!{sys.executable}\n" + FAKE, encoding="utf-8")
            sh.chmod(0o755)
        self.log = SCRATCH / "fake.log"   # a directory: one <session>.json per fake call
        self.out = SCRATCH / "results" / "run.json"
        env = {"PATH": str(fake_dir) + os.pathsep + os.environ.get("PATH", ""),
               "PA_LEDGER_DIR": str(SCRATCH / "ledger"), "CLAUDE_CONFIG_DIR": str(SCRATCH / "cfg"),
               "FAKE_LOG": str(self.log), "FAKE_FIXTURES": str(BENCH), "FAKE_SLEEP": "0.1",
               "ANTHROPIC_API_KEY": "must-not-leak"}
        self._patch(mock.patch.dict(os.environ, env))
        self.cwd = os.getcwd()
        os.chdir(SCRATCH)
        self.addCleanup(os.chdir, self.cwd)

    def _patch(self, p):
        p.start()
        self.addCleanup(p.stop)
        return p

    def pct(self, *values):
        """window_pct returns values in order, then the last forever."""
        seq = list(values)
        self._patch(mock.patch.object(bench, "window_pct", lambda: seq.pop(0) if len(seq) > 1 else seq[0]))

    def run_bench(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["run", *args, "--out", str(self.out), "--work-dir", str(SCRATCH / "work")])
        return rc, buf.getvalue()

    def calls(self, rubric=False):
        """The fake's calls; the rubric grader calls (arm _rubric) only with rubric=True."""
        if not self.log.exists():
            return []
        calls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.log.glob("*.json"))]
        return [c for c in calls if (c["arm"] == "_rubric") == rubric]

    def doc(self):
        return json.loads(self.out.read_text(encoding="utf-8"))

    def test_under_cap_runs_all_arms_and_writes_ledger(self):
        self.pct(10.0)
        os.environ["FAKE_PASS"] = "1"
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 0, text)
        doc = self.doc()
        self.assertEqual((doc["format"], doc["repeat"]), (2, 1))   # 3.9.7 T5: format 2 always
        self.assertTrue(all(t["attempt"] == 1 for a in doc["arms"] for t in a["tasks"]))
        self.assertFalse(list((SCRATCH / "work").glob("*/*/*/a1")))   # repeat 1: today's paths
        self.assertEqual([a["role"] for a in doc["arms"]], ["coder", "expert", "planner"])
        self.assertTrue(all(a["stopped"] == 0 for a in doc["arms"]))
        self.assertTrue(all(t["passed"] is not None for a in doc["arms"] for t in a["tasks"]))
        coder = doc["arms"][0]
        self.assertTrue(all(t["passed"] for t in coder["tasks"]), [t["detail"] for t in coder["tasks"]])
        t = coder["tasks"][0]
        self.assertEqual((t["turns"], t["turns_source"], t["cache_write"]), (2, "transcript", 40))
        self.assertEqual((coder["model"], coder["effort"], coder["w5h_before"]), ("claude-opus-5-5", "medium", 10.0))
        self.assertEqual(doc["claude_code"], "2.1.282")
        self.assertEqual(doc["harness_version"], "3.14.1")        # I11: the package VERSION
        self.assertIn("coder coder-opus55 claude-opus-5-5/medium pass 4/4", text)
        self.assertTrue(list((SCRATCH / "work").glob("*/planner/p01.rubric.txt")))
        p01 = doc["arms"][2]["tasks"][0]   # 3.9.7 T4: rubric side score, grader cost added
        self.assertEqual(p01["rubric"], {"score": 11.0, "max": 3 + 8, "model": "claude-opus-5-5"})
        self.assertAlmostEqual(p01["cost_usd"], 0.07)
        self.assertIn("rubric 11/11 $0.02", p01["detail"])
        self.assertEqual(len(self.calls(rubric=True)), 1)
        readme = (SCRATCH / "README.md").read_text(encoding="utf-8")
        self.assertIn("| canary | max20 | coder | coder-opus55 |", readme)   # 3.10.6 T6: preset column
        self.assertEqual(doc["preset"], "max20")
        calls = self.calls()
        self.assertEqual(len(calls), 7)
        self.assertFalse(any(c["hidden"] or c["api_key"] or not c["prompt"] for c in calls))
        self.assertTrue(all(c["hooks_off"] == "1" for c in calls))
        self.assertTrue(all(c["argv"][c["argv"].index("--settings") + 1].endswith("allow.json") for c in calls))
        con = sqlite3.connect(SCRATCH / "ledger" / "ledger.sqlite")
        cols = ("role", "agent", "model", "effort", "body_hash", "fixture_hash", "harness_version",
                "price_version", "suite", "tier", "source")
        rows = con.execute(f"SELECT {', '.join(cols)} FROM bench").fetchall()
        con.close()
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertTrue(all(v is not None for v in row), dict(zip(cols, row)))

    def test_preset_pro_renders_and_skips(self):
        """3.10.6 T6: --preset pro runs the ladder-rendered critic copy, records the preset; expert-fable is skipped."""
        self.pct(10.0)
        rc, text = self.run_bench("critic", "--suite", "full", "--preset", "pro", "--no-rubric")
        self.assertEqual(rc, 0, text)
        doc = self.doc()
        self.assertEqual(doc["preset"], "pro")
        arm = doc["arms"][0]
        self.assertEqual((arm["role"], arm["effort"]), ("critic", "medium"))
        self.assertFalse(any("!= file" in t["detail"] for t in arm["tasks"]), arm["tasks"][0]["detail"])
        copy = next((SCRATCH / "work").glob("*/_agents/critic.md")).read_text(encoding="utf-8")
        self.assertIn("model: claude-opus-5-5[1m]\n", copy)
        self.assertIn("effort: medium\n", copy)
        clone = next((SCRATCH / "work").glob("*/critic/k01/.claude/agents/critic.md")).read_text(encoding="utf-8")
        self.assertEqual(clone, copy)
        self.assertIn("claude-fable-5-1", (bench.HERE.parent / "agents" / "critic.md").read_text(encoding="utf-8"))
        self.assertIn("| full | pro | critic | critic |", (SCRATCH / "README.md").read_text(encoding="utf-8"))
        self.out.unlink()
        rc, text = self.run_bench("expert-fable", "--suite", "full", "--preset", "pro")
        self.assertEqual((rc, text.strip()), (0, "skipped expert-fable: preset pro has no hard rung"))
        self.assertFalse(self.out.exists())

    def test_answers_retained_and_harness_rule(self):
        """T1 (3.9.7): four files beside each clone; denials counted; failed + denial -> harness, else null."""
        self.pct(10.0)
        os.environ["FAKE_DENY"] = "e01"
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 0, text)
        doc = self.doc()
        work = Path(doc["work_dir"])
        self.assertEqual(work.parent, (SCRATCH / "work").resolve())
        for arm in doc["arms"]:
            for t in arm["tasks"]:
                for ext in (".out.json", ".transcript.jsonl", ".grade.txt", ".diff"):
                    self.assertTrue((work / arm["role"] / f"{t['id']}{ext}").is_file(), (t["id"], ext))
        out = json.loads((work / "expert" / "e01.out.json").read_text(encoding="utf-8"))
        self.assertEqual(out["result"], "fake answer\nline 2")
        self.assertIn(".claude/agents/", (work / "expert" / "e01.diff").read_text(encoding="utf-8"))
        tasks = {t["id"]: t for a in doc["arms"] for t in a["tasks"]}
        e01, e07 = tasks["e01"], tasks["e07"]
        self.assertEqual((e01["passed"], e01["denials"], e01["error"], e01["failure_kind"]),
                         (False, 1, None, "harness"))
        self.assertIn("permission denial", e01["audit_note"])
        self.assertEqual((e07["passed"], e07["denials"], e07["failure_kind"], e07["audit_note"]),
                         (False, 0, None, None))
        # 3.9.7 T3: shape failure -> consequence null, no second claude call; other arms null too
        self.assertTrue(all(t["consequence"] is None for t in tasks.values()))
        self.assertFalse([c for c in self.calls() if c["id"].endswith(".c")])

    def expert_run(self):
        self.pct(10.0)
        os.environ["FAKE_BRIEF"] = "1"
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 0, text)
        doc = self.doc()
        return doc, text, next(a for a in doc["arms"] if a["role"] == "expert")

    def test_consequence_launches_bench_coder(self):
        """T3 (3.9.7): a shape pass launches bench-coder in <work>/expert/<id>.c/, graded by hidden/<based_on>."""
        os.environ["FAKE_PASS"] = "1"
        doc, text, expert = self.expert_run()
        work = Path(doc["work_dir"])
        calls = {c["id"]: c for c in self.calls() if c["arm"] == "expert"}
        self.assertTrue(expert["tasks"])
        for t in expert["tasks"]:
            con = t["consequence"]
            self.assertTrue(t["passed"], t["detail"])
            self.assertEqual((con["passed"], con["cost_usd"], con["turns"]), (True, 0.07, 2), t["id"])
            self.assertTrue(con["session_id"] and con["secs"] is not None)
            self.assertEqual(t["cost_usd"], 0.05)
            call = calls[f"{t['id']}.c"]
            self.assertEqual(call["argv"][:5], ["-p", "--agent", "bench-coder", "--effort", "medium"])
            ref = (BENCH / "tasks" / "expert" / f"{t['id']}.ref.md").read_text(encoding="utf-8")
            self.assertEqual(call["stdin"], "Implement this brief in the current repository.\n\n" + ref)
            based_on = json.loads((BENCH / "tasks" / "expert" / f"{t['id']}.json").read_text(encoding="utf-8"))
            clone = work / "expert" / f"{t['id']}.c"
            self.assertTrue((clone / ".claude" / "agents" / "bench-coder.md").is_file())
            self.assertTrue((clone / "tests" / f"test_hidden_{based_on['expect']['based_on']}.py").is_file())
            self.assertTrue((work / "expert" / f"{t['id']}.c.out.json").is_file())
        cost = sum(t["cost_usd"] + t["consequence"]["cost_usd"] for t in expert["tasks"])
        self.assertIn(f"expert {expert['agent']} {expert['model']}/{expert['effort']} ", text)
        line = next(ln for ln in text.splitlines() if ln.startswith("expert "))
        self.assertTrue(line.endswith(f" ${cost:.2f}"), line)

    def test_no_rubric_skips_it(self):
        """T4 (3.9.7): --no-rubric: no grader call, rubric null, cost the agent's alone."""
        self.pct(10.0)
        rc, text = self.run_bench("canary", "--no-rubric")
        self.assertEqual(rc, 0, text)
        p01 = self.doc()["arms"][2]["tasks"][0]
        self.assertEqual((p01["rubric"], p01["cost_usd"]), (None, 0.05))
        self.assertEqual(self.calls(rubric=True), [])
        self.assertFalse(list((SCRATCH / "work").glob("*/planner/p01.rubric.txt")))

    def rubric_task(self, arm, tid):
        """run_task on one task of arm with the fake claude; the record."""
        work = SCRATCH / "work" / "direct"
        (work / arm).mkdir(parents=True, exist_ok=True)
        ctx = {"fixtures": BENCH, "work": work, "claude": shutil.which("claude"), "model": None, "rubric": True}
        meta = {"path": PKG / "agents" / f"{arm}.md", "agent": arm, "effort": "medium",
                "file_model": "claude-opus-5-5"}
        task = json.loads((BENCH / "tasks" / arm / f"{tid}.json").read_text(encoding="utf-8"))
        return bench.run_task(ctx, arm, task, meta), work

    def test_rubric_fills_critic_and_review(self):
        """T4 (3.9.7): critic and review records carry rubric {score, max, model}; grader cost added; the grader
        call runs in an empty <work>/_rubric/<arm>-<id>/ with tools off, no --agent, no --bare."""
        for arm, tid, mx in (("critic", "k01", 7), ("review", "v01", 6)):
            rec, work = self.rubric_task(arm, tid)
            self.assertEqual(rec["rubric"], {"score": 11.0, "max": mx, "model": "claude-opus-5-5"}, arm)
            self.assertAlmostEqual(rec["cost_usd"], 0.07)
            self.assertIn(f"rubric 11/{mx} $0.02", rec["detail"])
            prompt = (work / arm / f"{tid}.rubric.txt").read_text(encoding="utf-8")
            self.assertEqual(list((work / "_rubric" / f"{arm}-{tid}").iterdir()), [])
            call = next(c for c in self.calls(rubric=True) if c["id"] == f"{arm}-{tid}")
            self.assertEqual(call["stdin"], prompt)
            argv = call["argv"]
            self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
            self.assertIn("Read,Edit,Write,Bash,Grep,Glob,Agent,WebFetch,WebSearch", argv)
            self.assertFalse({"--agent", "--bare", "--settings"} & set(argv))

    def test_rubric_failure_leaves_null(self):
        os.environ["FAKE_RUBRIC"] = "no score here"
        rec, _ = self.rubric_task("critic", "k01")
        self.assertIsNone(rec["rubric"])
        self.assertIn("rubric failed $0.02: no `total <n>` on line 1", rec["detail"])

    def test_rubric_score_parses_total(self):
        cwd = SCRATCH / "work" / "_rubric" / "x"
        cwd.mkdir(parents=True)
        grader = {"model": "claude-opus-5-5", "effort": "medium", "max": 12}
        for line, want in (("S1 1 C2 1 total 11", 11.0), ("S1 1 | total 11", 11.0), ("no score", None)):
            os.environ["FAKE_RUBRIC"] = line
            score, mx, d = bench._bench_grade.rubric_score("p", grader, shutil.which("claude"), cwd)
            self.assertEqual((score, mx, d["line"], d["cost_usd"]), (want, 12, line, 0.02), line)

    def test_scored_task_agent_kind_and_mu(self):
        """T24: a coder task with score_cmd is graded by grade_scored; score 0.5 < 1.0 -> failed, kind agent at run
        time; the arm summary carries the mean score."""
        root = SCRATCH / "fx-max"
        (root / "repo").mkdir(parents=True)
        (root / "repo" / "a.txt").write_text("x\n", encoding="utf-8")
        (root / "settings").mkdir()
        shutil.copyfile(BENCH / "settings" / "allow.json", root / "settings" / "allow.json")
        (root / "score.py").write_text("print('score: 0.5')\n", encoding="utf-8")
        task = {"id": "m01", "tier": 1, "suites": ["max"], "prompt": "p", "timeout": 60,
                "expect": {"score_cmd": [sys.executable, "{fixtures}/score.py", "{clone}"], "pass_score": 1.0}}
        work = SCRATCH / "work" / "direct"
        (work / "coder").mkdir(parents=True)
        ctx = {"fixtures": root, "work": work, "claude": shutil.which("claude"), "model": None, "rubric": True}
        meta = {"path": PKG / "agents" / "coder-opus55.md", "agent": "coder-opus55", "effort": "medium",
                "file_model": "claude-opus-5-5"}
        rec = bench.run_task(ctx, "coder", task, meta)
        self.assertEqual((rec["passed"], rec["score"], rec["failure_kind"], rec["audit_note"]),
                         (False, 0.5, "agent", "score 0.50 < 1.0"))
        self.assertIsNone(bench._unrun(task)["score"])
        self.assertEqual(bench._arm_summary({"tasks": [rec]})[0], "pass 0/1 (t1 0/1) · score μ0.50")

    def test_scored_failure_keeps_agent_kind_despite_denials(self):
        """3.11 T26: a denial no longer turns a measured scored failure into harness; with no score it does."""
        root = SCRATCH / "fx-max"
        (root / "repo").mkdir(parents=True)
        (root / "repo" / "a.txt").write_text("x\n", encoding="utf-8")
        (root / "settings").mkdir()
        shutil.copyfile(BENCH / "settings" / "allow.json", root / "settings" / "allow.json")
        (root / "score.py").write_text("print('score: 0.5')\n", encoding="utf-8")
        task = {"id": "m01", "tier": 1, "suites": ["max"], "prompt": "p", "timeout": 60,
                "expect": {"score_cmd": [sys.executable, "{fixtures}/score.py", "{clone}"], "pass_score": 1.0}}
        work = SCRATCH / "work" / "direct"
        (work / "coder").mkdir(parents=True)
        ctx = {"fixtures": root, "work": work, "claude": shutil.which("claude"), "model": None, "rubric": True}
        meta = {"path": PKG / "agents" / "coder-opus55.md", "agent": "coder-opus55", "effort": "medium",
                "file_model": "claude-opus-5-5"}
        os.environ["FAKE_DENY"] = "m01"
        rec = bench.run_task(ctx, "coder", task, meta)
        self.assertEqual((rec["passed"], rec["denials"], rec["failure_kind"], rec["audit_note"]),
                         (False, 1, "agent", "score 0.50 < 1.0; 1 permission denial(s): Bash"))
        (root / "score.py").write_text("print('no score')\n", encoding="utf-8")   # nothing measured
        rec = bench.run_task(ctx, "coder", task, meta)
        self.assertEqual((rec["passed"], rec["score"], rec["failure_kind"]), (False, None, "harness"))
        self.assertEqual(rec["audit_note"], "1 permission denial(s): Bash")

    def test_consequence_failure_fails_task(self):
        """T3 (3.9.7): shape passes, the coder's change fails the hidden tests -> passed false."""
        _, _, expert = self.expert_run()
        for t in expert["tasks"]:
            self.assertEqual((t["passed"], t["consequence"]["passed"]), (False, False), t["id"])
            self.assertIn("consequence FAIL", t["detail"])

    def test_order_first_alone_then_at_most_four(self):
        self.pct(10.0)
        os.environ["FAKE_SLEEP"] = "0.6"
        rc, text = self.run_bench("coder-opus55", "--jobs", "4")
        self.assertEqual(rc, 0, text)
        calls = sorted(self.calls(), key=lambda c: c["start"])
        self.assertEqual(len(calls), 14)   # was 12; 3.9.8 T1 adds c21, c22 to light
        first = [c for c in calls if c["id"] == "c01"][0]
        self.assertEqual(calls[0]["id"], first["id"])
        self.assertTrue(all(c["start"] >= first["end"] for c in calls if c is not first))
        events = sorted([(c["start"], 1) for c in calls] + [(c["end"], -1) for c in calls])
        live = peak = 0
        for _, d in events:
            live += d
            peak = max(peak, live)
        self.assertLessEqual(peak, 4)
        self.assertGreaterEqual(peak, 2)

    def test_arms_run_sequentially(self):
        self.pct(10.0)
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 0, text)
        calls = self.calls()
        for a, b in (("coder", "expert"), ("expert", "planner")):
            self.assertLessEqual(max(c["end"] for c in calls if c["arm"] == a),
                                 min(c["start"] for c in calls if c["arm"] == b))

    def test_mid_run_over_cap_clean_stops(self):
        self.pct(10.0, 95.0)
        self._patch(mock.patch.object(bench, "POLL_EVERY_S", 0))
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 0, text)
        coder, expert, planner = self.doc()["arms"]
        self.assertEqual(coder["stopped"], 1)
        self.assertIsNotNone(coder["tasks"][0]["passed"])
        self.assertTrue(all(t["passed"] is None for t in coder["tasks"][1:]))
        for arm in (expert, planner):
            self.assertEqual(arm["stopped"], 1)
            self.assertTrue(all(t["passed"] is None for t in arm["tasks"]))
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("clean-stop", text)

    def test_no_stop_runs_through(self):
        self.pct(10.0, 95.0)
        self._patch(mock.patch.object(bench, "POLL_EVERY_S", 0))
        rc, text = self.run_bench("canary", "--no-stop")
        self.assertEqual(rc, 0, text)
        self.assertTrue(all(a["stopped"] == 0 for a in self.doc()["arms"]))
        self.assertEqual(len(self.calls()), 7)

    def test_gate_refuses_then_force_runs(self):
        self.pct(95.0)
        rc, text = self.run_bench("canary")
        self.assertEqual(rc, 2)
        self.assertEqual(len(text.strip().splitlines()), 1, text)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.out.exists())
        rc, text = self.run_bench("canary", "--force", "--no-stop")
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(self.calls()), 7)

    def test_repeat_two_attempts(self):
        """T5 (3.9.7): --repeat 2 clones <id>/a1, a2; records id-major with attempt 1/2; first task's attempt 1
        alone; retained files <id>.a<i>.*; per-id tally; ledger rows per attempt."""
        self.pct(10.0)
        os.environ.update(FAKE_PASS="1", FAKE_SLEEP="0.4")
        rc, text = self.run_bench("coder-opus55", "--suite", "canary", "--repeat", "2", "--jobs", "4")
        self.assertEqual(rc, 0, text)
        doc = self.doc()
        self.assertEqual((doc["format"], doc["repeat"], doc["suite"]), (2, 2, "canary"))
        tasks = doc["arms"][0]["tasks"]
        ids = sorted({t["id"] for t in tasks})
        self.assertEqual([(t["id"], t["attempt"]) for t in tasks], [(i, a) for i in ids for a in (1, 2)])
        self.assertTrue(all(t["passed"] for t in tasks), [t["detail"] for t in tasks])
        self.assertIn(f"pass {len(ids)}/{len(ids)}", text)
        work = Path(doc["work_dir"]) / "coder"
        for i in ids:
            for a in (1, 2):
                self.assertTrue((work / i / f"a{a}" / ".git").is_dir(), (i, a))
                for ext in (".out.json", ".grade.txt", ".diff"):
                    self.assertTrue((work / f"{i}.a{a}{ext}").is_file(), (i, a, ext))
            self.assertFalse((work / f"{i}.out.json").exists())
        calls = sorted(self.calls(), key=lambda c: c["start"])
        self.assertEqual(sorted((c["id"], c["attempt"]) for c in calls), [(i, a) for i in ids for a in (1, 2)])
        first = calls[0]
        self.assertEqual((first["id"], first["attempt"]), (ids[0], 1))
        self.assertTrue(all(c["start"] >= first["end"] for c in calls[1:]))
        con = sqlite3.connect(SCRATCH / "ledger" / "ledger.sqlite")
        n = con.execute("SELECT COUNT(*) FROM bench").fetchone()[0]
        con.close()
        self.assertEqual(n, 2 * len(ids))

    def agents_ab(self, role_b="coder"):
        d = SCRATCH / "agents"
        d.mkdir()
        src = (PKG / "agents" / "coder-opus55.md").read_text(encoding="utf-8")
        for name, role in (("coder-a", "coder"), ("coder-b", role_b)):
            (d / f"{name}.md").write_text(src.replace("name: coder-opus55", f"name: {name}")
                                          .replace("role: coder", f"role: {role}"), encoding="utf-8")
        return d

    def ab(self, d, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["ab", "coder-a", "coder-b", *args, "--agents-dir", str(d),
                             "--work-dir", str(SCRATCH / "work"), "--results-dir", str(SCRATCH / "results")])
        return rc, buf.getvalue()

    def test_ab_refuses_roles_differ(self):
        self.pct(10.0)
        rc, text = self.ab(self.agents_ab("expert"), "canary")
        self.assertEqual((rc, text.strip()), (2, "ab: roles differ (coder vs expert)"))
        self.assertEqual(self.calls(), [])

    def test_ab_runs_both_and_prints_table(self):
        """T5 (3.9.7): A then B as `run`, two results files, both in the ledger, paired table and totals line."""
        self.pct(10.0)
        os.environ["FAKE_PASS"] = "1"
        rc, text = self.ab(self.agents_ab(), "canary", "--repeat", "2")
        self.assertEqual(rc, 0, text)
        files = sorted((SCRATCH / "results").glob("*.json"))
        self.assertEqual(len(files), 2, files)
        docs = [json.loads(p.read_text(encoding="utf-8")) for p in files]
        self.assertEqual(sorted(d["arms"][0]["agent"] for d in docs), ["coder-a", "coder-b"])
        self.assertNotEqual(docs[0]["run_id"], docs[1]["run_id"])
        ids = sorted({t["id"] for t in docs[0]["arms"][0]["tasks"]})
        lines = text.strip().splitlines()
        self.assertEqual(lines[-len(ids) - 1:-1], [f"{i} | A 2/2 $0.05 2.0 | B 2/2 $0.05 2.0" for i in ids], text)
        cost = 0.05 * 2 * len(ids)
        self.assertEqual(lines[-1], f"A better 0 · B better 0 · tie {len(ids)} · cost A ${cost:.2f} / "
                                    f"B ${cost:.2f} · turns A 2.0 / B 2.0")
        con = sqlite3.connect(SCRATCH / "ledger" / "ledger.sqlite")
        runs = con.execute("SELECT COUNT(DISTINCT run_id), COUNT(*) FROM bench").fetchone()
        con.close()
        self.assertEqual(runs, (2, 4 * len(ids)))


class BenchPlan(unittest.TestCase):
    def plan(self, target):
        return subprocess.run([sys.executable, str(TOOLS / "bench.py"), "plan", target], capture_output=True,
                              text=True, encoding="utf-8")

    def test_plan_lines(self):
        want = {"light": "coder 14 · expert 8 · planner 5 · retriever 10 · router 8 · critic 4 · review 4",   # was "... · router 8" (no critic, review): +k01,k03,k04,k09, v01,v04,v05,v07 (3.10 T9); was router 6: +o14, o15 (3.10 T17); was router 5: +o13 (3.10 T20)
                "canary": "coder 4 · expert 2 · planner 1",
                "full": "coder 28 · expert 12 · planner 12 · retriever 28 · router 15 · critic 12 · review 12",   # was router 13: +o14, o15 (3.10 T17); was router 12: +o13 (3.10 T20)
                "coder-opus55": "coder 14"}
        for target, line in want.items():
            r = self.plan(target)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, line), target)
        r = self.plan("nosuch-agent")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)

    def test_plan_preset_skip(self):
        """3.10.6 T6: expert-fable has no rung on pro (one skip line, no counts); on max20 it counts."""
        skip = "skipped expert-fable: preset pro has no hard rung"
        r = subprocess.run([sys.executable, str(TOOLS / "bench.py"), "plan", "expert-fable", "--preset", "pro"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, skip))
        r = subprocess.run([sys.executable, str(TOOLS / "bench.py"), "plan", "expert-fable", "--preset", "max20"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("skipped", r.stdout)
        self.assertTrue(r.stdout.startswith("expert "), r.stdout)

    def test_window_cap_default_80(self):
        self.assertEqual(config.DEFAULTS["bench"]["window_max_pct"], 80)
        text = (PKG / "templates" / "pa.json.template").read_text(encoding="utf-8")
        data = json.loads(text.replace("{{PY}}", "python").replace("{{PROJECT_NAME}}", "t"))
        self.assertEqual(data["bench"]["window_max_pct"], 80)


class BenchPullCompare(unittest.TestCase):
    """pull (source=public) and compare's five verdicts on a temp agents dir and series (3.9.6 T7)."""

    BASE_BODY = "".join(f"line {i}\n" for i in range(10))

    def setUp(self):
        self.tmp = SCRATCH.parent / "bench-compare-test"   # never system temp
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"PA_LEDGER_DIR": str(self.tmp / "ledger"), "PA_HOOKS_OFF": "1"})
        p.start()
        self.addCleanup(p.stop)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)

    def bench(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(list(argv))
        return rc, buf.getvalue()

    def agent(self, d, name, body, version="1"):
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(f"---\nname: {name}\nrole: coder\nversion: {version}\n"
                                      f"model: claude-opus-5-5\n---\n{body}", encoding="utf-8")

    @staticmethod
    def arm(name, body, version, passed, n=4):
        return {"role": "coder", "agent": name, "model": "claude-opus-5-5", "effort": None,
                "body_hash": bench._body_hash(body), "version": version, "stopped": 0,
                "tasks": [{"id": f"t{i}", "tier": 1, "passed": i < passed, "cost_usd": 0.1, "secs": 1,
                           "turns": 2} for i in range(n)]}

    def test_pull_and_compare_verdicts(self):
        base, inst, pub = self.tmp / "base", self.tmp / "agents", self.tmp / "public"
        light = self.BASE_BODY.replace("line 3\n", "line three\n")
        heavy = "".join(f"other {i}\n" for i in range(10))
        for name in ("light", "heavy"):
            self.agent(base, name, self.BASE_BODY)
        self.agent(inst, "matched", "body M\n")
        self.agent(inst, "newer", "body U1\n")
        self.agent(inst, "fresh", "body R new\n")
        self.agent(inst, "heavy", heavy, "user/1")
        self.agent(inst, "light", light, "1+u1")
        self.agent(inst, "unseen", "body N\n")
        doc = {"format": 1, "run_id": "r1", "date": "2026-09-20T00:00:00Z", "suite": "full",
               "harness_version": "x", "price_version": "p", "fixture_hash": "f", "claude_code": "2.1.281",
               "arms": [self.arm("matched", "body M\n", "1", 3), self.arm("newer", "body U1\n", "1", 2),
                        self.arm("newer", "body U2\n", "2", 3), self.arm("fresh", "body R old\n", "1", 3),
                        self.arm("heavy", self.BASE_BODY, "1", 3), self.arm("light", self.BASE_BODY, "1", 3)]}
        pub.mkdir()
        (pub / "2026-09-20.json").write_text(json.dumps(doc), encoding="utf-8")
        (pub / "notes.json").write_text("[]", encoding="utf-8")
        rc, out = self.bench("pull", "--source", str(pub))
        self.assertEqual((rc, len(out.strip().splitlines())), (0, 1), out)
        lc, conn = bench._ledger()
        try:
            src = conn.execute("SELECT source, COUNT(*) FROM bench GROUP BY source").fetchall()
        finally:
            lc.db.close(conn)
        self.assertEqual([tuple(r) for r in src], [("public", 24)])
        rc, out = self.bench("compare", "--agents", str(inst), "--base", str(base))
        self.assertEqual(rc, 0)
        got = [ln.split()[:2] for ln in out.strip().splitlines()]
        self.assertEqual(got, [["fresh", "run-offered"], ["heavy", "run-offered"], ["light", "lightly-altered"],
                               ["matched", "match"], ["newer", "update"], ["unseen", "no-series"]], out)
        self.assertIn("user/1", out.splitlines()[1])

    def test_mark_dotted_versions(self):
        self.assertEqual(bench._mark("3.10.3"), ((1, 3, 10, 3), ""))
        self.assertEqual(bench._mark("3.10.3+u2"), ((1, 3, 10, 3), "3.10.3+u2"))
        self.assertEqual(bench._mark("user/2"), (None, "user/2"))
        self.assertEqual(bench._mark("5"), ((0, 5), ""))
        self.assertEqual(sorted(["3.9.4", "3.10.1", "3.10.10", "3.10.2"], key=lambda v: bench._mark(v)[0]),
                         ["3.9.4", "3.10.1", "3.10.2", "3.10.10"])

    def test_compare_dotted_beats_plain_series(self):
        inst, pub = self.tmp / "agents", self.tmp / "public"
        self.agent(inst, "router", "body D\n", "3.10.7")
        doc = {"format": 1, "run_id": "r1", "date": "2026-09-20T00:00:00Z", "suite": "full",
               "harness_version": "x", "price_version": "p", "fixture_hash": "f", "claude_code": "2.1.281",
               "arms": [self.arm("router", "body D\n", "3.10.7", 2), self.arm("router", "body old\n", "7", 4)]}
        pub.mkdir()
        (pub / "2026-09-20.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(self.bench("pull", "--source", str(pub))[0], 0)
        rc, out = self.bench("compare", "--agents", str(inst), "--base", str(self.tmp / "base"))
        self.assertEqual(rc, 0)
        self.assertEqual(out.split()[:2], ["router", "match"], out)

    def test_compare_preset_suffix(self):
        """3.10.6 T6: each line ends ' · preset <p>': the matching row's, else the agent's newest row's, else a dash."""
        inst, pub = self.tmp / "agents", self.tmp / "public"
        self.agent(inst, "router", "body D\n", "3.10.7")
        self.agent(inst, "other", "body X new\n", "3.10.7")
        self.agent(inst, "unseen", "body N\n")
        doc = {"format": 1, "run_id": "r1", "date": "2026-09-20T00:00:00Z", "suite": "full", "preset": "pro",
               "harness_version": "x", "price_version": "p", "fixture_hash": "f", "claude_code": "2.1.281",
               "arms": [self.arm("router", "body D\n", "3.10.7", 2), self.arm("other", "body X old\n", "3.10.6", 2)]}
        pub.mkdir()
        (pub / "2026-09-20.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(self.bench("pull", "--source", str(pub))[0], 0)
        rc, out = self.bench("compare", "--agents", str(inst), "--base", str(self.tmp / "base"))
        self.assertEqual(rc, 0)
        ends = {ln.split()[0]: ln.rsplit(" · ", 1)[1] for ln in out.strip().splitlines()}
        self.assertEqual(ends, {"other": "preset pro", "router": "preset pro", "unseen": "preset —"}, out)

    def test_pull_without_source(self):
        import pa.paths
        with mock.patch.object(pa.paths, "package_clone_dir", return_value=str(self.tmp / "noclone")):
            rc, out = self.bench("pull")
        self.assertEqual((rc, len(out.strip().splitlines())), (0, 1), out)
        rc, out = self.bench("pull", "--source", str(self.tmp / "nosuch"))
        self.assertEqual((rc, len(out.strip().splitlines())), (0, 1), out)

    def test_pull_defaults_to_clone_results(self):
        import pa.paths
        clone = self.tmp / "clone"
        res = clone / "bench" / "results"
        res.mkdir(parents=True)
        doc = {"format": 1, "run_id": "r1", "date": "2026-09-20T00:00:00Z", "suite": "full",
               "harness_version": "x", "price_version": "p", "fixture_hash": "f", "claude_code": "2.1.281",
               "arms": [self.arm("matched", "body M\n", "1", 3)]}
        (res / "2026-09-20.json").write_text(json.dumps(doc), encoding="utf-8")
        with mock.patch.object(pa.paths, "package_clone_dir", return_value=str(clone)):
            rc, out = self.bench("pull")
        self.assertEqual(rc, 0, out)
        self.assertIn(str(res), out)
        lc, conn = bench._ledger()
        try:
            n = conn.execute("SELECT COUNT(*) FROM bench WHERE source='public'").fetchone()[0]
        finally:
            lc.db.close(conn)
        self.assertEqual(n, 4)

    @unittest.skipUnless(WORKFLOW.exists(), "the bench Action lives in the dev repo; not in the public tree")
    def test_workflow_publish_vars(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        for s in ("vars.BENCH_PUBLISH_REPO", "vars.BENCH_PUBLISH_BRANCH", "github.repository",
                  "'bench-results'", "secrets.BENCH_PUBLISH_TOKEN"):
            self.assertIn(s, wf)


class BenchCheck(unittest.TestCase):
    """check: budget, graders, noise, calibration lines; exit 1 only on budget or graders FAIL (3.9.7 T6)."""

    GOK = "graders OK (full: 0 grader, 0 harness, 0 unaudited; light: 0 grader, 0 harness, 0 unaudited)"

    def setUp(self):
        self.dir = SCRATCH.parent / "bench-check-test"   # never system temp
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)

    @staticmethod
    def arm(role, t1=(3, 4), t2=(3, 4), before=10.0, after=12.0, stopped=0, kind="agent"):
        tasks = [{"id": f"{role}-{tier}-{i}", "tier": tier, "passed": i < k}
                 for tier, (k, n) in ((1, t1), (2, t2)) for i in range(n)]
        for t in tasks:
            if not t["passed"]:
                t["failure_kind"] = kind
        return {"role": role, "agent": f"{role}-opus55", "stopped": stopped,
                "w5h_before": before, "w5h_after": after, "tasks": tasks}

    @staticmethod
    def repeat_arm():
        """Format 2, two attempts per id: a passes both, b..d flip; by id 1/4 pass, by record 5/8."""
        tasks = [{"id": i, "tier": 1, "attempt": n + 1, "passed": p, "failure_kind": None if p else "agent"}
                 for i, ps in (("a", (True, True)), ("b", (True, False)), ("c", (False, True)), ("d", (True, False)))
                 for n, p in enumerate(ps)]
        return {"role": "coder", "agent": "coder-opus55", "stopped": 0, "w5h_before": 20.0, "w5h_after": 24.0,
                "tasks": tasks}

    def write(self, suite, date, arms, fmt=1):
        doc = {"format": fmt, "suite": suite, "date": date, "arms": arms}
        (self.dir / f"{date[:10]}-{suite}.json").write_text(json.dumps(doc), encoding="utf-8")

    def ok_pair(self):
        self.write("light", "2026-09-20T00:00:00Z", [self.arm("coder", before=10.0, after=11.0),
                                                      self.arm("expert", before=11.0, after=13.5)])
        self.write("full", "2026-09-21T00:00:00Z", [self.arm("coder", before=20.0, after=24.0),
                                                     self.arm("expert", before=24.0, after=29.0)])

    def check(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["check", "--results", str(self.dir)])
        return rc, buf.getvalue().strip().splitlines()

    def test_ok(self):
        self.ok_pair()
        self.assertEqual(self.check(), (0, ["budget OK (light 3.5, full 9.0 points)", self.GOK,
                                            "noise coder-opus55 unknown", "noise expert-opus55 unknown",
                                            "calibration 4/4"]))

    def full_kind(self, kind):
        self.ok_pair()
        self.write("full", "2026-09-22T00:00:00Z", [self.arm("coder", before=1.0, after=2.0, kind=kind)])
        return self.check()

    def test_unaudited_fails(self):
        rc, out = self.full_kind(None)
        self.assertEqual((rc, out[1]), (1, "graders FAIL (full: 0 grader, 0 harness, 2 unaudited; "
                                           "light: 0 grader, 0 harness, 0 unaudited)"), out)

    def test_grader_kind_fails(self):
        rc, out = self.full_kind("grader")
        self.assertEqual((rc, out[1]), (1, "graders FAIL (full: 2 grader, 0 harness, 0 unaudited; "
                                           "light: 0 grader, 0 harness, 0 unaudited)"), out)

    def test_harness_kind_fails(self):
        rc, out = self.full_kind("harness")
        self.assertEqual((rc, out[1]), (1, "graders FAIL (full: 0 grader, 2 harness, 0 unaudited; "
                                           "light: 0 grader, 0 harness, 0 unaudited)"), out)

    def test_agent_kind_ok(self):
        rc, out = self.full_kind("agent")
        self.assertEqual((rc, out[1]), (0, self.GOK), out)

    def test_tier_out_of_band_only_calibration(self):
        self.ok_pair()
        self.write("full", "2026-09-22T00:00:00Z", [self.arm("coder", t2=(9, 10), before=1.0, after=2.0)])
        self.assertEqual(self.check(), (0, ["budget OK (light 3.5, full 1.0 points)", self.GOK,
                                            "noise coder-opus55 unknown",
                                            "calibration 1/2 (out: coder coder-opus55 t2 9/10 (90%))"]))

    def test_stopped_arm_ignored(self):
        self.ok_pair()
        self.write("full", "2026-09-22T00:00:00Z", [
            self.arm("coder", before=1.0, after=2.0),
            self.arm("expert", t1=(0, 4), before=None, after=None, stopped=1)])
        rc, out = self.check()
        self.assertEqual((rc, out[0]), (1, "budget FAIL (light 3.5, full an arm stopped points)"), out)
        self.assertEqual(out[2:], ["noise coder-opus55 unknown", "calibration 2/2"], out)

    def test_budget_full_at_10_fails(self):
        self.ok_pair()
        self.write("full", "2026-09-22T00:00:00Z", [self.arm("coder", before=20.0, after=30.0)])
        rc, out = self.check()
        self.assertEqual((rc, out[0]), (1, "budget FAIL (light 3.5, full 10.0 over limit 10 points)"), out)
        self.assertEqual(out[1], self.GOK, out)

    def test_negative_delta_fails(self):
        self.ok_pair()
        self.write("light", "2026-09-22T00:00:00Z", [self.arm("coder", before=70.0, after=2.0)])
        rc, out = self.check()
        self.assertEqual((rc, out[0]), (1, "budget FAIL (light window reset during run, full 9.0 points)"), out)

    def test_missing_light_fails(self):
        self.write("full", "2026-09-21T00:00:00Z", [self.arm("coder", before=20.0, after=24.0)])
        rc, out = self.check()
        self.assertEqual((rc, out[:2]), (1, ["budget FAIL (light no run, full 4.0 points)",
                                             "graders OK (full: 0 grader, 0 harness, 0 unaudited; light: no run)"]))

    def test_no_full_run(self):
        self.write("light", "2026-09-20T00:00:00Z", [self.arm("coder", before=10.0, after=11.0)])
        rc, out = self.check()
        self.assertEqual((rc, out[2:]), (1, ["noise unknown (no full run)", "calibration unknown (no full run)"]))
        self.assertEqual(out[1], "graders OK (full: no run; light: 0 grader, 0 harness, 0 unaudited)")

    def test_repeats_by_id(self):
        """Format 2 attempts: calibration counts ids (1/4, not 5/8 records); noise flips/ids from the same run."""
        self.write("light", "2026-09-20T00:00:00Z", [self.arm("coder", before=10.0, after=11.0)])
        self.write("full", "2026-09-21T00:00:00Z", [self.repeat_arm()], fmt=2)
        rc, out = self.check()
        self.assertEqual((rc, out[2:]), (0, ["noise coder-opus55 ±3/4",
                                             "calibration 0/1 (out: coder coder-opus55 t1 1/4 (25%))"]), out)

    def test_max_run_parts(self):
        """T24: a max run adds 'max <pts>' to budget (never FAIL), 'max: ...' to graders, 'noise max ...' lines."""
        self.ok_pair()
        mx = self.repeat_arm()
        for t, s in zip(mx["tasks"], (1.0, 1.0, 0.75, 0.5, 0.5, 0.75, 1.0, 0.5)):   # |Δ| 0, .25, .25, .5
            t["score"] = s
        mx["tasks"][3].update(failure_kind="grader")   # b attempt 2; c1, d2 stay agent
        self.write("max", "2026-09-22T00:00:00Z", [dict(mx, w5h_before=30.0, w5h_after=70.0)], fmt=2)
        rc, out = self.check()
        self.assertEqual((rc, out[:2]), (1, ["budget OK (light 3.5, full 9.0, max 40.0 points)",
                                             "graders FAIL (full: 0 grader, 0 harness, 0 unaudited; light: 0 grader, "
                                             "0 harness, 0 unaudited; max: 1 grader, 0 harness, 0 unaudited)"]), out)
        self.assertEqual(out[2:5], ["noise coder-opus55 ±3/4 (2026-09-22)", "noise expert-opus55 unknown",
                                    "noise max coder-opus55 ±3/4 Δscore 0.25"], out)

    def test_noise_from_earlier_doc(self):
        self.write("light", "2026-09-19T00:00:00Z", [self.repeat_arm()], fmt=2)
        self.ok_pair()
        rc, out = self.check()
        self.assertEqual((rc, out[2:4]), (0, ["noise coder-opus55 ±3/4 (2026-09-19)",
                                              "noise expert-opus55 unknown"]), out)

    def test_noise_from_newer_doc(self):
        """3.9.7 T8.1: the newest repeat doc of the agent counts whatever its date (here newer than the full)."""
        self.ok_pair()
        self.write("light", "2026-09-30T00:00:00Z", [dict(self.repeat_arm(), w5h_after=23.0)], fmt=2)
        rc, out = self.check()
        self.assertEqual((rc, out[2:4]), (0, ["noise coder-opus55 ±3/4 (2026-09-30)",
                                              "noise expert-opus55 unknown"]), out)

    def test_readme(self):
        """readme: README.md inside a results dir not named results/ (the Action's publish job, 3.9.6 T9)."""
        self.ok_pair()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["readme", "--results", str(self.dir)])
        path = self.dir.resolve() / "README.md"
        self.assertEqual((rc, buf.getvalue().strip()), (0, f"readme: {path}"))
        rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2026-")]
        self.assertEqual([r.split(" | ")[1] for r in rows], ["full", "full", "light", "light"], rows)
        self.assertEqual({r.split(" | ")[2] for r in rows}, {"—"}, rows)   # 3.10.6 T6: no preset recorded
        self.assertIn("| suite | preset | role |", path.read_text(encoding="utf-8"))

    def test_readme_never_overwrites_parent(self):
        """fix-1: a results dir not named results/ never overwrites its parent's README.md (T17 root README)."""
        rdir = self.dir / "out"
        rdir.mkdir()
        sentinel = self.dir / "README.md"
        sentinel.write_text("sentinel\n", encoding="utf-8")
        path = bench._write_readme(rdir.resolve())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "sentinel\n")
        self.assertEqual(path, rdir.resolve() / "README.md")
        self.assertTrue(path.exists())

    def test_fixtures_check_prints_hash(self):
        """fixtures --check prints the full manifest hash, the value the Action's JSON records (T9)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["--fixtures", str(BENCH), "fixtures", "--check"])
        pinned = json.loads((BENCH / "MANIFEST.json").read_text(encoding="utf-8"))["hash"]
        self.assertEqual((rc, buf.getvalue().strip()), (0, f"fixtures OK {pinned}"))


class BenchAudit(unittest.TestCase):
    """audit: list failed tasks, show the retained answer, classify with --set (3.9.7 T1)."""

    def setUp(self):
        self.dir = SCRATCH.parent / "bench-audit-test"   # never system temp
        if self.dir.exists():
            shutil.rmtree(self.dir)
        (self.dir / "work" / "expert").mkdir(parents=True)
        (self.dir / "bench" / "results").mkdir(parents=True)
        (self.dir / "work" / "expert" / "e01.out.json").write_text(
            json.dumps({"result": "first line\nsecond\nthird"}), encoding="utf-8")
        tasks = [{"id": "e01", "tier": 1, "passed": False, "detail": "no CHANGE", "failure_kind": None},
                 {"id": "e02", "tier": 1, "passed": True, "detail": "ok"}]
        doc = {"format": 1, "suite": "full", "date": "2026-09-25T00:00:00Z", "work_dir": str(self.dir / "work"),
               "arms": [{"role": "expert", "agent": "expert-opus55", "tasks": tasks}]}
        self.path = self.dir / "bench" / "results" / "2026-09-25.json"
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        self.imports = []   # 3.9.7 T5: --set re-imports the JSON into the ledger
        p = mock.patch.object(bench, "_ledger_import", lambda path: self.imports.append(Path(path)) or 0)
        p.start()
        self.addCleanup(p.stop)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, self.cwd)

    def audit(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main(["audit", *argv])
        return rc, buf.getvalue().strip().splitlines()

    def test_list_show_set(self):
        rc, out = self.audit("--latest", "full", "--unaudited", "--show", "2")
        self.assertEqual((rc, out), (0, ["expert/e01 [unaudited] no CHANGE", "    first line", "    second",
                                         "unaudited 1 · agent 0 · grader 0 · harness 0"]))
        rc, out = self.audit("--latest", "full", "--set", "expert/e01", "grader", "--why", "x")
        self.assertEqual((rc, out[-1]), (0, "unaudited 0 · agent 0 · grader 1 · harness 0"), out)
        t = json.loads(self.path.read_text(encoding="utf-8"))["arms"][0]["tasks"][0]
        self.assertEqual((t["failure_kind"], t["audit_note"]), ("grader", "x"))
        self.assertEqual(self.audit("--latest", "full", "--unaudited")[1],
                         ["unaudited 0 · agent 0 · grader 1 · harness 0"])
        self.assertEqual(self.imports, [self.path.resolve()])

    def test_repeat_doc_attempt_ids(self):
        """T5 (3.9.7): repeat >= 2 lists <arm>/<id>#a<i>; --set #a<i> one attempt, <arm>/<id> every failed one."""
        (self.dir / "work" / "expert" / "e01.a2.out.json").write_text(json.dumps({"result": "a2 answer"}),
                                                                       encoding="utf-8")
        tasks = [{"id": "e01", "tier": 1, "attempt": 1, "passed": False, "detail": "d1", "failure_kind": None},
                 {"id": "e01", "tier": 1, "attempt": 2, "passed": False, "detail": "d2", "failure_kind": None},
                 {"id": "e02", "tier": 1, "attempt": 1, "passed": True, "detail": "ok"},
                 {"id": "e02", "tier": 1, "attempt": 2, "passed": True, "detail": "ok"}]
        doc = {"format": 2, "repeat": 2, "suite": "full", "date": "2026-09-26T00:00:00Z",
               "work_dir": str(self.dir / "work"), "arms": [{"role": "expert", "agent": "x", "tasks": tasks}]}
        path = self.dir / "bench" / "results" / "2026-09-26.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        rc, out = self.audit("--latest", "full", "--arm", "expert", "--show", "1")
        self.assertEqual((rc, out), (0, ["expert/e01#a1 [unaudited] d1", "    (no retained answer)",
                                         "expert/e01#a2 [unaudited] d2", "    a2 answer",
                                         "unaudited 2 · agent 0 · grader 0 · harness 0"]))
        rc, out = self.audit(str(path), "--set", "expert/e01#a2", "grader", "--why", "w")
        self.assertEqual(out[-1], "unaudited 1 · agent 0 · grader 1 · harness 0", out)
        rc, out = self.audit(str(path), "--set", "expert/e01", "agent", "--why", "v")
        self.assertEqual(out[-1], "unaudited 0 · agent 2 · grader 0 · harness 0", out)
        got = json.loads(path.read_text(encoding="utf-8"))["arms"][0]["tasks"]
        self.assertEqual([t.get("failure_kind") for t in got], ["agent", "agent", None, None])
        self.assertEqual(self.imports, [path.resolve()] * 2)

    def test_missing_file_exits_2(self):
        self.assertEqual(self.audit(str(self.dir / "nope.json"))[0], 2)
        self.assertEqual(self.audit("--latest", "light")[0], 2)

    def test_published_results_load(self):
        """Each published results JSON (format 1, without the T1 fields) loads, audits, and check runs on it."""
        pub = PKG.parent / "bench" / "results"
        files = sorted(pub.glob("*.json")) if pub.is_dir() else []
        if not files:
            self.skipTest("no published results in this checkout")
        for f in files:
            rc, out = self.audit(str(f))
            self.assertEqual(rc, 0, (f.name, out))
            self.assertTrue(out[-1].startswith("unaudited "), out)
            d = self.dir / "check" / f.stem
            d.mkdir(parents=True)
            shutil.copyfile(f, d / f.name)
            self.assertEqual(len(bench._load_results(d)), 1, f.name)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bench.main(["check", "--results", str(d)])
            self.assertIn(rc, (0, 1))
            self.assertTrue(buf.getvalue().strip(), f.name)


if __name__ == "__main__":
    unittest.main()
