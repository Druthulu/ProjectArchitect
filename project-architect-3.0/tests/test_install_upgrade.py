"""The ``--project`` refresh against ``.claude/pa3-managed.json`` (3.9.7 T8).

    cd project-architect-3.0 && python -m unittest tests.test_install_upgrade -v
"""

import hashlib
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_install_project import ProjectCase, git           # noqa: E402
from pa.install import managed                              # noqa: E402
from pa.install import project as pj                        # noqa: E402

RECORD = ".claude/pa3-managed.json"
UPGRADE = ".claude/pa3-upgrade/UPGRADE.md"
A, B, C = (".claude/agents/critic.md", ".claude/agents/coder-opus55.md",
           ".claude/agents/auditor.md")
COPY = ("agents", "commands", "rules-seed", "settings", "skills", "templates", "tools")


class TestUpgradeRefresh(ProjectCase):

    def setUp(self):
        ProjectCase.setUp(self)
        src = pj.package_root()
        self.pkg = os.path.join(self.tmp, "pkg")
        for name in COPY:
            shutil.copytree(os.path.join(src, name), os.path.join(self.pkg, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(os.path.join(src, "project-architect-3.0.md"),
                        os.path.join(self.pkg, "project-architect-3.0.md"))
        patcher = mock.patch.object(pj, "package_root", return_value=self.pkg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.install()
        self.rec0 = self.record()
        self.append(self.path(*A.split("/")), b"\nmy line for A\n")
        self.append(self.path(*C.split("/")), b"\nmy line for C\n")
        self.append(os.path.join(self.pkg, "agents", "critic.md"), b"\nupstream A\n")
        self.append(os.path.join(self.pkg, "agents", "coder-opus55.md"), b"\nupstream B\n")

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def append(path, data):
        with open(path, "ab") as fh:
            fh.write(data)

    @staticmethod
    def raw(path):
        with open(path, "rb") as fh:
            return fh.read()

    def repo_bytes(self, rel):
        return self.raw(self.path(*rel.split("/")))

    def pkg_bytes(self, name):
        return self.raw(os.path.join(self.pkg, "agents", name))

    def record(self):
        return json.loads(self.repo_bytes(RECORD).decode("utf-8"))

    def walk(self):
        out = {}
        for dirpath, dirs, files in os.walk(self.repo):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                full = os.path.join(dirpath, name)
                out[os.path.relpath(full, self.repo)] = hashlib.sha256(self.raw(full)).hexdigest()
        return out

    # -- tests ------------------------------------------------------------ #
    def test_refresh_classifies_and_stages_the_conflict(self):
        a_before, c_before = self.repo_bytes(A), self.repo_bytes(C)
        out = self.install()
        self.assertEqual(self.repo_bytes(A), a_before)
        self.assertEqual(self.repo_bytes(".claude/pa3-upgrade/%s.upstream" % A),
                         self.pkg_bytes("critic.md"))
        lines = [l for l in self.repo_bytes(UPGRADE).decode("utf-8").splitlines()
                 if l.startswith("- ")]
        self.assertEqual(len(lines), 1, lines)
        line = lines[0]
        self.assertTrue(line.startswith("- %s | " % A), line)
        self.assertIn("| base %s |" % self.rec0["files"][A]["base"], line)
        self.assertIn("| yours %s |" % managed.sha(a_before)[:8], line)
        self.assertIn("| compare: no series |", line)
        self.assertIn("! %s kept yours; upstream staged" % A, out)
        self.assertIn("  conflict %s\n" % A, out)
        self.assertIn("  upstream-changed %s\n" % B, out)
        self.assertIn("  user-changed %s\n" % C, out)
        self.assertRegex(out, r"classes: unchanged \d+, upstream-changed 1, user-changed 1"
                              r", merged 0, replaced 0, conflict 1, created 0\n")
        self.assertIn("restart: needed\n", out)
        self.assertEqual(self.repo_bytes(B), self.pkg_bytes("coder-opus55.md"))
        self.assertEqual(self.repo_bytes(C), c_before)
        self.assertFalse(os.path.exists(self.path(*(".claude/pa3-upgrade/%s.upstream" % C)
                                                  .split("/"))))
        rec = self.record()
        self.assertEqual(set(rec["files"]), set(self.rec0["files"]))
        changed = {rel for rel in rec["files"] if rec["files"][rel] != self.rec0["files"][rel]}
        self.assertEqual(changed, {B})

    def test_force_refresh_overwrites_all(self):
        self.install("--force")
        self.assertEqual(self.repo_bytes(A), self.pkg_bytes("critic.md"))
        self.assertEqual(self.repo_bytes(B), self.pkg_bytes("coder-opus55.md"))
        self.assertEqual(self.repo_bytes(C), self.pkg_bytes("auditor.md"))

    def test_dry_run_refresh_writes_nothing(self):
        before = self.walk()
        out = self.install("--dry-run")
        self.assertEqual(self.walk(), before)
        self.assertRegex(out, r"classes: unchanged \d+, upstream-changed \d+, user-changed \d+"
                              r", merged \d+, replaced \d+, conflict \d+, created \d+\n")

    def test_crlf_upstream_staged_lf_only(self):
        src = os.path.join(self.pkg, "agents", "critic.md")
        lf = self.raw(src).replace(b"\r\n", b"\n")
        with open(src, "wb") as fh:
            fh.write(lf.replace(b"\n", b"\r\n"))
        self.install()
        staged = self.repo_bytes(".claude/pa3-upgrade/%s.upstream" % A)
        self.assertNotIn(b"\r\n", staged)
        self.assertEqual(staged, lf)

    def test_crlf_managed_copy_healed_lf_only(self):
        rel = ".claude/agents/expert-opus55.md"
        lf = self.repo_bytes(rel)
        with open(self.path(*rel.split("/")), "wb") as fh:
            fh.write(lf.replace(b"\n", b"\r\n"))
        out = self.install()
        self.assertEqual(self.repo_bytes(rel), lf)
        self.assertIn("~ %s rewritten LF-only (CRLF line endings)" % rel, out)
        for label in ("upstream-changed", "user-changed", "conflict", "created", "forced"):
            self.assertNotIn("  %s %s\n" % (label, rel), out)    # class stays unchanged
        self.assertEqual(self.record()["files"][rel], self.rec0["files"][rel])

    # -- T20: resolutions recorded on UPGRADE.md lines ----------------------- #
    def resolve(self, verdict):
        """Stage A's conflict, then append `` | resolve: <verdict>`` to its UPGRADE.md line."""
        self.install()
        text = self.repo_bytes(UPGRADE).decode("utf-8")
        lines = [l + " | resolve: " + verdict if l.startswith("- %s | " % A) else l
                 for l in text.split("\n")]
        with open(self.path(*UPGRADE.split("/")), "wb") as fh:
            fh.write("\n".join(lines).encode("utf-8"))

    def assert_settled(self, out, line):
        self.assertIn(line + "\n", out)
        self.assertNotIn("  conflict %s\n" % A, out)
        self.assertFalse(os.path.exists(self.path(*UPGRADE.split("/"))))
        again = self.install()              # upstream unchanged: no conflict, nothing applied
        self.assertNotIn("  conflict %s\n" % A, again)
        self.assertNotIn("upgrade: %s" % A, again)
        self.assertFalse(os.path.exists(self.path(*UPGRADE.split("/"))))

    def test_resolve_keep(self):
        a_before = self.repo_bytes(A)
        self.resolve("keep")
        out = self.install()
        self.assertEqual(self.repo_bytes(A), a_before)
        self.assert_settled(out, "upgrade: %s: keep" % A)
        self.assertEqual(self.repo_bytes(A), a_before)

    def test_resolve_upstream(self):
        self.resolve("upstream")
        out = self.install()
        self.assertEqual(self.repo_bytes(A), self.pkg_bytes("critic.md").replace(b"\r\n", b"\n"))
        self.assert_settled(out, "upgrade: %s: upstream" % A)

    def test_resolve_merged(self):
        merged = ".run/pa3-upgrade/" + A
        path = self.path(*merged.split("/"))
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(b"---\nname: critic\n---\r\nmerged body\r\n")
        self.resolve("merged: " + merged)
        out = self.install()
        self.assertEqual(self.repo_bytes(A), b"---\nname: critic\n---\nmerged body\n")
        self.assert_settled(out, "upgrade: %s: merged from %s" % (A, merged))
        self.assertEqual(self.repo_bytes(A), b"---\nname: critic\n---\nmerged body\n")

    def test_resolve_merged_missing_keeps_the_line(self):
        a_before = self.repo_bytes(A)
        self.resolve("merged: .run/nope.md")
        out = self.install()
        self.assertIn("upgrade: %s: merged path missing: .run/nope.md\n" % A, out)
        self.assertEqual(self.repo_bytes(A), a_before)
        lines = [l for l in self.repo_bytes(UPGRADE).decode("utf-8").splitlines()
                 if l.startswith("- %s | " % A)]
        self.assertEqual(len(lines), 1, lines)

    def test_parse_resolutions(self):
        text = ("# h\n- a.md | base 1 | default: keep yours | resolve: upstream\n"
                "- b.md | base 2 | default: keep yours\n"
                "- c.md | base 3 | default: keep yours | resolve: merged: .run/pa3-upgrade/c.md\n"
                "- d.md | base 4 | default: keep yours | resolve: keep\n")
        self.assertEqual(list(managed.parse_resolutions(text).items()),
                         [("a.md", ("upstream", None)),
                          ("c.md", ("merged", ".run/pa3-upgrade/c.md")), ("d.md", ("keep", None))])


class TestUpgradeHistory(ProjectCase):
    """3.10 T15: past package versions come from the package's own git history."""

    TOOL = "tools/outline.py"

    def setUp(self):
        ProjectCase.setUp(self)
        src = pj.package_root()
        self.pkg = os.path.join(self.tmp, "pkg")
        for name in COPY:
            shutil.copytree(os.path.join(src, name), os.path.join(self.pkg, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(os.path.join(src, "project-architect-3.0.md"),
                        os.path.join(self.pkg, "project-architect-3.0.md"))
        TestUpgradeRefresh.append(os.path.join(self.pkg, "CHANGES.md"), b"## 3.10 test\n")
        git(self.pkg, "init", "-q")
        for key, val in (("user.email", "dev@example.com"), ("user.name", "Dev"),
                         ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
            git(self.pkg, "config", key, val)
        self.commit("v1")
        patcher = mock.patch.object(pj, "package_root", return_value=self.pkg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.install()

    # -- helpers ---------------------------------------------------------- #
    def commit(self, msg):
        git(self.pkg, "add", "-A")
        git(self.pkg, "commit", "-qm", msg)

    def repo_bytes(self, rel):
        return TestUpgradeRefresh.raw(self.path(*rel.split("/")))

    def pkg_file(self, rel):
        return os.path.join(self.pkg, *rel.split("/"))

    def upstream(self, rel, data):
        TestUpgradeRefresh.append(self.pkg_file(rel), data)
        self.commit("v2")

    def mine(self, rel, data):
        TestUpgradeRefresh.append(self.path(*rel.split("/")), data)

    def record(self):
        return json.loads(self.repo_bytes(RECORD).decode("utf-8"))

    # -- tests ------------------------------------------------------------ #
    def test_old_package_version_is_upstream_changed(self):
        os.remove(self.path(*RECORD.split("/")))            # no base: history decides
        self.upstream("agents/critic.md", b"\nupstream A\n")
        out = self.install()
        self.assertIn("  upstream-changed %s\n" % A, out)
        self.assertEqual(self.repo_bytes(A), TestUpgradeRefresh.raw(self.pkg_file("agents/critic.md")))

    def test_both_sides_merge_clean(self):
        path = self.path(*A.split("/"))
        lines = TestUpgradeRefresh.raw(path).split(b"\n")
        mid = len(lines) // 2
        with open(path, "wb") as fh:
            fh.write(b"\n".join(lines[:mid] + [b"my line for A"] + lines[mid:]))
        self.upstream("agents/critic.md", b"\nupstream A\n")
        new = TestUpgradeRefresh.raw(self.pkg_file("agents/critic.md"))
        out = self.install()
        self.assertIn("  merged %s\n" % A, out)
        self.assertIn("restart: needed\n", out)
        got = self.repo_bytes(A)
        self.assertIn(b"my line for A", got)
        self.assertIn(b"upstream A", got)
        self.assertNotIn(b"\r\n", got)
        ent = self.record()["files"][A]
        self.assertEqual(ent["sha"], managed.sha(new))
        self.assertEqual(ent["base"], managed.base_of(new.decode("utf-8")))
        self.assertEqual(ent["user"]["state"], "merged")
        self.assertEqual(ent["user"]["sha"], managed.sha(got))
        self.assertFalse(os.path.exists(self.path(".run", "pa3-merge")))
        out = self.install()                                 # the merge is now the user's copy
        self.assertIn("  user-changed %s\n" % A, out)
        self.assertIn("restart: not needed\n", out)

    def test_overlapping_tool_is_replaced(self):
        self.mine(self.TOOL, b"\n# mine\n")
        mine = self.repo_bytes(self.TOOL)
        self.upstream(self.TOOL, b"\n# theirs\n")
        out = self.install()
        yours = ".claude/pa3-upgrade/%s.yours" % self.TOOL
        self.assertIn("  replaced %s (yours: %s)\n" % (self.TOOL, yours), out)
        self.assertIn("restart: not needed\n", out)
        self.assertEqual(self.repo_bytes(self.TOOL),
                         TestUpgradeRefresh.raw(self.pkg_file(self.TOOL)).replace(b"\r\n", b"\n"))
        self.assertEqual(self.repo_bytes(yours), mine.replace(b"\r\n", b"\n"))
        self.assertIsNone(self.record()["files"][self.TOOL]["user"])

    def test_untouched_doc_is_upstream_changed_without_a_record_entry(self):
        """3.11 T25: the methodology doc is managed; an older package's copy updates, even in a
        project installed before T25 (no record entry: history decides)."""
        self.assertIn(managed.DOC, self.record()["files"])      # a fresh install records it
        os.remove(self.path(*RECORD.split("/")))
        self.upstream("project-architect-3.0.md", b"\nupstream note\n")
        out = self.install()
        self.assertIn("  upstream-changed %s\n" % managed.DOC, out)
        self.assertEqual(self.repo_bytes(managed.DOC),
                         TestUpgradeRefresh.raw(self.pkg_file("project-architect-3.0.md")))
        self.assertIsNone(self.record()["files"][managed.DOC]["user"])

    def test_edited_doc_is_replaced_and_yours_kept(self):
        self.mine(managed.DOC, b"\nmy note\n")
        mine = self.repo_bytes(managed.DOC)
        self.upstream("project-architect-3.0.md", b"\nupstream note\n")
        out = self.install()
        yours = ".claude/pa3-upgrade/%s.yours" % managed.DOC
        self.assertIn("  replaced %s (yours: %s)\n" % (managed.DOC, yours), out)
        self.assertEqual(self.repo_bytes(managed.DOC),
                         TestUpgradeRefresh.raw(self.pkg_file("project-architect-3.0.md"))
                         .replace(b"\r\n", b"\n"))
        self.assertEqual(self.repo_bytes(yours), mine.replace(b"\r\n", b"\n"))

    def test_overlapping_agent_is_a_conflict(self):
        self.mine(A, b"\nmy line for A\n")
        before = self.repo_bytes(A)
        self.upstream("agents/critic.md", b"\nupstream A\n")
        out = self.install()
        self.assertIn("  conflict %s\n" % A, out)
        self.assertEqual(self.repo_bytes(A), before)
        up = self.repo_bytes(UPGRADE).decode("utf-8")
        self.assertTrue(up.startswith(managed.UPGRADE_HEADER), up)
        self.assertIn("\n- %s | " % A, up)

    def test_record_carries_git_and_phase(self):
        rec = self.record()
        head = pj._git(self.pkg, "rev-parse", "--short", "HEAD")[1].strip()
        self.assertEqual(rec["git"], head)
        self.assertEqual(rec["phase"], "3.10")
        from pa import __version__
        self.assertEqual(rec["upstream_version"], __version__)          # I11: no pkg VERSION
        with open(self.pkg_file("VERSION"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("3.12\n")
        self.upstream("agents/critic.md", b"\nupstream A\n")
        self.install()
        self.assertNotEqual(self.record()["git"], head)
        self.assertEqual(self.record()["upstream_version"], "3.12")


class TestLadderRefresh(ProjectCase):
    """3.10.6 T2: a plan-tier change between runs re-renders the managed agents."""

    FABLE = ".claude/agents/expert-fable.md"
    PLAIN, CRITIC = ".claude/agents/plain.md", ".claude/agents/critic.md"
    STAGED = ".claude/pa3-upgrade/.claude/agents/expert-fable.md.upstream"

    def setUp(self):
        ProjectCase.setUp(self)
        self.install()                                  # max20 (the harness pin)
        self.rec0 = self.record()

    repo_bytes = TestUpgradeRefresh.repo_bytes
    record = TestUpgradeRefresh.record
    raw = staticmethod(TestUpgradeRefresh.raw)

    def exists_rel(self, rel):
        return os.path.isfile(self.path(*rel.split("/")))

    def test_max20_to_max5(self):
        self.set_tier("max5")
        out = self.install()
        self.assertIn("  upstream-changed %s\n" % self.PLAIN, out)
        self.assertNotIn(self.CRITIC, out)              # max5 judges keep max20's row
        self.assertIn("  removed %s (not in this plan tier)\n" % self.FABLE, out)
        self.assertIn("restart: needed\n", out)
        self.assertFalse(self.exists_rel(self.FABLE))
        self.assertIn(b"\neffort: medium\n", self.repo_bytes(self.PLAIN))
        rec = self.record()
        self.assertEqual(rec["files"][self.FABLE],
                         {"absent": "tier", "base": None, "sha": None, "user": None})
        self.assertEqual(rec["files"][self.PLAIN]["sha"],
                         managed.sha(self.repo_bytes(self.PLAIN)))
        self.assertFalse(self.exists_rel(UPGRADE))
        out = self.install()                            # stays max5
        self.assertIn("restart: not needed\n", out)
        self.assertEqual(self.record()["files"], rec["files"])

    def test_downgrade_removal_is_staged_in_git(self):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "installed")
        self.set_tier("max5")
        made = []
        base = pj.Manifest

        class Spy(base):
            def __init__(self):
                base.__init__(self)
                made.append(self)

        with mock.patch.object(pj, "Manifest", Spy):
            self.install()
        self.assertIn(self.FABLE, made[-1].removed)

    def test_max20_to_pro_updates_the_judges(self):
        self.set_tier("pro")
        out = self.install()
        for name in ("critic", "review", "auditor", "memory-curator", "plain"):
            rel = ".claude/agents/%s.md" % name
            self.assertIn("  upstream-changed %s\n" % rel, out)
            self.assertIn(b"\nmodel: claude-opus-5-5[1m]\neffort: medium\n", self.repo_bytes(rel))
        self.assertFalse(self.exists_rel(self.FABLE))

    def test_hand_edited_judge_is_a_conflict(self):
        path = self.path(*self.CRITIC.split("/"))
        TestUpgradeRefresh.append(path, b"\nmy line\n")
        mine = self.repo_bytes(self.CRITIC)
        self.set_tier("pro")
        out = self.install()
        self.assertIn("  conflict %s\n" % self.CRITIC, out)
        self.assertEqual(self.repo_bytes(self.CRITIC), mine)

    def test_hand_edited_expert_fable_kept_and_staged(self):
        path = self.path(*self.FABLE.split("/"))
        TestUpgradeRefresh.append(path, b"\nmy line\n")
        mine = self.repo_bytes(self.FABLE)
        self.set_tier("max5")
        out = self.install()
        self.assertEqual(self.repo_bytes(self.FABLE), mine)
        self.assertIn("! %s kept yours; upstream staged" % self.FABLE, out)
        self.assertTrue(self.exists_rel(self.STAGED))
        line = [l for l in self.repo_bytes(UPGRADE).decode("utf-8").splitlines()
                if l.startswith("- %s | " % self.FABLE)]
        self.assertEqual(len(line), 1)
        self.assertIn("| upstream absent (tier) (version", line[0])
        ent = self.record()["files"][self.FABLE]
        self.assertEqual(ent["absent"], "tier")
        self.assertEqual(ent["sha"], self.rec0["files"][self.FABLE]["sha"])
        text =self.repo_bytes(UPGRADE).decode("utf-8").replace(
            line[0], line[0] + " | resolve: upstream")
        with open(self.path(*UPGRADE.split("/")), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.install()                                  # resolve: upstream = remove it
        self.assertFalse(self.exists_rel(self.FABLE))
        self.assertIsNone(self.record()["files"][self.FABLE]["sha"])


if __name__ == "__main__":
    unittest.main()
