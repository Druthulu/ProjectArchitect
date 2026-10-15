"""pa_install.py --project: detection, bootstrap, file copies, CLAUDE.md, settings.

Every test runs against a throw-away git repository plus a throw-away ``--pa3-dir``
and an explicit ``--python``: the real ``~/.claude`` is never read and never written
(``TestSafety`` asserts it).

    cd project-architect-3.0 && python -m unittest tests.test_install_project -v
"""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa.install import build_parser, main as install_main  # noqa: E402
from pa.install import detect as detect_mod                # noqa: E402
from pa.install import project as pj                       # noqa: E402

PY = sys.executable
_STEP_RE = re.compile(r"^(P\d+)\s+\S.*?\s+(SKIP|DONE|FAIL)\s\s")


def steps(out):
    """``{"P1": "DONE", ...}`` parsed from the step lines of a run."""
    got = {}
    for line in out.splitlines():
        m = _STEP_RE.match(line)
        if m:
            got[m.group(1)] = m.group(2)
    return got


def git(repo, *args):
    return subprocess.run(("git", "-C", repo) + args, capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def past_agent(name):
    """The last packaged bytes of a retired agent (3.24 T5: only an unedited copy is pruned)."""
    top = git(PKG, "rev-parse", "--show-toplevel").stdout.strip()
    path = os.path.relpath(os.path.join(PKG, "agents", name), top).replace(os.sep, "/")
    c = git(top, "log", "-1", "--format=%H", "--", path).stdout.strip()
    shown = subprocess.run(("git", "-C", top, "show", "%s^:%s" % (c, path)), capture_output=True)
    if not c or shown.returncode:
        raise unittest.SkipTest("needs ProjectArchitect's history (%s); not in a public checkout" % name)
    return shown.stdout


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def entries(root):
    """Every path under ``root`` except ``.git`` (directories included)."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in dirnames + filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.add(rel.replace("\\", "/"))
    return out


def tree_hash(root):
    """A digest of every tracked-ish file's path and bytes (``.git`` excluded)."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            h.update(os.path.relpath(full, root).replace("\\", "/").encode("utf-8"))
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


class ProjectCase(unittest.TestCase):
    """A throw-away git repo, a throw-away pa3 dir and an in-process runner."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-proj-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "demo")
        os.makedirs(self.repo)
        self.pa3 = os.path.join(self.tmp, "pa3")
        write(os.path.join(self.pa3, "VERSION"), "version: 3.0.0-test\n")
        self.git_init(self.repo)
        # isolate the usage ledger the P9 step touches (T8.c2)
        self.ledger = os.path.join(self.tmp, "ledger")
        self._old_ledger_dir = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        # pin the ladder preset whatever this machine's credentials say (3.10.6 T2): max20
        # renders the shipped agent bytes
        self._old_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "claude-config")
        self.set_tier("max20")

    def tearDown(self):
        if self._old_ledger_dir is not None:
            os.environ["PA_LEDGER_DIR"] = self._old_ledger_dir
        else:
            os.environ.pop("PA_LEDGER_DIR", None)
        if self._old_config_dir is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_config_dir
        else:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def set_tier(self, tier):
        """Credentials in the temp CLAUDE_CONFIG_DIR that resolve to ``tier`` (max20|max5|pro)."""
        oauth = {"max20": {"rateLimitTier": "default_claude_max_20x"},
                 "max5": {"rateLimitTier": "default_claude_max_5x"},
                 "pro": {"subscriptionType": "pro"}}[tier]
        write(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".credentials.json"),
              json.dumps({"claudeAiOauth": oauth}))

    # -- helpers ---------------------------------------------------------- #
    def git_init(self, repo):
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "dev@example.com")
        git(repo, "config", "user.name", "Dev")
        git(repo, "config", "commit.gpgsign", "false")
        git(repo, "config", "core.autocrlf", "false")
        write(os.path.join(repo, "README.md"), "# demo\n")
        git(repo, "add", "README.md")
        git(repo, "commit", "-qm", "init")

    def run_install(self, *extra, **kw):
        repo = kw.get("repo", self.repo)
        argv = ["--project", repo, "--python", PY, "--pa3-dir", self.pa3]
        if kw.get("yes", True):
            argv.append("--yes")
        argv.extend(extra)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = install_main(argv)
        return rc, buf.getvalue()

    def install(self, *extra, **kw):
        rc, out = self.run_install("--no-commit", *extra, **kw)
        self.assertEqual(rc, 0, out)
        return out

    def path(self, *parts):
        return os.path.join(self.repo, *parts)

    def exists(self, rel, out=""):
        self.assertTrue(os.path.isfile(self.path(*rel.split("/"))),
                        "missing %s\n%s" % (rel, out))


# --------------------------------------------------------------------------- P1..P10

class TestFreshInstall(ProjectCase):

    def test_creates_the_installed_tree(self):
        out = self.install()
        got = steps(out)
        self.assertEqual(got.get("P1"), "DONE", out)
        for sid in ("P2", "P3", "P6", "P7"):
            self.assertEqual(got.get(sid), "DONE", out)
        self.assertEqual(got.get("P10"), "SKIP", out)        # --no-commit
        # 3.24 T3.1: derived, not pinned (was "124 created" before the Gen 3 loop left): every
        # file the install leaves on disk is one "created" except the repo's own README.md.
        made = 0
        for _d, ds, fs in os.walk(self.repo):
            ds[:] = [x for x in ds if x != ".git"]
            made += len(fs)
        self.assertIn("%d created" % (made - 1), out)

        for rel in (".claude/pa.json", ".claude/agents/pa-claude.md",   # 3.24 T3.1: was pa-session.md
                    ".claude/skills/project-architect/SKILL.md",
                    ".claude/settings.json", "tools/plan_edit.py", "tools/run.sh",
                    "tools/outline.py", "templates/task.template.md",
                    "templates/TASK_PROGRESS.template.md", "templates/research.template.md",
                    "templates/PHASE_PLAN.template.md",
                    "templates/GENERATION_PLAN.template.md",
                    "rules/INDEX.md", "rules/M4.md", "cookbook/INDEX.md",
                    "docs/project-architect.md",
                    "phase-ends/README.md", "phase-ends/TASK_INDEX.md",
                    "phase-ends/RESEARCH_INDEX.md", "phase-ends/LEGACY_INDEX.md",
                    "phase-ends/current/tasks/INDEX.md",
                    "phase-ends/current/research/INDEX.md",
                    "phase-ends/current/discussions/INDEX.md",
                    "CLAUDE.md", "HOW_WE_WORK.md", ".gitignore",
                    ".claude-state/memory/MEMORY.md"):
            self.exists(rel, out)
        self.assertIn("## Migration", read(self.path("docs", "project-architect.md")))
        for sub in ("tasks", "logs", "research", "discussions"):
            self.assertTrue(os.path.isdir(self.path("phase-ends", "current", sub)), sub)
        self.assertTrue(os.path.isdir(self.path(".run")))
        self.assertEqual(len(os.listdir(self.path(".claude", "agents"))),        # 3.24 T3.1: the package's
                         len(os.listdir(os.path.join(PKG, "agents"))))           # agents at max20 (was 19)

    def test_placeholders_are_filled(self):
        self.install()
        skill = read(self.path(".claude", "skills", "project-architect", "SKILL.md"))
        self.assertNotIn("{{", skill)
        self.assertIn("add headlines with tools/rules_add.py promote", skill)
        self.assertIn(PY.replace("\\", "/"), skill)

        pa_json = json.loads(read(self.path(".claude", "pa.json")))
        self.assertEqual(pa_json["project"], "demo")
        self.assertEqual(pa_json["python"], PY.replace("\\", "/"))
        self.assertTrue(pa_json["pa_version"].startswith("3."))
        self.assertRegex(pa_json["installed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_claude_md_is_small_and_names_the_project(self):
        self.install("--name", "Vantage", "--tagline", "a book-length thing")
        with open(self.path("CLAUDE.md"), "rb") as fh:
            raw = fh.read()
        self.assertLessEqual(len(raw), 1100, "CLAUDE.md is %d bytes" % len(raw))
        text = raw.decode("utf-8")
        self.assertNotIn("{{", text)
        self.assertIn("Vantage", text.splitlines()[0])
        self.assertIn("a book-length thing", text)
        self.assertIn("never push", text)

    def test_how_we_work_takes_the_developer_defaults(self):
        self.install()
        text = read(self.path("HOW_WE_WORK.md"))
        self.assertIn("# How we work — demo", text)
        self.assertNotIn("{{DEVELOPER_NAME}}", text)         # T10 (3.11): the interview's defaults
        self.assertIn("Dev", text)                           # git user.name
        self.assertIn("PY = %s" % PY.replace("\\", "/"), text)  # interpreter defined once
        self.assertIn("PY tools/launch.py", text)                     # tools rows use PY
        self.assertNotIn("{{PY}}", text)
        self.assertNotIn("{{PROJECT_NAME}}", text)

    def test_how_we_work_gets_the_card_from_constitution_and_environment(self):
        """T3.c2: the constitution's tagline and detect.environment() land in the
        card; --yes skips the TTY interview, so the card takes the interview's
        defaults (T10, 3.11: the placeholders were never filled and the first
        archive refused on them)."""
        write(self.path("PROJECT_CONTEXT.md"),
              "# demo — a card grown from the constitution\n\n"
              "## Quick Reference Card\n\n"
              "- `src/`: source code\n")
        self.install()
        text = read(self.path("HOW_WE_WORK.md"))
        project = text[text.find("## Project"):text.find("## Developer")]
        self.assertIn("a card grown from the constitution", project)
        env_section = text[text.find("## Environment"):text.find("## Docs map")]
        self.assertIn("OS / shells:", env_section)
        self.assertIn(PY.replace("\\", "/"), env_section)
        self.assertNotIn("{{OS_AND_SHELLS}}", env_section)
        developer = text[text.find("## Developer"):]
        developer = developer[:developer.find("\n## ", 1)]
        self.assertNotIn("{{", text)
        self.assertIn("Dev", developer)                                     # git user.name
        self.assertIn("advanced", developer)

    def test_the_bang_line_install_passes_the_card_check(self):
        """T10 (3.11): a skeleton constitution under --yes (the ! line): the **Project:**
        line is the tagline in CLAUDE.md and the card, no Quick Reference Card bullet
        lands in ## Paths, and tools/card.py check exits 0, so the first archive can run."""
        write(self.path("PROJECT_CONTEXT.md"),
              "# demo — Project Context & Roadmap\n\n"
              "## Quick Reference Card\n\n"
              "- **Project:** demo — a countdown for Python versions\n"
              "- **Stack:** Python 3.12+, standard library only (`json`, `datetime`)\n"
              "- **Environment:** see `HOW_WE_WORK.md`\n")
        self.install()
        claude_md = read(self.path("CLAUDE.md")).splitlines()
        self.assertEqual(claude_md[1], "a countdown for Python versions")
        text = read(self.path("HOW_WE_WORK.md"))
        project = text[text.find("## Project"):text.find("## Developer")]
        self.assertIn("a countdown for Python versions", project)
        self.assertNotIn("Project Context & Roadmap", project)
        paths = text[text.find("## Paths"):]
        paths = paths[:paths.find("\n## ", 1)]
        self.assertNotIn("**Stack:**", paths)
        self.assertNotIn("**Environment:**", paths)
        r = subprocess.run([PY, os.path.join("tools", "card.py"), "check"], cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_rules_seed_lands_whole(self):
        self.install()
        index = read(self.path("rules", "INDEX.md"))
        rules = [ln for ln in index.splitlines() if re.match(r"^[A-Z]+\d+ \| ", ln)]
        self.assertEqual(len(rules), 31, index)  # was 32 (3.25 T7: P7 removed)
        files = [n for n in os.listdir(self.path("rules")) if n != "INDEX.md"]
        self.assertEqual(len(files), 31)  # was 32 (3.25 T7: P7 removed)
        self.assertNotIn("INDEX.seed.md", files)

    def test_settings_merge_has_no_hooks(self):
        self.install()
        settings = json.loads(read(self.path(".claude", "settings.json")))
        self.assertIs(settings["todoFeatureEnabled"], False)
        self.assertNotIn("hooks", settings)
        self.assertNotIn("statusLine", settings)
        for key in ("agent", "modelSettings"):                                 # 3.24 T3.1: the pa-session
            self.assertNotIn(key, settings)                                     # pins left
        self.assertEqual(settings["model"], "claude-opus-5-5[1m]")              # was sonnet-5-5 (3.12 T1)
        self.assertEqual(settings["effortLevel"], "medium")
        allow = settings["permissions"]["allow"]
        self.assertIn("Bash(%s tools/*)" % PY.replace("\\", "/"), allow)      # 3.11 T15: was tools/*:*,
        self.assertIn("Bash(bash tools/*)", allow)                              # a literal * in Claude Code
        self.assertFalse([a for a in allow if a.endswith("tools/*:*)")], allow)
        self.assertIn("Bash(git push:*)", settings["permissions"]["deny"])

    def test_settings_merge_keeps_what_the_project_had(self):
        write(self.path(".claude", "settings.json"), json.dumps(
            {"model": "claude-sonnet-5", "permissions": {"allow": ["Bash(cargo test:*)"]},
             "modelSettings": {"claude-sonnet-5": {"effortLevel": "high"}},
             "env": {"RUST_BACKTRACE": "1", "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}},
            indent=2) + "\n")
        self.install()
        settings = json.loads(read(self.path(".claude", "settings.json")))
        self.assertEqual(settings["model"], "claude-sonnet-5")                # hand-set: kept
        self.assertEqual(settings["modelSettings"],                            # hand-set: kept; 3.24 T3.1:
                         {"claude-sonnet-5": {"effortLevel": "high"}})          # nothing shipped adds to it
        self.assertIn("Bash(cargo test:*)", settings["permissions"]["allow"])
        self.assertEqual(settings["env"]["RUST_BACKTRACE"], "1")
        self.assertNotIn("CLAUDE_CODE_ENABLE_TODO_TOOLS", settings["env"])      # fix-6
        self.assertIs(settings["todoFeatureEnabled"], False)
        # D3: backups now go under .run/backups/
        bak_dir = self.path(".run", "backups")
        self.assertTrue(os.path.isdir(bak_dir), "no .run/backups/ directory")
        backups = [n for n in os.listdir(bak_dir)
                   if n.startswith(".claude--settings.json.bak-")]
        self.assertEqual(len(backups), 1, backups)

    def test_settings_old_default_model_migrates(self):
        """3.24 T5: the shipped Sonnet 5 pins go, the 3.2 pins come; other keys stay. Was (3.12 T1):
        moved to Sonnet 5.5."""
        write(self.path(".claude", "settings.json"), json.dumps(
            {"model": "claude-sonnet-5[1m]",
             "modelSettings": {"claude-sonnet-5": {"effortLevel": "medium"},
                               "claude-fable-5-1": {"effortLevel": "high"}}}, indent=2) + "\n")
        self.install()
        settings = json.loads(read(self.path(".claude", "settings.json")))
        self.assertEqual(settings["model"], "claude-opus-5-5[1m]")            # was sonnet-5-5[1m]
        self.assertEqual(settings["modelSettings"],                            # hand-set high: kept
                         {"claude-fable-5-1": {"effortLevel": "high"}})

    def test_pa3_upgrade_migrates_only_the_old_model_defaults(self):
        """3.24 T5: a pa3 re-install removes the shipped pins, sets the 3.2 ones where absent and
        touches nothing else. Was (3.12 T1): Sonnet 5 -> Sonnet 5.5."""
        self.install()
        rel = self.path(".claude", "settings.json")
        settings = json.loads(read(rel))
        settings["model"] = "claude-sonnet-5[1m]"
        settings["modelSettings"] = {"claude-sonnet-5": {"effortLevel": "medium"}}
        settings["env"] = {"MINE": "1"}
        write(rel, json.dumps(settings, indent=2) + "\n")
        out = self.install()
        after = json.loads(read(rel))
        self.assertEqual(after["model"], "claude-opus-5-5[1m]", out)          # was sonnet-5-5[1m]
        self.assertNotIn("modelSettings", after)                               # emptied: dropped
        self.assertIn("restart: needed", out)                                  # a pin changed
        self.assertEqual(after["env"], {"MINE": "1"})                        # no merge on pa3
        out = self.install()
        self.assertEqual(json.loads(read(rel)), after)                        # idempotent
        self.assertIn("left alone", out)

    def test_pa3_upgrade_adds_absent_defaults_and_keeps_values(self):
        """3.22 T2d: a pa3 upgrade adds promptSuggestionEnabled/idleCompaction only when absent."""
        self.install()
        rel = self.path(".claude", "settings.json")
        settings = json.loads(read(rel))
        del settings["promptSuggestionEnabled"]
        settings["idleCompaction"] = True
        write(rel, json.dumps(settings, indent=2) + "\n")
        out = self.install()
        after = json.loads(read(rel))
        self.assertIs(after["promptSuggestionEnabled"], False, out)
        self.assertIs(after["idleCompaction"], True)                         # project's value kept
        self.assertIn("~ settings  promptSuggestionEnabled: null -> false", out)
        self.assertNotIn("idleCompaction:", out)
        self.assertEqual({k: v for k, v in after.items() if k != "promptSuggestionEnabled"},
                         settings)                                            # nothing else moved
        out = self.install()
        self.assertEqual(json.loads(read(rel)), after)                        # idempotent
        self.assertIn("left alone", out)

    def test_gitignore_block_is_appended_once(self):
        write(self.path(".gitignore"), "target/\n")
        self.install()
        text = read(self.path(".gitignore"))
        self.assertIn("target/", text)
        self.assertIn(".run/", text)
        self.install()
        self.assertEqual(read(self.path(".gitignore")).count("# Project Architect 3.0"), 1)

    def test_memory_stub_never_touches_an_existing_memory_dir(self):
        """Existing memory files are kept; the index is created alongside them."""
        write(self.path(".claude-state", "memory", "feedback-2025.md"), "old memory\n")
        self.install()
        # P6 creates MEMORY.md index even when the dir already exists
        self.assertTrue(os.path.isfile(self.path(".claude-state", "memory", "MEMORY.md")))
        self.assertEqual(read(self.path(".claude-state", "memory", "feedback-2025.md")),
                         "old memory\n")


class TestProjectScopeMerge(unittest.TestCase):
    """3.1 T9: model/modelSettings are add-if-absent for project settings, refused for the user merge."""

    def test_project_scope_adds_once_and_keeps_edits(self):
        from pa.install import settings_merge as sm

        snip = {"model": "claude-sonnet-5[1m]",
                "modelSettings": {"claude-sonnet-5": {"effortLevel": "medium"}}}
        merged, report = sm.merge({}, snip, scope="project")
        self.assertEqual(merged["model"], "claude-sonnet-5[1m]")
        self.assertEqual(merged["modelSettings"]["claude-sonnet-5"]["effortLevel"], "medium")
        self.assertIn("model", report["added"])
        edited = {"model": "claude-opus-4-6",
                  "modelSettings": {"claude-sonnet-5": {"effortLevel": "high"}}}
        merged, report = sm.merge(edited, snip, scope="project")
        self.assertEqual(merged["model"], "claude-opus-4-6")
        self.assertEqual(merged["modelSettings"]["claude-sonnet-5"]["effortLevel"], "high")
        self.assertEqual(report["added"], [])
        again, _ = sm.merge(merged, snip, scope="project")
        self.assertEqual(again, merged)                                     # idempotent
        self.assertRaises(sm.MergeError, sm.merge, {}, snip)                # user-level: refused


class TestSecondRun(ProjectCase):

    def test_second_run_changes_nothing(self):
        self.install()
        before = tree_hash(self.repo)
        out = self.install()
        self.assertEqual(tree_hash(self.repo), before, out)
        got = steps(out)
        self.assertEqual(got.get("P1"), "DONE", out)
        for sid in ("P2", "P3", "P6", "P7", "P10"):
            self.assertEqual(got.get(sid), "SKIP", out)
        self.assertIn("already current", out)

    def test_upgrade_refreshes_packaged_files_only(self):
        self.install()
        write(self.path("HOW_WE_WORK.md"), "# hand written\n")
        write(self.path("rules", "M4.md"), "# edited rule\n")
        agent = self.path(".claude", "agents", "pa-claude.md")              # 3.24 T3.1: was pa-session.md
        os.remove(agent)
        out = self.install()
        self.assertTrue(os.path.isfile(agent), out)
        self.assertEqual(read(self.path("HOW_WE_WORK.md")), "# hand written\n")
        self.assertEqual(read(self.path("rules", "M4.md")), "# edited rule\n")

    def test_upgrade_refreshes_the_methodology_with_force(self):
        # 3.3 T12: docs/project-architect.md is a packaged file; a pa3 upgrade restores it
        self.install()
        doc = self.path("docs", "project-architect.md")
        self.assertIn("## Migration", read(doc))
        write(doc, "# edited by the project\n")
        self.install()                                   # project-edited: left alone
        self.assertEqual(read(doc), "# edited by the project\n")
        self.install("--force")
        self.assertIn("## Migration", read(doc))

    def test_upgrade_prunes_retired_agents(self):
        self.install()
        stale = self.path(".claude", "agents", "coder-opus46.md")
        write_bytes(stale, past_agent("coder-opus46.md"))  # 3.24 T5: unedited
        self.assertTrue(os.path.isfile(stale))
        out = self.install()
        self.assertFalse(os.path.isfile(stale), out)
        self.assertIn("removed retired agent coder-opus46.md", out)

    def test_upgrade_prunes_retired_coder_sonnet(self):
        self.install()
        stale = self.path(".claude", "agents", "coder-sonnet.md")
        write_bytes(stale, past_agent("coder-sonnet.md"))  # 3.24 T5: unedited
        out = self.install()
        self.assertFalse(os.path.isfile(stale), out)
        self.assertIn("removed retired agent coder-sonnet.md", out)

    def test_upgrade_prunes_retired_expert_fable_high(self):
        self.install()
        stale = self.path(".claude", "agents", "expert-fable-high.md")
        write_bytes(stale, past_agent("expert-fable-high.md"))  # 3.24 T5: unedited
        out = self.install()
        self.assertFalse(os.path.isfile(stale), out)
        self.assertIn("removed retired agent expert-fable-high.md", out)

    def test_upgrade_prunes_the_per_model_role_agents(self):
        """3.25 T6a: pa-expert, pa-coder and pa-thinker replace five agents; an upgrade removes them."""
        self.install()
        old = ("expert-opus55.md", "expert-fable.md", "coder-opus55.md", "thinker.md", "deep-thinker.md")
        for name in old:
            write_bytes(self.path(".claude", "agents", name), past_agent(name))
        out = self.install()
        for name in old:
            self.assertFalse(os.path.isfile(self.path(".claude", "agents", name)), out)
            self.assertIn("removed retired agent %s" % name, out)
        for name in ("pa-expert.md", "pa-coder.md", "pa-thinker.md"):
            self.exists(".claude/agents/" + name, out)

    def test_upgrade_prunes_a_legacy_pa3_skill_only(self):
        self.install()
        legacy = self.path(".claude", "skills", "old-rules", "SKILL.md")
        write(legacy, "---\nname: old-rules\ndescription: " + detect_mod.SKILL_DESC
              + " agents.\n---\n# House rules\n")
        user = self.path(".claude", "skills", "my-skill", "SKILL.md")
        write(user, "---\nname: my-skill\ndescription: a user skill.\n---\n")
        out = self.install("--force")
        self.assertFalse(os.path.isdir(self.path(".claude", "skills", "old-rules")), out)
        self.assertIn("removed legacy PA3 skill old-rules", out)
        self.assertTrue(os.path.isfile(user), out)
        self.assertTrue(os.path.isfile(self.path(".claude", "skills", "project-architect",
                                                 "SKILL.md")), out)

    # -- T2.c2: the refresh path T8 found ------------------------------------ #
    def legacy_skill(self, body="# House rules\n"):
        return write(self.path(".claude", "skills", "old-rules", "SKILL.md"),
                     "---\nname: old-rules\ndescription: " + detect_mod.SKILL_DESC
                     + " agents.\n---\n" + body)

    def skill(self):
        return read(self.path(".claude", "skills", "project-architect", "SKILL.md"))

    def test_force_refresh_keeps_section_11_headlines(self):
        self.install()
        path = self.path(".claude", "skills", "project-architect", "SKILL.md")
        text = self.skill().replace(pj.NO_PROJECT_RULES, "- P11 — keep it short\n- P12 — two")
        write(path, text)
        out = self.install("--force")
        self.assertIn("## 11. Project rules\n- P11 — keep it short\n- P12 — two\n", self.skill(), out)
        self.assertNotIn(pj.NO_PROJECT_RULES, self.skill())

    def test_headlines_carried_from_a_pruned_legacy_skill(self):
        self.install()
        self.legacy_skill("# House rules\n## 10. Style\nx\n## 11. Project rules\n\n"
                          "- P13 — carried\n\n")
        out = self.install("--force")
        self.assertFalse(os.path.isdir(self.path(".claude", "skills", "old-rules")), out)
        self.assertIn("## 11. Project rules\n- P13 — carried\n", self.skill(), out)

    def test_refresh_commit_stages_the_prunes(self):
        rc, out = self.run_install()
        self.assertEqual(rc, 0, out)
        self.legacy_skill()
        write(self.path(".claude", "agents", "coder-opus46.md"), "---\nname: coder-opus46\n---\n")
        git(self.repo, "add", "--", ".claude")
        git(self.repo, "commit", "-qm", "old files")
        rc, out = self.run_install("--force")
        self.assertEqual(rc, 0, out)
        self.assertEqual(git(self.repo, "status", "--short").stdout.strip(), "", out)
        files = git(self.repo, "ls-files").stdout
        self.assertNotIn(".claude/skills/old-rules/", files)
        self.assertNotIn("coder-opus46.md", files)
        self.assertIn("- .claude/skills/old-rules/SKILL.md", out)

    def test_refresh_commit_stages_an_earlier_prune(self):
        rc, out = self.run_install()
        self.assertEqual(rc, 0, out)
        self.legacy_skill()
        write(self.path(".claude", "agents", "coder-opus46.md"), "---\nname: coder-opus46\n---\n")
        git(self.repo, "add", "--", ".claude")
        git(self.repo, "commit", "-qm", "old files")
        shutil.rmtree(self.path(".claude", "skills", "old-rules"))       # pruned, never staged
        os.remove(self.path(".claude", "agents", "coder-opus46.md"))
        rc, out = self.run_install()
        self.assertEqual(rc, 0, out)
        self.assertIn("staged earlier prune of .claude/skills/old-rules/SKILL.md", out)
        self.assertIn("staged earlier prune of .claude/agents/coder-opus46.md", out)
        self.assertEqual(git(self.repo, "status", "--short").stdout.strip(), "", out)

    def test_force_refresh_renames_the_skill_in_project_files(self):
        self.install()
        claude = write(self.path("CLAUDE.md"),
                       "# demo\nRoles: x. Rules: the `old-rules` skill.\nSee the `old-rules` notes.\n")
        p10 = write(self.path("rules", "P10.md"),
                    "# P10\nOnly a headline short enough to live in the `old-rules` skill is\n"
                    "kept; the `old-rules` skill dir is gone.\n")
        before = (read(claude), read(p10))
        self.install()                                                # no --force: left alone
        self.assertEqual((read(claude), read(p10)), before)
        out = self.install("--force")
        self.assertEqual(read(claude), "# demo\nRoles: x. Rules: the `project-architect` skill.\n"
                                       "See the `old-rules` notes.\n", out)
        self.assertEqual(read(p10), "# P10\nOnly a headline short enough to live in the "
                                    "`project-architect` skill is\n"
                                    "kept; the `old-rules` skill dir is gone.\n", out)
        self.assertIn("renamed the skill in CLAUDE.md", out)
        self.assertIn("renamed the skill in rules/P10.md", out)

    def test_project_edited_copies_are_kept_until_force(self):
        self.install()
        tool = self.path("tools", "plan_edit.py")
        write(tool, "# project's own edit\n")
        out = self.install()
        self.assertEqual(read(tool), "# project's own edit\n")
        self.assertIn("tools/plan_edit.py", out)
        self.assertIn("project-edited", out)
        self.install("--force")
        self.assertNotEqual(read(tool), "# project's own edit\n")
        self.assertIn("plan_edit.py", read(tool))


class TestInstalledAt(ProjectCase):
    """T10.1: pa.json's installed_at is stamped once and never moved."""

    def test_written_once_and_preserved(self):
        self.install()
        doc = json.loads(read(self.path(".claude", "pa.json")))
        doc["installed_at"] = "2020-01-01T00:00:00Z"
        write(self.path(".claude", "pa.json"), json.dumps(doc))

        opts = build_parser().parse_args(
            ["--project", self.repo, "--python", PY, "--pa3-dir", self.pa3, "--yes"])
        env = pj.detect_project(opts)
        text = pj.pa_json_text(env)
        self.assertIn('"2020-01-01T00:00:00Z"', text)


class TestTier(ProjectCase):
    """pa.json tier: existing value > interview answer > "max5"; one interview feeds both texts."""

    def _env(self, *extra):
        opts = build_parser().parse_args(
            ["--project", self.repo, "--python", PY, "--pa3-dir", self.pa3] + list(extra))
        return pj.detect_project(opts)

    def test_default_without_interview(self):
        self.install()
        self.assertEqual(json.loads(read(self.path(".claude", "pa.json")))["tier"], "max5")

    def test_existing_value_wins(self):
        self.install()
        doc = json.loads(read(self.path(".claude", "pa.json")))
        doc["tier"] = "max20"
        write(self.path(".claude", "pa.json"), json.dumps(doc))
        env = self._env()
        env._interview_answers = {"tier": "pro"}
        self.assertEqual(json.loads(pj.pa_json_text(env))["tier"], "max20")

    def test_interview_answer_and_cache(self):
        from unittest import mock
        env = self._env()
        answers = iter(["", "", "", "", "", "", "pro"])
        with mock.patch.object(pj.sys, "stdin", mock.Mock(isatty=lambda: True)), \
                mock.patch("builtins.input", side_effect=lambda _p: next(answers)) as ask:
            self.assertEqual(json.loads(pj.pa_json_text(env))["tier"], "pro")
            self.assertEqual(pj._interview(env)["tier"], "pro")      # cached: no second interview
        self.assertEqual(ask.call_count, len(pj._INTERVIEW_PROMPTS))

    def test_bad_answer_falls_back(self):
        from unittest import mock
        env = self._env()
        with mock.patch.object(pj.sys, "stdin", mock.Mock(isatty=lambda: True)), \
                mock.patch("builtins.input", return_value="max"):
            self.assertEqual(json.loads(pj.pa_json_text(env))["tier"], "max5")


class TestMod(ProjectCase):
    """3.22: pa.json keys, the mod settings, pa-claude/thinker/deep-thinker. 3.24 T3.1: always (no
    ``--mod off``); ``--mod on`` still parses, a no-op."""

    MOD = (".claude/agents/pa-claude.md", ".claude/agents/pa-thinker.md")   # 3.22 T8g: was claude.md (T8f), partner.md;
    # 3.25 T6a: pa-thinker was thinker and deep-thinker
    PLANNING = ".claude/skills/pa-planning/SKILL.md"                   # 3.23 T2: mod on only

    def _pa(self):
        return json.loads(read(self.path(".claude", "pa.json")))

    def _record(self):
        return json.loads(read(self.path(".claude", "pa3-managed.json")))["files"]

    def test_mod_on_adds_claude_and_thinkers(self):
        out = self.install()                                                # 3.24 T3.1: was --mod on
        self.assertEqual(len(os.listdir(self.path(".claude", "agents"))),   # 3.24 T3.1: the package's
                         len(os.listdir(os.path.join(PKG, "agents"))))      # agents at max20 (was 22)
        rec = self._record()
        for rel in self.MOD:
            self.exists(rel, out)
            self.assertIsNone(rec[rel]["user"])
        thinker = read(self.path(".claude", "agents", "pa-thinker.md"))
        self.assertIn("\nmodel: claude-opus-5-5[1m]\neffort: max\n", thinker)   # 3.25 T6a: the base row
        self.exists(self.PLANNING, out)                                  # 3.23 T2
        self.assertIn(self.PLANNING, rec)
        planning = read(self.path(*self.PLANNING.split("/")))
        self.assertNotIn("{{PY}}", planning)
        self.assertIn("tools/plan_edit.py lint", planning)

    def test_mod_on_thinker_at_pro(self):
        self.set_tier("pro")
        self.install()
        self.assertFalse(os.path.exists(self.path(".claude", "agents", "deep-thinker.md")))
        thinker = read(self.path(".claude", "agents", "pa-thinker.md"))   # 3.27 T11: Pro's own row
        self.assertIn("\nmodel: claude-opus-5-5[1m]\neffort: medium\n", thinker)

    def test_mod_on_pa_json_and_settings(self):
        self.install("--mod", "on")                                         # 3.24 T3.1: still parses
        pa = self._pa()
        self.assertEqual((pa["mod"], pa["upgrade"]), ("on", "auto"))         # 3.24 T5: was ask
        self.assertNotIn("warmer", pa)                                      # 3.24 T3.1: retired
        st = json.loads(read(self.path(".claude", "settings.json")))
        for key in ("agent", "modelSettings"):
            self.assertNotIn(key, st)
        self.assertEqual(st["model"], "claude-opus-5-5[1m]")                # 3.23 T13d.c8
        self.assertEqual(st["effortLevel"], "medium")
        self.assertIs(st["showThinkingSummaries"], True)
        self.assertIs(st["promptSuggestionEnabled"], False)
        self.assertIs(st["precomputeCompactionEnabled"], False)
        self.assertIs(st["autoContinueAtUsageLimit"], True)                  # 3.23 T2
        self.assertTrue(pa["bash"])
        if os.name == "nt" and not os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") and pa["bash"] != "bash":
            self.assertTrue(pa["bash"].endswith("/bin/bash.exe"), pa["bash"])
        self.assertEqual(st["permissions"]["defaultMode"], "auto")
        for key in ("todoFeatureEnabled", "autoCompactEnabled", "awaySummaryEnabled",
                    "idleCompaction"):
            self.assertIs(st[key], False, key)
        self.assertIn("BASH_MAX_TIMEOUT_MS", st["env"])

    def test_reinstall_carries_mod_and_upgrade(self):
        self.install()
        doc = self._pa()
        doc["upgrade"] = "auto"
        doc["bash"] = "C:/custom/bash.exe"                                 # 3.23 T2: kept
        write(self.path(".claude", "pa.json"), json.dumps(doc))
        opts = build_parser().parse_args(
            ["--project", self.repo, "--python", PY, "--pa3-dir", self.pa3, "--yes"])
        env = pj.detect_project(opts)
        got = json.loads(pj.pa_json_text(env))
        self.assertEqual((got["mod"], got["upgrade"], got["bash"]), ("on", "auto", "C:/custom/bash.exe"))

    def test_mod_snippet_leaves_the_shipped_snippet(self):
        from pa.install import settings_merge as sm
        snip = sm.load_snippet(name="project")
        before = json.dumps(snip, sort_keys=True)
        out = pj.mod_snippet(snip)
        self.assertEqual(json.dumps(snip, sort_keys=True), before)
        for key in ("agent", "model", "modelSettings", "effortLevel"):      # 3.24 T3.1: the shipped
            self.assertNotIn(key, snip)                                     # snippet has no pins
        self.assertEqual((out["model"], out["effortLevel"]), ("claude-opus-5-5[1m]", "medium"))

    def test_pro_partner_pin(self):
        """3.27 T11: Pro pins the partner to Sonnet 5.5; a shipped Opus pin moves; a hand-edited one stays."""
        rel = self.path(".claude", "settings.json")
        self.set_tier("pro")
        self.install()
        st = json.loads(read(rel))
        self.assertEqual((st["model"], st["effortLevel"]), ("claude-sonnet-5-5[1m]", "medium"))
        st["model"] = "claude-opus-5-5[1m]"                                 # another tier's shipped pin
        write(rel, json.dumps(st, indent=2) + "\n")
        self.install()
        self.assertEqual(json.loads(read(rel))["model"], "claude-sonnet-5-5[1m]")
        st["model"] = "claude-fable-5-1[1m]"                                # hand-edited: kept
        write(rel, json.dumps(st, indent=2) + "\n")
        self.install()
        self.assertEqual(json.loads(read(rel))["model"], "claude-fable-5-1[1m]")

    def test_max20_partner_pin(self):
        self.set_tier("max20")
        self.install()
        st = json.loads(read(self.path(".claude", "settings.json")))
        self.assertEqual((st["model"], st["effortLevel"]), ("claude-opus-5-5[1m]", "medium"))


class TestRetireAndDryRun(ProjectCase):

    def test_pre_pa3_claude_md_is_retired(self):
        write(self.path("CLAUDE.md"), "# demo project\n\nRead everything first.\n")
        git(self.repo, "add", "CLAUDE.md")
        git(self.repo, "commit", "-qm", "claude")
        rc, out = self.run_install()                    # commit: the git mv is visible
        self.assertEqual(rc, 0, out)
        self.exists("docs/retired/CLAUDE.pre-pa3.md", out)
        self.assertIn("Read everything first.",
                      read(self.path("docs", "retired", "CLAUDE.pre-pa3.md")))
        new = read(self.path("CLAUDE.md"))
        self.assertIn("Project Architect 3.0", new.splitlines()[0])
        rc = git(self.repo, "log", "--follow", "--oneline", "--",
                 "docs/retired/CLAUDE.pre-pa3.md")
        self.assertIn("claude", rc.stdout)          # git mv kept the history

    def test_dry_run_writes_nothing(self):
        before = entries(self.repo)
        rc, out = self.run_install("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(entries(self.repo), before, out)
        self.assertIn("dry run -- nothing was written", out)
        self.assertIn("would write", out)
        self.assertIn(".claude/agents/pa-claude.md", out)                  # 3.24 T3.1: was pa-session.md

    def test_dry_run_keeps_a_retired_agent(self):
        self.install()
        stale = self.path(".claude", "agents", "coder-opus46.md")
        write_bytes(stale, past_agent("coder-opus46.md"))  # 3.24 T5: unedited
        rc, out = self.run_install("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(stale), out)
        self.assertIn("would remove retired agent coder-opus46.md", out)

    def test_refuses_outside_a_git_repo_without_yes(self):
        """Without --yes and no TTY, git_checks declines to init and detect_project fails."""
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        rc, out = self.run_install(repo=plain, yes=False)
        self.assertEqual(rc, 1, out)
        self.assertIn("not a git repository", out)


class TestCommit(ProjectCase):

    def test_one_commit_without_a_trailer(self):
        rc, out = self.run_install()
        self.assertEqual(rc, 0, out)
        log = git(self.repo, "log", "--format=%H|%s", "-n", "5").stdout.strip().splitlines()
        self.assertEqual(len(log), 2, log)                   # init + install
        self.assertEqual(log[0].split("|", 1)[1], "chore: install Project Architect 3.0")
        body = git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertNotIn("Co-Authored-By", body)
        self.assertNotIn("Generated with", body)
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout.strip(), "")
        names = git(self.repo, "show", "--name-only", "--format=", "HEAD").stdout
        self.assertIn(".claude/pa.json", names)
        self.assertIn("rules/INDEX.md", names)
        self.assertNotIn(".run/", names)

    def test_no_commit_leaves_the_tree_dirty(self):
        self.install()
        status = git(self.repo, "status", "--porcelain").stdout
        self.assertIn("CLAUDE.md", status)
        self.assertEqual(len(git(self.repo, "log", "--oneline").stdout.strip().splitlines()), 1)


# --------------------------------------------------------------------------- detect

class TestDetect(ProjectCase):

    def make(self, name, files=(), dirs=()):
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        for rel in dirs:
            os.makedirs(os.path.join(root, rel.replace("/", os.sep)), exist_ok=True)
        for rel in files:
            write(os.path.join(root, rel.replace("/", os.sep)), "# %s\n" % rel)
        return root

    def test_fresh_before_and_pa3_after(self):
        self.assertEqual(detect_mod.detect(self.repo), "fresh")
        self.install()
        self.assertEqual(detect_mod.detect(self.repo), "pa3")

    def test_stock20(self):
        root = self.make("stock", files=("CLAUDE.md", "RULES_REGISTRY.md",
                                         "phase-ends/PhaseEnd_Phase1.md"))
        self.assertEqual(detect_mod.detect(root), "stock20")

    def test_onex(self):
        root = self.make("onex", dirs=("Project Context Markdowns",))
        self.assertEqual(detect_mod.detect(root), "onex")
        root2 = self.make("onex2", files=("Project Context Markdowns/Vantage_Rules_Registry.md",
                                          "Project Context Markdowns/Vantage_Project_Context.md",
                                          "PhaseEnd_Phase11_5_7.md",
                                          "PhaseEnd_PhaseGen3_F_7.md"))
        self.assertEqual(detect_mod.detect(root2), "onex")

    def test_overlay(self):
        root = self.make("overlay", dirs=("decomp-architect",))
        self.assertEqual(detect_mod.detect(root), "overlay")
        root2 = self.make("overlay2", files=("DIGEST.md",))
        self.assertEqual(detect_mod.detect(root2), "overlay")

    def test_an_installed_tree_is_never_mistaken_for_a_migration(self):
        self.install()
        sig = detect_mod.signals(self.repo)
        self.assertEqual(sig["loose_phaseends"], [])        # templates/PhaseEnd.template.md
        self.assertEqual(sig["registry_like"], [])
        self.assertEqual(sig["context_like"], [])           # templates/*.skeleton.md
        self.assertEqual(detect_mod.classify(sig), "pa3")
        os.remove(self.path(".claude", "pa.json"))
        shutil.rmtree(self.path(".claude", "agents"))
        self.assertEqual(detect_mod.detect(self.repo), "fresh")   # never a migration path

    def test_a_legacy_pa3_skill_is_pa3(self):
        root = self.make("legacy", files=(".claude/agents/router.md",))
        write(os.path.join(root, ".claude", "skills", "old-rules", "SKILL.md"),
              "---\nname: old-rules\ndescription: " + detect_mod.SKILL_DESC + " agents.\n---\n")
        self.assertEqual(detect_mod.legacy_skills(root), ["old-rules"])
        self.assertEqual(detect_mod.detect(root), "pa3")

    def test_every_label_is_covered(self):
        self.assertEqual(sorted(detect_mod.LABEL), sorted(detect_mod.LAYOUTS))


# --------------------------------------------------------------------------- fixtures

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures")


def fixture_copy(name, tmp):
    """Copy a named fixture to *tmp*/<name>, ``git init`` + one commit; return the path."""
    src = os.path.join(FIXTURES_DIR, name)
    dst = os.path.join(tmp, name)
    shutil.copytree(src, dst)
    git(dst, "init", "-q")
    git(dst, "config", "user.email", "test@example.com")
    git(dst, "config", "user.name", "Test")
    git(dst, "config", "commit.gpgsign", "false")
    git(dst, "config", "core.autocrlf", "false")
    git(dst, "add", ".")
    git(dst, "commit", "-qm", "fixture")
    return dst


class TestResolvePackage(ProjectCase):
    """--project from the installed root reads the package from VERSION source (3.9 T2)."""

    def empty_root(self):
        root = os.path.join(self.tmp, "installed-root")
        os.makedirs(os.path.join(root, "pa"), exist_ok=True)
        return root

    def fake_package(self, root):
        write(os.path.join(root, "skills", "project-architect", "SKILL.template.md"), "x\n")
        os.makedirs(os.path.join(root, "templates"), exist_ok=True)
        os.makedirs(os.path.join(root, "agents"), exist_ok=True)
        return root

    def test_resolve_package_falls_back_to_the_version_source(self):
        from unittest import mock
        write(os.path.join(self.pa3, "VERSION"), "version: 3.0.0-test\nsource: %s\n"
              % os.path.dirname(PKG).replace("\\", "/"))
        with mock.patch("pa.install.project.package_root", return_value=self.empty_root()):
            out = self.install("--no-clone")
        self.assertEqual(steps(out).get("P1"), "DONE", out)
        m = re.search(r"package\s+(\S+)", out)
        self.assertIsNotNone(m, out)
        self.assertEqual(os.path.normcase(os.path.normpath(m.group(1))),
                         os.path.normcase(os.path.normpath(PKG)), out)

    def test_resolve_package_fails_cleanly_without_a_source(self):
        from unittest import mock
        before = entries(self.repo)
        with mock.patch("pa.install.project.package_root", return_value=self.empty_root()):
            rc, out = self.run_install("--no-commit", "--no-clone")
        self.assertNotEqual(rc, 0, out)
        self.assertRegex(out, r"P1\s+detect\s+FAIL")
        self.assertIn("package not found", out)
        self.assertEqual(entries(self.repo), before)

    def test_resolve_package_branches(self):
        script = self.fake_package(os.path.join(self.tmp, "script"))
        self.assertEqual(pj.resolve_package(self.pa3, script), script)
        empty = self.empty_root()
        self.assertIsNone(pj.resolve_package(self.pa3, empty))
        clone = os.path.join(self.tmp, "clone")
        pkg = self.fake_package(os.path.join(clone, "project-architect-3.0"))
        write(os.path.join(self.pa3, "VERSION"), "version: 3.0.0-test\nsource: %s\n"
              % clone.replace("\\", "/"))
        self.assertEqual(os.path.normpath(pj.resolve_package(self.pa3, empty)),
                         os.path.normpath(pkg))
        bare = self.fake_package(os.path.join(self.tmp, "bare"))
        write(os.path.join(self.pa3, "VERSION"), "version: 3.0.0-test\nsource: %s\n"
              % bare.replace("\\", "/"))
        self.assertEqual(os.path.normpath(pj.resolve_package(self.pa3, empty)),
                         os.path.normpath(bare))


class TestCloneTarget(ProjectCase):
    """3.9 T3: dry run never clones; VERSION ``source:`` clone is maintained in place."""

    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.tmp, "origin")
        os.makedirs(self.origin)
        self.git_init(self.origin)
        self.named = os.path.join(self.tmp, "shared", "pa3-src")
        subprocess.run(["git", "clone", "-q", self.origin, self.named],
                       capture_output=True, text=True)
        write(os.path.join(self.pa3, "VERSION"), "version: 3.0.0-test\nsource: %s\n"
              % self.named.replace("\\", "/"))
        self.cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfg)
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_CONFIG_DIR", old)
                        if old is not None else os.environ.pop("CLAUDE_CONFIG_DIR", None))

    def opts(self, *extra):
        return build_parser().parse_args(
            ["--project", self.repo, "--python", PY, "--pa3-dir", self.pa3, "--yes"]
            + list(extra))

    def test_dry_run_never_calls_ensure_clone(self):
        from unittest import mock
        for extra in ((), ("--source", self.origin)):
            with mock.patch("pa.install.root.ensure_clone") as ec:
                with redirect_stdout(io.StringIO()):
                    pj.detect_project(self.opts("--dry-run", *extra))
            ec.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "pa3-src")))

    def test_version_source_clone_is_maintained_in_place(self):
        from unittest import mock
        from pa.install import root as root_mod
        with mock.patch("pa.install.root.ensure_clone", wraps=root_mod.ensure_clone) as ec:
            with redirect_stdout(io.StringIO()):
                env = pj.detect_project(self.opts())
        self.assertIsNotNone(env)
        self.assertEqual(ec.call_count, 1)
        info_env = ec.call_args[0][0]
        target = os.path.join(info_env.config_dir, info_env._clone_dir_name)
        self.assertEqual(os.path.normcase(os.path.normpath(target)),
                         os.path.normcase(os.path.normpath(self.named)))
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "pa3-src")))

    def test_bootstrap_line_names_the_phase_ends_dir(self):
        with redirect_stdout(io.StringIO()):
            env = pj.detect_project(self.opts("--no-clone"))
        env.phase_ends_dir = "Project Context Markdowns"
        buf = io.StringIO()
        with redirect_stdout(buf):
            pj.bootstrap(env)
        self.assertIn("Project Context Markdowns/current/{", buf.getvalue())
        self.assertNotIn("phase-ends/current/{", buf.getvalue())


