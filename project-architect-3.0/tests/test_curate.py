"""Tests for curate.py: ops --reindex refreshes line counts and appends missing rows;
memory --route writes each fact to its store, then demotes it."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PKG, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, PKG)

import curate as CU  # noqa: E402


# --------------------------------------------------------------------------- fixture helpers

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


HEADER = "<!-- Ops index: <file> | <title> | <lines>. One line per topic; grep it, never read it whole. -->"
RETIRED = "- [Old topic](../retired/ops/old.md) — retired gen 2"
INDEX_TEXT = HEADER + "\n" + "alpha.md | Alpha as written | 2\n" + "\n" + RETIRED + "\n"

ALPHA = "# Alpha heading\n\nline 3\nline 4\n"          # 4 lines, row says 2
BETA = "intro\n# Beta topic\nbody\n"                    # 3 lines, no row


class OpsReindexTest(unittest.TestCase):
    def setUp(self):
        scratch = os.path.join(os.path.dirname(PKG), ".run")
        os.makedirs(scratch, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="pa3-curate-test-", dir=scratch)
        self.ops = os.path.join(self.tmp, "docs", "ops")
        self.index = os.path.join(self.ops, "INDEX.md")
        _write(self.index, INDEX_TEXT)
        _write(os.path.join(self.ops, "alpha.md"), ALPHA)
        _write(os.path.join(self.ops, "beta.md"), BETA)
        self._env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CU.main(["ops", "--reindex"] + list(extra))
        return rc, buf.getvalue()

    def test_reindex_updates_and_appends(self):
        rc, _ = self._run()
        self.assertEqual(rc, 0)
        lines = _read(self.index).decode("utf-8").split("\n")
        self.assertEqual(lines[0], HEADER)
        self.assertEqual(lines[1], "alpha.md | Alpha as written | 4")
        self.assertEqual(lines[2], "beta.md | Beta topic | 3")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], RETIRED)
        self.assertEqual(lines[5:], [""])

    def test_dry_run_writes_nothing(self):
        before = _read(self.index)
        rc, out = self._run("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(_read(self.index), before)
        self.assertEqual(out.splitlines(),
                         ["alpha.md: 2 -> 4 lines", "add beta.md | Beta topic | 3"])


# --------------------------------------------------------------------------- memory --route

FIXTURE = os.path.join(PKG, "tests", "fixtures", "curate_memory")
BASH = shutil.which("bash")
CARD = ("# How we work\n\n## Developer <!-- roles: all -->\n- Name: Drew\n- Tone: plain\n\n"
        "## Conventions & house style\n- Terse files\n\n## Tools\n- tools/run.sh\n")
ROUTES = ["user-profile.md=developer", "feedback-commits.md=how-we-work",
          "feedback-no-force.md=rule", "reference-deploy.md=ops", "project-phase12.md=archive"]


def _tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = _read(p)
    return out


class MemoryRouteTest(unittest.TestCase):
    def setUp(self):
        scratch = os.path.join(os.path.dirname(PKG), ".run")
        os.makedirs(scratch, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="pa3-curate-mem-", dir=scratch)
        self.mem = os.path.join(self.tmp, ".claude-state", "memory")
        shutil.copytree(FIXTURE, self.mem)
        self.set_cap(7000)
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), CARD)
        _write(os.path.join(self.tmp, "rules", "INDEX.md"),
               "# Rules\nL1 | Old rule | legacy | active | -\n")
        _write(os.path.join(self.tmp, "cookbook", "INDEX.md"), "# Cookbook\n")
        _write(os.path.join(self.tmp, "docs", "ops", "INDEX.md"), HEADER + "\n")
        self._env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_cap(self, cap):
        _write(os.path.join(self.tmp, ".claude", "pa.json"),
               '{"card": {"max_chars": %d}}\n' % cap)

    def _run(self, routes, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CU.main(["memory", "--gen", "2", "--route"] + routes + list(extra))
        return rc, buf.getvalue()

    def text(self, *parts):
        return _read(os.path.join(self.tmp, *parts)).decode("utf-8")

    def assert_demoted(self, files, originals):
        index = self.text(".claude-state", "memory", "MEMORY.md")
        archive = self.text(".claude-state", "memory", "gen2.md")
        for f in files:
            self.assertNotIn("](%s)" % f, index)
            self.assertFalse(os.path.exists(os.path.join(self.mem, f)))
            self.assertIn(originals[f].rstrip("\n"), archive)
        self.assertIn("](keep.md)", index)
        self.assertIn("[Generation 2 memories](gen2.md)", index)

    def test_routes_card_rule_ops_archive(self):
        files = [r.split("=")[0] for r in ROUTES]
        originals = {f: _read(os.path.join(FIXTURE, f)).decode("utf-8") for f in files}
        rc, out = self._run(ROUTES)
        self.assertEqual(rc, 0, out)
        card = self.text("HOW_WE_WORK.md")
        self.assertIn("- Tone: plain\n- Developer profile: Solo developer; wants "
                      "recommendations, not questions\n\n## Conventions", card)
        self.assertIn("- Terse files\n- Commit lines: Imperative, one line, at most 100 chars, "
                      "task id first\n\n## Tools", card)
        rule = self.text("rules", "L2.md")
        self.assertIn("Never force-push and never rewrite published history.", rule)
        self.assertIn("legacy memory feedback-no-force.md", rule)
        self.assertIn("L2 | Never force-push |", self.text("rules", "INDEX.md"))
        self.assertEqual(self.text("docs", "ops", "reference-deploy.md"),
                         "# Deploying the site\n\n1. `npm run build`\n"
                         "2. `rsync -a out/ host:/srv/site/`\n")
        self.assertIn("reference-deploy.md | Deploying the site | 4",
                      self.text("docs", "ops", "INDEX.md"))
        self.assert_demoted(files, originals)

    @unittest.skipIf(BASH is None, "bash not on PATH")
    def test_route_cookbook(self):
        original = _read(os.path.join(FIXTURE, "project-bake-retry.md")).decode("utf-8")
        rc, out = self._run(["project-bake-retry.md=cookbook"])
        self.assertEqual(rc, 0, out)
        entry = self.text("cookbook", "C0001.md")
        self.assertIn("Retry a flaky bake once", entry)
        self.assertIn("the lock clears itself", entry)
        self.assertIn("C0001 | Retry a flaky bake once | legacy,memory |",
                      self.text("cookbook", "INDEX.md"))
        self.assert_demoted(["project-bake-retry.md"], {"project-bake-retry.md": original})

    def test_card_cap_refused_writes_nothing(self):
        self.set_cap(len(CARD) + 20)
        before = _tree(self.tmp)
        rc, out = self._run(["feedback-no-force.md=rule", "user-profile.md=developer"])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"(?m)^refused: user-profile\.md: HOW_WE_WORK\.md would be "
                              r"\d+/%d chars$" % (len(CARD) + 20))
        self.assertEqual(_tree(self.tmp), before)

    def test_dry_run_writes_nothing(self):
        before = _tree(self.tmp)
        rc, out = self._run(ROUTES, "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(_tree(self.tmp), before)
        self.assertIn("  route: user-profile.md -> developer (HOW_WE_WORK.md)\n", out)
        self.assertIn("  route: feedback-no-force.md -> rule (rules/L2.md)\n", out)
        self.assertIn("  route: reference-deploy.md -> ops (docs/ops/reference-deploy.md)\n", out)
        self.assertIn("  archive: .claude-state/memory/gen2.md (5 entries after)\n", out)
        self.assertTrue(out.endswith("dry-run: no changes\n"))


if __name__ == "__main__":
    unittest.main()
