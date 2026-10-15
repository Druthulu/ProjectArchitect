"""Tests for the text-graded bench fixtures (expert) and their grader (3.9.6 T5).
3.24: the planner, router, critic and review fixtures, graders and rubric tests left with their arms.

    python -m unittest discover -s project-architect-3.0/tests -p test_bench_grade_text.py
"""

import json
import shutil
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
REF_EXT = {"expert": "md"}   # 3.24: was + planner md, router jsonl, critic md, review md
# arm: (count, per tier, {suite: (tier 1, tier 2)})
COUNTS = {
    "expert": (12, 6, {"canary": (1, 1), "light": (4, 4), "full": (6, 6)}),
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


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


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

class GradeRefs(unittest.TestCase):
    def test_every_ref_passes(self):
        failures = []
        for t in load_tasks("expert"):
            ok, detail = grade.grade_expert(ref_text(t), t["expect"])
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


if __name__ == "__main__":
    unittest.main()