class TestFixtureLayouts(unittest.TestCase):
    """Detect classifies each fixture as its intended layout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-fix-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_stock20(self):
        root = fixture_copy("stock20", self.tmp)
        self.assertEqual(detect_mod.detect(root), "stock20")

    def test_onex(self):
        root = fixture_copy("onex", self.tmp)
        self.assertEqual(detect_mod.detect(root), "onex")

    def test_overlay(self):
        root = fixture_copy("overlay", self.tmp)
        self.assertEqual(detect_mod.detect(root), "overlay")


class TestMigrationRefusal(ProjectCase):

    def refuse(self, name, files=(), dirs=()):
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        for rel in dirs:
            os.makedirs(os.path.join(root, rel), exist_ok=True)
        for rel in files:
            write(os.path.join(root, rel.replace("/", os.sep)), "# %s\n" % rel)
        self.git_init(root)
        before = entries(root)
        rc, out = self.run_install(repo=root)
        self.assertEqual(rc, 1, out)
        self.assertIn("not implemented", out)
        self.assertEqual(entries(root), before, out)
        return out

    # stock20 migration is now implemented (T4); tested in test_install_stock20.py
    # onex migration is now implemented (T6); tested in test_install_onex.py
    # overlay migration is now implemented (T7); tested in test_install_overlay.py


# --------------------------------------------------------------------------- memory link (P6)

def _is_link(path):
    """True when *path* is a symlink or a Windows junction."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    if sys.platform.startswith("win") or os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


