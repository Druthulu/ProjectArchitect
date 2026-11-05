"""Tests for tools/pa_bugreport.py (3.32.1 T6): a fixture project with a fake config folder.

    cd project-architect-3.0 && python -m unittest tests.test_pa_bugreport -v
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "tools", "pa_bugreport.py")
spec = importlib.util.spec_from_file_location("pa_bugreport", TOOL)
bug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bug)

EXPERT = "a0123456000000001"
SECRETS = ("abc123", "xyz789", "hunter2", "alice@example.com")


def iso(offset_s):
    return datetime.fromtimestamp(time.time() + offset_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-bugreport-")
        self.home = os.path.join(self.tmp, "alice")
        self.root = os.path.join(self.home, "proj")
        self.cfg = os.path.join(self.home, ".claude")
        r, cfg = self.root, self.cfg
        put(os.path.join(self.home, ".claude", "pa3", "VERSION"), "version: 3.30.9\nsource: x\n")
        put(os.path.join(r, ".claude", "pa.json"), {"pa_version": "3.30.9", "mod": "on", "preset": "max", "drive": "coop",
                                                    "upgrade": "auto"})
        put(os.path.join(r, ".claude", "agents", "pa-expert.md"), "---\nname: pa-expert\nmodel: opus\neffort: medium\n---\nbody\n")
        put(os.path.join(cfg, "settings.json"), {"permissions": {"defaultMode": "acceptEdits"},
                                                 "enabledPlugins": {"other@market": True, "old@market": False}})
        put(os.path.join(r, ".claude", "settings.json"), {"enabledPlugins": {"pa-watch@pa3": True}})
        put(os.path.join(cfg, "plugins", "installed_plugins.json"),
            {"version": 2, "plugins": {"other@market": [{"version": "1.2.0", "installPath": "C:\\Users\\alice\\p"}]}})
        now_ms = int(time.time() * 1000)
        put(os.path.join(r, ".run", "mod", "loop.json"), {
            "v": 1, "ownerSid": "sess1", "heartbeatAt": now_ms - 90000, "state": "running", "holds": ["talk"],
            "task": {"id": "T3", "attempt": 1, "agentId": EXPERT, "childBusy": True, "pendingContract": "STATUS: done " * 20},
            "queuedEdits": [{"command": "x"}], "inboxDone": {"%d-1-release" % (now_ms - 60000): now_ms}})
        put(os.path.join(r, ".run", "status.json"), {"phase": "3.1", "waiting": None})
        put(os.path.join(r, ".run", "mod", "update.json"), {"state": "done", "mode": "auto", "rc": 0, "at": time.time() - 600})
        inbox = os.path.join(r, ".run", "mod", "sess1", "inbox")
        put(os.path.join(inbox, "%d-1-release.json" % (now_ms - 60000)), {"kind": "release", "input": {"hold": "review"}})
        self.unanswered = "%d-2-release" % (now_ms - 30000)
        put(os.path.join(inbox, self.unanswered + ".json"), {"kind": "release", "input": {"hold": "update"}})
        put(os.path.join(r, ".run", "mod", "sess1", "agents", EXPERT + ".json"),
            {"type": "pa-expert", "parentId": "", "updatedAt": now_ms})
        put(os.path.join(r, ".run", "mod", "sess1", "shells.json"),
            {EXPERT: [{"id": "b1", "name": "run.sh", "at": now_ms, "type": "pa-expert"}]})
        put(os.path.join(r, ".claude-state", "journal", "INDEX-2026-11.md"), "\n".join([
            "%s | T3 | start | expert %s" % (iso(-3600), EXPERT),
            "%s | T3 | interim | done: the change is in" % iso(-3000),
            "%s | T3 | end | done" % iso(-1200)]) + "\n")
        proj = os.path.join(cfg, "projects", re.sub(r"[^A-Za-z0-9]", "-", r))
        put(os.path.join(proj, "sess1.jsonl"), json.dumps({"type": "user", "version": "2.1.300", "timestamp": iso(-3600),
                                                           "message": {"content": "hi"}}) + "\n")
        sub = os.path.join(proj, "sess1", "subagents")
        put(os.path.join(sub, "agent-%s.meta.json" % EXPERT), {"agentType": "pa-expert", "description": "T3 expert"})
        words = ("Stuck. See C:\\Users\\alice\\proj, C:/Users/alice/x, /Users/alice/y, /home/alice/z, "
                 "C--Users-alice-proj; mail alice@example.com; API_KEY=abc123, token: xyz789, Password hunter2; alice waits.")
        put(os.path.join(sub, "agent-%s.jsonl" % EXPERT), "\n".join(json.dumps(e) for e in [
            {"type": "assistant", "version": "2.1.310", "timestamp": iso(-3500), "message": {"model": "claude-opus", "content": [
                {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "ls C:/Users/alice/proj " + "x" * 200}}]}},
            {"type": "assistant", "version": "2.1.310", "timestamp": iso(-3400), "message": {"content": [
                {"type": "text", "text": words}]}}]) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def report(self):
        return bug.report(self.root, self.cfg, self.home, 30)

    def test_every_section(self):
        t = self.report()
        for h in ("## 1. Versions", "## 2. Plugins", "## 3. The loop", "## 4. Requests nobody answered", "## 5. The journal",
                  "## 6. Helpers", "## 7. The newest stall snapshot"):
            self.assertIn(h, t)
        self.assertIn("PA3 installed: 3.30.9", t)
        self.assertIn("Claude Code: 2.1.310", t)
        self.assertIn("mod on, preset max, drive coop, upgrade auto", t)
        self.assertIn("home settings: acceptEdits", t)
        self.assertIn("Agent pa-expert: model opus, effort medium", t)
        self.assertIn("old@market (off), other@market", t)
        self.assertIn("pa-watch@pa3", t)
        self.assertIn("other@market 1.2.0", t)
        self.assertIn("state running, holds ['talk'], ownerSid sess1", t)
        self.assertIn("queuedEdits 1", t)
        self.assertIn('Update marker: state "done"', t)
        self.assertIn("| T3 | interim |", t)
        self.assertRegex(t, r"T3 %s: started .* waited 30 min" % EXPERT)
        self.assertIn("#### pa-expert %s" % EXPERT, t)
        self.assertIn("agent %s: type pa-expert" % EXPERT, t)
        self.assertIn("shell of %s: b1 run.sh" % EXPERT, t)

    def test_unanswered_release(self):
        t = self.report()
        sec = t.split("## 4.")[1].split("## 5.")[0]
        self.assertIn("%s, kind release, hold update" % self.unanswered, sec)
        self.assertNotIn("hold review", sec)

    def test_scrub(self):
        t = self.report()
        self.assertNotIn("alice", t.lower())
        for s in SECRETS:
            self.assertNotIn(s, t)
        for s in ("~\\proj", "~/x", "~/y", "~/z", "~-proj", "<email>", "API_KEY=[redacted]", "token: [redacted]",
                  "Password [redacted]", "you waits"):
            self.assertIn(s, t)
        self.assertIn(EXPERT, t)  # ids stay
        self.assertTrue(t.startswith("# PA3 bug report"))
        self.assertIn("Read it before you post it", t)
        cmd = re.search(r"  - \d\d-\d\d \d\d:\d\d Bash (ls .*)", t).group(1)
        self.assertLessEqual(len(cmd), 120)

    def test_missing_files(self):
        t = self.report()
        self.assertIn("(missing: `.run/mod/stalls`, skipped)", t)
        self.assertIn("(missing: `.claude/settings.local.json`, skipped)", t)
        self.assertIn("(missing: `.run/mod/release.json`, skipped)", t)
        shutil.rmtree(os.path.join(self.root, ".run"))
        t = self.report()
        self.assertIn("(missing: `.run/mod/loop.json`, skipped)", t)
        self.assertIn("(no inbox under `.run/mod/`, skipped)", t)

    def test_cap_and_output(self):
        put(os.path.join(self.root, ".run", "mod", "stalls", "T3-20261101-100000.md"), "stall line\n" * 20000)
        t = self.report()
        self.assertLessEqual(len(t), bug.CAP)
        self.assertTrue(t.rstrip().endswith("(cut at 120000 characters)"))
        p = subprocess.run([sys.executable, "-X", "utf8", TOOL, "--config-dir", self.cfg, "--home", self.home],
                           cwd=self.root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = re.search(r"wrote (\S+)", p.stdout).group(1)
        self.assertRegex(os.path.basename(out), r"^pa-bugreport-\d{8}-\d{4}\.md$")
        self.assertEqual(os.path.dirname(os.path.abspath(out)), os.path.join(self.root, ".run"))
        with open(out, encoding="utf-8") as fh:
            self.assertLessEqual(len(fh.read()), bug.CAP)


if __name__ == "__main__":
    unittest.main()
