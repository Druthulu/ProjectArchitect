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

from test_install_project import (PKG, PY, ProjectCase, fixture_copy, git, read,  # noqa: E402
                                  tree_hash, write, write_bytes)
from pa import ledger_cli                                   # noqa: E402
from pa.install import managed                              # noqa: E402
from pa.install import project as pj                        # noqa: E402

RECORD = ".claude/pa3-managed.json"
UPGRADE = ".claude/pa3-upgrade/UPGRADE.md"
A, B, C = (".claude/agents/memory-curator.md", ".claude/agents/pa-coder.md",
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
        self.append(os.path.join(self.pkg, "agents", "memory-curator.md"), b"\nupstream A\n")
        self.append(os.path.join(self.pkg, "agents", "pa-coder.md"), b"\nupstream B\n")

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
                         self.pkg_bytes("memory-curator.md"))
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
        self.assertEqual(self.repo_bytes(B), self.pkg_bytes("pa-coder.md"))
        self.assertEqual(self.repo_bytes(C), c_before)
        self.assertFalse(os.path.exists(self.path(*(".claude/pa3-upgrade/%s.upstream" % C)
                                                  .split("/"))))
        rec = self.record()
        self.assertEqual(set(rec["files"]), set(self.rec0["files"]))
        changed = {rel for rel in rec["files"] if rec["files"][rel] != self.rec0["files"][rel]}
        self.assertEqual(changed, {B})

    def test_force_refresh_overwrites_all(self):
        self.install("--force")
        self.assertEqual(self.repo_bytes(A), self.pkg_bytes("memory-curator.md"))
        self.assertEqual(self.repo_bytes(B), self.pkg_bytes("pa-coder.md"))
        self.assertEqual(self.repo_bytes(C), self.pkg_bytes("auditor.md"))

    def test_dry_run_refresh_writes_nothing(self):
        before = self.walk()
        out = self.install("--dry-run")
        self.assertEqual(self.walk(), before)
        self.assertRegex(out, r"classes: unchanged \d+, upstream-changed \d+, user-changed \d+"
                              r", merged \d+, replaced \d+, conflict \d+, created \d+\n")

    def test_crlf_upstream_staged_lf_only(self):
        src = os.path.join(self.pkg, "agents", "memory-curator.md")
        lf = self.raw(src).replace(b"\r\n", b"\n")
        with open(src, "wb") as fh:
            fh.write(lf.replace(b"\n", b"\r\n"))
        self.install()
        staged = self.repo_bytes(".claude/pa3-upgrade/%s.upstream" % A)
        self.assertNotIn(b"\r\n", staged)
        self.assertEqual(staged, lf)

    def test_crlf_managed_copy_healed_lf_only(self):
        rel = ".claude/agents/pa-expert.md"
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
        self.assertEqual(self.repo_bytes(A), self.pkg_bytes("memory-curator.md").replace(b"\r\n", b"\n"))
        self.assert_settled(out, "upgrade: %s: upstream" % A)

    def test_resolve_merged(self):
        merged = ".run/pa3-upgrade/" + A
        path = self.path(*merged.split("/"))
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(b"---\nname: memory-curator\n---\r\nmerged body\r\n")
        self.resolve("merged: " + merged)
        out = self.install()
        self.assertEqual(self.repo_bytes(A), b"---\nname: memory-curator\n---\nmerged body\n")
        self.assert_settled(out, "upgrade: %s: merged from %s" % (A, merged))
        self.assertEqual(self.repo_bytes(A), b"---\nname: memory-curator\n---\nmerged body\n")

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
        self.upstream("agents/memory-curator.md", b"\nupstream A\n")
        out = self.install()
        self.assertIn("  upstream-changed %s\n" % A, out)
        self.assertEqual(self.repo_bytes(A), TestUpgradeRefresh.raw(self.pkg_file("agents/memory-curator.md")))

    def test_both_sides_merge_clean(self):
        path = self.path(*A.split("/"))
        lines = TestUpgradeRefresh.raw(path).split(b"\n")
        mid = len(lines) // 2
        with open(path, "wb") as fh:
            fh.write(b"\n".join(lines[:mid] + [b"my line for A"] + lines[mid:]))
        self.upstream("agents/memory-curator.md", b"\nupstream A\n")
        new = TestUpgradeRefresh.raw(self.pkg_file("agents/memory-curator.md"))
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
        self.upstream("agents/memory-curator.md", b"\nupstream A\n")
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
        self.upstream("agents/memory-curator.md", b"\nupstream A\n")
        self.install()
        self.assertNotEqual(self.record()["git"], head)
        self.assertEqual(self.record()["upstream_version"], "3.12")


