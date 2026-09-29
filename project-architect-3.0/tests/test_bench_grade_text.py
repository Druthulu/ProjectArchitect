"""Tests for the text-graded bench fixtures (expert, planner, router, critic, review) and their graders (3.9.6 T5).

    python -m unittest discover -s project-architect-3.0/tests -p test_bench_grade_text.py
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TOOLS = PKG / "tools"
BENCH = HERE / "fixtures" / "bench"
TASKS = BENCH / "tasks"
SCRATCH = PKG.parent / ".run" / "bench-grade-text-test"   # never system temp

sys.path.insert(0, str(TOOLS))
import _bench_grade as grade  # noqa: E402

BASE = {"id", "arm", "tier", "suites", "prompt", "expect", "hidden"}
OPTIONAL = {"cli", "settings", "files", "overlay", "rubric"}
REF_EXT = {"expert": "md", "planner": "md", "router": "jsonl", "critic": "md", "review": "md"}
# arm: (count, per tier, {suite: (tier 1, tier 2)})
COUNTS = {
    "expert": (12, 6, {"canary": (1, 1), "light": (4, 4), "full": (6, 6)}),
    "planner": (12, 6, {"canary": (1, 0), "light": (3, 2), "full": (6, 6)}),   # was (6, 3, ... (2, 2), (3, 3)) before 3.9.8 T2
    "router": (15, (9, 6), {"canary": (0, 0), "light": (6, 2), "full": (9, 6)}),   # was (13, (7, 6), ... (4, 2), (7, 6)): +o14, o15 (3.10 T17); was (12, 6,... (3, 2), (6, 6)): +o13 (3.10 T20); was (8, 4, ... (2, 2), (4, 4)) before 3.9.8 T3
    "critic": (12, 6, {"canary": (0, 0), "light": (3, 1), "full": (6, 6)}),   # was light (0, 0) before 3.10 T9; was (6, 3, ... (3, 3)) before 3.9.8 T2
    "review": (12, 6, {"canary": (0, 0), "light": (2, 2), "full": (6, 6)}),   # was light (0, 0) before 3.10 T9; was (6, 3, ... (3, 3)) before 3.9.8 T3
}


def load_tasks(arm):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((TASKS / arm).glob("*.json"))]


def ref_path(task):
    return TASKS / task["arm"] / f"{task['id']}.ref.{REF_EXT[task['arm']]}"


def ref_text(task):
    return ref_path(task).read_text(encoding="utf-8")


def scratch(name, text):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


MODEL_NAMES = ("claude-", "opus", "sonnet", "fable", "haiku")
AGENT_IDS = ("expert-", "coder-", "retriever-", "planner-", "pa-session", "critic", "review agent")


def leaks(text):
    """Model names and agent ids a rubric file or the grader template must not carry (3.9.7 T4)."""
    low = text.lower()
    return [w for w in MODEL_NAMES + AGENT_IDS if w in low]


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def gen_milestones(path=BENCH / "plan" / "GENERATION_PLAN.md"):
    """{'1.N': [clause, ...]} from a generation plan's phase lines (plan/ or plan2/, 3.9.8 T2)."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^- (\d+\.\d+) .*?\| milestone: (.*?) \| scope:", line)
        if m:
            out[m.group(1)] = m.group(2).split("; ")
    return out


def phase_lines(path):
    return [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if re.match(r"^- \d+\.\d+ ", ln)]


