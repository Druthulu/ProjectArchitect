"""pa/health.py: the mod's health check and its healing at session start (3.24 T6).

Every scenario runs in one fixture home, never the real ``~/.claude``: a scratch USERPROFILE/HOME
holding its own root (``<home>/.claude/pa3``), CLAUDE_CONFIG_DIR with its own clone (``pa3-src``) of a
local upstream holding the working-tree package at VERSION 3.24.0, PA_LEDGER_DIR, and a ``mod: on``
project installed from the clone.  Later tests commit "fix" releases one VERSION higher upstream and
fetch them into the clone.  The heal's detached spawn is replaced by a synchronous run of the same
argv, so ``tools/pa3_update.py`` really runs.  The mod's heartbeat and error record are written
directly (the mod side writes them).  Tests run in name order; each starts with no hook, heartbeat,
error, marker or lock file.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from pa import config, health  # noqa: E402
from pa.hooks import session_start as ss  # noqa: E402
from pa.hooks import user_prompt_submit as ups  # noqa: E402

PKG = "project-architect-3.0"
ENV_KEYS = ("USERPROFILE", "HOME", "CLAUDE_CONFIG_DIR", "PA_LEDGER_DIR", "GIT_CONFIG_NOSYSTEM",
            "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "PA_MOD_OFF")


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


class HealthFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = {k: os.environ.get(k) for k in ENV_KEYS}
        cls.S = tempfile.mkdtemp(prefix="pa3-health-")
        S = cls.S
        cls.home = os.path.join(S, "home")
        cls.cfg = os.path.join(S, "cfg")
        cls.up = os.path.join(S, "up")
        cls.clone = os.path.join(cls.cfg, "pa3-src")
        cls.proj = os.path.join(S, "proj")
        cls.pa3 = os.path.join(cls.home, ".claude", "pa3")
        for d in (cls.home, cls.cfg, os.path.join(S, "ledger"), cls.proj):
            os.makedirs(d)
        os.environ.update({"USERPROFILE": cls.home, "HOME": cls.home, "CLAUDE_CONFIG_DIR": cls.cfg,
                           "PA_LEDGER_DIR": os.path.join(S, "ledger"), "GIT_CONFIG_NOSYSTEM": "1",
                           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@t"})
        os.environ.pop("PA_MOD_OFF", None)
        shutil.copytree(HERE, os.path.join(cls.up, PKG),
                        ignore=shutil.ignore_patterns("tests", "docs", "__pycache__", ".run"))
        cls._write(os.path.join(cls.up, PKG, "VERSION"), "3.24.0\n")
        git(S, "init", "-q", "-b", "main", cls.up)
        git(cls.up, "add", "-A")
        git(cls.up, "commit", "-qm", "release 3.24.0")
        git(S, "clone", "-q", cls.up, cls.clone)
        inst = os.path.join(cls.clone, PKG, "pa_install.py")
        cls._check([sys.executable, inst, "--root", "--no-clone", "--yes"], S)
        git(cls.proj, "init", "-q", "-b", "main")
        cls._check([sys.executable, inst, "--project", cls.proj, "--yes", "--mod", "on"], S)
        cls.calls = []
        cls._spawner = health._SPAWNER

        def spawn(argv, cwd):
            r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            cls.calls.append((argv, r.returncode, r.stdout + r.stderr))
        health._SPAWNER = spawn

    @classmethod
    def tearDownClass(cls):
        health._SPAWNER = cls._spawner
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(cls.S, ignore_errors=True)

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    @staticmethod
    def _check(argv, cwd):
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise AssertionError("%s -> %d\n%s" % (argv, r.returncode, (r.stdout + r.stderr)[-2000:]))

    def setUp(self):
        for name in (health.HOOK, health.HEARTBEAT, health.ERROR, health.MARKER, "update.lock"):
            try:
                os.remove(self.mod_run(name))
            except OSError:
                pass
        del self.calls[:]
        self.set_pa("upgrade", "auto")

    # ---------------------------------------------------------------- helpers

    def mod_run(self, name):
        return os.path.join(self.proj, health.MOD_RUN, name)

    def read_json(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def set_pa(self, key, value):
        p = os.path.join(self.proj, ".claude", "pa.json")
        doc = self.read_json(p)
        doc[key] = value
        self._write(p, json.dumps(doc, indent=1))

    def beat(self, sid):
        self._write(self.mod_run(health.HEARTBEAT), json.dumps(
            {"sid": sid, "version": "3.24.0", "engine": "2.1.296", "at": time.time(), "plugin": "pa"}))

    def root_version(self):
        return health.installed_version(self.pa3)

    def damage(self):
        rel = "mod/pa/hooks/seed.ts"
        with open(os.path.join(self.pa3, rel), "a", encoding="utf-8") as fh:
            fh.write("\n// damaged\n")
        os.remove(os.path.join(self.pa3, "mod", "pa-watch", "hooks", "feed.ts"))

    def run_update(self, *args):
        r = subprocess.run([sys.executable, "tools/pa3_update.py"] + list(args), cwd=self.proj,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout + r.stderr

    def bump(self, version):
        """A "fix" release upstream, fetched into the clone (not pulled)."""
        self._write(os.path.join(self.up, PKG, "VERSION"), version + "\n")
        with open(os.path.join(self.up, PKG, "CHANGES.md"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n## %s\n- fix\n" % version)
        git(self.up, "commit", "-qam", "release " + version)
        git(self.clone, "fetch", "-q")

    def no_beat_session(self, p):
        """Session p0 left a heartbeat, p1 left none; returns p2's session-start line."""
        self.beat(p + "0")
        self.assertIsNone(health.session_start(self.proj, p + "0", "startup"))
        self.assertIsNone(health.session_start(self.proj, p + "1", "startup"))
        self.assertEqual(self.calls, [])
        return health.session_start(self.proj, p + "2", "startup")

    def assert_ran(self, mode):
        self.assertEqual(len(self.calls), 1, self.calls)
        argv, rc, out = self.calls[0]
        self.assertEqual(rc, 0, out[-1500:])
        self.assertEqual("--repair" in argv, mode == "repair")
        mk = self.read_json(self.mod_run(health.MARKER))
        self.assertEqual((mk["state"], mk["mode"], mk["rc"]), ("done", mode, 0))
        with open(self.mod_run("update.log"), encoding="utf-8") as fh:
            self.assertIn("pa3_update: done", fh.read())
        return mk

    def assert_plain(self, line):
        """The line reads in plain words: what broke, what was done, when the mod loads, no internals."""
        self.assertTrue(line.startswith("PA3 health: "), line)
        for word in ("heartbeat", "sid", "None", "Traceback", "{", "hook.json", "update.json", "exit "):
            self.assertNotIn(word, line)

    # ---------------------------------------------------------------- scenarios

    def test_00_fixture_is_healthy(self):
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")
        self.assertEqual(health.registration(os.path.join(self.cfg, "settings.json"), self.pa3)["state"], "ok")
        self.assertEqual(self.root_version(), "3.24.0")
        self.assertTrue(health.mod_on(self.proj))
        self.assertIsNone(health.session_start(self.proj, "h0", "startup"))
        self.assertEqual(self.calls, [])

    def test_01_damaged_root_file_is_repaired(self):
        self.damage()
        files = health.root_files(self.pa3)
        self.assertEqual(files["state"], "bad")
        self.assertEqual(files["bad"], ["mod/pa-watch/hooks/feed.ts", "mod/pa/hooks/seed.ts"])
        line = health.session_start(self.proj, "d1", "startup")
        self.assert_plain(line)
        self.assertIn("2 of the PA3 root's", line)
        self.assertIn("damaged or missing", line)
        self.assertIn("reinstall of the installed release (PA3 3.24.0)", line)
        self.assertIn(health.RELOAD, line)
        self.assert_ran("repair")
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")
        self.assertEqual(self.root_version(), "3.24.0")
        self.assertEqual(health.hook_record(self.proj)["healed"], "d1")

    def test_02_removed_registration_is_restored(self):
        sp = os.path.join(self.cfg, "settings.json")
        doc = self.read_json(sp)
        del doc["extraKnownMarketplaces"]["pa3"]
        self._write(sp, json.dumps(doc, indent=2))
        self.assertEqual(health.registration(sp, self.pa3)["state"], "missing")
        line = health.session_start(self.proj, "r1", "startup")
        self.assert_plain(line)
        self.assertIn("the mod is not registered in the user settings", line)
        self.assert_ran("repair")
        self.assertEqual(health.registration(sp, self.pa3)["state"], "ok")

    def test_03_no_heartbeat_is_repaired_by_reinstall(self):
        line = self.no_beat_session("n")
        self.assert_plain(line)
        self.assertIn("the mod did not load in the last session", line)
        self.assertIn("reinstall of the installed release", line)
        self.assert_ran("repair")
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")

    def test_04_error_record_is_repaired(self):
        self.beat("e0")
        self._write(self.mod_run(health.ERROR), json.dumps(
            {"sid": "e0", "plugin": "pa", "hook": "session.start", "error": "boom", "at": time.time()}))
        self.assertIsNone(health.session_start(self.proj, "e0", "startup"))
        line = health.session_start(self.proj, "e1", "startup")
        self.assert_plain(line)
        self.assertIn("the mod failed with an error in the last session", line)
        self.assert_ran("repair")

    def test_05_hook_entries_carry_the_line(self):
        # session start, through the hook's run: a mod that left no heartbeat
        self.beat("k0")
        self.assertIsNone(health.session_start(self.proj, "k0", "startup"))
        self.assertIsNone(health.session_start(self.proj, "k1", "startup"))
        with mock.patch.object(ss.accounts, "auth_status", return_value={"email": None}), \
                mock.patch.object(ss, "_offer_install", return_value=None), \
                mock.patch.object(ss, "_update_check", return_value=None), \
                mock.patch.object(ss, "_record_behind", return_value=None), \
                mock.patch("pa.repairs.request"):
            out = ss.run({"session_id": "k2", "cwd": self.proj, "source": "startup",
                          "hook_event_name": "SessionStart"}, config.defaults())
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("PA3 health: the mod did not load in the last session", ctx)
        self.assert_ran("repair")
        # the first prompt of a session whose mod did not load: healed in the same session, once
        del self.calls[:]
        os.remove(self.mod_run(health.MARKER))
        inp = {"session_id": "k3", "cwd": self.proj, "prompt": "hello", "hook_event_name": "UserPromptSubmit"}
        with mock.patch.object(ups, "_renewal_after_switch", return_value=None):
            self.assertIsNone(health.session_start(self.proj, "k3", "startup"))   # k2 healed: owes nothing
            out = ups.run(inp, config.defaults())
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assert_plain(ctx)
            self.assertIn("the mod did not load in this session", ctx)
            self.assert_ran("repair")
            self.assertIsNone(ups.run(inp, config.defaults()))
            # a session whose mod loaded: nothing
            self.beat("k4")
            self.assertIsNone(ups.run(dict(inp, session_id="k4"), config.defaults()))
        self.assertEqual(len(self.calls), 1)

    def test_06_below_the_floor_changes_nothing(self):
        self.damage()
        with mock.patch("pa.install.root.claude_version", return_value="2.1.200"):
            line = health.session_start(self.proj, "f1", "startup")
        self.assertIn("damaged or missing", line)
        self.assertIn("Claude Code 2.1.200 is older than", line)
        self.assertIn("nothing was changed", line)
        self.assertIn("update Claude Code (claude update)", line)
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.mod_run(health.MARKER)))
        self.assertEqual(health.root_files(self.pa3)["state"], "bad")
        self.assertNotEqual(health.hook_record(self.proj).get("healed"), "f1")
        rc, out = self.run_update("--repair")                  # the developer's own repair
        self.assertEqual(rc, 0, out[-1500:])
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")

    def test_07_once_per_session_and_single_flight(self):
        self.damage()
        self.assertIsNotNone(health.session_start(self.proj, "o1", "startup"))
        self.assert_ran("repair")
        self.damage()
        self.assertIsNone(health.session_start(self.proj, "o1", "resume"))
        self.assertEqual(len(self.calls), 1)                   # once per session
        self.assertEqual(health.root_files(self.pa3)["state"], "bad")
        # another run holds the lock: a second run does nothing
        self._write(self.mod_run("update.lock"), "1\n")
        rc, out = self.run_update("--repair")
        self.assertEqual(rc, 3, out)
        self.assertIn("another update is running", out)
        self.assertEqual(health.root_files(self.pa3)["state"], "bad")
        # a running marker: the heal starts nothing
        self._write(self.mod_run(health.MARKER), json.dumps({"state": "running", "mode": "repair",
                                                             "at": time.time()}))
        line = health.session_start(self.proj, "o2", "startup")
        self.assert_plain(line)
        self.assertIn("an update run is already going", line)
        self.assertEqual(len(self.calls), 1)
        os.remove(self.mod_run("update.lock"))
        rc, out = self.run_update("--repair")
        self.assertEqual(rc, 0, out[-1500:])
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")

    def test_08_never_a_downgrade(self):
        cv = os.path.join(self.clone, PKG, "VERSION")
        self._write(cv, "3.23.0\n")
        try:
            self.assertIsNone(health.newer_release(self.pa3, self.clone))
            rc, out = self.run_update("--repair")
            self.assertEqual(rc, 2, out)
            self.assertIn("a repair reinstalls only the installed release", out)
            why = ss_upd().refusal(2, self.proj, self.clone, self.pa3)
            self.assertIn("PA3 never installs older code", why)
            self.assertEqual(self.root_version(), "3.24.0")
        finally:
            git(self.clone, "checkout", "--", PKG + "/VERSION")

    def test_08b_two_part_release_is_newer_everywhere(self):
        """3.30 (3.3 T12): a two-part version above 3.29.9 reads newer in every installed comparator, and here."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import pa3_update
        from pa import statusline
        from pa.hooks import session_start
        for key in (health._vkey, pa3_update._version_key, statusline._version_key, session_start._version_key):
            self.assertGreater(key("3.30"), key("3.29.9"), key)
        self.bump("3.30")
        self.assertEqual(health.newer_release(self.pa3, self.clone), "3.30")

    def test_08c_a_fix_above_a_two_part_release_is_newer_everywhere(self):
        """3.30.1: a three-part fix reads newer than the two-part 3.30 in every installed comparator, and here."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import pa3_update
        from pa import statusline
        from pa.hooks import session_start
        for key in (health._vkey, pa3_update._version_key, statusline._version_key, session_start._version_key):
            self.assertGreater(key("3.30.1"), key("3.30"), key)
            self.assertGreater(key("3.30.2"), key("3.30.1"), key)
            self.assertGreater(key("3.30.3"), key("3.30.2"), key)
            self.assertGreater(key("3.30.4"), key("3.30.3"), key)
            self.assertGreater(key("3.30.5"), key("3.30.4"), key)
            self.assertGreater(key("3.30.6"), key("3.30.5"), key)
            self.assertGreater(key("3.30.7"), key("3.30.6"), key)
        self.bump("3.30.1")
        self.assertEqual(health.newer_release(self.pa3, self.clone), "3.30.1")

    def test_09_newer_fix_ask_offers_auto_installs(self):
        self.bump("3.24.1")
        self.assertEqual(health.newer_release(self.pa3, self.clone), "3.24.1")
        self.set_pa("upgrade", "ask")
        self.beat("a0")
        self.assertIsNone(health.session_start(self.proj, "a0", "startup"))
        self.assertIsNone(health.session_start(self.proj, "a1", "startup"))
        self.damage()
        line = health.session_start(self.proj, "a2", "startup")
        self.assert_plain(line)
        self.assertIn("damaged or missing", line)
        # ask: the break is repaired at once (the installed release), the newer one is only offered
        self.assertIn("reinstall of the installed release (PA3 3.24.0)", line)
        self.assertIn("PA3 3.24.1, a newer release, is on offer (upgrade: ask)", line)
        self.assert_ran("repair")
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")
        self.assertEqual(self.root_version(), "3.24.0")
        self.assertEqual(health.hook_record(self.proj)["healed"], "a2")
        self.setUp()
        line = self.no_beat_session("u")
        self.assert_plain(line)
        self.assertIn("started the update to PA3 3.24.1", line)
        mk = self.assert_ran("update")
        self.assertEqual(self.root_version(), "3.24.1")
        self.assertEqual(health.root_files(self.pa3)["state"], "ok")
        self.assertIn("from", mk)
        self.assertIn("to", mk)
        self.assertIsNone(health.newer_release(self.pa3, self.clone))

    def test_09b_start_check_note(self):
        """3.29.1 T8: the hook's note that this start's update check answered, and whether a run is going."""
        before = time.time()
        ss._start_checked(self.proj, "q9", False)
        with open(os.path.join(self.proj, ".run", "mod", "start-check.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["session"], doc["update"], doc["state"]), ("q9", False, "done"))
        self.assertGreaterEqual(doc["at"], before)
        ss._start_checked(self.proj, "q9", True)
        with open(os.path.join(self.proj, ".run", "mod", "start-check.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["update"])

    def test_10_auto_run_after_the_i48_checks(self):
        self.bump("3.24.2")
        offer = "PA3: update available: 1 commits (release 3.24.2) (version 3.24.2). Say yes to update."
        self.assertEqual(ss._auto_run(None, "q0", offer), offer)
        self.set_pa("upgrade", "ask")
        self.assertEqual(ss._auto_run(self.proj, "q1", offer), offer)   # ask: the mod asks
        self.assertEqual(self.calls, [])
        self.set_pa("upgrade", "auto")
        line = ss._auto_run(self.proj, "q2", offer)
        self.assertTrue(line.startswith("PA3: updating: the session-start hook started the update to the newer "
                                        "release (version 3.24.2)"), line)
        self.assertIn(health.RELOAD, line)
        with open(os.path.join(self.proj, ".run", "mod", "release.json"), encoding="utf-8") as fh:   # 3.29.2
            self.assertEqual(json.load(fh), {"version": "3.24.2", "kind": "hotfix", "state": "running"})
        self.assert_ran("update")
        self.assertEqual(self.root_version(), "3.24.2")
        self.assertEqual(health.hook_record(self.proj)["healed"], "q2")

    def test_11_resume_checks_with_mod_on_only(self):
        """3.28 T5: a resume in a mod: on project runs the release check and its auto-run; mod off does not."""
        self.bump("3.24.3")

        def resume(sid):
            with mock.patch.object(ss.accounts, "auth_status", return_value={"email": None}), \
                    mock.patch.object(ss, "_offer_install", return_value=None), \
                    mock.patch.object(health, "session_start", return_value=None), \
                    mock.patch("pa.repairs.request"):
                out = ss.run({"session_id": sid, "cwd": self.proj, "source": "resume",
                              "hook_event_name": "SessionStart"}, config.defaults())
            return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""

        self.set_pa("mod", "off")
        try:
            self.assertNotIn("PA3: update", resume("m0"))
            self.assertEqual(self.calls, [])
        finally:
            self.set_pa("mod", "on")
        ctx = resume("m1")
        self.assertIn("PA3: updating: the session-start hook started the update to the newer release "
                      "(version 3.24.2 -> 3.24.3)", ctx)
        self.assert_ran("update")
        self.assertEqual(self.root_version(), "3.24.3")


def ss_upd():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pa3_update", os.path.join(HERE, "tools", "pa3_update.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