class TestLadderRefresh(ProjectCase):
    """3.10.6 T2: a plan-tier change between runs re-renders the managed agents."""

    # 3.25 T6a: no base row is absent at any tier now; pa-thinker plays the old expert-fable (absent on max5, pro)
    FABLE = ".claude/agents/pa-thinker.md"
    JUDGE = ".claude/agents/auditor.md"     # 3.24 T3.1: was CRITIC critic.md; PLAIN plain.md left
    STAGED = ".claude/pa3-upgrade/.claude/agents/pa-thinker.md.upstream"

    def setUp(self):
        ProjectCase.setUp(self)
        from pa.install import ladder
        for tier in ("max5", "pro"):
            p = mock.patch.dict(ladder.PRESETS[tier], {"pa-thinker": None})
            p.start()
            self.addCleanup(p.stop)
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
        self.assertNotIn(self.JUDGE, out)               # max5 judges keep max20's row
        self.assertNotIn("pa-expert", out)              # and so does the expert's base row
        self.assertIn("  removed %s (not in this plan tier)\n" % self.FABLE, out)
        self.assertIn("restart: needed\n", out)
        self.assertFalse(self.exists_rel(self.FABLE))
        rec = self.record()
        self.assertEqual(rec["files"][self.FABLE],
                         {"absent": "tier", "base": None, "sha": None, "user": None})
        self.assertFalse(self.exists_rel(UPGRADE))
        out = self.install()                            # stays max5
        self.assertIn("restart: not needed\n", out)
        self.assertEqual(self.record()["files"], rec["files"])

    def test_preset_written_to_pa_json(self):
        """3.24 T12e: the install writes the resolved preset to pa.json in the same run."""
        pa = lambda: json.loads(read(self.path(".claude", "pa.json")))    # noqa: E731
        self.assertEqual(pa().get("preset"), "max20")
        self.set_tier("max5")
        self.install()
        self.assertEqual(pa().get("preset"), "max5")

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
        rel = ".claude/agents/auditor.md"    # 3.24 T3.1: was critic, review, auditor, memory-curator, plain
        self.assertIn("  upstream-changed %s\n" % rel, out)
        self.assertIn(b"\nmodel: claude-opus-5-5[1m]\neffort: medium\n", self.repo_bytes(rel))
        # 3.24 T17e: the curator runs Opus 5.5 at medium on every tier, so a tier change leaves it as it is
        cur = ".claude/agents/memory-curator.md"
        self.assertNotIn("  upstream-changed %s\n" % cur, out)
        self.assertIn(b"\nmodel: claude-opus-5-5[1m]\neffort: medium\n", self.repo_bytes(cur))
        self.assertFalse(self.exists_rel(self.FABLE))

    def test_hand_edited_judge_is_a_conflict(self):
        path = self.path(*self.JUDGE.split("/"))
        TestUpgradeRefresh.append(path, b"\nmy line\n")
        mine = self.repo_bytes(self.JUDGE)
        self.set_tier("pro")
        out = self.install()
        self.assertIn("  conflict %s\n" % self.JUDGE, out)
        self.assertEqual(self.repo_bytes(self.JUDGE), mine)

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


# --------------------------------------------------------------------------- 3.24 T5: Gen 3 -> 3.2

GEN3_REV = "d06505ce^"          # the last 3.15.3 package (T3.1 removed the Gen 3 loop at d06505ce)
GEN3_SRC = (["agents/%s.md" % a for a in ledger_cli.GEN3_AGENTS]
            + ["commands/%s.md" % c for c in ledger_cli.GEN3_COMMANDS]
            + ["tools/expert_ttl.py", "templates/discussion.template.md"])