class TaskFiles(unittest.TestCase):
    def test_counts_tiers_suites(self):
        for arm, (count, per_tier, suites) in COUNTS.items():
            tasks = load_tasks(arm)
            self.assertEqual(len(tasks), count, arm)
            for tier in (1, 2):
                want = per_tier[tier - 1] if isinstance(per_tier, tuple) else per_tier   # tuple since o13 (3.10 T20)
                self.assertEqual(sum(t["tier"] == tier for t in tasks), want, (arm, tier))
            ids = {name: {t["id"] for t in tasks if name in t["suites"]} for name in suites}
            for name, want in suites.items():
                got = tuple(sum(t["tier"] == tier and name in t["suites"] for t in tasks) for tier in (1, 2))
                self.assertEqual(got, want, (arm, name))
            self.assertLessEqual(ids["canary"], ids["light"], arm)
            self.assertLessEqual(ids["light"], ids["full"], arm)
            self.assertEqual(ids["full"], {t["id"] for t in tasks}, arm)

    def test_schema_and_refs(self):
        for arm in COUNTS:
            for i, t in enumerate(load_tasks(arm), 1):
                self.assertLessEqual(BASE, set(t), t["id"])
                self.assertLessEqual(set(t) - BASE, OPTIONAL, t["id"])
                self.assertEqual((t["arm"], t["hidden"]), (arm, "\u2014"), t["id"])
                self.assertEqual(t["id"], f"{t['id'][0]}{i:02d}")
                self.assertIn(t["tier"], (1, 2))
                self.assertTrue(t["prompt"].startswith("bench: "), t["id"])
                self.assertTrue(ref_path(t).is_file(), t["id"])
        self.assertEqual(list(TASKS.rglob("*.ref.json")), [])

    def test_expert_cli_and_basis(self):
        for t in load_tasks("expert"):
            self.assertEqual(t["cli"], ["--disallowedTools", "Agent"], t["id"])
            coder = json.loads((TASKS / "coder" / f"{t['expect']['based_on']}.json").read_text(encoding="utf-8"))
            self.assertEqual(t["tier"], coder["tier"], t["id"])
            self.assertEqual(sorted(t["expect"]["must_files"]), sorted(coder["expect"]["files"]), t["id"])

    def test_router_settings_deny_agent(self):
        for t in load_tasks("router"):
            settings = json.loads((BENCH / t["settings"]).read_text(encoding="utf-8"))
            # T6: a PreToolUse hook blocks the Agent call (recorded in the transcript); deny would drop the tool
            # 3.9.7 T4: TaskStop too (o08 stops the running expert before the respawn)
            hook = settings["hooks"]["PreToolUse"][0]
            self.assertEqual(hook["matcher"], "Agent|TaskStop", t["id"])
            self.assertIn("exit 2", hook["hooks"][0]["command"], t["id"])

    def test_planner_milestones_match_plan(self):
        plan, plan2 = BENCH / "plan" / "GENERATION_PLAN.md", BENCH / "plan2" / "GENERATION_PLAN.md"
        self.assertEqual(sorted(gen_milestones(plan)), [f"1.{n}" for n in range(1, 7)])
        self.assertEqual(sorted(gen_milestones(plan2), key=lambda k: int(k.split(".")[1])),
                         [f"1.{n}" for n in range(1, 13)])
        self.assertEqual(phase_lines(plan2)[:6], phase_lines(plan))
        for t in load_tasks("planner"):
            gen = gen_milestones(BENCH / (t["files"][0] if t.get("files") else "plan/GENERATION_PLAN.md"))
            self.assertEqual(t["overlay"], "plan")
            self.assertEqual(t["rubric"], "rubric/planner.md")
            self.assertTrue(6 <= len(t["expect"]["rubric_items"]) <= 10, t["id"])   # 3.9.7 T4
            self.assertEqual(t["expect"]["draft"], ".run/PHASE_PLAN.draft.md")
            self.assertEqual(list(dict.fromkeys(c["clause"] for c in t["expect"]["milestone"])),   # a clause may split (3.9.6 T7)
                             gen[t["expect"]["phase"]], t["id"])
            for c in t["expect"]["milestone"]:
                for tok in c["tokens"]:
                    self.assertIn(tok.lower(), c["clause"].lower(), t["id"])
        for name in ("GENERATION_PLAN.md", "PROJECT_CONTEXT.md", "HOW_WE_WORK.md", "PHASE_SEED.md"):
            self.assertTrue((BENCH / "plan" / name).is_file(), name)

    def test_planner_refs_lint_clean(self):
        for t in load_tasks("planner"):
            r = subprocess.run([sys.executable, str(TOOLS / "plan_edit.py"), "lint", str(ref_path(t))],
                               cwd=SCRATCH.parent, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "OK"), t["id"])

    def test_rubric_files(self):
        """3.9.7 T4: one grader template, a rubric per arm; no model name and no agent id in any of them."""
        prompt = (BENCH / "rubric" / "grader-prompt.txt").read_text(encoding="utf-8")
        for ph in ("{rubric}", "{items}", "{material}", "{reference}", "{answer}"):
            self.assertEqual(prompt.count(ph), 1, ph)
        self.assertIn("one acceptable answer, not the only one", prompt)
        self.assertIn("| total <n>", prompt)
        grader = json.loads((BENCH / "rubric" / "grader.json").read_text(encoding="utf-8"))
        self.assertEqual(grader, {"model": "claude-opus-5-5", "effort": "medium", "version": 1})
        for name, prefix in (("planner", "S"), ("critic", "C"), ("review", "R")):
            ids = re.findall(r"^([SCR]\d+)\s", (BENCH / "rubric" / f"{name}.md").read_text(encoding="utf-8"), re.M)
            self.assertTrue(ids and all(i.startswith(prefix) for i in ids), (name, ids))
            self.assertTrue(name == "planner" or 5 <= len(ids) <= 8, (name, ids))
        self.assertFalse((BENCH / "rubric" / "planner-3.8.md").exists())
        for path in sorted((BENCH / "rubric").glob("*")):
            if path.name != "grader.json":
                self.assertEqual(leaks(path.read_text(encoding="utf-8")), [], path.name)
        self.assertNotRegex(prompt, r"[A-Za-z]:/|/Users/|scratchpad|\.run/")