class TestMemoryLink(ProjectCase):
    """P6: .claude-state/memory/ linked from the projects dir."""

    def setUp(self):
        super().setUp()
        # redirect home so paths.projects_dir() resolves to a temp location
        self.fake_home = os.path.join(self.tmp, "home")
        os.makedirs(self.fake_home)
        self._old_home = os.environ.get("USERPROFILE")
        self._old_home2 = os.environ.get("HOME")
        os.environ["USERPROFILE"] = self.fake_home
        os.environ["HOME"] = self.fake_home

    def tearDown(self):
        if self._old_home is not None:
            os.environ["USERPROFILE"] = self._old_home
        else:
            os.environ.pop("USERPROFILE", None)
        if self._old_home2 is not None:
            os.environ["HOME"] = self._old_home2
        else:
            os.environ.pop("HOME", None)
        super().tearDown()

    def _proj_mem(self):
        from pa import paths as pp
        slug = pp.project_slug(self.repo.replace("\\", "/"))
        return os.path.join(self.fake_home, ".claude", "projects", slug, "memory")

    def test_fresh_install_creates_link(self):
        self.install()
        repo_mem = self.path(".claude-state", "memory")
        self.assertTrue(os.path.isdir(repo_mem))
        self.assertTrue(os.path.isfile(os.path.join(repo_mem, "MEMORY.md")))
        proj_mem = self._proj_mem()
        self.assertTrue(_is_link(proj_mem), "expected link at %s" % proj_mem)
        # INVENTORY.md is written
        self.assertTrue(os.path.isfile(os.path.join(repo_mem, "INVENTORY.md")))

    def test_move_on_first_link(self):
        """A pre-existing real directory's files are moved into the repo."""
        proj_mem = self._proj_mem()
        os.makedirs(proj_mem, exist_ok=True)
        write(os.path.join(proj_mem, "old-note.md"), "# old note\n")
        self.install()
        repo_mem = self.path(".claude-state", "memory")
        self.assertTrue(os.path.isfile(os.path.join(repo_mem, "old-note.md")),
                        "old-note.md should be moved into the repo")
        self.assertTrue(_is_link(proj_mem))

    def test_first_link_keeps_the_arriving_index(self):
        """T5 expert: an index arriving from the projects dir is the index; a stub gives way."""
        from pa.install import memory_sort as ms
        write(self.path(".claude-state", "memory", "MEMORY.md"), ms.MEMORY_INDEX)
        proj_mem = self._proj_mem()
        os.makedirs(proj_mem, exist_ok=True)
        write(os.path.join(proj_mem, "MEMORY.md"), "- [B](b.md) -- beta\n")
        write(os.path.join(proj_mem, "b.md"), "# b\n")
        self.install()
        repo_mem = self.path(".claude-state", "memory")
        self.assertEqual(read(os.path.join(repo_mem, "MEMORY.md")), "- [B](b.md) -- beta\n")
        self.assertTrue(os.path.isfile(os.path.join(repo_mem, "b.md")))
        self.assertEqual([n for n in os.listdir(repo_mem) if n.startswith("MEMORY.")], ["MEMORY.md"])
        self.assertTrue(_is_link(proj_mem))

    def test_collision_rename(self):
        """A name collision keeps both: the incoming file gets a machine tag."""
        # pre-create a file in the repo memory dir
        write(self.path(".claude-state", "memory", "notes.md"), "# repo version\n")
        # pre-create the same filename in the projects dir
        proj_mem = self._proj_mem()
        os.makedirs(proj_mem, exist_ok=True)
        write(os.path.join(proj_mem, "notes.md"), "# projects version\n")
        self.install()
        repo_mem = self.path(".claude-state", "memory")
        # both should exist: the original and the renamed one
        self.assertTrue(os.path.isfile(os.path.join(repo_mem, "notes.md")))
        from pa import paths as pp
        tag = pp.machine_tag()
        renamed = os.path.join(repo_mem, "notes.%s.md" % tag)
        self.assertTrue(os.path.isfile(renamed),
                        "expected collision-renamed file at %s" % renamed)
        self.assertEqual(read(os.path.join(repo_mem, "notes.md")), "# repo version\n")
        self.assertIn("projects version", read(renamed))

    def test_index_merge_on_collision(self):
        """When both sides have MEMORY.md, the index lines are merged."""
        write(self.path(".claude-state", "memory", "MEMORY.md"),
              "# repo index\n- [A](a.md) -- alpha\n")
        proj_mem = self._proj_mem()
        os.makedirs(proj_mem, exist_ok=True)
        write(os.path.join(proj_mem, "MEMORY.md"),
              "# proj index\n- [B](b.md) -- beta\n")
        self.install()
        idx = read(self.path(".claude-state", "memory", "MEMORY.md"))
        self.assertIn("- [A](a.md)", idx)
        self.assertIn("- [B](b.md)", idx)

    def test_skip_on_second_run(self):
        self.install()
        out = self.install()
        got = steps(out)
        self.assertEqual(got.get("P6"), "SKIP", out)

    def test_dry_run_writes_nothing(self):
        before = entries(self.repo)
        rc, out = self.run_install("--dry-run")
        self.assertEqual(rc, 0, out)
        proj_mem = self._proj_mem()
        self.assertFalse(_is_link(proj_mem))

    def test_inventory_md_rows(self):
        """INVENTORY.md has a row for every .md file."""
        write(self.path(".claude-state", "memory", "rule-no-push.md"), "type: rule\n# no push\n")
        write(self.path(".claude-state", "memory", "who-is-dev.md"), "# dev profile\n")
        self.install()
        inv = read(self.path(".claude-state", "memory", "INVENTORY.md"))
        self.assertIn("rule-no-push.md", inv)
        self.assertIn("who-is-dev.md", inv)
        self.assertIn("MEMORY.md", inv)
        self.assertIn("| rule |", inv)  # guess for rule-no-push.md


