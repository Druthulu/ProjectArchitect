"""pa.notify: what the detached child would be, and the toast rate limit.

Nothing here starts a process: :func:`pa.notify.set_spawner` swaps the spawner
for a recorder, so the tests assert the exact argv/env the hook would hand to
PowerShell (or msg.exe / notify-send) and the rate-limit state machine around it.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import config, notify  # noqa: E402


class Recorder(object):
    """Stand-in for ``subprocess.Popen``; records argv + env instead."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, env):
        self.calls.append((list(cmd), dict(env)))
        return True

    @property
    def last(self):
        return self.calls[-1] if self.calls else (None, None)


class NotifyTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-notify-")
        os.environ["PA_LEDGER_DIR"] = self.dir
        self.rec = Recorder()
        notify.set_spawner(self.rec)
        self.cfg = config.defaults()

    def tearDown(self):
        notify.set_spawner(None)
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- command construction

    def test_toast_records_the_command_it_would_run(self):
        out = notify.toast("PA3 · probe", "body text", tag="t1", kind="stop",
                           session_id="s1", cfg=self.cfg)
        self.assertTrue(out["sent"])
        cmd, env = self.rec.last
        self.assertEqual(len(self.rec.calls), 1)
        self.assertTrue(cmd, "a command must be built on this machine")
        self.assertEqual(env["PA_TOAST_TITLE"], "PA3 · probe")
        self.assertEqual(env["PA_TOAST_BODY"], "body text")
        self.assertEqual(env["PA_TOAST_TAG"], "t1")
        joined = " ".join(cmd).lower()
        self.assertTrue("powershell" in joined or "msg" in joined or "notify-send" in joined)
        if "powershell" in joined:
            self.assertIn("-NoProfile", cmd)
            self.assertIn("-File", cmd)
            self.assertEqual(cmd[cmd.index("-WindowStyle") + 1], "Hidden")   # a hidden console: toasts need one
            self.assertEqual(cmd[-1].replace("\\", "/").split("/")[-1], "notify_toast.ps1")

    def test_wsl_forwards_the_text_to_windows(self):
        """3.14.1: WSLENV ``/w`` reaches powershell.exe from WSL; ``/u`` (Win32 -> WSL) left it blank."""
        real = notify._exists
        notify._exists = lambda path: True
        try:
            cmd, env, channel = notify.build_command("PA3 · t", "b", tag="g", machine="wsl")
        finally:
            notify._exists = real
        self.assertEqual(channel, "toast-wsl")
        flags = dict(v.split("/", 1) for v in env["WSLENV"].split(":") if v.startswith("PA_TOAST_"))
        self.assertEqual(flags, {"PA_TOAST_TITLE": "w", "PA_TOAST_BODY": "w", "PA_TOAST_TAG": "w"})

    def test_toast_script_exists_next_to_the_module(self):
        self.assertTrue(os.path.exists(notify.script_path()))

    def test_explicit_spawner_argument_is_honoured(self):
        rec = Recorder()
        notify.toast("t", "b", kind="stop", session_id="s-explicit", cfg=self.cfg, spawner=rec)
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(len(self.rec.calls), 0)

    def test_disabled_toasts_do_nothing(self):
        cfg = config.defaults()
        cfg["notify"]["toast"] = False
        out = notify.toast("t", "b", kind="stop", session_id="s2", cfg=cfg)
        self.assertFalse(out["sent"])
        self.assertEqual(self.rec.calls, [])

    def test_discord_webhook_moves_the_work_into_one_python_child(self):
        cfg = config.defaults()
        cfg["notify"]["discord_webhook"] = "https://discord.example/hook"
        cfg["notify"]["discord_events"] = ["question"]
        notify.toast("title", "body", kind="question", session_id="s3", cfg=cfg)
        cmd, env = self.rec.last
        self.assertIn("-c", cmd)
        self.assertEqual(env["PA_DISCORD_WEBHOOK"], "https://discord.example/hook")
        self.assertTrue(json.loads(env["PA_TOAST_CMD"]), "the toast still runs in that child")

    # ---- rate limit (design A C.2)

    def test_second_toast_within_20s_is_rate_limited(self):
        first = notify.toast("a", "b", kind="stop", session_id="sid", cfg=self.cfg, now=1000.0)
        second = notify.toast("a", "b", kind="stop", session_id="sid", cfg=self.cfg, now=1005.0)
        self.assertTrue(first["sent"])
        self.assertFalse(second["sent"])
        self.assertIn("rate-limited", second["reason"])
        self.assertEqual(len(self.rec.calls), 1)

    def test_question_is_exempt_from_the_rate_limit(self):
        notify.toast("a", "b", kind="stop", session_id="sid", cfg=self.cfg, now=1000.0)
        out = notify.toast("q", "?", kind="question", session_id="sid", cfg=self.cfg, now=1005.0)
        self.assertTrue(out["sent"])
        self.assertEqual(len(self.rec.calls), 2)

    def test_every_exempt_kind_passes(self):
        for kind in sorted(notify.EXEMPT_KINDS):
            with self.subTest(kind=kind):
                ok, why = notify.allowed_now(kind, "sid", self.cfg, now=1001.0)
                self.assertTrue(ok, why)

    def test_rate_limit_expires(self):
        notify.toast("a", "b", kind="stop", session_id="sid", cfg=self.cfg, now=1000.0)
        out = notify.toast("a", "b", kind="stop", session_id="sid", cfg=self.cfg, now=1021.0)
        self.assertTrue(out["sent"])

    def test_rate_limit_is_per_session(self):
        notify.toast("a", "b", kind="stop", session_id="one", cfg=self.cfg, now=1000.0)
        out = notify.toast("a", "b", kind="stop", session_id="two", cfg=self.cfg, now=1001.0)
        self.assertTrue(out["sent"])

    def test_state_file_stays_bounded(self):
        for i in range(70):
            notify.toast("a", "b", kind="stop", session_id="s%d" % i, cfg=self.cfg,
                         now=1000.0 + i)
        with open(os.path.join(self.dir, "state", "notify.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertLessEqual(len(state["sessions"]), 64)

    # ---- text helper

    # ---- the pa.json toast level (3.9.5 T2)

    def test_toast_level_values(self):
        for val, level in ((True, "all"), (False, "off"), ("waiting", "waiting"), ("all", "all"),
                           ("off", "off"), ("ALL", "all"), (None, "waiting"), ("loud", "waiting")):
            with self.subTest(val=val):
                self.assertEqual(config.toast_level({"toast": val}), level)
        self.assertEqual(config.toast_level({}), "waiting")
        self.assertEqual(config.toast_level(None), "waiting")

    def test_hooks_toast_decides_by_level_and_cause_and_records_each(self):
        from pa import db, hooks, paths

        cases = (("off", "question", 0), ("off", "crash", 0),
                 ("waiting", "question", 1), ("waiting", "stop", 0), ("waiting", "idle", 0),
                 ("waiting", "model", 0), ("waiting", "crash", 1),
                 ("all", "stop", 1), ("all", "model", 1),
                 ("off", "empty", 0), ("waiting", "empty", 0), ("all", "empty", 0))
        for i, (level, cause, sent) in enumerate(cases):
            with self.subTest(level=level, cause=cause):
                out = hooks.toast(self.cfg, "t", "b", kind="question", session_id="lv%d" % i,
                                  project_cfg={"toast": level}, cause=cause, run_id="r%d" % i)
                self.assertEqual(bool(out["sent"]), bool(sent))
        self.assertEqual(len(self.rec.calls), sum(c[2] for c in cases))
        conn = db.connect(paths.db_path())
        try:
            got = [json.loads(r[0]) for r in conn.execute(
                "SELECT detail_json FROM events WHERE kind='toast' ORDER BY rowid").fetchall()]
        finally:
            db.close(conn)
        self.assertEqual([(d["level"], d["cause"], d["sent"]) for d in got], list(cases))

    def test_waiting_causes_are_the_planned_set(self):
        from pa import hooks

        self.assertEqual(hooks.WAITING_CAUSES, ("question", "review", "replan", "permission",
                                                "input", "discussion", "crash"))

    def test_plain_strips_markdown_and_truncates(self):
        self.assertEqual(notify.plain("**bold**  `code`\n\nnext"), "bold code next")
        self.assertEqual(len(notify.plain("x" * 500)), 240)
        self.assertEqual(notify.plain(None), "")


if __name__ == "__main__":
    unittest.main()