class GradeRefs(unittest.TestCase):
    def test_every_ref_passes(self):
        failures = []
        for t in load_tasks("expert"):
            ok, detail = grade.grade_expert(ref_text(t), t["expect"])
            failures += [] if ok else [f"{t['id']}: {detail}"]
        for t in load_tasks("planner"):
            ok, detail = grade.grade_planner(ref_path(t), t["expect"])
            failures += [] if ok else [f"{t['id']}: {detail}"]
        for t in load_tasks("router"):
            ok, detail = grade.grade_router(ref_path(t), t["expect"])
            failures += [] if ok else [f"{t['id']}: {detail}"]
        for t in load_tasks("critic"):
            ok, detail = grade.grade_critic(ref_text(t), t["expect"])
            failures += [] if ok else [f"{t['id']}: {detail}"]
        for t in load_tasks("review"):
            ok, detail = grade.grade_review(ref_text(t), t["expect"])
            failures += [] if ok else [f"{t['id']}: {detail}"]
        self.assertEqual(failures, [])


def drop_lines(text, prefix):
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(prefix)) + "\n"


class WrongAnswers(unittest.TestCase):
    """Per arm: (a) a missing field and (b) a wrong target both FAIL, for every task."""

    def fails(self, result, needle, tid):
        self.assertFalse(result[0], tid)
        self.assertIn(needle, result[1], tid)

    def test_expert(self):
        repo_mods = sorted(p.relative_to(BENCH / "repo").as_posix() for p in (BENCH / "repo" / "shelf").glob("*.py"))
        for t in load_tasks("expert"):
            ref = ref_text(t)
            self.fails(grade.grade_expert(drop_lines(ref, "INTERFACES"), t["expect"]), "missing INTERFACES", t["id"])
            outside = next(m for m in repo_mods if m not in t["expect"]["files"])
            bad = ref.replace("\nCONSTRAINTS:", f"\nCONSTRAINTS: also update {outside};", 1)
            ok, detail = grade.grade_expert(bad, t["expect"])   # 3.9.7 T3: the files bound is a note
            self.assertTrue(ok, (t["id"], detail))
            self.assertIn(f"names {outside} outside expect.files", detail, t["id"])
            long = ref.replace("\nINTERFACES:", "\n" + "\n".join(f"{i}. x" for i in range(5, 14)) + "\nINTERFACES:", 1)
            self.fails(grade.grade_expert(long, t["expect"]), "CHANGE has", t["id"])
            wrong_bt = ref.replace(t["expect"]["build_test"], "python -m pytest")
            self.fails(grade.grade_expert(wrong_bt, t["expect"]), "BUILD/TEST lacks", t["id"])

    def test_expert_live_answers(self):
        """3.9.7 T3: e06/e10 live alts pass with the files note; e07.fail.md fails on CHANGE length."""
        for tid in ("e06", "e10"):
            t = json.loads((TASKS / "expert" / f"{tid}.json").read_text(encoding="utf-8"))
            ok, detail = grade.grade_expert((TASKS / "expert" / f"{tid}.alt.md").read_text(encoding="utf-8"),
                                            t["expect"])
            self.assertTrue(ok, (tid, detail))
            self.assertIn("names shelf/dates.py outside expect.files", detail, tid)
        t = json.loads((TASKS / "expert" / "e07.json").read_text(encoding="utf-8"))
        self.fails(grade.grade_expert((TASKS / "expert" / "e07.fail.md").read_text(encoding="utf-8"), t["expect"]),
                   "CHANGE has 9 lines (1-8)", "e07")

    def test_planner(self):
        for t in load_tasks("planner"):
            ref = ref_text(t)
            no_verify = re.sub(r"( \| verify: [^|]*)", "", ref, count=1)
            self.fails(grade.grade_planner(scratch(f"{t['id']}-a.md", no_verify), t["expect"]), "T1 has no verify",
                       t["id"])
            no_files = re.sub(r"(\| files: )[^|]*", "\\1\u2014 ", ref, count=1)
            self.fails(grade.grade_planner(scratch(f"{t['id']}-b.md", no_files), t["expect"]), "T1 has no files",
                       t["id"])
            tok = t["expect"]["milestone"][0]["tokens"][0]
            uncovered = re.sub(re.escape(tok), "zzz", ref, flags=re.I)
            self.fails(grade.grade_planner(scratch(f"{t['id']}-c.md", uncovered), t["expect"]), "clause uncovered",
                       t["id"])

    def test_planner_rubric_is_side_score(self):
        """3.9.7 T4: grade_planner is the pass alone; rubric_prompt fills every slot and counts max."""
        t = load_tasks("planner")[0]
        ok, detail = grade.grade_planner(ref_path(t), t["expect"])
        self.assertTrue(ok, detail)
        prompt, mx = grade.rubric_prompt("planner", t, ref_text(t), BENCH)
        self.assertEqual(mx, 3 + len(t["expect"]["rubric_items"]))
        self.assertIn("## Tasks", prompt)
        self.assertIn("# Generation 1", prompt)
        self.assertIn("P1 " + t["expect"]["rubric_items"][0], prompt)
        self.assertNotRegex(prompt, r"\{(rubric|items|material|reference|answer)\}")
        bad = scratch("bad-draft.md", re.sub(r"( \| verify: [^|]*)", "", ref_text(t), count=1))
        self.assertFalse(grade.grade_planner(bad, t["expect"])[0])

    def test_planner_rubric_uses_task_files(self):
        """3.9.8 T2: a task["files"] entry replaces plan/*.md of the same basename in {material}."""
        p01, p07 = (next(t for t in load_tasks("planner") if t["id"] == tid) for tid in ("p01", "p07"))
        line = "- 1.7 Catalog tags |"
        self.assertIn(line, grade.rubric_prompt("planner", p07, ref_text(p07), BENCH)[0])
        self.assertNotIn(line, grade.rubric_prompt("planner", p01, ref_text(p01), BENCH)[0])

    def test_rubric_prompt_names_no_agent_or_model(self):
        """3.9.7 T4: a prompt filled for every rubric task under a fake meta carries neither its agent nor model."""
        meta = {"agent": "zz-agent", "model": "zz-model", "arm": "zz-arm"}
        for arm, mx in (("planner", None), ("critic", 7), ("review", 6)):
            for t in load_tasks(arm):
                prompt, got = grade.rubric_prompt(arm, t, ref_text(t), BENCH)
                self.assertEqual(got, mx or 3 + len(t["expect"]["rubric_items"]), t["id"])
                for word in meta.values():
                    self.assertNotIn(word, prompt, t["id"])
                if arm != "planner":
                    self.assertIn("----- ITEMS -----\nnone\n", prompt, t["id"])

    def router_rows(self, t):
        return [json.loads(ln) for ln in ref_text(t).splitlines() if ln.strip()]

    def write_rows(self, name, rows):
        return scratch(name, "".join(json.dumps(r) + "\n" for r in rows))

    def test_router(self):
        for t in load_tasks("router"):
            rows = self.router_rows(t)
            if t["expect"].get("no_agent"):
                silent = [r for r in rows if r["type"] != "assistant"]
                self.fails(grade.grade_router(self.write_rows(f"{t['id']}-a.jsonl", silent), t["expect"]),
                           "missing text", t["id"])
                spawn = {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "critic", "prompt": "x"}}]}}
                self.fails(grade.grade_router(self.write_rows(f"{t['id']}-b.jsonl", rows + [spawn]), t["expect"]),
                           "expected no Agent call", t["id"])
                continue
            call = next(b for r in rows if r["type"] == "assistant"
                        for b in r["message"]["content"] if b.get("name") in grade.AGENT_TOOLS)
            call["input"].pop("subagent_type")
            self.fails(grade.grade_router(self.write_rows(f"{t['id']}-a.jsonl", rows), t["expect"]),
                       "subagent_type None", t["id"])
            call["input"]["subagent_type"] = "coder-opus55"
            self.fails(grade.grade_router(self.write_rows(f"{t['id']}-b.jsonl", rows), t["expect"]),
                       "subagent_type 'coder-opus55'", t["id"])

    def test_router_task_tool_and_brief_head(self):
        t = next(t for t in load_tasks("router") if t["expect"].get("brief_head"))
        rows = self.router_rows(t)
        for r in rows:
            for b in r["message"]["content"] if isinstance(r["message"]["content"], list) else []:
                if b.get("type") == "tool_use":
                    b["name"] = "Task"
        self.assertTrue(grade.grade_router(self.write_rows("task-tool.jsonl", rows), t["expect"])[0])
        for r in rows:
            for b in r["message"]["content"] if isinstance(r["message"]["content"], list) else []:
                if b.get("type") == "tool_use":
                    b["input"]["prompt"] = "\n\nPlease do T9 now"
        self.fails(grade.grade_router(self.write_rows("head.jsonl", rows), t["expect"]), "brief head", t["id"])

    def test_router_taskstop_ids(self):
        """3.9.8 T3: a ref's TaskStop names a task id its prompt gives; o09-o12 offer and use no TaskStop."""
        for t in load_tasks("router"):
            for r in self.router_rows(t):
                for b in r["message"]["content"] if isinstance(r["message"]["content"], list) else []:
                    if b.get("type") == "tool_use" and b.get("name") == "TaskStop":
                        tid = b["input"].get("task_id") or b["input"].get("shell_id") or ""
                        self.assertTrue(tid and tid in t["prompt"], (t["id"], tid))
            if t["id"] >= "o09":
                self.assertNotIn("TaskStop", t["prompt"], t["id"])
                self.assertNotIn("TaskStop", ref_text(t), t["id"])

    def test_router_o04_live_alt(self):
        # 3.9.7 T4: the live pa-session text that asked before closing without the exact phrase
        t = next(t for t in load_tasks("router") if t["id"] == "o04")
        ok, detail = grade.grade_router(TASKS / "router" / "o04.alt.jsonl", t["expect"])
        self.assertTrue(ok, detail)

    def test_critic(self):
        # 3.9.7 T4: only the contract gates; expect.reference is reported in the detail
        for t in load_tasks("critic"):
            ref = ref_text(t)
            self.fails(grade.grade_critic(drop_lines(ref, "DECISION"), t["expect"]), "missing DECISION", t["id"])
            self.fails(grade.grade_critic(re.sub(r"(?m)^DECISION: .*$", "DECISION: maybe", ref), t["expect"]),
                       "DECISION 'maybe'", t["id"])
        k01, k04 = (next(t for t in load_tasks("critic") if t["id"] == tid) for tid in ("k01", "k04"))
        ref = ref_text(k01)
        self.fails(grade.grade_critic(drop_lines(ref, "python tools/plan_edit.py"), k01["expect"]),
                   "edit with no plan_edit.py line", "k01")
        self.fails(grade.grade_critic(ref.replace("plan_edit.py reopen", "plan_edit.py rewrite"), k01["expect"]),
                   "unknown subcommand 'rewrite'", "k01")
        self.fails(grade.grade_critic(ref.replace("DECISION: edit", "DECISION: continue"), k01["expect"]),
                   "continue with a plan_edit.py line", "k01")
        self.fails(grade.grade_critic(ref_text(k04).replace("Recommended:", "I suggest"), k04["expect"]),
                   "BRIEF lacks 'Recommended:'", "k04")

    def test_critic_live_alts(self):
        for tid in ("k01", "k04", "k06"):
            t = next(t for t in load_tasks("critic") if t["id"] == tid)
            text = (TASKS / "critic" / f"{tid}.alt.md").read_text(encoding="utf-8")
            ok, detail = grade.grade_critic(text, t["expect"])
            self.assertTrue(ok, (tid, detail))
            self.assertIn("ref ", detail, tid)
            self.assertIn("got ", detail, tid)

    def test_review(self):
        for t in load_tasks("review"):
            ref = ref_text(t)
            self.fails(grade.grade_review(drop_lines(ref, "RECOMMENDED:"), t["expect"]), "no RECOMMENDED line",
                       t["id"])
            no_by = ref.replace("--by developer", "")
            self.fails(grade.grade_review(no_by, t["expect"]), "--by developer` option", t["id"])
        v01 = next(t for t in load_tasks("review") if t["id"] == "v01")
        ref = ref_text(v01)
        self.fails(grade.grade_review(ref.replace("RECOMMENDED: 1a", "RECOMMENDED: 1c"), v01["expect"]),
                   "1c: no such option", "v01")
        self.fails(grade.grade_review(ref.replace("RECOMMENDED: 1a", "RECOMMENDED: 1b"), v01["expect"]),
                   "1b is not listed first", "v01")
        ok, detail = grade.grade_review(ref, v01["expect"])
        self.assertTrue(ok, detail)
        self.assertIn("ref 1a · got 1a", detail)


if __name__ == "__main__":
    unittest.main()