# --------------------------------------------------------------------------- ledger slices (P9)

def _write_gz_jsonl(path, rows):
    import gzip
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class TestLedgerSlices(ProjectCase):
    """P9: ``.claude-state/ledger/*.jsonl.gz`` imported into the local ledger (T8.c2)."""

    def _slice_path(self):
        return self.path(".claude-state", "ledger", "win-phase1.jsonl.gz")

    def _sessions_rows(self):
        from pa import db, paths as pp
        conn = db.connect(pp.db_path())
        try:
            return conn.execute("SELECT session_id FROM sessions").fetchall()
        finally:
            db.close(conn)

    def test_slice_is_imported(self):
        _write_gz_jsonl(self._slice_path(), [
            {"type": "header", "schema": 2},
            {"table": "sessions", "session_id": "sess-1", "account": "a@example.com",
             "machine": "win", "project": self.repo, "cwd": self.repo,
             "started": "2026-09-01T00:00:00Z"},
        ])
        out = self.install()
        got = steps(out)
        self.assertEqual(got.get("P9"), "DONE", out)
        rows = self._sessions_rows()
        self.assertEqual([r["session_id"] for r in rows], ["sess-1"])

    def test_second_install_is_nothing_new(self):
        _write_gz_jsonl(self._slice_path(), [
            {"type": "header", "schema": 2},
            {"table": "sessions", "session_id": "sess-1", "account": "a@example.com",
             "machine": "win", "project": self.repo, "cwd": self.repo,
             "started": "2026-09-01T00:00:00Z"},
        ])
        self.install()
        out = self.install()
        self.assertEqual(steps(out).get("P9"), "SKIP", out)
        self.assertIn("nothing new", out)

    def test_no_slices_is_skip(self):
        out = self.install()
        self.assertEqual(steps(out).get("P9"), "SKIP", out)
        self.assertIn("no slices", out)