GEN3_PINS = {"agent": "pa-session", "model": "claude-sonnet-5-5[1m]",
             "modelSettings": {"claude-sonnet-5-5": {"effortLevel": "medium"},
                               "claude-fable-5-1": {"effortLevel": "medium"}}}
MEMORY = ".claude-state/memory/feedback-terse.md"


def _top():
    return git(PKG, "rev-parse", "--show-toplevel").stdout.strip()


def _show(rev, src):
    """``src`` (package-relative) at ``rev`` of this repo, as bytes."""
    import subprocess
    return subprocess.run(("git", "-C", _top(), "show", "%s:project-architect-3.0/%s" % (rev, src)),
                          capture_output=True, check=True).stdout


def _gen3_rel(src):
    return (".claude/" + src) if src.split("/")[0] in ("agents", "commands") else src


class FakeHome(object):
    """USERPROFILE/HOME in the temp tree: the projects dir (P6) and the doctor's install dir are never
    the real ``~/.claude``."""

    def home(self):
        self.fake_home = os.path.join(self.tmp, "home")
        os.makedirs(self.fake_home, exist_ok=True)
        for key in ("USERPROFILE", "HOME"):
            patcher = mock.patch.dict(os.environ, {key: self.fake_home})
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(pj, "_REFUSE_PATTERNS", None)    # cached from the home: never leak
        patcher.start()
        self.addCleanup(patcher.stop)

    def doctor(self, repo=None):
        doc = ledger_cli._Doctor()
        ledger_cli._doctor_project_mod(doc, repo or self.repo)
        return doc


