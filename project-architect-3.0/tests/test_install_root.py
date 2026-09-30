"""pa_install.py --root: detect, package copy, ledger init, settings merge, manifest.

Every test runs against a throw-away config dir plus ``--pa3-dir``/``PA_LEDGER_DIR``
overrides: the real ``~/.claude`` and ``~/.claude/usage-ledger`` are never touched
(``SafetyTest`` asserts that explicitly).
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa import db  # noqa: E402
from pa.install import build_parser, main as install_main  # noqa: E402
from pa.install import root as install  # noqa: E402
from pa.install import settings_merge as sm  # noqa: E402

FIXTURES = os.path.join(PKG, "tests", "fixtures")
WIN_FIXTURE = os.path.join(FIXTURES, "settings_user_win.json")
WSL_FIXTURE = os.path.join(FIXTURES, "settings_user_wsl.json")

PA_EVENTS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "StopFailure": "stop_failure",
    "Notification": "notification",
    "PostModelSwitch": "model_switch",
    "SessionEnd": "session_end",
}

_STEP_RE = re.compile(r"^(R\d)\s+\S.*?\s+(SKIP|DONE|FAIL)\s\s")


def steps(out):
    """``{"R1": "DONE", ...}`` parsed from the step lines of a run."""
    got = {}
    for line in out.splitlines():
        m = _STEP_RE.match(line)
        if m:
            got[m.group(1)] = m.group(2)
    return got


def run_install(argv, ledger_dir):
    """Run ``pa_install.py`` in-process with ``PA_LEDGER_DIR`` pointed at a temp dir."""
    previous = os.environ.get("PA_LEDGER_DIR")
    os.environ["PA_LEDGER_DIR"] = ledger_dir
    argv = list(argv)
    if "--root" in argv and "--source" not in argv and "--no-clone" not in argv:
        argv.append("--no-clone")                 # hermetic: never clone install.source (GitHub)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = install_main(argv)
    finally:
        if previous is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = previous
    return rc, buf.getvalue()


def make_root(fixture=WIN_FIXTURE):
    """A temp dir with ``cfg/settings.json`` seeded from a fixture."""
    tmp = tempfile.mkdtemp(prefix="pa3-root-")
    cfg = os.path.join(tmp, "cfg")
    os.makedirs(cfg)
    if fixture:
        shutil.copyfile(fixture, os.path.join(cfg, "settings.json"))
    return tmp, cfg, os.path.join(tmp, "pa3"), os.path.join(tmp, "ledger")


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def snapshot(root):
    """``{relative path: bytes}`` for every file under ``root``."""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = fh.read()
    return out


def hook_entries(settings, event):
    out = []
    for group in (settings.get("hooks") or {}).get(event) or []:
        for entry in group.get("hooks") or []:
            out.append((group.get("matcher"), entry))
    return out


# --------------------------------------------------------------------------- win

class RootWindowsTest(unittest.TestCase):
    """One ``--root`` run over a copy of the real Windows ``settings.json``."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WIN_FIXTURE)
        cls.settings_path = os.path.join(cls.cfg, "settings.json")
        # T8: pin the WSL extra-root suggestion instead of depending on whether
        # /mnt/c/Users/*/.claude/usage-ledger exists on the machine running the
        # suite -- this class runs unmodified under WSL too (env.machine there
        # reflects the real host, not the WIN_FIXTURE content).
        cls.wsl_candidate = "/mnt/c/Users/pa3test/.claude/usage-ledger"
        orig_glob = install._wsl_ledger_glob
        install._wsl_ledger_glob = lambda: [cls.wsl_candidate]
        try:
            cls.rc, cls.out = run_install(
                ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3,
                 "--yes", "--renewal-day", "21"], cls.ledger)
        finally:
            install._wsl_ledger_glob = orig_glob
        with open(cls.settings_path, "r", encoding="utf-8") as fh:
            cls.merged = json.load(fh)
        with open(WIN_FIXTURE, "r", encoding="utf-8") as fh:
            cls.fixture = json.load(fh)
        cls.py = install.detect_interpreter(None)[0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run_reports_ok_and_every_step(self):
        self.assertEqual(self.rc, 0, self.out)
        self.assertIn("\nOK", "\n" + self.out)
        self.assertEqual(sorted(steps(self.out)), ["R1", "R2", "R3", "R4", "R5"])
        self.assertNotIn("FAIL", self.out)

    def test_merged_settings_parse(self):
        self.assertIsInstance(self.merged, dict)

    def test_todo_feature_forced_false_and_announced(self):
        self.assertTrue(self.fixture["todoFeatureEnabled"])        # fixture had it on
        self.assertIs(self.merged["todoFeatureEnabled"], False)
        self.assertIn("todoFeatureEnabled forced to false", self.out)

    def test_cleanup_period_days_not_lowered(self):
        self.assertEqual(self.merged["cleanupPeriodDays"], 3650)

    def test_model_and_model_settings_are_byte_identical(self):
        self.assertEqual(json.dumps(self.merged["model"]),
                         json.dumps(self.fixture["model"]))
        self.assertEqual(json.dumps(self.merged["modelSettings"], sort_keys=True),
                         json.dumps(self.fixture["modelSettings"], sort_keys=True))

    def test_ctx_guard_hook_is_gone(self):
        self.assertIn("ctx_guard.sh", json.dumps(self.fixture))
        self.assertNotIn("ctx_guard", json.dumps(self.merged))
        self.assertIn("- hook PostToolUse:", self.out)

    def test_eleven_pa_hook_events(self):
        self.assertEqual(sorted(self.merged["hooks"]), sorted(PA_EVENTS))
        for event, action in sorted(PA_EVENTS.items()):
            entries = hook_entries(self.merged, event)
            commands = [e["command"] for _m, e in entries]
            wanted = [c for c in commands if c.endswith("pa_hook.py " + action)]
            self.assertEqual(len(wanted), 1, "%s: %r" % (event, commands))
            cmd = wanted[0]
            self.assertTrue(cmd.startswith('"%s" -I -X utf8 ' % self.py), cmd)
            self.assertIn(self.pa3.replace("\\", "/") + "/pa_hook.py", cmd)
            self.assertEqual(entries[0][1]["type"], "command")

    def test_quoted_interpreter_exists_on_this_machine(self):
        self.assertTrue(os.path.isfile(self.py), self.py)

    def test_pre_tool_use_and_notification_matchers(self):
        self.assertEqual(hook_entries(self.merged, "PreToolUse")[0][0],
                         "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell")
        self.assertEqual(hook_entries(self.merged, "Notification")[0][0],
                         "idle_prompt|permission_prompt|agent_needs_input|elicitation_dialog")

    def test_statusline_points_at_the_installed_renderer(self):
        cmd = self.merged["statusLine"]["command"]
        self.assertTrue(cmd.startswith('"%s" -I -X utf8 ' % self.py), cmd)
        self.assertTrue(cmd.endswith(self.pa3.replace("\\", "/") + "/pa_statusline.py"), cmd)

    def test_old_statusline_kept_as_pa2(self):
        pa2 = os.path.join(self.cfg, "statusline.sh.pa2")
        self.assertTrue(os.path.isfile(pa2))
        with open(pa2, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(self.fixture["statusLine"]["command"], text)
        self.assertTrue(all(line.startswith("#") for line in text.splitlines() if line.strip()))

    def test_env_has_the_five_variables(self):
        self.assertEqual(sorted(self.merged["env"]), [
            "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_OUTPUT_LENGTH", "BASH_MAX_TIMEOUT_MS",
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "CLAUDE_CODE_TOTAL_TOKENS_REMINDER"])
        self.assertEqual(self.merged["env"]["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"], "off")
        self.assertEqual(self.merged["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "3")

    def test_backup_holds_the_original_bytes(self):
        backups = [n for n in os.listdir(self.cfg) if n.startswith("settings.json.bak-")]
        self.assertEqual(len(backups), 1, backups)
        self.assertRegex(backups[0], r"^settings\.json\.bak-\d{8}-\d{6}$")
        with open(os.path.join(self.cfg, backups[0]), "rb") as fh:
            saved = fh.read()
        with open(WIN_FIXTURE, "rb") as fh:
            self.assertEqual(saved, fh.read())

    def test_untouched_keys_survive(self):
        for key in ("theme", "effortLevel", "autoUpdatesChannel", "skipWorkflowUsageWarning",
                    "attribution", "includeCoAuthoredBy"):
            self.assertEqual(self.merged[key], self.fixture[key], key)

    def test_permissions_allow_is_a_union_in_order(self):
        self.assertEqual(self.merged["permissions"]["allow"],       # T20: + the pa3_update rule
                         self.fixture["permissions"]["allow"]
                         + ["Bash(%s tools/pa3_update.py)" % self.py.replace("\\", "/")])

    def test_pa3_dir_holds_the_package(self):
        self.assertTrue(os.path.isfile(os.path.join(self.pa3, "pa", "db.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.pa3, "pa_install.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.pa3, "VERSION")))
        for name in install.ENTRY_SCRIPTS:
            if os.path.isfile(os.path.join(PKG, name)):        # siblings may still be writing
                self.assertTrue(os.path.isfile(os.path.join(self.pa3, name)), name)

    def test_doctor_latency_fixture_is_copied(self):
        src = os.path.join(PKG, "tests", "fixtures", "hooks", "probe1.log")
        dst = os.path.join(self.pa3, "tests", "fixtures", "hooks", "probe1.log")
        self.assertTrue(os.path.isfile(dst))
        with open(src, "rb") as a, open(dst, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_installed_files_are_byte_identical(self):
        for rel, src in install.package_files(PKG):
            dst = os.path.join(self.pa3, rel.replace("/", os.sep))
            if os.path.isfile(dst):                             # concurrent writes aside
                with open(src, "rb") as a, open(dst, "rb") as b:
                    self.assertEqual(a.read(), b.read(), rel)

    def test_version_file_carries_version_and_git_hash(self):
        with open(os.path.join(self.pa3, "VERSION"), encoding="utf-8") as fh:
            text = fh.read()
        from pa import __version__
        self.assertIn("version: %s" % __version__, text)
        self.assertEqual(text.splitlines()[0], "version: 3.14")    # I11
        self.assertRegex(text, r"git: [0-9a-f]{6,40}")

    def test_version_parse_forms_and_fallback(self):
        """I11: ``pa.__version__`` reads a bare or ``version:`` first line, else ``3.14``."""
        import pa
        self.assertEqual(pa.parse_version("3.11\n"), "3.11")
        self.assertEqual(pa.parse_version("version: 3.12\ngit: abc\n"), "3.12")
        self.assertEqual(pa.parse_version("git: abc\n"), "")
        self.assertEqual(pa.parse_version(""), "")
        d = tempfile.mkdtemp(prefix="pa3-ver-", dir=self.tmp)
        shutil.copytree(os.path.join(PKG, "pa"), os.path.join(d, "pa"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        code = "import pa; print(pa.__version__)"

        def ver(text):
            path = os.path.join(d, "VERSION")
            if text is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            return subprocess.run([sys.executable, "-c", code], cwd=d, capture_output=True,
                                  text=True, check=True).stdout.strip()
        self.assertEqual(ver("3.12\n"), "3.12")
        self.assertEqual(ver("version: 3.14\ngit: abc\n"), "3.14")
        self.assertEqual(ver(None), "3.14")
        self.assertEqual(ver("garbage: x\n"), "3.14")

    def test_ledger_has_every_table(self):
        conn = db.connect(os.path.join(self.ledger, "ledger.sqlite"), create=False)
        try:
            have = set(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        finally:
            db.close(conn)
        self.assertEqual(sorted(set(db.TABLES) - have), [])
        self.assertTrue(os.path.isdir(os.path.join(self.ledger, "spool")))
        self.assertTrue(os.path.isdir(os.path.join(self.ledger, "state")))

    def test_config_json_carries_the_answers(self):
        cfg = read_json(os.path.join(self.ledger, "config.json"))
        self.assertIsNone(cfg.get("renewal_day"))       # 3.11 T9: a bare --renewal-day names no account
        self.assertIn("a bare --renewal-day N names no account and is ignored", self.out)
        self.assertEqual(cfg["machine"], "win" if os.name == "nt" else cfg["machine"])
        self.assertEqual(cfg["python"], self.py)
        self.assertEqual(cfg["schema"], 1)
        # T8: empty on Windows (no WSL suggestion path); under WSL, the pinned
        # candidate is exactly the detected Windows root (the suite never
        # depends on whatever a real /mnt/c/Users/*/.claude/usage-ledger holds).
        if cfg["machine"] == "wsl":
            self.assertEqual(cfg["extra_roots"], [self.wsl_candidate])
        else:
            self.assertEqual(cfg["extra_roots"], [])

    def test_installed_at_is_written_once(self):
        """T10: a fresh root install stamps installed_at (ISO UTC)."""
        cfg = read_json(os.path.join(self.ledger, "config.json"))
        self.assertRegex(cfg["installed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_manifest_prints_dirs_and_both_reminders(self):
        self.assertIn(self.cfg.replace("\\", "/"), self.out)
        self.assertIn(self.pa3.replace("\\", "/"), self.out)
        self.assertIn(self.ledger.replace("\\", "/"), self.out)
        self.assertIn("Remote Control", self.out)
        self.assertIn("recaps", self.out)


# --------------------------------------------------------------------------- todo env (fix-6)

def run_root_with_env(env):
    """``--root`` over the Windows fixture with ``env`` set; returns (tmp, out, fixture, merged)."""
    tmp, cfg, pa3, ledger = make_root(None)
    fixture = read_json(WIN_FIXTURE)
    fixture["env"] = env
    path = os.path.join(cfg, "settings.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(fixture, fh, indent=2)
    _rc, out = run_install(["--root", "--config-dir", cfg, "--pa3-dir", pa3, "--yes"], ledger)
    return tmp, out, fixture, read_json(path)


class RootTodoEnvTest(unittest.TestCase):
    """fix-6: a truthy env.CLAUDE_CODE_ENABLE_TODO_TOOLS is removed and announced."""

    TODO = "CLAUDE_CODE_ENABLE_TODO_TOOLS"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.out, cls.fixture, cls.merged = run_root_with_env(
            {cls.TODO: "1", "MINE": "keep"})
        cls.tmp0, cls.out0, _f, cls.merged0 = run_root_with_env({cls.TODO: "0"})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.tmp0, ignore_errors=True)

    def test_truthy_value_removed_others_kept(self):
        self.assertEqual(self.fixture["env"][self.TODO], "1")
        env = self.merged["env"]
        self.assertNotIn(self.TODO, env)
        self.assertEqual(env["MINE"], "keep")
        for key in sm.load_snippet()["env"]:
            self.assertIn(key, env)

    def test_removal_is_announced(self):
        self.assertIn('~ env.%s: "1" -> "<removed>"' % self.TODO, self.out)
        self.assertIn("env.%s removed: it re-enabled the Task tools" % self.TODO, self.out)

    def test_second_merge_leaves_env_alone(self):
        _m, report = sm.merge(self.merged, sm.load_snippet())
        touched = [k for k in report["added"] if k.startswith("env")]
        touched += [c[0] for c in report["changed"] if c[0].startswith("env")]
        self.assertEqual(touched, [])

    def test_falsy_value_stays_without_note(self):
        self.assertEqual(self.merged0["env"][self.TODO], "0")
        self.assertNotIn(self.TODO, self.out0)


# --------------------------------------------------------------------------- idempotence

class SecondRunTest(unittest.TestCase):
    """A second ``--root`` changes nothing: every mutating step SKIPs."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WIN_FIXTURE)
        argv = ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3, "--yes"]
        cls.rc1, cls.out1 = run_install(argv, cls.ledger)
        with open(os.path.join(cls.cfg, "settings.json"), "rb") as fh:
            cls.first = fh.read()
        cls.installed_at_1 = read_json(os.path.join(cls.ledger, "config.json"))["installed_at"]
        cls.rc2, cls.out2 = run_install(argv, cls.ledger)
        with open(os.path.join(cls.cfg, "settings.json"), "rb") as fh:
            cls.second = fh.read()
        cls.installed_at_2 = read_json(os.path.join(cls.ledger, "config.json"))["installed_at"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_both_runs_ok(self):
        self.assertEqual((self.rc1, self.rc2), (0, 0), self.out2)

    def test_second_run_skips_every_mutating_step(self):
        got = steps(self.out2)
        self.assertEqual([got.get(s) for s in ("R2", "R3", "R4", "R5")],
                         ["SKIP", "SKIP", "SKIP", "SKIP"], self.out2)

    def test_settings_file_is_byte_identical(self):
        self.assertEqual(self.first, self.second)

    def test_installed_at_is_never_moved(self):
        """T10: a re-install never overwrites installed_at."""
        self.assertTrue(self.installed_at_1)
        self.assertEqual(self.installed_at_1, self.installed_at_2)

    def test_only_one_backup_was_written(self):
        backups = [n for n in os.listdir(self.cfg) if ".bak-" in n]
        self.assertEqual(len(backups), 1, backups)


# --------------------------------------------------------------------------- dry run

class DryRunTest(unittest.TestCase):
    """``--dry-run`` prints the plan and writes nothing at all."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WIN_FIXTURE)
        cls.before = snapshot(cls.tmp)
        cls.rc, cls.out = run_install(
            ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3, "--dry-run", "--yes"],
            cls.ledger)
        cls.after = snapshot(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_nothing_was_written(self):
        self.assertEqual(self.rc, 0, self.out)
        self.assertEqual(self.before, self.after)
        self.assertFalse(os.path.exists(self.pa3))
        self.assertFalse(os.path.exists(self.ledger))

    def test_plan_names_every_hook_command_and_key_change(self):
        self.assertIn("dry run -- nothing was written", self.out)
        for action in PA_EVENTS.values():
            self.assertIn("pa_hook.py " + action, self.out)
        self.assertIn("-I -X utf8", self.out)
        self.assertIn("~ todoFeatureEnabled", self.out)
        self.assertIn("+ env", self.out)

    def test_every_step_reports_skip(self):
        self.assertEqual([steps(self.out).get(s) for s in ("R2", "R3", "R4", "R5")],
                         ["SKIP", "SKIP", "SKIP", "SKIP"], self.out)


# --------------------------------------------------------------------------- switches

class SwitchesTest(unittest.TestCase):

    def setUp(self):
        self.tmp, self.cfg, self.pa3, self.ledger = make_root(WIN_FIXTURE)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings_path = os.path.join(self.cfg, "settings.json")

    def merged(self):
        with open(self.settings_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_hooks_off_writes_no_hook_entries(self):
        rc, out = run_install(["--root", "--config-dir", self.cfg, "--pa3-dir", self.pa3,
                               "--yes", "--hooks", "off"], self.ledger)
        self.assertEqual(rc, 0, out)
        merged = self.merged()
        self.assertNotIn("pa_hook.py", json.dumps(merged))
        self.assertEqual(merged["hooks"], read_json(WIN_FIXTURE)["hooks"])
        self.assertIs(merged["todoFeatureEnabled"], False)       # the rest still merged
        self.assertIn("--hooks off", out)

    def test_no_statusline_keeps_the_old_command(self):
        rc, out = run_install(["--root", "--config-dir", self.cfg, "--pa3-dir", self.pa3,
                               "--yes", "--no-statusline"], self.ledger)
        self.assertEqual(rc, 0, out)
        merged = self.merged()
        self.assertEqual(merged["statusLine"], read_json(WIN_FIXTURE)["statusLine"])
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "statusline.sh.pa2")))
        self.assertIn("pa_hook.py session_start", json.dumps(merged))

    def test_flags_pre_answer_the_config_prompts(self):
        rc, out = run_install(["--root", "--config-dir", self.cfg, "--pa3-dir", self.pa3,
                               "--yes", "--renewal-day", "7",
                               "--renewal-day", "dev@example.com=9",
                               "--extra-root", "/mnt/c/Users/x/.claude/usage-ledger",
                               "--label", "dev@example.com=win-main"], self.ledger)
        self.assertEqual(rc, 0, out)
        cfg = read_json(os.path.join(self.ledger, "config.json"))
        self.assertIsNone(cfg.get("renewal_day"))                    # 3.11 T9: the bare 7 names no account
        self.assertEqual(cfg["extra_roots"], ["/mnt/c/Users/x/.claude/usage-ledger"])
        self.assertEqual(cfg["accounts"], {"dev@example.com": {"label": "win-main",
                                                                "renewal_day": 9}})
        self.assertEqual(cfg["recalc_default_account"], "dev@example.com")

    def test_project_subcommand_dispatches_to_the_per_repo_install(self):
        # M2.c3 implemented --project; the details live in tests/test_install_project.py.
        # Never "." here: that is this repository.  A directory outside a git repo is
        # the one case that refuses before it writes anything.
        plain = tempfile.mkdtemp(prefix="pa3-plain-")
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        rc, out = run_install(["--project", plain], self.ledger)
        self.assertEqual(rc, 1, out)
        self.assertIn("not a git repository", out)
        self.assertEqual(os.listdir(plain), [])

    def test_parser_knows_every_documented_switch(self):
        opts = build_parser().parse_args(["--root"])
        for name in ("config_dir", "pa3_dir", "python", "dry_run", "yes", "renewal_day",
                     "extra_root", "label", "no_statusline", "hooks", "allow_stale"):
            self.assertTrue(hasattr(opts, name), name)
        self.assertEqual(opts.hooks, "on")


# --------------------------------------------------------------------------- wsl

class RootWslTest(unittest.TestCase):
    """The WSL config dir: ``--python /usr/bin/python3`` -> POSIX commands."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WSL_FIXTURE)
        cls.rc, cls.out = run_install(
            ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3,
             "--python", "/usr/bin/python3", "--yes"], cls.ledger)
        with open(os.path.join(cls.cfg, "settings.json"), "r", encoding="utf-8") as fh:
            cls.merged = json.load(fh)
        with open(WSL_FIXTURE, "r", encoding="utf-8") as fh:
            cls.fixture = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ok(self):
        self.assertEqual(self.rc, 0, self.out)

    def test_commands_are_posix(self):
        commands = [e["command"] for event in PA_EVENTS
                    for _m, e in hook_entries(self.merged, event)]
        commands.append(self.merged["statusLine"]["command"])
        self.assertEqual(len(commands), 12)
        for cmd in commands:
            self.assertTrue(cmd.startswith('"/usr/bin/python3" -I -X utf8 '), cmd)
            self.assertNotIn("\\", cmd)

    def test_home_relative_ctx_guard_entry_removed(self):
        original = self.fixture["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(original, 'bash "$HOME"/.claude/ctx_guard.sh')
        self.assertNotIn("ctx_guard", json.dumps(self.merged))

    def test_model_and_wsl_only_keys_survive(self):
        self.assertEqual(self.merged["model"], self.fixture["model"])
        self.assertEqual(self.merged["modelSettings"], self.fixture["modelSettings"])
        self.assertEqual(self.merged["tui"], "fullscreen")
        self.assertTrue(self.merged["agentPushNotifEnabled"])

    def test_missing_keys_were_added(self):
        self.assertIs(self.merged["todoFeatureEnabled"], False)
        self.assertEqual(sorted(self.merged["env"]), sorted(self.merged["env"]))
        self.assertIn("permissions", self.merged)
        self.assertFalse(self.merged["includeCoAuthoredBy"])


# --------------------------------------------------------------------------- safety

class SafetyTest(unittest.TestCase):
    """A temp-dir install never reaches the developer's real config dir."""

    def test_real_claude_dir_is_untouched(self):
        from pa import paths

        real = paths.claude_dir()
        before = sorted(os.listdir(real)) if os.path.isdir(real) else None
        tmp, cfg, pa3, ledger = make_root(WIN_FIXTURE)
        self.addCleanup(shutil.rmtree, tmp, True)
        rc, out = run_install(["--root", "--config-dir", cfg, "--pa3-dir", pa3, "--yes"],
                              ledger)
        self.assertEqual(rc, 0, out)
        after = sorted(os.listdir(real)) if os.path.isdir(real) else None
        self.assertEqual(before, after)

    def test_guard_scripts_outside_the_config_dir_are_never_renamed(self):
        tmp = tempfile.mkdtemp(prefix="pa3-guard-")
        self.addCleanup(shutil.rmtree, tmp, True)
        outside = os.path.join(tmp, "elsewhere")
        inside = os.path.join(tmp, "cfg")
        os.makedirs(outside)
        os.makedirs(inside)
        for d in (outside, inside):
            with open(os.path.join(d, "ctx_guard.sh"), "w", encoding="utf-8") as fh:
                fh.write("# guard\n")
        done = install.retire_guard_scripts(
            inside, [os.path.join(outside, "ctx_guard.sh").replace("\\", "/")])
        self.assertEqual(len(done), 1)
        self.assertTrue(os.path.isfile(os.path.join(outside, "ctx_guard.sh")))
        self.assertFalse(os.path.isfile(os.path.join(inside, "ctx_guard.sh")))
        self.assertTrue(os.path.isfile(os.path.join(inside, "ctx_guard.sh.retired")))


# --------------------------------------------------------------------------- merge unit

class SettingsMergeTest(unittest.TestCase):

    def setUp(self):
        self.snippet = sm.fill(sm.load_snippet(), "C:/py/python.exe", "C:/pa3")

    def test_snippet_documentation_keys_are_dropped(self):
        self.assertNotIn("_comment", self.snippet)
        self.assertNotIn("_merge_rules", self.snippet)
        self.assertNotIn("{{", json.dumps(self.snippet))

    def test_empty_settings_gain_every_key(self):
        merged, report = sm.merge({}, self.snippet)
        self.assertEqual(sorted(merged), sorted(self.snippet))
        self.assertEqual(len(report["hooks_added"]), 11)
        self.assertEqual(report["statusline_old"], None)

    def test_merge_is_a_fixed_point(self):
        once, _ = sm.merge({}, self.snippet)
        twice, report = sm.merge(once, self.snippet)
        self.assertEqual(json.dumps(once, sort_keys=True), json.dumps(twice, sort_keys=True))
        self.assertEqual(report["hooks_added"], [])
        self.assertEqual(report["hooks_replaced"], [])

    def test_cleanup_period_days_is_raise_only(self):
        merged, _ = sm.merge({"cleanupPeriodDays": 9999}, self.snippet)
        self.assertEqual(merged["cleanupPeriodDays"], 9999)
        merged, report = sm.merge({"cleanupPeriodDays": 30}, self.snippet)
        self.assertEqual(merged["cleanupPeriodDays"], 3650)
        self.assertIn(("cleanupPeriodDays", 30, 3650), report["changed"])

    def test_foreign_hook_entries_are_kept(self):
        ghidra = {"hooks": [{"type": "command", "command": "bash start-ghidra.sh"}]}
        merged, _ = sm.merge({"hooks": {"SessionStart": [ghidra]}}, self.snippet)
        commands = [e["command"] for group in merged["hooks"]["SessionStart"]
                    for e in group["hooks"]]
        self.assertIn("bash start-ghidra.sh", commands)
        self.assertEqual(len([c for c in commands if "pa_hook.py" in c]), 1)

    def test_stale_pa_entry_is_replaced_in_place(self):
        stale = {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": '"old.exe" -I -X utf8 /old/pa_hook.py session_start',
             "timeout": 99}]}]}}
        merged, report = sm.merge(stale, self.snippet)
        self.assertEqual(len(merged["hooks"]["SessionStart"]), 1)
        self.assertEqual(merged["hooks"]["SessionStart"][0]["hooks"][0]["timeout"], 15)
        self.assertEqual([i[:2] for i in report["hooks_replaced"]],
                         [("SessionStart", "session_start")])

    def test_env_and_permissions_are_add_if_absent_and_union(self):
        cur = {"env": {"CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "on", "MINE": "1"},
               "permissions": {"allow": ["Bash(ls:*)", "Bash(git log:*)"], "deny": ["Bash(rm:*)"]}}
        merged, _ = sm.merge(cur, self.snippet)
        self.assertEqual(merged["env"]["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"], "on")
        self.assertEqual(merged["env"]["MINE"], "1")
        self.assertEqual(merged["env"]["BASH_MAX_TIMEOUT_MS"], "3600000")
        allow = merged["permissions"]["allow"]
        self.assertEqual(allow[:2], ["Bash(ls:*)", "Bash(git log:*)"])
        self.assertEqual(len(allow), len(set(allow)))
        self.assertIn("Bash(git status)", allow)
        self.assertEqual(merged["permissions"]["deny"], ["Bash(rm:*)"])

    def test_protected_keys_raise(self):
        self.assertRaises(sm.MergeError, sm.merge, {}, {"model": "x"})
        self.assertRaises(sm.MergeError, sm.merge, {}, {"modelSettings": {}})

    def test_pa_action_and_retired_token(self):
        self.assertEqual(sm.pa_action('"py" -I -X utf8 /p/pa_hook.py subagent_stop'),
                         "subagent_stop")
        self.assertEqual(sm.pa_action("/p/pa_statusline.py"), "statusline")
        self.assertIsNone(sm.pa_action("bash other.sh"))
        self.assertEqual(sm.retired_script_token('bash "$HOME"/.claude/ctx_guard.sh'),
                         '"$HOME"/.claude/ctx_guard.sh')
        self.assertTrue(sm.is_retired_command("bash /x/ctx90.sh"))

    def test_abs_fwd_keeps_posix_paths(self):
        self.assertEqual(sm.abs_fwd("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(sm.abs_fwd("C:\\a\\b\\"), "C:/a/b")
        self.assertEqual(sm.abs_fwd("//wsl.localhost/U/home/x/"), "//wsl.localhost/U/home/x")


# --------------------------------------------------------------------------- bootstrap scripts

INSTALL_SH = os.path.join(PKG, "install.sh")
INSTALL_CMD = os.path.join(PKG, "install.cmd")
BASH = shutil.which("bash")

STUB_SH = """#!/bin/sh
if [ "$1" = "-c" ]; then
    exit %(rc)d
fi
printf '%%s\\n' "$@" > "%(log)s"
exit 0
"""


def make_stub(path, version_ok, log):
    """A fake interpreter: fails ``-c <version check>`` unless ``version_ok``,
    otherwise records every argv it is called with (one per line) to ``log``.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(STUB_SH % {"rc": 0 if version_ok else 1, "log": log.replace("\\", "/")})
    os.chmod(path, 0o755)


@unittest.skipUnless(BASH, "bash not on PATH")
class InstallShTest(unittest.TestCase):
    """``install.sh``'s interpreter selection, run over a fake ``PATH``."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-install-sh-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_sh(self, path_dir, args=()):
        env = dict(os.environ)
        env["PATH"] = path_dir
        return subprocess.run([BASH, INSTALL_SH] + list(args),
                              capture_output=True, text=True, env=env)

    def test_old_stub_skipped_new_stub_chosen_and_receives_args(self):
        log = os.path.join(self.tmp, "argv.log")
        make_stub(os.path.join(self.tmp, "python3"), version_ok=False, log=log)  # 3.11: skipped
        make_stub(os.path.join(self.tmp, "python"), version_ok=True, log=log)    # 3.12: chosen
        p = self.run_sh(self.tmp, ["--dry-run", "--yes"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Using interpreter: python\n", p.stdout)
        with open(log, encoding="utf-8") as fh:
            argv = fh.read().splitlines()
        self.assertTrue(argv[0].endswith("pa_install.py"), argv)
        # without git on PATH, install.sh passes --no-clone
        self.assertEqual(argv[1:], ["--root", "--no-clone", "--dry-run", "--yes"])

    def test_no_interpreter_no_apt_prints_install_command_and_exits_1(self):
        p = self.run_sh(self.tmp)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        combined = p.stdout + p.stderr
        self.assertIn("Could not find or install a Python 3.12+ interpreter", combined)
        self.assertIn("python3", combined)

    def test_lf_line_endings(self):
        with open(INSTALL_SH, "rb") as fh:
            self.assertNotIn(b"\r", fh.read())


class ProjectEntryTest(unittest.TestCase):
    """3.11 T7: --project without a folder asks at a terminal; --ignore silences the setup offer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-entry-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = os.path.join(self.tmp, "ledger")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)

    def test_project_without_a_folder_asks_at_a_terminal(self):
        with mock.patch("sys.stdin") as stdin, \
                mock.patch("builtins.input", side_effect=[self.repo] + [""] * 30) as asked:
            stdin.isatty.return_value = True
            rc, out = run_install(["--project", "--dry-run"], self.ledger)
        self.assertIn("repository folder to set up", asked.call_args_list[0][0][0])
        self.assertIn(os.path.basename(self.repo), out)

    def test_ignore_writes_the_marker(self):
        from pa import setup_offer

        rc, out = run_install(["--ignore", self.repo], self.ledger)
        self.assertEqual(rc, 0, out)
        self.assertIn("will not offer to set up", out)
        with mock.patch.dict(os.environ, {"PA_LEDGER_DIR": self.ledger}):
            self.assertTrue(setup_offer.state(self.repo).get("never"))


class SetupProjectCmdTextTest(unittest.TestCase):
    """3.11 T7: ``setup-project.cmd`` only runs on Windows: check its content textually."""

    def setUp(self):
        with open(os.path.join(PKG, "setup-project.cmd"), "rb") as fh:
            self.raw = fh.read()
        self.text = self.raw.decode("ascii")

    def test_picker_or_dropped_folder_then_the_project_install(self):
        self.assertIn('set "FOLDER=%~1"', self.text)                         # a folder dropped on it
        self.assertIn("System.Windows.Forms.FolderBrowserDialog", self.text)  # else the picker
        self.assertIn('pa_install.py" --project "%FOLDER%"', self.text)
        self.assertLess(self.text.index("FolderBrowserDialog"), self.text.index("\n:run"))  # the label

    def test_double_click_marker_and_pauses(self):
        self.assertIn('set "PA3_DOUBLECLICK=1"', self.text)
        self.assertEqual(self.text.count("if defined PA3_DOUBLECLICK pause"), 2)   # no Python, no folder

    def test_crlf_like_install_cmd(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))


class InstallCmdTextTest(unittest.TestCase):
    """``install.cmd`` only runs on Windows: check its content textually."""

    def setUp(self):
        with open(INSTALL_CMD, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_interpreter_order_then_winget_id(self):
        order = ["py -3", "python", "python3", "Python.Python.3.12"]
        positions = [self.text.index(tok) for tok in order]
        self.assertEqual(positions, sorted(positions), (order, positions))

    def test_winget_install_flags(self):
        self.assertIn("winget install --id Python.Python.3.12 -e "
                      "--accept-package-agreements --accept-source-agreements", self.text)

    def test_runs_pa_install_with_root_and_pass_through_args(self):
        # without git: falls back to the local script with --no-clone
        self.assertIn('pa_install.py" --root --no-clone %*', self.text)
        # with a clone: runs from the clone
        self.assertIn('pa_install.py" --root %*', self.text)

    def test_crlf_line_endings(self):
        with open(INSTALL_CMD, "rb") as fh:
            data = fh.read()
        self.assertGreater(data.count(b"\r\n"), 0)
        self.assertEqual(re.sub(b"\r\n", b"", data).count(b"\n"), 0)


def _snapshot_config(installed):
    """A pre-T12 full-snapshot config.json: every default written out, plus user keys."""
    from pa import config as pa_config
    cfg = pa_config.merge(pa_config.defaults(), installed)
    cfg["renewal_day"] = 9
    cfg["accounts"] = {"a@example.com": {"label": "win-main"}}
    cfg["extra_roots"] = ["//wsl/usage-ledger"]
    cfg["statusline"]["lines"] = 6                        # past default (now 7)
    cfg["statusline"]["max_width"] = 120                  # hand-set override
    cfg["pinned_models"]["planner-gen"] = "claude-fable-5-1"   # past default
    cfg["handoff"]["roles"] = ["expert"]                  # past default
    return cfg


class SupersededMigrationTest(unittest.TestCase):
    """T5/T12: a second ``--root`` run drops current and past defaults from a snapshot."""

    @classmethod
    def setUpClass(cls):
        from pa import config as pa_config
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WIN_FIXTURE)
        argv = ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3, "--yes"]
        cls.rc1, cls.out1 = run_install(argv, cls.ledger)
        cfg_path = os.path.join(cls.ledger, "config.json")
        cls.first_cfg = read_json(cfg_path)
        cfg = _snapshot_config(cls.first_cfg)
        with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg, fh)
        pa_config._CACHE.clear()
        cls.rc2, cls.out2 = run_install(argv, cls.ledger)
        cls.final_cfg = read_json(cfg_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fresh_install_writes_slim(self):
        self.assertNotIn("pinned_models", self.first_cfg)
        self.assertNotIn("statusline", self.first_cfg)
        self.assertIn("renewal_day", self.first_cfg)

    def test_second_run_ok(self):
        self.assertEqual(self.rc2, 0, self.out2)

    def test_defaults_dropped(self):
        self.assertNotIn("pinned_models", self.final_cfg)
        self.assertNotIn("roles", self.final_cfg)
        self.assertNotIn("handoff", self.final_cfg)
        self.assertEqual(self.final_cfg["statusline"], {"max_width": 120})

    def test_user_keys_kept(self):
        self.assertEqual(self.final_cfg["renewal_day"], 9)
        self.assertEqual(self.final_cfg["accounts"], {"a@example.com": {"label": "win-main"}})
        self.assertEqual(self.final_cfg["extra_roots"], ["//wsl/usage-ledger"])

    def test_note_printed(self):
        self.assertIn("dropped statusline.lines", self.out2)
        self.assertIn("dropped pinned_models.planner-gen", self.out2)

    def test_r3_line_says_migrated(self):
        self.assertIn("config.json migrated", self.out2)

    def test_installed_at_preserved(self):
        self.assertTrue(self.final_cfg.get("installed_at"))


class SupersededDryRunTest(unittest.TestCase):
    """T5: ``--dry-run`` prints what it would migrate and writes nothing."""

    @classmethod
    def setUpClass(cls):
        from pa import config as pa_config
        cls.tmp, cls.cfg, cls.pa3, cls.ledger = make_root(WIN_FIXTURE)
        argv = ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3, "--yes"]
        cls.rc1, cls.out1 = run_install(argv, cls.ledger)
        # patch the installed config: set lines to 6 (superseded)
        cls.cfg_path = os.path.join(cls.ledger, "config.json")
        cfg = read_json(cls.cfg_path)
        cfg["statusline"] = {"lines": 6}
        with open(cls.cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg, fh)
        pa_config._CACHE.clear()
        cls.before = snapshot(cls.tmp)
        cls.rc2, cls.out2 = run_install(
            ["--root", "--config-dir", cls.cfg, "--pa3-dir", cls.pa3, "--dry-run", "--yes"],
            cls.ledger)
        cls.after = snapshot(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_dry_run_prints_would_drop(self):
        self.assertIn("would drop: statusline.lines", self.out2)

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.before, self.after)


_HAS_GIT = shutil.which("git") is not None


def _make_source_repo(tmp):
    """Create a bare-minimum git repo that looks like a PA3 package source."""
    src = os.path.join(tmp, "source-repo")
    os.makedirs(src)
    subprocess.run(["git", "init", "-q", src], check=True, timeout=30,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", src, "config", "user.email", "test@test.com"],
                   check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", src, "config", "user.name", "Test"],
                   check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", src, "config", "commit.gpgsign", "false"],
                   check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Place a minimal package tree
    pkg_dir = os.path.join(src, "project-architect-3.0")
    pa_dir = os.path.join(pkg_dir, "pa")
    os.makedirs(pa_dir)
    with open(os.path.join(pa_dir, "__init__.py"), "w") as fh:
        fh.write("__version__ = '3.0.0-test'\n")
    with open(os.path.join(pkg_dir, "pa_install.py"), "w") as fh:
        fh.write("# stub\n")
    subprocess.run(["git", "-C", src, "add", "."], check=True, timeout=10,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", src, "commit", "-qm", "init"], check=True, timeout=30,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return src


@unittest.skipUnless(_HAS_GIT, "git not on PATH")
class CloneTest(unittest.TestCase):
    """ensure_clone: clone from a fixture source repo into a temp config dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-clone-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = _make_source_repo(self.tmp)
        self.cfg_dir = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfg_dir)

    def test_clone_creates_repo_with_autocrlf_false(self):
        env = install.Env(config_dir=self.cfg_dir)
        result = install.ensure_clone(env, self.source, timeout_s=30)
        self.assertEqual(result["status"], "cloned")
        clone = result["path"].replace("/", os.sep)
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        # autocrlf should be false
        proc = subprocess.run(["git", "-C", clone, "config", "core.autocrlf"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.stdout.strip(), "false")
        self.assertIsNotNone(result["head"])

    def test_second_run_pulls_or_reports_current(self):
        env = install.Env(config_dir=self.cfg_dir)
        r1 = install.ensure_clone(env, self.source, timeout_s=30)
        self.assertEqual(r1["status"], "cloned")
        # second run with no new commits: current
        r2 = install.ensure_clone(env, self.source, timeout_s=30)
        self.assertIn(r2["status"], ("current", "pulled"))

    def test_second_run_pulls_new_commit(self):
        env = install.Env(config_dir=self.cfg_dir)
        install.ensure_clone(env, self.source, timeout_s=30)
        # add a commit to source
        f = os.path.join(self.source, "new.txt")
        with open(f, "w") as fh:
            fh.write("update\n")
        subprocess.run(["git", "-C", self.source, "add", "new.txt"],
                       check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "-C", self.source, "commit", "-qm", "update"],
                       check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        r2 = install.ensure_clone(env, self.source, timeout_s=30)
        self.assertEqual(r2["status"], "pulled")

    def test_offline_for_unreachable_source(self):
        env = install.Env(config_dir=self.cfg_dir)
        install.ensure_clone(env, self.source, timeout_s=30)
        # break the remote by pointing at a nonexistent path
        clone_path = os.path.join(self.cfg_dir, "pa3-src")
        subprocess.run(["git", "-C", clone_path, "remote", "set-url", "origin",
                         "/nonexistent/path/repo.git"],
                       check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        r = install.ensure_clone(env, "/nonexistent/path/repo.git", timeout_s=5)
        self.assertEqual(r["status"], "offline")
        self.assertIsNotNone(r["head"])

    def _break_remote(self):
        env = install.Env(config_dir=self.cfg_dir)
        install.ensure_clone(env, self.source, timeout_s=30)
        subprocess.run(["git", "-C", os.path.join(self.cfg_dir, "pa3-src"), "remote", "set-url",
                        "origin", os.path.join(self.tmp, "no-such-repo")],
                       check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return env

    def test_offline_carries_cause_and_unknown_behind(self):
        r = install.ensure_clone(self._break_remote(), self.source, timeout_s=10)
        self.assertEqual(r["status"], "offline")
        self.assertTrue(r["cause"])
        self.assertIsNone(r["behind"])
        self.assertTrue(install.clone_warning(r).startswith("WARN clone offline: "))
        self.assertIn("behind unknown: ", install.clone_warning(r))

    def test_fresh_clone_failure_is_offline_with_cause(self):
        env = install.Env(config_dir=self.cfg_dir)
        r = install.ensure_clone(env, os.path.join(self.tmp, "no-such-repo"), timeout_s=10)
        self.assertEqual(r["status"], "offline")
        self.assertTrue(r["cause"])
        self.assertIsNone(r["behind"])
        self.assertIn(" at none, behind unknown: ", install.clone_warning(r))

    def _root_run(self, *extra):
        pa3 = os.path.join(self.tmp, "pa3")
        rc, out = run_install(["--root", "--config-dir", self.cfg_dir, "--pa3-dir", pa3,
                               "--yes", "--source", self.source] + list(extra),
                              os.path.join(self.tmp, "ledger"))
        return rc, out, pa3

    def test_install_root_refuses_offline_clone(self):
        self._break_remote()
        rc, out, pa3 = self._root_run()
        self.assertEqual(rc, 2, out)
        self.assertIn("WARN clone offline: ", out)
        self.assertIn("--allow-stale", out)
        self.assertFalse(os.path.exists(pa3))                      # nothing written

    def test_install_root_allow_stale_installs(self):
        self._break_remote()
        rc, out, pa3 = self._root_run("--allow-stale")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN clone offline: ", out)

    def test_install_root_current_clone_no_warn(self):
        install.ensure_clone(install.Env(config_dir=self.cfg_dir), self.source, timeout_s=30)
        rc, out, pa3 = self._root_run()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARN", out)

    def test_root_install_allow_entry_matches_update_step_once(self):
        """T20: one `pa3_update.py` allow entry, no T16 per-clone entries; a re-run changes nothing."""
        settings = os.path.join(self.cfg_dir, "settings.json")
        rc, out, _ = self._root_run("--python", sys.executable)
        self.assertEqual(rc, 0, out)
        with open(settings, "rb") as fh:
            first = fh.read()
        rc, out, _ = self._root_run("--python", sys.executable)
        self.assertEqual(rc, 0, out)
        with open(settings, "rb") as fh:
            self.assertEqual(fh.read(), first)
        allow = read_json(settings)["permissions"]["allow"]
        entry = "Bash(%s tools/pa3_update.py)" % sys.executable.replace("\\", "/")
        self.assertEqual(allow.count(entry), 1, allow)
        self.assertFalse([a for a in allow if "pa_install.py --root --yes" in a], allow)

    def test_version_text_names_clone(self):
        env = install.Env(config_dir=self.cfg_dir)
        r = install.ensure_clone(env, self.source, timeout_s=30)
        ver = install.version_text(self.source, clone_path=r["path"], clone_head=r["head"])
        self.assertIn("source: ", ver)
        # source should use / separators
        for line in ver.splitlines():
            if line.startswith("source:"):
                src = line.split(":", 1)[1].strip()
                self.assertNotIn("\\", src)
        self.assertIn("git: ", ver)
        tree = subprocess.run(["git", "-C", r["path"], "rev-parse", "HEAD:project-architect-3.0"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        self.assertTrue(tree)
        self.assertIn("tree: %s\n" % tree, ver)

    def test_no_clone_flag_uses_local_package(self):
        """--no-clone keeps the package next to pa_install.py (today's behaviour)."""
        env = install.Env(config_dir=self.cfg_dir)
        # Clone first, so the clone exists
        r = install.ensure_clone(env, self.source, timeout_s=30)
        env.clone_path = None
        env.clone_head = None
        # version_text without clone info should name the pkg, not the clone (the package
        # itself may live in a machine clone named pa3-src, so match the paths, not the name)
        ver = install.version_text(install.package_root())
        fwd = lambda p: os.path.abspath(p).replace("\\", "/")
        self.assertNotIn(fwd(r["path"]), ver)
        self.assertIn("source: " + fwd(install.package_root()), ver)


class NoQuestionsTest(unittest.TestCase):
    """3.11 T6: the machine install asks nothing, even at a terminal; a double-clicked console waits."""

    def test_root_install_at_a_terminal_asks_nothing(self):
        tmp, cfg, pa3, ledger = make_root(None)
        try:
            with mock.patch("sys.stdin") as stdin, \
                    mock.patch("builtins.input", side_effect=AssertionError("the install asked")):
                stdin.isatty.return_value = True
                rc, out = run_install(["--root", "--config-dir", cfg, "--pa3-dir", pa3], ledger)
            self.assertEqual(rc, 0, out)
            self.assertIn("Next: open one of your repositories in Claude Code", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_owned_console(self):
        from pa.install import owned_console

        self.assertTrue(owned_console(["cmd.exe", "py.exe", "python.exe"]))
        self.assertTrue(owned_console(["CMD.EXE", "python.exe"]))
        self.assertFalse(owned_console(["pwsh.exe", "cmd.exe", "py.exe", "python.exe"]))
        self.assertFalse(owned_console(["powershell.exe", "cmd.exe", "python.exe"]))
        self.assertFalse(owned_console(["bash.exe", "cmd.exe", "python.exe"]))
        self.assertFalse(owned_console(["cmd.exe", "cmd.exe", "python.exe"]))   # install.cmd from a cmd prompt
        self.assertFalse(owned_console([]))                                      # unknown: never wait

    def test_hold_only_for_a_double_click(self):
        from pa import install as inst

        env = {k: v for k, v in os.environ.items() if k != "PA3_DOUBLECLICK"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("builtins.input", side_effect=AssertionError("held without a double-click")):
            self.assertFalse(inst.hold_if_double_clicked())
        with mock.patch.dict(os.environ, {"PA3_DOUBLECLICK": "1"}), mock.patch("os.name", "nt"), \
                mock.patch.object(inst, "is_tty", return_value=True), \
                mock.patch.object(inst, "_console_process_names", return_value=["cmd.exe", "py.exe", "python.exe"]), \
                mock.patch("builtins.input", return_value="") as held:
            self.assertTrue(inst.hold_if_double_clicked())
            held.assert_called_once()
        with mock.patch.dict(os.environ, {"PA3_DOUBLECLICK": "1"}), mock.patch("os.name", "nt"), \
                mock.patch.object(inst, "is_tty", return_value=True), \
                mock.patch.object(inst, "_console_process_names", return_value=["pwsh.exe", "cmd.exe", "python.exe"]), \
                mock.patch("builtins.input", side_effect=AssertionError("held in a PowerShell run")):
            self.assertFalse(inst.hold_if_double_clicked())


if __name__ == "__main__":
    unittest.main()