# --------------------------------------------------------------------------- misc

class TestSafety(ProjectCase):

    def test_nothing_is_written_under_the_real_claude_dir(self):
        home = os.path.expanduser("~")
        claude = os.path.join(home, ".claude")
        before = os.path.getmtime(claude) if os.path.isdir(claude) else None
        self.install()
        after = os.path.getmtime(claude) if os.path.isdir(claude) else None
        self.assertEqual(before, after)

    def test_every_written_file_is_utf8_lf(self):
        self.install()
        for dirpath, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                full = os.path.join(dirpath, name)
                with open(full, "rb") as fh:
                    raw = fh.read()
                rel = os.path.relpath(full, self.repo)
                self.assertNotIn(b"\r\n", raw, rel)
                raw.decode("utf-8")

    def test_parser_exposes_the_project_flags(self):
        opts = build_parser().parse_args(
            ["--project", ".", "--name", "x", "--tagline", "y", "--force", "--no-commit"])
        self.assertEqual(opts.project, ".")
        self.assertEqual(opts.name, "x")
        self.assertEqual(opts.tagline, "y")
        self.assertTrue(opts.force)
        self.assertTrue(opts.no_commit)

    def test_root_and_project_are_separate_runs(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            install_main(["--root", "--project", "."])
        self.assertIn("separate runs", err.getvalue())

    def test_gitignore_helper_is_idempotent(self):
        first = pj.gitignore_text("target/\n")
        self.assertTrue(first.endswith(pj.GITIGNORE_BLOCK))
        self.assertIsNone(pj.gitignore_text(first))


_HAS_GIT = shutil.which("git") is not None


@unittest.skipUnless(_HAS_GIT, "git not on PATH")
class TestGitChecks(ProjectCase):
    """git_checks: the refusal, the git-init offer, and the safecrlf line."""

    def _opts(self, **kw):
        """A minimal opts namespace."""
        defaults = {"dry_run": False, "yes": False, "force": False, "project": ".",
                    "python": PY, "pa3_dir": self.pa3, "no_clone": True, "source": None}
        defaults.update(kw)
        import types
        return types.SimpleNamespace(**defaults)

    def test_refuse_home(self):
        home = os.path.expanduser("~")
        msg = pj.git_checks(home, self._opts())
        self.assertIsNotNone(msg)
        self.assertIn("refusing to govern", msg)

    def test_refuse_downloads(self):
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(dl):
            os.makedirs(dl, exist_ok=True)
        msg = pj.git_checks(dl, self._opts())
        self.assertIsNotNone(msg)
        self.assertIn("refusing to govern", msg)

    def test_refuse_drive_root(self):
        root = "C:\\" if os.name == "nt" else "/"
        msg = pj.git_checks(root, self._opts())
        self.assertIsNotNone(msg)
        self.assertIn("refusing to govern", msg)

    def test_git_init_with_yes(self):
        empty = os.path.join(self.tmp, "empty-proj")
        os.makedirs(empty)
        buf = io.StringIO()
        with redirect_stdout(buf):
            msg = pj.git_checks(empty, self._opts(yes=True))
        self.assertIsNone(msg)
        self.assertTrue(os.path.isdir(os.path.join(empty, ".git")))

    def test_git_init_dry_run(self):
        empty = os.path.join(self.tmp, "empty-proj2")
        os.makedirs(empty)
        buf = io.StringIO()
        with redirect_stdout(buf):
            msg = pj.git_checks(empty, self._opts(dry_run=True))
        self.assertIsNone(msg)
        self.assertFalse(os.path.isdir(os.path.join(empty, ".git")))

    def test_safecrlf_set(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            msg = pj.git_checks(self.repo, self._opts())
        self.assertIsNone(msg)
        proc = subprocess.run(["git", "-C", self.repo, "config", "core.safecrlf"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.stdout.strip(), "false")



class TestBashPath(unittest.TestCase):
    """3.23 T10: pa.json ``bash`` finds Git Bash from either git location, never a bare ``bash`` on Windows."""

    def _git_tree(self, root):
        for rel in ("Git/bin/bash.exe", "Git/cmd/git.exe", "Git/mingw64/bin/git.exe"):
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        return os.path.join(root, "Git", "bin", "bash.exe").replace("\\", "/")

    def _with(self, which, name="nt", env=None):
        from unittest import mock
        with mock.patch.object(pj.os, "name", name),                 mock.patch.object(pj.shutil, "which", return_value=which),                 mock.patch.dict(pj.os.environ, env or {}, clear=False):
            if not env:
                pj.os.environ.pop("CLAUDE_CODE_GIT_BASH_PATH", None)
            return pj._bash_path()

    def test_cmd_and_mingw64_git_both_find_bin_bash(self):
        with tempfile.TemporaryDirectory() as root:
            want = self._git_tree(root)
            self.assertEqual(self._with(os.path.join(root, "Git", "cmd", "git.exe")), want)
            self.assertEqual(self._with(os.path.join(root, "Git", "mingw64", "bin", "git.exe")), want)

    def test_no_git_bash_is_empty_not_bare(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self._with(os.path.join(root, "nowhere", "git.exe")), "")
            self.assertEqual(self._with(None), "")

    def test_env_wins_and_posix_is_bash(self):
        self.assertEqual(self._with(None, env={"CLAUDE_CODE_GIT_BASH_PATH": "D:/G/bin/bash.exe"}), "D:/G/bin/bash.exe")
        self.assertEqual(self._with(None, name="posix"), "bash")

if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
