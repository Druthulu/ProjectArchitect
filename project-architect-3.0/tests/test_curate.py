"""Tests for curate.py: ops --reindex refreshes line counts and appends missing rows;
memory --route writes each fact to its store, then demotes it."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
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

    def _run(self, routes, *extra, gen="2"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CU.main(["memory", "--gen", gen, "--route"] + routes + list(extra))
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

    def test_superseded_by_pa3_archived(self):
        files = ["feedback-fresh-session.md", "feedback-plain-recaps.md"]
        originals = {f: _read(os.path.join(FIXTURE, f)).decode("utf-8") for f in files}
        rules_before = self.text("rules", "INDEX.md")
        card_before = self.text("HOW_WE_WORK.md")
        rc, out = self._run([f + "=archive" for f in files])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "rules", "L2.md")))
        self.assertEqual(self.text("rules", "INDEX.md"), rules_before)
        self.assertEqual(self.text("HOW_WE_WORK.md"), card_before)
        self.assert_demoted(files, originals)

    def conf(self):
        return json.loads(self.text(".claude", "pa.json"))

    def test_legacy_route_marks_pa_json(self):
        """3.14 T2: a legacy migration route sets memory_routed to the package version."""
        import pa
        rc, out = self._run(["project-phase12.md=archive"], gen="legacy")
        self.assertEqual(rc, 0, out)
        conf = self.conf()
        self.assertEqual(conf["memory_routed"], pa.__version__)
        self.assertEqual(conf["card"], {"max_chars": 7000})       # other keys kept

    def test_legacy_dry_run_no_marker(self):
        self._run(["project-phase12.md=archive"], "--dry-run", gen="legacy")
        self.assertNotIn("memory_routed", self.conf())

    def test_other_gen_no_marker(self):
        rc, out = self._run(["project-phase12.md=archive"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("memory_routed", self.conf())

    def test_zero_route_legacy_marks_only(self):
        """3.14 T4: `--route` with no specs + `--gen legacy`: nothing left to route; marker only."""
        index_before = self.text(".claude-state", "memory", "MEMORY.md")
        rc, out = self._run([], gen="legacy")
        self.assertEqual(rc, 0, out)
        self.assertIn("route: nothing left to route", out)
        self.assertIn("memory_routed", self.conf())
        self.assertEqual(self.text(".claude-state", "memory", "MEMORY.md"), index_before)
        self.assertFalse(os.path.exists(os.path.join(self.mem, "genlegacy.md")))

    def test_zero_route_dry_run_no_marker(self):
        index_before = self.text(".claude-state", "memory", "MEMORY.md")
        rc, out = self._run([], "--dry-run", gen="legacy")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry-run: no changes", out)
        self.assertNotIn("memory_routed", self.conf())
        self.assertEqual(self.text(".claude-state", "memory", "MEMORY.md"), index_before)

    def test_zero_route_other_gen_dies(self):
        with self.assertRaises(SystemExit):
            self._run([])


GENLEGACY = ("## who-is-dev\n\n---\nname: who-is-dev\ndescription: the developer's profile\n"
             "metadata:\n  type: user\n---\n\nDrew, solo.\n\n## How to apply\n\nAsk for a recommendation.\n\n"
             "## plain-recap\n\n---\nname: Plain recap\ndescription: recap in plain English\n---\n\n"
             "End each phase with a recap.\n")


class FirstRun32Test(unittest.TestCase):
    """3.24 T7b: the 3.2 first-run commands: memory --route --gen 3.2 (memory store, genlegacy
    restores, the marker), memory --reindex, rules --reseed, claude-md / card --refresh."""

    def setUp(self):
        scratch = os.path.join(os.path.dirname(PKG), ".run")
        os.makedirs(scratch, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="pa3-curate-32-", dir=scratch)
        self.mem = os.path.join(self.tmp, ".claude-state", "memory")
        shutil.copytree(FIXTURE, self.mem)
        _write(os.path.join(self.mem, "genlegacy.md"), GENLEGACY)
        _write(os.path.join(self.tmp, ".claude", "pa.json"), json.dumps(
            {"card": {"max_chars": 7000}, "first_run": {"from": "gen3", "at": "3.24.0"}}) + "\n")
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), CARD)
        self._env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CU.main(list(argv))
        return rc, buf.getvalue()

    def text(self, *parts):
        return _read(os.path.join(self.tmp, *parts)).decode("utf-8")

    def conf(self):
        return json.loads(self.text(".claude", "pa.json"))

    ROUTES = ["genlegacy.md#who-is-dev=memory", "genlegacy.md#plain-recap=developer",
              "keep.md=memory", "project-phase12.md=archive"]

    def test_route_32_restores_keeps_archives_and_marks(self):
        rc, out = self._run("memory", "--gen", "3.2", "--route", *self.ROUTES)
        self.assertEqual(rc, 0, out)
        restored = self.text(".claude-state", "memory", "who-is-dev.md")
        self.assertTrue(restored.startswith("---\nname: who-is-dev\n"))
        self.assertIn("## How to apply\n\nAsk for a recommendation.", restored)   # its subheading kept
        self.assertNotIn("plain-recap", restored)
        index = self.text(".claude-state", "memory", "MEMORY.md")
        self.assertIn("- [who-is-dev](who-is-dev.md) — the developer's profile\n", index)
        self.assertIn("](keep.md)", index)
        self.assertNotIn("](project-phase12.md)", index)
        self.assertIn("[Generation 3.2 memories](gen3.2.md) — 1 entries", index)
        self.assertIn("Phase 12 closed", self.text(".claude-state", "memory", "gen3.2.md"))
        self.assertIn("- Plain recap: recap in plain English\n", self.text("HOW_WE_WORK.md"))
        self.assertEqual(self.text(".claude-state", "memory", "genlegacy.md"), GENLEGACY)
        conf = self.conf()
        self.assertEqual(conf["migrated"], "3.2")
        self.assertNotIn("first_run", conf)
        self.assertEqual(conf["card"], {"max_chars": 7000})

    def test_route_32_dry_run_writes_nothing(self):
        before = _tree(self.tmp)
        rc, out = self._run("memory", "--gen", "3.2", "--route", *self.ROUTES, "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("  route: genlegacy.md#who-is-dev -> memory "
                      "(.claude-state/memory/who-is-dev.md)\n", out)
        self.assertIn("  route: keep.md -> memory (.claude-state/memory/keep.md)\n", out)
        self.assertEqual(_tree(self.tmp), before)

    def test_route_32_no_specs_marks_only(self):
        index = self.text(".claude-state", "memory", "MEMORY.md")
        rc, out = self._run("memory", "--gen", "3.2", "--route")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.conf()["migrated"], "3.2")
        self.assertNotIn("first_run", self.conf())
        self.assertEqual(self.text(".claude-state", "memory", "MEMORY.md"), index)
        rc, out = self._run("memory", "--gen", "3.2", "--route", "--dry-run")
        self.assertEqual(rc, 0, out)

    def test_route_32_refusals_write_nothing(self):
        _write(os.path.join(self.mem, "who-is-dev.md"), "taken\n")
        before = _tree(self.tmp)
        rc, out = self._run("memory", "--gen", "3.2", "--route", "genlegacy.md#who-is-dev=memory",
                            "genlegacy.md#nope=memory", "gen9.md#x=memory", "keep.md#x=memory")
        self.assertEqual(rc, 1)
        self.assertIn("refused: genlegacy.md#who-is-dev: .claude-state/memory/who-is-dev.md exists",
                      out)
        self.assertIn("refused: genlegacy.md#nope: no section '## nope' in genlegacy.md", out)
        self.assertIn("refused: gen9.md#x: no archive at", out)
        self.assertIn("refused: keep.md#x: expected gen<X>.md#<stem>", out)
        self.assertEqual(_tree(self.tmp), before)
        self.assertIn("first_run", self.conf())

    def test_reindex(self):
        _write(os.path.join(self.mem, "MEMORY.md"), "# Memory Index\n\n- 2026-01-01: phase 3 in progress\n"
               "- [Generation legacy memories](genlegacy.md) — 2 entries\n")
        before = _tree(self.tmp)
        rc, out = self._run("memory", "--reindex", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(_tree(self.tmp), before)
        rc, out = self._run("memory", "--reindex")
        self.assertEqual(rc, 0, out)
        lines = self.text(".claude-state", "memory", "MEMORY.md").splitlines()
        self.assertEqual(len(lines), 10)                         # 9 memory files + the archive
        self.assertIn("- [Developer profile](user-profile.md) — Solo developer; wants "
                      "recommendations, not questions", lines)
        self.assertEqual(lines[-1], "- [Generation legacy memories](genlegacy.md) — 2 entries")
        self.assertFalse(any("phase 3 in progress" in ln or ln.startswith("#") for ln in lines))

    def _rules(self):
        _write(os.path.join(self.tmp, "rules", "INDEX.md"),
               "# Rules\nP3 | Old gates headline | gates | active | seed-2.0\nL1 | Mine | x | active | -\n")
        _write(os.path.join(self.tmp, "rules", "P3.md"), "old P3 text\n")

    def test_rules_reseed(self):
        self._rules()
        before = _tree(self.tmp)
        rc, out = self._run("rules", "--reseed", "P3", "--gen", "3.2", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(_tree(self.tmp), before)
        rc, out = self._run("rules", "--reseed", "P3", "--gen", "3.2")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.text("rules", "gen3.2", "P3.md"), "old P3 text\n")
        self.assertEqual(_read(os.path.join(self.tmp, "rules", "P3.md")),
                         _read(os.path.join(PKG, "rules-seed", "P3.md")))
        index = self.text("rules", "INDEX.md")
        self.assertIn("P3 | Two gates per phase: the plan approval and the milestone | gates"
                      " | active | seed-2.0\n", index)
        self.assertIn("L1 | Mine |", index)
        self.assertIn("P3 | Old gates headline |", self.text("rules", "gen3.2", "INDEX.md"))

    def test_rules_reseed_from_root(self):
        """3.24 T7b part 2c: a project's tools/curate.py finds rules-seed/ in the root R2 placed
        (fixture home: USERPROFILE/HOME -> <tmp>/home, root at <home>/.claude/pa3)."""
        from pa.install import root as install
        home = os.path.join(self.tmp, "home")
        pa3 = os.path.join(home, ".claude", "pa3")
        with contextlib.redirect_stdout(io.StringIO()):
            got = install.copy_package(types.SimpleNamespace(pkg=PKG, pa3_dir=pa3),
                                       types.SimpleNamespace(dry_run=False))
        self.assertEqual(got["status"], "DONE")
        for rel in ("rules-seed/HISTORY.json", "rules-seed/P3.md", "templates/CLAUDE.template.md"):
            self.assertTrue(os.path.isfile(os.path.join(pa3, rel)), rel)
        tool = os.path.join(self.tmp, "tools", "curate.py")
        os.makedirs(os.path.dirname(tool))
        shutil.copyfile(os.path.join(TOOLS, "curate.py"), tool)
        self._rules()
        env = dict(os.environ, HOME=home, USERPROFILE=home, PA_PROJECT_ROOT=self.tmp)
        proc = subprocess.run([sys.executable, tool, "rules", "--reseed", "P3", "--gen", "3.2"],
                              cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_read(os.path.join(self.tmp, "rules", "P3.md")),
                         _read(os.path.join(pa3, "rules-seed", "P3.md")))
        self.assertEqual(self.text("rules", "gen3.2", "P3.md"), "old P3 text\n")

    def test_rules_reseed_refusals(self):
        self._rules()
        before = _tree(self.tmp)
        rc, out = self._run("rules", "--reseed", "P3", "L1", "P99", "--gen", "3.2")
        self.assertEqual(rc, 1)
        self.assertIn("refused: L1: no rules/L1.md", out)
        self.assertIn("refused: P99: no rules/P99.md", out)
        self.assertEqual(_tree(self.tmp), before)

    def _claude_md(self, extra=""):
        tmpl = _read(os.path.join(PKG, "templates", "CLAUDE.template.md")).decode("utf-8")
        old = ("Roles: planner · router · expert · coder · retriever · critic. "
               "Rules: the `project-architect` skill.")
        cur = [ln for ln in tmpl.splitlines() if ln.startswith("Roles:")][0]
        text = (tmpl.replace(cur, old).replace("{{PROJECT_NAME}}", "Demo")
                .replace("{{PROJECT_TAGLINE}}", "a demo") + extra)
        _write(os.path.join(self.tmp, "CLAUDE.md"), text)
        return cur

    def test_claude_md_refresh(self):
        cur = self._claude_md("Our own line about the build farm.\n")
        before = _tree(self.tmp)
        rc, out = self._run("claude-md", "--refresh", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(_tree(self.tmp), before)
        self.assertRegex(out, r"(?m)^  replace: line 4: Roles: planner")
        self.assertRegex(out, r"(?m)^  yours: line \d+: Our own line about the build farm\.$")
        rc, out = self._run("claude-md", "--refresh")
        self.assertEqual(rc, 0, out)
        got = self.text("CLAUDE.md").splitlines()
        self.assertEqual(got[3], cur)
        self.assertEqual(got[0], "# Demo — Project Architect 3.0")
        self.assertIn("Our own line about the build farm.", got)

    def test_card_refresh(self):
        card = ("# How we work — Demo\n\n## Developer <!-- roles: planner review auditor curator -->\n"
                "- Name: Drew\n\n## Docs map <!-- roles: expert coder router planner review critic "
                "discuss auditor curator -->\n- `notes/` — our own notes\n")
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), card)
        rc, out = self._run("card", "--refresh", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.text("HOW_WE_WORK.md"), card)
        self.assertIn("  yours: line 7: - `notes/` — our own notes\n", out)
        rc, out = self._run("card", "--refresh")
        self.assertEqual(rc, 0, out)
        got = self.text("HOW_WE_WORK.md")
        self.assertIn("## Developer <!-- roles: claude planner review auditor curator -->\n", got)
        self.assertIn("## Docs map <!-- roles: expert coder planner review auditor curator -->\n", got)
        self.assertIn("- Name: Drew\n", got)

    def test_template_history_is_current(self):
        """templates/HISTORY.json was rebuilt after the templates last changed: every new block
        is in the current template (rebuild: python -m pa.install.template_history)."""
        with open(os.path.join(PKG, "templates", "HISTORY.json"), encoding="utf-8") as fh:
            table = json.load(fh)
        for name in ("CLAUDE.template.md", "HOW_WE_WORK.template.md"):
            cur = _read(os.path.join(PKG, "templates", name)).decode("utf-8").split("\n")
            self.assertTrue(table[name], name)
            for old, new in table[name]:
                for ln in new:
                    self.assertIn(ln, cur, name)

    def test_card_refresh_cap_refused(self):
        card = "# How we work — Demo\n\n## Developer <!-- roles: planner review auditor curator -->\n"
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), card)
        _write(os.path.join(self.tmp, ".claude", "pa.json"), '{"card": {"max_chars": %d}}\n' % len(card))
        rc, out = self._run("card", "--refresh")
        self.assertEqual(rc, 1)
        self.assertIn("refused: HOW_WE_WORK.md would be", out)
        self.assertEqual(self.text("HOW_WE_WORK.md"), card)


class CuratorAgentTest(unittest.TestCase):
    def test_first_run_carries_the_developers_rules(self):
        # 3.24 T7a (developer 2026-10-09): the 3.2 first-run sort replaces 3.14's PA2 -> PA3 migration. The curator
        # decides by the policy: the PA2 voice (plain-English recaps) lives on the card; "capture knowledge before a
        # fresh session" is archived as a workflow contradiction; stale items are archived.
        with open(os.path.join(PKG, "agents", "memory-curator.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("\n## Migration\n", body)
        policy = body.split("\n## Where each thing goes", 1)[1].split("\n## ", 1)[0]
        self.assertIn("plain-English recaps", policy)
        self.assertIn("capture knowledge before a fresh session", policy)
        self.assertIn("stale items", policy)
        first = body.split("\n## First run in 3.2\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`gen3`", first)
        self.assertIn("genlegacy.md", first)
        self.assertIn("--gen 3.2", first)
        self.assertIn("superseded by 3.2", body)


if __name__ == "__main__":
    unittest.main()
