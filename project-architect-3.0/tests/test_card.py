"""tests for tools/card.py, detect.environment() and howwework.write().

A fresh fixture project with a PROJECT_CONTEXT.md gets a card whose
Environment, Build / run / test and Project sections come from environment()
and the constitution; interview --defaults leaves zero placeholders;
slice expert prints the expert-tagged sections only; check validates.

    cd project-architect-3.0 && python -m unittest tests.test_card -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PKG, "tools")
sys.path.insert(0, PKG)
sys.path.insert(0, TOOLS)

from pa.install.detect import environment  # noqa: E402

PY = sys.executable


def _template_text():
    tmpl = os.path.join(PKG, "templates", "HOW_WE_WORK.template.md")
    with open(tmpl, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


class TestEnvironment(unittest.TestCase):
    """detect.environment() returns the expected keys."""

    def test_keys(self):
        env = environment()
        for key in ("os", "shells", "interpreter", "py_version",
                     "git_version", "build_guess", "test_guess", "run_guess"):
            self.assertIn(key, env, "missing key: %s" % key)

    def test_interpreter(self):
        env = environment()
        self.assertEqual(env["interpreter"], sys.executable)

    def test_py_version(self):
        env = environment()
        self.assertEqual(env["py_version"], "%d.%d.%d" % sys.version_info[:3])

    def test_build_guess_python_project(self):
        tmp = tempfile.mkdtemp(prefix="pa3-env-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write(os.path.join(tmp, "pyproject.toml"), "[tool.pytest]\n")
        env = environment(tmp)
        self.assertEqual(env["test_guess"], "python -m pytest")

    def test_build_guess_node_project(self):
        tmp = tempfile.mkdtemp(prefix="pa3-env-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write(os.path.join(tmp, "package.json"), "{}\n")
        env = environment(tmp)
        self.assertEqual(env["build_guess"], "npm run build")
        self.assertEqual(env["test_guess"], "npm test")

    def test_build_guess_cargo(self):
        tmp = tempfile.mkdtemp(prefix="pa3-env-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write(os.path.join(tmp, "Cargo.toml"), "[package]\n")
        env = environment(tmp)
        self.assertEqual(env["build_guess"], "cargo build")
        self.assertEqual(env["test_guess"], "cargo test")

    def test_build_guess_go(self):
        tmp = tempfile.mkdtemp(prefix="pa3-env-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write(os.path.join(tmp, "go.mod"), "module example\n")
        env = environment(tmp)
        self.assertEqual(env["build_guess"], "go build ./...")
        self.assertEqual(env["test_guess"], "go test ./...")


class TestCardFixtureProject(unittest.TestCase):
    """A fixture project with PROJECT_CONTEXT.md gets a card from the template.

    The card is created the way the fresh install does it: PROJECT_NAME, PY and
    RHYTHM are filled, environment-derived fields are filled, and the developer
    interview placeholders ({{DEVELOPER_NAME}} etc.) are left as ``{{...}}``.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-card-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "myproject")
        os.makedirs(self.repo)
        _write(os.path.join(self.repo, "PROJECT_CONTEXT.md"),
               "# MyProject — a test project\n\n"
               "## Quick Reference Card\n"
               "- `src/` — source code\n"
               "- `data/` — datasets\n")
        # write pyproject.toml so environment() guesses python
        _write(os.path.join(self.repo, "pyproject.toml"), "[tool.pytest]\n")
        # write a pa.json
        os.makedirs(os.path.join(self.repo, ".claude"))
        _write(os.path.join(self.repo, ".claude", "pa.json"),
               json.dumps({"card": {"max_chars": 7000}}))
        # simulate fresh install: fill PROJECT_NAME, PY, RHYTHM and env-derived
        # fields, but leave Developer placeholders as {{...}}
        tmpl = _template_text()
        py = PY.replace("\\", "/")
        env_data = environment(self.repo)
        fills = {
            "PROJECT_NAME": "myproject",
            "PROJECT_TAGLINE": "a test project",
            "PY": py,
            "RHYTHM": "autonomous",
            "PY_VERSION": env_data["py_version"],
            "OS_AND_SHELLS": "%s / %s" % (env_data["os"], ", ".join(env_data["shells"])),
        }
        if env_data["build_guess"]:
            fills["BUILD_COMMAND"] = env_data["build_guess"]
        if env_data["test_guess"]:
            fills["TEST_COMMAND"] = env_data["test_guess"]
        if env_data["run_guess"]:
            fills["RUN_COMMAND"] = env_data["run_guess"]
        text = tmpl
        for key, val in fills.items():
            text = text.replace("{{%s}}" % key, val)
        _write(os.path.join(self.repo, "HOW_WE_WORK.md"), text)

    def _run_card(self, *args):
        """Run card.py in the fixture project."""
        cmd = [PY, os.path.join(TOOLS, "card.py")] + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=self.repo, timeout=30)
        return r

    def test_check_has_placeholders(self):
        """The card straight from the template has placeholders."""
        r = self._run_card("check")
        self.assertIn("placeholders=", r.stdout)
        m = re.search(r"placeholders=(\d+)", r.stdout)
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 0)
        self.assertNotEqual(r.returncode, 0)

    def test_interview_defaults_zero_placeholders(self):
        """interview --defaults leaves zero placeholders."""
        r = self._run_card("interview", "--defaults")
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = self._run_card("check")
        m = re.search(r"placeholders=(\d+)", r2.stdout)
        self.assertIsNotNone(m, r2.stdout)
        self.assertEqual(int(m.group(1)), 0, r2.stdout)

    def test_slice_expert_sections(self):
        """slice expert prints expert-tagged sections and not Developer/Rhythm."""
        self._run_card("interview", "--defaults")
        r = self._run_card("slice", "expert")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        # expert gets: Project, Tools, Skills, Paths, Build, Conventions, Environment, Docs map
        self.assertIn("## Project", out)
        self.assertIn("## Tools", out)
        self.assertIn("## Skills", out)
        self.assertIn("## Build / run / test", out)
        self.assertIn("## Environment", out)
        # expert does NOT get Developer or Rhythm
        self.assertNotIn("## Developer", out)
        self.assertNotIn("## Rhythm", out)

    def test_slice_router_includes_developer_rhythm(self):
        """slice router includes Developer and Rhythm."""
        self._run_card("interview", "--defaults")
        r = self._run_card("slice", "router")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("## Developer", out)
        self.assertIn("## Rhythm", out)
        self.assertIn("## Project", out)
        self.assertIn("## Tools", out)

    def test_check_fails_over_cap(self):
        """check exits 1 when chars > cap."""
        pa_json = os.path.join(self.repo, ".claude", "pa.json")
        # set cap to a tiny value
        _write(pa_json, json.dumps({"card": {"max_chars": 100}}))
        r = self._run_card("check")
        self.assertEqual(r.returncode, 1)

    def test_check_fails_on_standing_decisions(self):
        """check exits 1 when a Standing decisions heading exists."""
        card = _read(os.path.join(self.repo, "HOW_WE_WORK.md"))
        card += "\n## Standing decisions\n- something\n"
        _write(os.path.join(self.repo, "HOW_WE_WORK.md"), card)
        r = self._run_card("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("standing=1", r.stdout)

    def test_check_ok(self):
        """check exits 0 on a healthy card (after interview --defaults)."""
        self._run_card("interview", "--defaults")
        r = self._run_card("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("standing=0", r.stdout)
        self.assertIn("placeholders=0", r.stdout)

    def test_no_standing_decisions_in_template(self):
        """The template must not have a Standing decisions heading."""
        tmpl = _template_text()
        self.assertNotIn("## Standing decisions", tmpl)


class TestCardCheck7001(unittest.TestCase):
    """check exits 1 on a 7001-char fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-card7k-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.repo, ".claude"))
        _write(os.path.join(self.repo, ".claude", "pa.json"),
               json.dumps({"card": {"max_chars": 7000}}))
        # write a card of exactly 7001 chars (no placeholders, no Standing decisions)
        card = "# How we work\n\n## Project\n"
        card += "x" * (7001 - len(card) - 1) + "\n"
        assert len(card) == 7001
        _write(os.path.join(self.repo, "HOW_WE_WORK.md"), card)

    def _run_card(self, *args):
        cmd = [PY, os.path.join(TOOLS, "card.py")] + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=self.repo, timeout=30)
        return r

    def test_7001_chars_fails(self):
        r = self._run_card("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("chars=7001", r.stdout)
        self.assertIn("cap=7000", r.stdout)


class TestHowWeWorkWrite(unittest.TestCase):
    """howwework.write() fills from environment() and constitution."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-hww-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(self.repo)
        # minimal env-like object
        self.env = _FakeEnv(self.repo, "testproj", PY.replace("\\", "/"))

    def test_write_fills_environment(self):
        from pa.install.howwework import write
        text = write(self.env, {})
        self.assertIn("## Environment", text)
        # should have OS name filled in
        import platform
        self.assertIn(platform.system(), text)

    def test_write_with_constitution(self):
        from pa.install.howwework import write
        const = "# MyProject — a great tool\n\n## Quick Reference Card\n- `src/` — code\n"
        text = write(self.env, {}, constitution=const)
        self.assertIn("## Project", text)

    def test_write_with_interview(self):
        from pa.install.howwework import write
        iv = {"name": "Alice", "experience": "advanced", "domain": "web",
              "autonomy": "full", "notify": "toast", "rhythm": "autonomous"}
        text = write(self.env, {}, interview=iv)
        self.assertIn("Alice", text)
        self.assertNotIn("{{DEVELOPER_NAME}}", text)

    def test_tagline_from_the_skeleton_project_line(self):
        """T10 (3.11): the Quick Reference Card's **Project:** line wins; the skeleton's
        heading title is never a tagline; an unfilled {{...}} counts as none."""
        from pa.install.howwework import _tagline_from_constitution as tag
        skel = ("# demo — Project Context & Roadmap\n\n## Quick Reference Card\n\n"
                "- **Project:** demo — `eol.py`, a countdown\n- **Stack:** Python (`json`)\n")
        self.assertEqual(tag(skel), "`eol.py`, a countdown")
        self.assertEqual(tag("# demo — Project Context & Roadmap\n"), "")
        self.assertEqual(tag("# MyProject — a great tool\n"), "a great tool")
        self.assertEqual(tag("# x — Project Context & Roadmap\n- **Project:** x — {{ONE_LINE_WHAT}}\n"), "")

    def test_paths_take_only_path_bullets(self):
        """T10 (3.11): a Quick Reference Card bullet is a path only when it starts with one."""
        from pa.install.howwework import _paths_from_constitution as paths
        const = ("## Quick Reference Card\n- **Stack:** Python (`json`)\n- `src/` — code\n"
                 "- **Environment:** see `HOW_WE_WORK.md`\n- `data/`: datasets\n")
        self.assertEqual(paths(const), ["- `src/` — code", "- `data/`: datasets"])


class _FakeEnv:
    """Minimal env object for howwework.write() tests."""
    def __init__(self, repo, name, py_exe):
        self.repo = repo
        self.name = name
        self.py_exe = py_exe
        self.tagline = ""
        self.force = True
        self.dry_run = False
        self.man = _FakeManifest()
        self.pkg = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def path(self, *parts):
        return os.path.join(self.repo, *parts)


class _FakeManifest:
    """Minimal manifest tracker."""
    def __init__(self):
        self.created = []
        self.skipped = []
        self.edited = []
        self.forced = []

    def mark(self):
        return len(self.created), len(self.skipped)


if __name__ == "__main__":
    unittest.main()