class TestGen3Upgrade(FakeHome, ProjectCase):
    """3.24 T5: a 3.15.3 (Gen 3) project upgraded by the installer ends on PA 3.2: ``mod: on``, the right
    ``upgrade``, no Gen 3 pins or files (an edited one removed, its copy kept: T5c), the 3.2 agents, the
    first-run marker."""

    EDITED_CMD = ".claude/commands/proceed.md"

    def setUp(self):
        ProjectCase.setUp(self)
        self.home()

    def pa(self):
        return json.loads(read(self.path(".claude", "pa.json")))

    def put_json(self, rel, doc):
        write(self.path(*rel.split("/")), json.dumps(doc, indent=2) + "\n")

    def record(self):
        return json.loads(read(self.path(*RECORD.split("/"))))

    def gen3(self, package="3.15.3", pa_version="3.15.3", pa_keys=None, edited_tool=None,
             untracked=()):
        """A current install turned back into a Gen 3 project: the 3.15.3 Gen 3 files with record
        entries (``untracked`` ones neither recorded nor committed), the shipped pins, a pa.json without
        mod/upgrade/bash (or ``pa_keys``), the old CLAUDE.md lines, an old P3, a memory file, an edited
        ``edited_tool`` and an edited Gen 3 command."""
        p3 = git(_top(), "log", "--format=%H", "--", "project-architect-3.0/rules-seed/P3.md").stdout.split()
        if len(p3) < 3 or any(git(_top(), "cat-file", "-e", rev + ":project-architect-3.0").returncode
                              for rev in (GEN3_REV, "733c89af", "b4cba465")):
            self.skipTest("needs ProjectArchitect's history (the Gen 3 package); not in a public checkout")
        self.install()
        py = pj.detect_interpreter(PY)[0]
        rec = self.record()
        later = []
        for src in GEN3_SRC:
            data = _show(GEN3_REV, src)
            if src.startswith("commands/"):
                data = pj._fill(data.decode("utf-8"), {"PY": py}).encode("utf-8")
            rel = _gen3_rel(src)
            if rel in untracked:
                later.append((rel, data))
                continue
            write_bytes(self.path(*rel.split("/")), data)
            rec["files"][rel] = {"base": managed.base_of(data.decode("utf-8")),
                                 "sha": managed.sha(data), "user": None}
        if edited_tool:
            old = _show(GEN3_REV, edited_tool)
            write_bytes(self.path(*edited_tool.split("/")), old + b"# my local line\n")
            rec["files"][edited_tool] = {"base": managed.base_of(old.decode("utf-8")),
                                         "sha": managed.sha(old), "user": None}
        rec["package"] = package
        self.put_json(RECORD, rec)
        TestUpgradeRefresh.append(self.path(*self.EDITED_CMD.split("/")), b"\nmy proceed line\n")
        st = json.loads(read(self.path(".claude", "settings.json")))
        st.pop("effortLevel", None)
        st.update(GEN3_PINS)
        self.put_json(".claude/settings.json", st)
        doc = self.pa()
        for key in ("mod", "upgrade", "bash"):
            doc.pop(key, None)
        doc.update(pa_keys or {})
        doc["pa_version"] = pa_version
        self.put_json(".claude/pa.json", doc)
        old_roles = [ln for ln in _show("733c89af", "templates/CLAUDE.template.md").decode("utf-8")
                     .split("\n") if ln.startswith("Roles:")][0]
        old_mem = pj._MEMORY_RE.search(_show("b4cba465", "templates/CLAUDE.template.md")
                                       .decode("utf-8")).group(0)
        text = read(self.path("CLAUDE.md"))
        text = "\n".join(old_roles if ln.startswith("Roles:") else ln for ln in text.split("\n"))
        text = pj._MEMORY_RE.sub(lambda _m: old_mem, text)
        self.assertIn(old_roles, text)
        self.assertIn(old_mem, text)
        write(self.path("CLAUDE.md"), text)
        self.old_p3 = _show(p3[2], "rules-seed/P3.md")
        write_bytes(self.path("rules", "P3.md"), self.old_p3)
        write(self.path(*MEMORY.split("/")), "# Terse\nKeep answers short.\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "gen3 fixture")
        for rel, data in later:
            write_bytes(self.path(*rel.split("/")), data)
        self.assertEqual(sorted(ledger_cli._gen3_pins(st)),
                         ["agent", "model", "modelSettings.claude-fable-5-1",
                          "modelSettings.claude-sonnet-5-5"])
        self.assertTrue(self.doctor().fails)

    def upgraded(self, out, upgrade="auto"):
        """The 3.2 end state after the first upgrade run; the edited Gen 3 command removed, its copy
        kept under the staging folder with one UPGRADE.md line (T5c)."""
        pa = self.pa()
        self.assertEqual((pa["mod"], pa["upgrade"]), ("on", upgrade))
        self.assertTrue(pa["bash"])
        self.assertEqual(pa[pj.FIRST_RUN]["from"], "gen3")
        st = json.loads(read(self.path(".claude", "settings.json")))
        self.assertEqual(ledger_cli._gen3_pins(st), [])
        self.assertNotIn("agent", st)
        self.assertNotIn("modelSettings", st)
        self.assertEqual((st["model"], st["effortLevel"]), ("claude-opus-5-5[1m]", "medium"))
        for src in GEN3_SRC:
            rel = _gen3_rel(src)
            self.assertFalse(os.path.isfile(self.path(*rel.split("/"))), rel)
        copy = managed.yours_rel(self.EDITED_CMD)
        self.assertIn(b"my proceed line", TestUpgradeRefresh.raw(self.path(*copy.split("/"))))
        line = [ln for ln in read(self.path(*UPGRADE.split("/"))).splitlines()
                if ln.startswith("- %s | " % self.EDITED_CMD)]
        self.assertEqual(len(line), 1, out)
        self.assertIn("your edited copy: %s | no resolution needed" % copy, line[0])
        self.assertEqual(managed.parse_resolutions(line[0]), {})
        self.assertNotIn(self.EDITED_CMD, self.record()["files"])
        for name in os.listdir(os.path.join(PKG, "agents")):
            self.exists(".claude/agents/" + name, out)
        tmpl = read(os.path.join(PKG, "templates", "CLAUDE.template.md"))
        claude = read(self.path("CLAUDE.md"))
        self.assertIn([ln for ln in tmpl.split("\n") if ln.startswith("Roles:")][0] + "\n", claude)
        self.assertIn(pj._MEMORY_RE.search(tmpl).group(0), claude)
        self.assertEqual(read(self.path("rules", "P3.md")),
                         read(os.path.join(PKG, "rules-seed", "P3.md")))
        self.assertIn("restart: needed\n", out)
        doc = self.doctor()
        self.assertEqual([ln for ln in doc.lines if not ln.startswith("OK")], [], doc.lines)

    def test_terrain_diffusion_like(self):
        tool = "tools/phaseend_index.py"
        self.gen3(edited_tool=tool)
        self.upgraded(self.install())
        self.assertIn(b"# my local line\n", TestUpgradeRefresh.raw(self.path(*tool.split("/"))))
        before = read(self.path(".claude", "pa.json"))
        again = self.install()
        self.assertFalse(os.path.exists(self.path(*self.EDITED_CMD.split("/"))))
        self.assertTrue(os.path.isfile(self.path(*managed.yours_rel(self.EDITED_CMD).split("/"))))
        self.assertEqual(read(self.path(".claude", "pa.json")), before)     # the marker is written once
        self.assertNotIn("first run:", again)

    def test_bfm_like(self):
        fable = ".claude/agents/expert-fable-5m.md"
        tool = "tools/commit_task.sh"
        self.gen3(package="3.15.0", pa_version="3.11", edited_tool=tool, untracked=(fable,))
        self.assertNotIn(fable, self.record()["files"])
        # an earlier build's `keep` (record and UPGRADE.md line) counts as upstream (T5c)
        rec = self.record()
        cur = TestUpgradeRefresh.raw(self.path(*self.EDITED_CMD.split("/")))
        rec["files"][self.EDITED_CMD] = {"absent": "retired", "base": None, "sha": None, "user": {
            "sha": managed.sha(cur), "mark": None, "state": "keep", "at": "2026-10-09T00:00:00Z"}}
        self.put_json(RECORD, rec)
        write(self.path(*UPGRADE.split("/")), managed.UPGRADE_HEADER + "- %s | base unknown | upstream"
              " absent (retired) | default: keep yours | resolve: keep\n" % self.EDITED_CMD)
        self.upgraded(self.install())
        self.assertIn(b"# my local line\n", TestUpgradeRefresh.raw(self.path(*tool.split("/"))))
        again = self.install()
        self.assertFalse(os.path.exists(self.path(*UPGRADE.split("/"))), again)
        self.assertFalse(os.path.exists(self.path(*self.EDITED_CMD.split("/"))))

    def test_specimen_like(self):
        self.gen3(pa_keys={"mod": "off", "upgrade": "ask"})
        self.upgraded(self.install(), upgrade="ask")

    def dead_rules(self, allow):
        """3.24 T12h: the specimen's old PA allow list; ``allow`` gets ``{py}`` and ``{bash}`` filled
        with the installed rules' ``<x>``; returns the working pair as installed."""
        st = json.loads(read(self.path(".claude", "settings.json")))
        work = [r for r in st["permissions"]["allow"] if r.endswith(" tools/*)")]
        self.assertEqual(len(work), 2, work)
        py = [r for r in work if not r.startswith("Bash(bash ")][0]
        x = {"py": py[len("Bash("):-len(" tools/*)")], "bash": "bash"}
        st["permissions"]["allow"] = [r.format(**x) for r in allow]
        self.put_json(".claude/settings.json", st)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "old PA allow rules")
        return py, "Bash(bash tools/*)"

    def allow(self):
        return json.loads(read(self.path(".claude", "settings.json")))["permissions"]["allow"]

    def test_dead_tool_rules_become_working(self):
        self.gen3()
        py, bash = self.dead_rules(["Bash({py} tools/*:*)", "Bash({bash} tools/*:*)",
                                    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git mv:*)"])
        out = self.install()
        self.assertEqual(self.allow(), [py, bash, "Bash(git add:*)", "Bash(git commit:*)",
                                        "Bash(git mv:*)"], out)
        self.assertIn('~ settings  permissions.allow: "Bash(bash tools/*:*)" -> "%s"' % bash, out)
        before = read(self.path(".claude", "settings.json"))
        again = self.install()
        self.assertEqual(read(self.path(".claude", "settings.json")), before)
        self.assertNotIn("permissions.allow", again)

    def test_dead_tool_rule_dropped_beside_its_twin(self):
        self.gen3()
        py, bash = self.dead_rules(["Bash({py} tools/*:*)", "Bash({py} tools/*)", "Bash(git add:*)",
                                    "Bash({bash} tools/*:*)"])
        out = self.install()
        self.assertEqual(self.allow(), [py, "Bash(git add:*)", bash], out)
        self.assertIn('permissions.allow: %s -> "<removed>"' % json.dumps(py[:-1] + ":*)"), out)

    def test_curator_done_blocks_the_marker(self):
        self.gen3()
        doc = self.pa()
        doc["migrated"] = "3.2"
        self.put_json(".claude/pa.json", doc)
        self.install()
        self.assertNotIn(pj.FIRST_RUN, self.pa())


class TestFirstRunAndFloor(FakeHome, ProjectCase):
    """3.24 T5: the floor stop, the vanilla and PA2 first-run markers, the seed history table."""

    def setUp(self):
        ProjectCase.setUp(self)
        self.home()

    def pa(self, repo=None):
        return json.loads(read(os.path.join(repo or self.repo, ".claude", "pa.json")))

    def test_floor_stop_writes_nothing(self):
        from pa.install import root
        before = tree_hash(self.repo)
        with mock.patch.dict(root._PROBE_CACHE, {"claude": "2.0.0"}):
            rc, out = self.run_install("--no-commit")
        self.assertEqual(rc, 1, out)
        self.assertIn("Claude Code 2.0.0 is older than %s" % root.mod_floor(PKG), out)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual(tree_hash(self.repo), before)

    def proj_mem(self, repo):
        from pa import paths as pp
        return os.path.join(self.fake_home, ".claude", "projects",
                            pp.project_slug(repo.replace("\\", "/")), "memory")

    def test_vanilla_marker(self):
        write(os.path.join(self.proj_mem(self.repo), "user-prefs.md"), "# prefs\n")
        out = self.install()
        self.assertEqual(self.pa()[pj.FIRST_RUN]["from"], "vanilla", out)
        self.assertTrue(os.path.isfile(self.path(".claude-state", "memory", "user-prefs.md")))
        self.assertEqual(self.pa()["mod"], "on")

    def test_moved_memories_are_committed(self):
        """3.24 T12e: the install commit names every memory P6 moved in; none stays untracked."""
        write(os.path.join(self.proj_mem(self.repo), "user-prefs.md"), "# prefs\n")
        write(os.path.join(self.proj_mem(self.repo), "MEMORY.md"), "- [prefs](user-prefs.md)\n")
        rc, out = self.run_install()
        self.assertEqual(rc, 0, out)
        status = git(self.repo, "status", "--porcelain", "--untracked-files=all").stdout
        self.assertNotIn(".claude-state/memory/", status, out)
        tracked = git(self.repo, "ls-files", ".claude-state/memory").stdout
        self.assertIn(".claude-state/memory/user-prefs.md", tracked)

    def test_vanilla_without_memories_has_no_marker(self):
        self.install()
        self.assertNotIn(pj.FIRST_RUN, self.pa())

    def stock20(self, memory):
        repo = fixture_copy("stock20", self.tmp)        # four memory files besides MEMORY.md
        if not memory:
            mem = os.path.join(repo, ".claude-state", "memory")
            for name in os.listdir(mem):
                if name != "MEMORY.md":
                    os.remove(os.path.join(mem, name))
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "no memories")
        out = self.install(repo=repo)
        return repo, out

    def test_pa2_marker(self):
        repo, out = self.stock20(True)
        pa = self.pa(repo)
        self.assertEqual(pa[pj.FIRST_RUN]["from"], "pa2", out)
        self.assertEqual((pa["mod"], pa["upgrade"]), ("on", "auto"))
        doc = self.doctor(repo)
        self.assertEqual(doc.fails, 0, doc.lines)

    def test_pa2_without_memories_has_no_marker(self):
        repo, _out = self.stock20(False)
        self.assertNotIn(pj.FIRST_RUN, self.pa(repo))

    def test_seed_table_holds_every_current_seed(self):
        from pa.install import seed_history
        for rid in pj.SEED_REFRESH:
            with open(os.path.join(PKG, "rules-seed", rid + ".md"), "rb") as fh:
                self.assertIn(managed.sha(fh.read()), seed_history.past(rid, PKG), rid)


if __name__ == "__main__":
    unittest.main()
