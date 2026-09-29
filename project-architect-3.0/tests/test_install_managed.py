"""The managed-file record ``.claude/pa3-managed.json`` (3.9.7 T6).

    cd project-architect-3.0 && python -m unittest tests.test_install_managed -v
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_install_project import ProjectCase, read, write   # noqa: E402
from pa.install import managed                              # noqa: E402
from pa.install import project as pj                        # noqa: E402

RECORD = ".claude/pa3-managed.json"
GROUPS = (".claude/agents/", ".claude/skills/project-architect/", ".claude/commands/",
          "tools/", "templates/", "docs/project-architect.md")      # 3.11 T25: the doc


class TestHelpers(unittest.TestCase):

    def test_sha_folds_crlf(self):
        self.assertEqual(managed.sha(b"a\r\nb\n"), managed.sha(b"a\nb\n"))

    def test_base_of(self):
        self.assertEqual(managed.base_of("---\nname: x\nversion: 1.2 \n---\nversion: 9\n"), "1.2")
        self.assertIsNone(managed.base_of("---\nname: x\n---\nversion: 9\n"))
        self.assertIsNone(managed.base_of("version: 9\n"))


class TestManagedRecord(ProjectCase):

    def install_capturing(self, *extra):
        made = []
        base = pj.Manifest

        class Spy(base):
            def __init__(self):
                base.__init__(self)
                made.append(self)

        with mock.patch.object(pj, "Manifest", Spy):
            out = self.install(*extra)
        return out, made[-1]

    def record(self):
        with open(self.path(*RECORD.split("/")), "rb") as fh:
            raw = fh.read()
        return raw, json.loads(raw.decode("utf-8"))

    def test_fresh_install_records_every_placed_managed_file(self):
        out, man = self.install_capturing()
        raw, rec = self.record()
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(rec["format"], 1)
        self.assertEqual(rec["package"], pj._pa3_version(pj.package_root()))
        self.assertTrue(rec["installed"].endswith("Z"))
        placed = {rel for rel in man.created if rel.startswith(GROUPS)}
        self.assertEqual(set(rec["files"]), placed, out)
        self.assertEqual(len(rec["files"]), len(placed))
        self.assertIn(RECORD, man.created)
        self.assertNotIn(RECORD, rec["files"])
        for rel, entry in rec["files"].items():
            with open(self.path(*rel.split("/")), "rb") as fh:
                self.assertEqual(entry["sha"], managed.sha(fh.read()), rel)
            self.assertIsNone(entry["user"], rel)
        agent = ".claude/agents/critic.md"
        text = read(self.path(*agent.split("/")))
        version = [l for l in text.splitlines() if l.startswith("version:")][0]
        self.assertEqual(rec["files"][agent]["base"], version.split(":", 1)[1].strip())

    def test_second_refresh_leaves_the_record_untouched(self):
        self.install()
        path = self.path(*RECORD.split("/"))
        before = read(path)
        os.utime(path, (1000000000, 1000000000))
        mtime = os.stat(path).st_mtime
        out, man = self.install_capturing()
        self.assertEqual(read(path), before)
        self.assertEqual(os.stat(path).st_mtime, mtime)
        self.assertEqual(man.created, [], out)
        self.assertEqual(man.forced, [], out)
        self.assertNotIn(RECORD, man.updated)

    def test_pre_manifest_refresh_omits_an_edited_file(self):
        self.install()
        os.remove(self.path(*RECORD.split("/")))
        agent = ".claude/agents/critic.md"
        apath = self.path(*agent.split("/"))
        write(apath, read(apath) + "\nmy own line\n")
        out, man = self.install_capturing()
        _raw, rec = self.record()
        self.assertNotIn(agent, rec["files"])
        self.assertIn("= %s (base unknown: differs from the package, not recorded)" % agent, out)
        same = {rel for rel in man.skipped if rel.startswith(GROUPS)}
        self.assertTrue(same)
        self.assertEqual(set(rec["files"]), same)
        self.assertIn(RECORD, man.created)


class TestLadderInstall(ProjectCase):
    """3.10.6 T2: agents placed through the ladder at the resolved plan tier."""

    FABLE = ".claude/agents/expert-fable.md"
    install_capturing = TestManagedRecord.install_capturing
    record = TestManagedRecord.record

    def agent(self, name):
        return read(self.path(".claude", "agents", name + ".md"))

    def test_fresh_install_max5_and_pro_leave_expert_fable_out(self):
        from pa.install import ladder
        for tier in ("max5", "pro"):
            with self.subTest(tier=tier):
                self.tearDown()
                self.setUp()
                self.set_tier(tier)
                out, man = self.install_capturing()
                self.assertFalse(os.path.exists(self.path(*self.FABLE.split("/"))), out)
                self.assertNotIn(self.FABLE, man.created)
                _raw, rec = self.record()
                self.assertEqual(rec["files"][self.FABLE]["absent"], "tier")
                self.assertIsNone(rec["files"][self.FABLE]["sha"])
                for name in ("critic", "plain"):
                    r = ladder.row(name, tier)
                    self.assertIn("\nmodel: %s\neffort: %s\n" % (r["model"], r["effort"]),
                                  self.agent(name))
                    rel = ".claude/agents/%s.md" % name
                    with open(self.path(*rel.split("/")), "rb") as fh:
                        self.assertEqual(rec["files"][rel]["sha"], managed.sha(fh.read()))

    def test_max5_refresh_is_same(self):
        self.set_tier("max5")
        self.install()
        path = self.path(*RECORD.split("/"))
        before = read(path)
        out, man = self.install_capturing()
        self.assertEqual(read(path), before)
        self.assertEqual([r for r in man.updated + man.created + man.removed
                          if r.startswith(".claude/agents/")], [], out)
        self.assertIn("restart: not needed", out)

    def test_forced_env_preset_wins(self):
        env = pj.Proj(preset="pro", repo=self.repo)
        self.assertEqual(pj._preset(env), "pro")
        self.assertEqual(pj._preset(pj.Proj(repo=self.repo)), "max20")


if __name__ == "__main__":
    unittest.main()
