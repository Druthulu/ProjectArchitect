"""Tests for the bench fixtures (tests/fixtures/bench), tools/_bench_grade.py and bench.py fixtures (3.9.6 T4).

    python -m unittest discover -s project-architect-3.0/tests -p test_bench_grade.py
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TOOLS = PKG / "tools"
BENCH = HERE / "fixtures" / "bench"
REPO = BENCH / "repo"
DEPOT = BENCH / "repo-depot"   # 3.9.8 T1: coder tier 3 (bug hunt) repo
SCRATCH = PKG.parent / ".run" / "bench-grade-test"   # never system temp

sys.path.insert(0, str(TOOLS))
import _bench_grade as grade  # noqa: E402
import bench  # noqa: E402

FIELDS = {"id", "arm", "tier", "suites", "prompt", "expect", "hidden"}
OPTIONAL = {"overlay", "repo"}   # 3.9.8 T1: tier-3 coder tasks
TIER3 = [f"c{n}" for n in range(21, 29)]


def load_tasks(arm):
    return [json.loads(p.read_text(encoding="utf-8")) | {"_file": p.stem}
            for p in sorted((BENCH / "tasks" / arm).glob("*.json"))]


def copy_repo(name):
    dst = SCRATCH / name
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def git(cwd, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd,
                          capture_output=True, text=True)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


class MiniRepo(unittest.TestCase):
    def test_size(self):
        mods = sorted((REPO / "shelf").glob("*.py"))
        self.assertTrue(6 <= len(mods) <= 12, len(mods))
        lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in mods)
        self.assertLessEqual(lines, 1500)

    def test_no_nested_git_or_init_on_the_way(self):
        self.assertFalse((REPO / ".git").exists())
        for d in (HERE / "fixtures", BENCH, REPO):   # REPO/tests/__init__.py is allowed since 3.9.6 T7: fixtures/ has none, so discovery stops there
            self.assertFalse((d / "__init__.py").exists(), d)

    def test_visible_suite_green(self):
        clone = copy_repo("visible")
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=clone,
                           capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])

    def test_depot_size(self):
        lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in (DEPOT / "depot").glob("*.py"))
        self.assertTrue(1800 <= lines <= 2200, lines)
        self.assertFalse((DEPOT / ".git").exists())
        self.assertFalse((DEPOT / "__init__.py").exists())


class TaskFiles(unittest.TestCase):
    def check_schema(self, arm, tasks):
        for t in tasks:
            self.assertEqual(set(t) - {"_file"} - OPTIONAL, FIELDS, t["_file"])
            self.assertEqual((t["id"], t["arm"]), (t["_file"], arm))
            self.assertIn(t["tier"], (1, 2, 3))
            self.assertIn("full", t["suites"])
            self.assertTrue(t["prompt"].strip())

    def suite(self, tasks, name, tier=None):
        return {t["id"] for t in tasks if name in t["suites"] and (tier is None or t["tier"] == tier)}

    def test_coder(self):
        tasks = load_tasks("coder")
        self.check_schema("coder", tasks)
        self.assertEqual(len(tasks), 28)
        for name, per_tier in (("canary", (2, 2, 0)), ("light", (6, 6, 2)), ("full", (10, 10, 8))):
            for tier in (1, 2, 3):
                self.assertEqual(len(self.suite(tasks, name, tier)), per_tier[tier - 1], (name, tier))
        self.assertLessEqual(self.suite(tasks, "canary"), self.suite(tasks, "light"))
        for t in tasks:
            hidden = BENCH / t["hidden"]
            self.assertEqual(t["hidden"], f"hidden/{t['id']}")
            self.assertTrue(list(hidden.glob("test_*.py")), t["id"])
            files = t["expect"]["files"]
            if t["tier"] == 3:
                self.assertEqual((t["repo"], t["overlay"]), ("repo-depot", f"overlay/{t['id']}"))
                self.assertEqual(len(files), 1, t["id"])
                self.assertTrue((BENCH / t["overlay"] / files[0]).is_file(), t["id"])
                continue
            self.assertTrue((hidden / "solution.patch").is_file(), t["id"])
            self.assertEqual(len(files) == 1, t["tier"] == 1, t["id"])

    def test_retriever(self):
        tasks = load_tasks("retriever")
        self.check_schema("retriever", tasks)
        self.assertEqual(len(tasks), 28)
        self.assertEqual(self.suite(tasks, "canary"), set())
        light = self.suite(tasks, "light")
        self.assertEqual(len(light), 10)
        self.assertTrue(self.suite(tasks, "light", 1) and self.suite(tasks, "light", 2))
        tier1 = len(self.suite(tasks, "full", 1))
        self.assertTrue(10 <= tier1 <= 18, tier1)
        for t in tasks:
            self.assertEqual(t["hidden"], "\u2014")
            self.assertEqual(set(t["expect"]), {"refs", "tokens"})   # 3.9.7 T2: forbidden dropped
            self.assertTrue(t["expect"]["refs"], t["id"])
            self.assertIn("path:line", t["prompt"])
        keys = (BENCH / "tasks" / "retriever" / "KEYS.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual([k.split(":")[0] for k in keys], sorted(t["id"] for t in tasks))

    def test_retriever_refs_point_at_symbols(self):
        for t in load_tasks("retriever"):
            for ref in t["expect"]["refs"]:
                lines = (REPO / ref["path"]).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(ref["line"], len(lines), (t["id"], ref))
                self.assertIn(ref["symbol"], lines[ref["line"] - 1], (t["id"], ref))
                if "to" in ref:   # 3.9.8 T9: the def's last line
                    self.assertTrue(ref["line"] <= ref["to"] <= len(lines), (t["id"], ref))


class GradeCoder(unittest.TestCase):
    def one(self, task):
        if task.get("repo"):   # tier 3: no solution.patch; DepotTier covers it
            return None
        clone = copy_repo(task["id"])
        if git(clone, "init", "-q").returncode:
            return f"{task['id']}: git init failed"
        before, _ = grade.grade_coder(clone, task)
        if before:
            return f"{task['id']}: hidden tests pass before the patch"
        r = git(clone, "apply", str(BENCH / task["hidden"] / "solution.patch"))
        if r.returncode:
            return f"{task['id']}: git apply: {r.stderr.strip()}"
        after, detail = grade.grade_coder(clone, task)
        shutil.rmtree(clone, ignore_errors=True)
        return None if after else f"{task['id']}: fails after the patch\n{detail[-600:]}"

    def test_false_before_true_after_patch(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            errors = [e for e in pool.map(self.one, load_tasks("coder")) if e]
        self.assertEqual(errors, [])

    def test_missing_hidden(self):
        ok, detail = grade.grade_coder(SCRATCH, {"hidden": "hidden/none"})
        self.assertFalse(ok)
        self.assertIn("no hidden tests", detail)


class GradeScored(unittest.TestCase):
    """T24: grade_scored runs expect.score_cmd; the last 'score: <float>' stdout line is the score."""

    def scored(self, body, pass_score=None):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        script = SCRATCH / "score.py"
        script.write_text(body, encoding="utf-8")
        expect = {"score_cmd": [sys.executable, str(script), "{clone}", "{id}"]}
        if pass_score is not None:
            expect["pass_score"] = pass_score
        return grade.grade_scored(SCRATCH / "clone", {"id": "m01", "expect": expect}, SCRATCH)

    def test_last_score_line(self):
        body = "import sys\nprint('score: 0.2')\nprint('id', sys.argv[2])\nprint('score: 0.75')\n"
        ok, detail, score = self.scored(body, pass_score=0.7)
        self.assertEqual((ok, score), (True, 0.75))
        self.assertIn("id m01", detail)
        self.assertEqual(self.scored(body)[:3:2], (False, 0.75))   # pass_score default 1.0

    def test_missing_line_or_exit(self):
        self.assertEqual(self.scored("print('no score here')\n")[::2], (False, None))
        ok, detail, score = self.scored("import sys\nprint('score: 1.0')\nsys.exit(3)\n")
        self.assertEqual((ok, score), (False, None))
        self.assertIn("score: 1.0", detail)


class DepotTier(unittest.TestCase):
    """3.9.8 T1: coder tier 3, c21-c28: one planted defect per overlay on repo-depot."""

    def tasks(self):
        tasks = [t for t in load_tasks("coder") if t["tier"] == 3]
        self.assertEqual([t["id"] for t in tasks], TIER3)
        return tasks

    def unittest_rc(self, clone, *args):
        return subprocess.run([sys.executable, "-m", "unittest", *args], cwd=clone, capture_output=True,
                              text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")).returncode

    def one(self, task):
        tid, errors = task["id"], []
        clean = SCRATCH / f"{tid}-clean"
        bench._clone(BENCH, {k: v for k, v in task.items() if k != "overlay"}, clean)
        ok, detail = grade.grade_coder(clean, task)
        if not ok:
            errors.append(f"{tid}: hidden red on clean\n{detail[-600:]}")
        bug = SCRATCH / f"{tid}-overlay"
        bench._clone(BENCH, task, bug)
        mods = [f"tests.{p.stem}" for p in sorted((bug / "tests").glob("test_*.py"))]
        if self.unittest_rc(bug, "discover", "-s", "tests"):
            errors.append(f"{tid}: visible suite red on overlay (discover)")
        if self.unittest_rc(bug, *mods):
            errors.append(f"{tid}: visible suite red on overlay (modules)")
        if grade.grade_coder(bug, task)[0]:
            errors.append(f"{tid}: hidden green on overlay")
        for d in (clean, bug):
            shutil.rmtree(d, onexc=bench._force_remove)
        return errors

    def test_clean_green_overlay_red_visible_green(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            errors = [e for errs in pool.map(self.one, self.tasks()) for e in errs]
        self.assertEqual(errors, [])

    def test_prompts_name_no_code(self):
        names = set()
        for p in (DEPOT / "depot").glob("*.py"):
            names |= {p.stem, f"{p.stem}.py"}
            names |= {n.name for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        compound = {n for n in names if "_" in n or sum(c.isupper() for c in n) >= 2}
        for t in self.tasks():
            prompt = t["prompt"]
            for bad in ("`", ".py", "depot."):
                self.assertNotIn(bad, prompt, t["id"])
            for name in names:
                self.assertNotIn(f"{name}(", prompt, (t["id"], name))
            for name in compound:
                self.assertIsNone(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", prompt), (t["id"], name))
            self.assertTrue(prompt.endswith(
                "Fix it and keep the existing tests passing (python -m unittest discover -s tests)."), t["id"])


class GradeRetriever(unittest.TestCase):
    KEY = {"refs": [{"path": "shelf/loans.py", "line": 40, "symbol": "x"},
                    {"path": "shelf/fees.py", "line": 10, "symbol": "y"}],
           "tokens": ["checkout"], "forbidden": ["flat_fee"]}

    def test_hit(self):
        ok, _ = grade.grade_retriever("checkout at shelf/loans.py:40 and shelf/fees.py:10", self.KEY)
        self.assertTrue(ok)

    def test_tolerance_range_and_separators(self):
        ok, _ = grade.grade_retriever("checkout: C:\\r\\shelf\\loans.py:42, `shelf/fees.py:5-12`", self.KEY)
        self.assertTrue(ok)
        ok, detail = grade.grade_retriever("checkout shelf/loans.py:43 shelf/fees.py:13-20", self.KEY)
        self.assertFalse(ok)
        self.assertIn("shelf/loans.py:40", detail)
        self.assertIn("shelf/fees.py:10", detail)

    def test_comma_list_cites_every_item(self):   # 2026-09-29 T2: r09#a1 cited `shelf/storage.py:45,56`
        key = {"refs": [{"path": "shelf/storage.py", "line": 56, "symbol": "x"}], "tokens": []}
        for text in ("see `shelf/storage.py:45,56`", "see shelf/storage.py:42, 56."):
            self.assertTrue(grade.grade_retriever(text, key)[0], text)
        ok, detail = grade.grade_retriever("see shelf/storage.py:45, then 56 more", key)
        self.assertFalse(ok)
        self.assertIn("missing ref shelf/storage.py:56", detail)

    def test_comma_range_list(self):
        key = {"refs": [{"path": "shelf/loans.py", "line": 11, "symbol": "a"},
                        {"path": "shelf/loans.py", "line": 20, "symbol": "b"}], "tokens": []}
        self.assertTrue(grade.grade_retriever("see shelf/loans.py:11-14,19-20", key)[0])
        self.assertTrue(grade.grade_retriever("see shelf/loans.py:11-14, 19-20", key)[0])

    def test_comma_list_first_item_unchanged(self):
        key = {"refs": [{"path": "shelf/storage.py", "line": 45, "symbol": "x"}], "tokens": []}
        self.assertTrue(grade.grade_retriever("see shelf/storage.py:45,56", key)[0])
        self.assertEqual(grade._citations("see shelf/storage.py:45,56"),
                         [("shelf/storage.py", 45, 45), ("shelf/storage.py", 56, 56)])
        self.assertEqual(grade._citations("see a/b.py:7-9, `c.py:3`"), [("a/b.py", 7, 9), ("c.py", 3, 3)])

    def test_range_gets_line_slack(self):
        key = {"refs": [{"path": "shelf/fees.py", "line": 5, "symbol": "x"}], "tokens": []}
        self.assertTrue(grade.grade_retriever("see shelf/fees.py:7-10", key)[0])
        self.assertFalse(grade.grade_retriever("see shelf/fees.py:8-10", key)[0])

    def test_ref_to_covers_the_def(self):   # 3.9.8 T9: a cite anywhere in line..to (±2) matches
        key = {"refs": [{"path": "shelf/holds.py", "line": 19, "to": 25, "symbol": "place"}], "tokens": []}
        for text in ("see shelf/holds.py:23", "see shelf/holds.py:21-22", "see shelf/holds.py:17",
                     "see shelf/holds.py:27", "see shelf/holds.py:10-17"):
            self.assertTrue(grade.grade_retriever(text, key)[0], text)
        for text in ("see shelf/holds.py:16", "see shelf/holds.py:28", "see shelf/holds.py:5-16"):
            ok, detail = grade.grade_retriever(text, key)
            self.assertFalse(ok, text)
            self.assertIn("missing ref shelf/holds.py:19-25", detail)

    def test_missing_path_and_token(self):
        ok, detail = grade.grade_retriever("see shelf/loan.py:40 and shelf/fees.py:10", self.KEY)
        self.assertFalse(ok)
        self.assertIn("missing ref shelf/loans.py:40", detail)
        self.assertIn("missing token 'checkout'", detail)

    def test_forbidden_ignored(self):
        ok, detail = grade.grade_retriever(
            "checkout shelf/loans.py:40 shelf/fees.py:10 (also flat_fee)", self.KEY)
        self.assertTrue(ok, detail)

    def test_suffix_both_ways(self):
        key = {"refs": [{"path": "shelf/storage.py", "line": 55, "symbol": "x"}], "tokens": []}
        self.assertTrue(grade.grade_retriever("see `storage.py:55`", key)[0])
        key = {"refs": [{"path": "storage.py", "line": 55, "symbol": "x"}], "tokens": []}
        self.assertTrue(grade.grade_retriever("see ./repo/shelf/storage.py:55", key)[0])
        self.assertFalse(grade.grade_retriever("see shelf/test_storage.py:55", {
            "refs": [{"path": "shelf/storage.py", "line": 55, "symbol": "x"}]})[0])

    def test_line_mention_in_path_context(self):
        key = {"refs": [{"path": "shelf/policy.py", "line": 7, "symbol": "x"},
                        {"path": "shelf/loans.py", "line": 81, "symbol": "y"}], "tokens": []}
        for text in ("`shelf/policy.py:29` returns DAILY_FEE_CENTS (line 7)\n\n**shelf/loans.py:**\n- Line 81",
                     "In `shelf/policy.py`, l. 7; loans.py lines 80-82",
                     "`shelf/policy.py`:7 and shelf/loans.py line 79–83"):
            ok, detail = grade.grade_retriever(text, key)
            self.assertTrue(ok, (text, detail))

    def test_line_mention_needs_path_in_paragraph(self):
        key = {"refs": [{"path": "shelf/policy.py", "line": 7, "symbol": "x"}], "tokens": []}
        ok, detail = grade.grade_retriever("shelf/policy.py is the file.\n\nThe constant is on line 7.", key)
        self.assertFalse(ok)
        self.assertIn("missing ref shelf/policy.py:7", detail)

    def test_wrong_line_fails(self):
        key = {"refs": [{"path": "shelf/policy.py", "line": 7, "symbol": "x"}], "tokens": []}
        ok, detail = grade.grade_retriever("In `shelf/policy.py`, line 30 holds it.", key)
        self.assertFalse(ok)
        self.assertIn("missing ref shelf/policy.py:7", detail)

    def test_missing_ref_fails(self):
        ok, detail = grade.grade_retriever("checkout at shelf/loans.py:40 only", self.KEY)
        self.assertFalse(ok)
        self.assertIn("missing ref shelf/fees.py:10", detail)

    def test_every_alt_passes(self):
        def planner(text, expect):   # planner: 3.9.6 T7; grade_planner takes a draft path, lint runs inside
            tmp = SCRATCH / "alt-planner"
            shutil.rmtree(tmp, ignore_errors=True)
            (tmp / ".run").mkdir(parents=True)
            draft = tmp / ".run" / "PHASE_PLAN.draft.md"
            draft.write_text(text.split("\n", 1)[1], encoding="utf-8", newline="\n")
            return grade.grade_planner(draft, expect)
        graders = {"retriever": grade.grade_retriever, "expert": grade.grade_expert,   # expert: 3.9.7 T3
                   "critic": grade.grade_critic, "planner": planner}   # critic: 3.9.7 T4
        alts = sorted((BENCH / "tasks").glob("*/*.alt*.md"))
        self.assertGreaterEqual(len([a for a in alts if a.parent.name == "retriever"]), 5)
        for alt in alts:
            arm, tid = alt.parent.name, alt.name.split(".")[0]
            self.assertIn(arm, graders, alt.name)
            task = json.loads((alt.parent / f"{tid}.json").read_text(encoding="utf-8"))
            ok, detail = graders[arm](alt.read_text(encoding="utf-8"), task["expect"])
            self.assertTrue(ok, (alt.name, detail))


class FixturesManifest(unittest.TestCase):
    def run_bench(self, *args):
        return subprocess.run([sys.executable, str(TOOLS / "bench.py"), *args],
                              capture_output=True, text=True)

    def test_check_ok(self):
        r = self.run_bench("fixtures", "--check")
        pinned = json.loads((BENCH / "MANIFEST.json").read_text(encoding="utf-8"))["hash"]
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"fixtures OK {pinned}"), r.stdout + r.stderr)

    def test_default_root_is_package(self):
        self.assertEqual(grade.fixture_root(), BENCH)

    def test_drift(self):
        tmp = SCRATCH / "drift"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(BENCH, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.assertEqual(self.run_bench("--fixtures", str(tmp), "fixtures", "--check").returncode, 0)
        with open(tmp / "repo" / "shelf" / "fees.py", "a", encoding="utf-8") as fh:
            fh.write("# drift\n")
        (tmp / "tasks" / "coder" / "c01.json").unlink()
        (tmp / "extra.txt").write_text("x", encoding="utf-8")
        r = self.run_bench("--fixtures", str(tmp), "fixtures", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("drifted repo/shelf/fees.py", r.stdout)
        self.assertIn("missing tasks/coder/c01.json", r.stdout)
        self.assertIn("extra extra.txt", r.stdout)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
