"""pa.install.managed drift / stamp / decline / bump and tools/managed.py (3.9.7 T7).

Temp project and a ``PA_LEDGER_DIR`` temp ledger; nothing touches ``~/.claude``.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

from pa import db, paths  # noqa: E402
from pa.install import managed  # noqa: E402
import managed as managed_cli  # noqa: E402  (tools/managed.py)

AGENT = ".claude/agents/x.md"
TOOL = "tools/y.py"


class ManagedCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-managed-")
        self.root = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.root, ".claude", "agents"))
        os.makedirs(os.path.join(self.root, "tools"))
        os.environ["PA_LEDGER_DIR"] = os.path.join(self.dir, "ledger")
        files = {}
        for rel, body in ((AGENT, b"---\r\nname: x\r\nversion: 4\r\n---\r\nbody\r\n"),
                          (TOOL, b"print('y')\n")):
            self.put(rel, body)
            files[rel] = {"base": managed.base_of(body.decode()), "sha": managed.sha(body),
                          "user": None}
        with open(self.path(managed.RECORD), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"format": 1, "package": "3.9.7", "installed": "2026-09-25T00:00:00Z",
                       "files": files}, fh, sort_keys=True, indent=2)
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.pop("PA_LEDGER_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def put(self, rel, data):
        with open(self.path(rel), "wb") as fh:
            fh.write(data)

    def get(self, rel):
        with open(self.path(rel), "rb") as fh:
            return fh.read()

    def record(self):
        with open(self.path(managed.RECORD), encoding="utf-8") as fh:
            return json.load(fh)

    def edit_agent(self, extra=b"more\r\n"):
        self.put(AGENT, self.get(AGENT) + extra)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = managed_cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class BumpTest(unittest.TestCase):
    def test_bump(self):
        self.assertEqual(managed.bump("4"), "4+u1")
        self.assertEqual(managed.bump("4+u1"), "4+u2")
        self.assertEqual(managed.bump("12+u9"), "12+u10")
        self.assertEqual(managed.bump("3.10.3"), "3.10.3+u1")
        self.assertEqual(managed.bump("3.10.3+u1"), "3.10.3+u2")
        for bad in (None, "", "user/3", "4.1", "4+x1"):
            self.assertIsNone(managed.bump(bad), bad)

    def test_agent_versions_dotted(self):
        import glob
        paths = glob.glob(os.path.join(HERE, "agents", "*.md"))
        self.assertTrue(paths)
        for p in paths:
            with open(p, encoding="utf-8") as fh:
                v = managed.base_of(fh.read())
            self.assertRegex(v or "", r"^\d+\.\d+(\.\d+)*\.\d+$", p)


class DriftTest(ManagedCase):
    def test_clean_tree_is_none(self):
        self.assertEqual(managed.drift(self.root), [])
        self.assertEqual(self.cli("drift")[1], "none\n")

    def test_lists_edited_not_untouched_and_skips_missing(self):
        self.edit_agent()
        os.remove(self.path(TOOL))
        rows = managed.drift(self.root)
        self.assertEqual([(r[0], r[1], r[3]) for r in rows], [(AGENT, "4", [])])
        self.assertEqual(rows[0][2], managed.sha(self.get(AGENT))[:8])
        self.assertEqual(self.cli("drift")[1],
                         "%s | base 4 | %s | edited in —\n" % (AGENT, rows[0][2]))

    def test_absent_entry_tolerated(self):
        """3.10.6 T2: an agent the tier leaves out is recorded ``absent`` with no file."""
        rec = self.record()
        rec["files"][".claude/agents/expert-fable.md"] = {"absent": "tier", "base": None,
                                                          "sha": None, "user": None}
        with open(self.path(managed.RECORD), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rec, fh, sort_keys=True, indent=2)
        self.assertEqual(managed.drift(self.root), [])
        self.assertEqual(self.cli("drift"), (0, "none\n", ""))
        self.edit_agent()
        self.assertEqual([r[0] for r in managed.drift(self.root)], [AGENT])
        rc, _out, err = self.cli("stamp", ".claude/agents/expert-fable.md")
        self.assertEqual(rc, 2, err)
        with self.assertRaises(KeyError):
            managed.decline(self.root, ".claude/agents/expert-fable.md")

    def test_edited_in_from_ledger_events(self):
        self.edit_agent()
        paths.ensure_ledger_tree()
        conn = db.connect(paths.db_path())
        db.init_schema(conn)
        for task, rel in (("T3", AGENT), ("T5", AGENT), ("T3", AGENT), ("T9", TOOL), (None, AGENT)):
            db.insert_event(conn, "managed_edit", {"path": rel, "task": task, "tool": "Edit"},
                            session_id="s")
        db.close(conn)
        self.assertEqual(managed.drift(self.root)[0][3], ["T3", "T5"])
        self.assertTrue(self.cli("drift")[1].rstrip().endswith("edited in T3,T5"))


class StampDeclineTest(ManagedCase):
    def test_stamp_twice_bumps_u1_then_u2(self):
        before = self.record()["files"][AGENT]
        self.edit_agent()
        rc, out, _ = self.cli("stamp", AGENT)
        self.assertEqual((rc, out), (0, "stamped %s +u1\n" % AGENT))
        data = self.get(AGENT)
        self.assertEqual(data, b"---\r\nname: x\r\nversion: 4+u1\r\n---\r\nbody\r\nmore\r\n")
        ent = self.record()["files"][AGENT]
        self.assertEqual((ent["base"], ent["sha"]), (before["base"], before["sha"]))
        self.assertEqual(ent["user"]["sha"], managed.sha(data))
        self.assertEqual((ent["user"]["mark"], ent["user"]["state"]), ("+u1", "stamped"))
        self.assertEqual(managed.drift(self.root), [])

        self.edit_agent(b"again\r\n")
        self.assertEqual([r[0] for r in managed.drift(self.root)], [AGENT])
        managed.stamp(self.root, AGENT)
        data = self.get(AGENT)
        self.assertIn(b"version: 4+u2\r\n", data)
        ent = self.record()["files"][AGENT]
        self.assertEqual((ent["user"]["sha"], ent["user"]["mark"]), (managed.sha(data), "+u2"))
        self.assertEqual(ent["sha"], before["sha"])
        self.assertEqual(managed.drift(self.root), [])

    def test_stamp_without_version_line(self):
        self.put(TOOL, b"print('mine')\n")
        user = managed.stamp(self.root, TOOL)
        self.assertEqual((user["mark"], user["state"]), (None, "stamped"))
        self.assertEqual(self.get(TOOL), b"print('mine')\n")
        self.assertEqual(managed.drift(self.root), [])

    def test_decline_silent_until_bytes_change(self):
        self.edit_agent()
        rc, out, _ = self.cli("decline", AGENT)
        self.assertEqual((rc, out), (0, "declined %s\n" % AGENT))
        ent = self.record()["files"][AGENT]
        self.assertEqual(ent["user"]["state"], "declined")
        self.assertIsNone(ent["user"]["mark"])
        self.assertIn(b"version: 4\r\n", self.get(AGENT))        # file untouched
        self.assertEqual(managed.drift(self.root), [])
        self.edit_agent(b"later\r\n")
        self.assertEqual([r[0] for r in managed.drift(self.root)], [AGENT])

    def test_record_format_and_unlisted_rel(self):
        self.edit_agent()
        managed.decline(self.root, AGENT)
        with open(self.path(managed.RECORD), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(raw.decode(), json.dumps(json.loads(raw), sort_keys=True, indent=2) + "\n")
        rc, _, err = self.cli("stamp", "tools/nope.py")
        self.assertEqual(rc, 2)
        self.assertIn("not listed", err)


class PackageVersionTest(unittest.TestCase):
    def test_upstream_version_from_package_version_file(self):
        """I11: the record's ``upstream_version`` is the package VERSION, else ``__version__``."""
        from pa import __version__
        self.assertEqual(managed._package_version(HERE), "3.14.2")
        d = tempfile.mkdtemp(prefix="pa3-pkgver-")
        try:
            self.assertEqual(managed._package_version(d), __version__)
            with open(os.path.join(d, "VERSION"), "w", encoding="utf-8") as fh:
                fh.write("3.12\n")
            self.assertEqual(managed._package_version(d), "3.12")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PaSessionStepTest(unittest.TestCase):
    def test_router_drift_step_and_version(self):
        with open(os.path.join(HERE, "agents", "pa-session.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(managed.base_of(text), "3.14.1.24")  # 3.14.1 a discuss return is the RECORD notification; was "3.14.23" (3.14 T2 migration curate line in any mode), "3.13.22" (3.13 T2 router asks before each curator run), "3.13.21" (3.13 T1 auditor spawnable), "3.12.20" (3.12 T1 Sonnet 5.5), "3.11.19" (3.11 T13 the expert's label T<n> <title>), "3.11.18" (3.11 T10 the generic push line), "3.11.17" (3.11 T9 the renewal question), "3.10.6.16" (3.10.6 T5 window gate), "3.10.5.15", "3.10.5.14" (T22 recalc before assemble), "3.10.13" (fix-19 no pause before the archive), "3.10.12" (T17 route every message, denials to the developer), "3.10.11" (T20 one wrapper, denial question), "3.10.10" (T16 root-install paragraph), "3.10.9" (T15.c4 older hook), "3.10.8" (T15 automatic upgrade)
        step1 = text[text.index("\n1. `PY tools/status.py inbox-consume`"):text.index("\n2. `PY tools/plan_edit.py next")]
        for s in ("PY tools/managed.py drift", "PY tools/managed.py stamp <rel>",
                  "PY tools/managed.py decline <rel>", "AskUserQuestion",
                  '"stamp|decline <rel>" <rel> .claude/pa3-managed.json'):
            self.assertIn(s, step1)
        self.assertIn("the step-1 drift question", text)

    def test_update_pa3_step(self):
        with open(os.path.join(HERE, "agents", "pa-session.md"), encoding="utf-8") as fh:
            text = fh.read()
        for s in ("`PA3 update`", "Upgrade: auto", "Upgrade: ask", "update pa3", "upgrade-<n>",
                  ".claude/pa3-upgrade/UPGRADE.md", "CHANGES.md", "restart: needed", "what changed"):
            self.assertIn(s, text)

    def test_update_step_wrapper_denial_resolve(self):
        with open(os.path.join(HERE, "agents", "pa-session.md"), encoding="utf-8") as fh:
            text = fh.read()
        step = text[text.index("`PA3 update` (every mode"):text.index("`what changed` (any mode)")]
        for s in ("tools/pa3_update.py", "one-time permission", "resolve:"):
            self.assertIn(s, step)

    def test_planner_has_no_upgrade_task(self):
        with open(os.path.join(HERE, "agents", "planner-phase.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("upgrade task", text)
        self.assertNotIn("`Upgrade:`", text)


class PaSessionAgentListTest(unittest.TestCase):
    def test_every_named_agent_is_spawnable(self):
        agents_dir = os.path.join(HERE, "agents")
        with open(os.path.join(agents_dir, "pa-session.md"), encoding="utf-8") as fh:
            text = fh.read()
        front, body = text.split("\n---\n", 1)
        tools = next(l for l in front.splitlines() if l.startswith("tools:"))
        spawnable = {s.strip() for s in tools[tools.index("Agent(") + 6:tools.rindex(")")].split(",")}
        stems = sorted(f[:-3] for f in os.listdir(agents_dir) if f.endswith(".md") and f != "pa-session.md")
        missing = [s for s in stems if "`%s`" % s in body and s not in spawnable]
        self.assertEqual(missing, [], "pa-session body names agents missing from Agent(...): %s" % missing)


if __name__ == "__main__":
    unittest.main()
