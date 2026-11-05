
"""history.py: the inventory on fixture repos and transcripts (3.32 T2)."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(PKG, "tools", "history.py")
sys.path.insert(0, os.path.join(PKG, "tools"))

import history as H  # noqa: E402


def _row(kind, ts, content, **kw):
    r = {"type": kind, "timestamp": ts, "sessionId": "s1", "uuid": "u-%s-%s" % (ts, kind),
         "message": {"role": kind, "content": content}}
    r.update(kw)
    return r


def _asst(ts, mid, block, **kw):
    r = _row("assistant", ts, [block], **kw)
    r["message"]["id"] = mid
    r["uuid"] = "a-%s" % ts
    return r


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class HistoryTest(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="pa3-history-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root = os.path.join(base, "proj")
        self.cfg = os.path.join(base, "cfg")
        os.makedirs(self.root)
        self._repo()
        self._transcripts()

    def _git(self, *args, date="2026-03-01T10:00:00+00:00"):
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        subprocess.run(["git"] + list(args), cwd=self.root, env=env, check=True, capture_output=True)

    def _put(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _commit(self, files, date, msg):
        for rel, text in files.items():
            self._put(rel, text)
        self._git("add", *files)
        self._git("commit", "-q", "-m", msg, date=date)

    def _repo(self):
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "T")
        self._commit({"README.md": "# Readme\n", "docs/PhaseEnd_1.md": "# Phase 1 end\n" + "x" * 400},
                     "2026-01-10T10:00:00+00:00", "one")
        self._commit({"phase-ends/PhaseEnd_2.md": "# Phase 2 end\n" + "y" * 400,
                      "phase-ends/LEGACY_INDEX.md": "| phase | file |\n|---|---|\n| 1 | docs/PhaseEnd_1.md |\n"
                                                    "| 2 | phase-ends/PhaseEnd_2.md |\n",
                      "CURRENT_PHASE.md": "# Current phase\n" + "z" * 400},
                     "2026-02-10T10:00:00+00:00", "two")
        self._git("branch", "merged-branch")
        self._git("checkout", "-q", "-b", "feature")
        self._commit({"docs/design/idea.md": "# Idea\n"}, "2026-02-15T10:00:00+00:00", "feature")
        self._git("checkout", "-q", "main")
        self._commit({"phase-ends/phase-3/RECAP.md": "# Recap 3\n", "PHASE_PLAN.md": "# Plan\n" + "p" * 400,
                      "docs/retired/old.md": "# Old\n", "cookbook/C0001.md": "# Tech\n",
                      "rules/R1.md": "# Rule\n", "research/R1-001.md": "# Report\n"},
                     "2026-03-02T10:00:00+00:00", "three")
        self._put(".claude-state/archive/transcripts.7z", "7z")
        self._put(".claude-state/archive/more.7z.001", "7z")        # a split archive
        self._put(".claude-state/memory/m1.md", "m")

    def _transcripts(self):
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(self.root))
        pdir = os.path.join(self.cfg, "projects")
        rows = [
            _row("user", "2026-03-02T12:00:00.000Z", "Please build it", cwd=self.root),
            _asst("2026-03-02T12:00:10.000Z", "msg_2", {"type": "text", "text": "Done"}),
        ]
        _write_jsonl(os.path.join(pdir, name, "s1.jsonl"), rows)
        _write_jsonl(os.path.join(pdir, name, "s1", "subagents", "agent-a.jsonl"),
                     [_row("user", "2026-03-02T12:30:00.000Z", "subagent text")])
        os.makedirs(os.path.join(pdir, name, "memory"))
        for m in ("MEMORY.md", "a.md"):
            self._put_abs(os.path.join(pdir, name, "memory", m))
        sub = os.path.join(self.root, "sub")
        _write_jsonl(os.path.join(pdir, name + "-sub", "s2.jsonl"),
                     [_row("user", "2026-03-05T12:00:00.000Z", "from the subfolder", cwd=sub, sessionId="s2")])
        _write_jsonl(os.path.join(pdir, name + "-other", "s3.jsonl"),
                     [_row("user", "2026-03-05T12:00:00.000Z", "a sibling project", cwd=self.root + "-other")])

    def test_mark_the_intake(self):
        """3.32 B9: `mark` sets the marker and keeps the rest of pa.json; `--incomplete` says it reached its cap."""
        self._put(".claude/pa.json", json.dumps({"mod": "on", "intake": "pending"}))
        self.assertEqual(self.run_tool("mark", "intake"), "intake: done\n")
        with open(os.path.join(self.root, ".claude", "pa.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"mod": "on", "intake": "done"})
        self.assertEqual(self.run_tool("mark", "intake", "--incomplete"), "intake: incomplete\n")
        with open(os.path.join(self.root, ".claude", "pa.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"mod": "on", "intake": "incomplete"})

    def _put_abs(self, p):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("m")

    def run_tool(self, *args, root=None):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.cfg)
        r = subprocess.run([sys.executable, TOOL, "--root", root or self.root] + list(args), capture_output=True,
                           env=env)
        out = r.stdout.decode("utf-8")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode("utf-8", "replace"))
        self.assertNotIn("\r", out)
        return out

    def _inventory(self):
        self.run_tool("inventory")
        with open(os.path.join(self.root, ".run", "history", "inventory.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_inventory(self):
        inv = self._inventory()
        g = inv["git"]
        self.assertEqual(g["commits"], 4)
        self.assertEqual((g["first"], g["last"]), ("2026-01-10", "2026-03-02"))
        self.assertEqual(g["per_month"], {"2026-01": 1, "2026-02": 2, "2026-03": 1})
        br = {b["name"]: b for b in g["branches"]}
        self.assertTrue(br["merged-branch"]["merged"])
        self.assertFalse(br["feature"]["merged"])
        self.assertEqual(br["feature"]["last"], "2026-02-15")
        pe = inv["phaseends"]
        self.assertEqual(sorted(pe["files"]), ["docs/PhaseEnd_1.md", "phase-ends/PhaseEnd_2.md",
                                               "phase-ends/phase-3/RECAP.md"])
        self.assertEqual(pe["legacy_index"], [["1", "docs/PhaseEnd_1.md"], ["2", "phase-ends/PhaseEnd_2.md"]])
        kinds = {d["path"]: d["kind"] for d in inv["docs"]}
        self.assertEqual(kinds["CURRENT_PHASE.md"], "checkpoint")
        self.assertEqual(kinds["PHASE_PLAN.md"], "plan")
        self.assertEqual(kinds["docs/retired/old.md"], "retired")
        self.assertEqual(kinds["cookbook/C0001.md"], "cookbook")
        self.assertEqual(kinds["rules/R1.md"], "rules")
        self.assertEqual(kinds["research/R1-001.md"], "report")
        self.assertEqual(kinds["README.md"], "other")
        self.assertNotIn("docs/design/idea.md", kinds)   # not on HEAD
        d = {x["path"]: x for x in inv["docs"]}["CURRENT_PHASE.md"]
        self.assertEqual((d["first"], d["last"], d["title"]), ("2026-02-10", "2026-02-10", "Current phase"))
        tr = inv["transcripts"]
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(self.root))
        self.assertEqual([f["folder"] for f in tr["folders"]], [name, name + "-sub"])
        self.assertEqual([f["file"] for f in tr["folders"][0]["files"]], ["s1.jsonl"])
        self.assertEqual(tr["folders"][0]["files"][0]["first"], "2026-03-02T12:00:00.000Z")
        self.assertEqual(tr["folders"][0]["files"][0]["last"], "2026-03-02T12:00:10.000Z")
        self.assertEqual(tr["archives"], [{"path": ".claude-state/archive/more.7z.001", "bytes": 2},
                                          {"path": ".claude-state/archive/transcripts.7z", "bytes": 2}])
        self.assertEqual(inv["memories"], {"projects/%s/memory" % name: 2, ".claude-state/memory": 1})
        self.assertEqual(inv["days"]["2026-02-10"]["docs"], (len("# Current phase\n") + 400) // 4
                         + (len("# Phase 2 end\n") + 400) // 4)
        with open(os.path.join(self.root, ".run", "history", "inventory.md"), "rb") as fh:
            md = fh.read().decode("utf-8")
        self.assertNotIn("\r", md)
        for head in ("## git", "## PhaseEnds", "## docs", "## transcripts", "## memories", "## reading estimate"):
            self.assertIn(head, md)

    def test_inventory_lists_every_text_document(self):    # 3.32 T4h: tracked or not, ignored never, kinds no bar
        self._commit({"docs/retired/ops-setup.md": "# Ops\n", "GUIDE DATA/items.txt": "Item list\nmore\n"},
                     "2026-02-20T10:00:00+00:00", "docs")
        self._put(".gitignore", "ignored.txt\n.claude/\n")
        self._put("ignored.txt", "no\n")
        self._put(".git/info/exclude", "local ideas.txt\n")     # the developer's own exclude: hidden from git, not from the history
        self._put("local ideas.txt", "Ideas kept out of git\n")
        self._put(".claude/skip.md", "# no\n")
        self._put("new ideas.txt", "\n===\nThe ideas\n")
        self._put("plan notes.txt", "not Markdown: never reading\n")
        self._put("guide.rst", "Guide\n=====\n")
        self._put("manual.pdf", "%PDF-1.7\n")
        self._put(".run/scratch.md", "# no\n")
        self._put(".claude-state/journal/history/2026-01-01.md", "# no\n")
        self._commit({".claude-state/journal/2026-01-01/120000-001r-mod-plan.md": "# no\n"}, "2026-02-21T10:00:00+00:00", "log")
        self._put(".claude-state/journal/TOPICS.md", "# no\n")
        inv = self._inventory()
        d = {x["path"]: x for x in inv["docs"]}
        self.assertEqual(d["docs/retired/ops-setup.md"]["kind"], "retired")
        self.assertEqual((d["GUIDE DATA/items.txt"]["first"], d["GUIDE DATA/items.txt"]["title"]),
                         ("2026-02-20", "Item list"))
        self.assertEqual(d["GUIDE DATA/items.txt"]["chars"], len("Item list\nmore\n"))
        new = d["new ideas.txt"]
        today = datetime.date.today().isoformat()
        self.assertEqual((new["first"], new["last"], new["title"], new["kind"]), (today, today, "The ideas", "other"))
        self.assertEqual(d["local ideas.txt"]["title"], "Ideas kept out of git")
        self.assertEqual(d["guide.rst"]["title"], "Guide")
        self.assertEqual((d["manual.pdf"]["title"], d["manual.pdf"]["chars"]), ("", len("%PDF-1.7\n")))
        for gone in ("ignored.txt", ".claude/skip.md", ".run/scratch.md", ".claude-state/journal/history/2026-01-01.md",
                     ".claude-state/journal/2026-01-01/120000-001r-mod-plan.md", ".claude-state/journal/TOPICS.md",
                     ".run/history/inventory.md"):
            self.assertNotIn(gone, d)
        self.assertEqual(d["plan notes.txt"]["kind"], "plan")
        self.assertFalse([e for e in inv["days"].values() if "plan notes.txt" in e["paths"]])
        self.assertEqual(inv["big_folders"], [])
        with open(os.path.join(self.root, ".run", "history", "inventory.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("- `manual.pdf` %s..%s 9 bytes, other: \n" % (today, today), md)

    def test_inventory_big_folder_collapses(self):
        for i in range(H.COLLAPSE):
            self._put("runs/a/out%02d.txt" % i, "x" * 10)
        for i in range(H.COLLAPSE - 1):
            self._put("logs/%02d/l.txt" % i, "x")      # one file a folder: only their parent holds many
        self._put("runs/README.md", "# Runs\n")
        self._put("runs/note.txt", "kept\n")
        self._put("few/a.txt", "kept\n")
        self._put("logs/b.txt", "the fortieth\n")
        inv = self._inventory()
        big = {b["folder"]: b for b in inv["big_folders"]}
        self.assertEqual(sorted(big), ["logs", "runs/a"])
        self.assertEqual((big["runs/a"]["count"], big["runs/a"]["bytes"]), (H.COLLAPSE, 10 * H.COLLAPSE))
        self.assertEqual(big["runs/a"]["sample"], ["out%02d.txt" % i for i in range(H.SAMPLES)])
        self.assertEqual(big["logs"]["count"], H.COLLAPSE)
        paths = [d["path"] for d in inv["docs"]]
        for kept in ("runs/README.md", "runs/note.txt", "few/a.txt"):
            self.assertIn(kept, paths)
        self.assertFalse([p for p in paths if p.startswith(("runs/a/", "logs/"))])
        with open(os.path.join(self.root, ".run", "history", "inventory.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("- `runs/a/`: %d files, %d bytes, such as `out00.txt`, " % (H.COLLAPSE, 10 * H.COLLAPSE), md)

    def test_project_path(self):
        """A clone at another path reads the original's transcripts with --project-path; none without it."""
        clone = os.path.join(os.path.dirname(self.root), "clone")
        shutil.copytree(self.root, clone)
        self.run_tool("inventory", root=clone)
        with open(os.path.join(clone, ".run", "history", "inventory.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["transcripts"]["folders"], [])
        self.run_tool("--project-path", self.root, "inventory", root=clone)
        with open(os.path.join(clone, ".run", "history", "inventory.json"), encoding="utf-8") as fh:
            tr = json.load(fh)["transcripts"]
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(self.root))
        self.assertEqual([f["folder"] for f in tr["folders"]], [name, name + "-sub"])

    def test_notes(self):
        """A doc under docs/history/ is `notes`: reading, dated by its front matter, never by its commit."""
        board = '---\ntitle: "v1.0 notes"\nsource: trello\nfirst: 2021-08-07\nlast: 2024-09-18\n---\n\n# v1.0 notes\n\n' + "note " * 400
        self._commit({"docs/history/v1.0-notes.md": board, "docs/history/plain.md": "# no front matter\n"},
                     "2026-10-01T10:00:00+00:00", "save the board")
        docs = {d["path"]: d for d in H.docs_info(self.root)}
        d = docs["docs/history/v1.0-notes.md"]
        self.assertEqual((d["kind"], d["first"], d["last"], d["title"]), ("notes", "2021-08-07", "2024-09-18", "v1.0 notes"))
        self.assertEqual(docs["docs/history/plain.md"]["last"], "2026-10-01")
        self.assertIn("docs/history/v1.0-notes.md", H.reading(list(docs.values()), {})["2024-09-18"]["paths"])

    def test_conversation_files(self):
        """docs/history/claude-ai/ files: kind conversation, dated by the front matter, their messages reading."""
        rel = H.CONV_DIR + "/2026-03-03-plan.md"
        self._commit({rel: '---\ntitle: "Plan the bag: v1"\nfirst: 2026-03-03T12:00:00Z\nlast: 2026-03-04T12:00:00Z\n---\n\n'
                           "## 2026-03-03T12:00:00Z developer\n\nHello\n\n"
                           "## 2026-03-04T12:00:00Z claude\n\n" + "Answer " * 100 + "\n"},
                     "2026-10-01T10:00:00+00:00", "chats")
        inv = self._inventory()
        d3, d4 = (H._local(t).date().isoformat() for t in ("2026-03-03T12:00:00Z", "2026-03-04T12:00:00Z"))
        doc = {d["path"]: d for d in inv["docs"]}[rel]
        self.assertEqual((doc["kind"], doc["first"], doc["last"], doc["title"]), ("conversation", d3, d4, "Plan the bag: v1"))
        self.assertGreater(inv["days"][d4]["conversation"], 0)

    def _index(self):
        """Installer-style LEGACY_INDEX.md rows with dates long before the fixture's commits."""
        self._commit({"phase-ends/PhaseEnd_3.md": "# Three\n\nClosed 2025-11-05.\n",
                      "phase-ends/PhaseEnd_4.md": "# Four\n" + "\n" * 40 + "2025-12-25\n",
                      "phase-ends/LEGACY_INDEX.md":
                          "# Legacy PhaseEnds (pre-PA3)\n# phase | title | milestone | date | path\n"
                          "1 | PhaseEnd one | - | 2025-10-01 | docs/PhaseEnd_1.md\n"
                          "2 | PhaseEnd two | - | 2025-10-20 | phase-ends/PhaseEnd_2.md\n"
                          "3 | PhaseEnd three | - | - | phase-ends/PhaseEnd_3.md\n"
                          "4 | PhaseEnd four | - | - | phase-ends/PhaseEnd_4.md\n"},
                     "2026-03-03T10:00:00+00:00", "index")

    def test_phaseend_dates(self):
        self._index()
        inv = self._inventory()
        self.assertEqual(len(inv["phaseends"]["legacy_index"]), 4)
        d = {x["path"]: x for x in inv["docs"]}
        self.assertEqual(d["docs/PhaseEnd_1.md"]["last"], "2025-10-01")
        self.assertEqual(d["phase-ends/PhaseEnd_2.md"]["first"], "2025-10-20")
        self.assertEqual(d["phase-ends/PhaseEnd_3.md"]["last"], "2025-11-05")   # from the file
        self.assertEqual(d["phase-ends/PhaseEnd_4.md"]["last"], "2025-11-05")   # the previous row's

    def test_until(self):
        self.run_tool("inventory", "--until", "2026-02-12")
        with open(os.path.join(self.root, ".run", "history", "inventory.json"), encoding="utf-8") as fh:
            inv = json.load(fh)
        self.assertEqual(inv["until"], "2026-02-12")
        self.assertEqual((inv["git"]["commits"], inv["git"]["last"]), (2, "2026-02-10"))
        paths = [d["path"] for d in inv["docs"]]
        self.assertIn("CURRENT_PHASE.md", paths)
        self.assertNotIn("PHASE_PLAN.md", paths)
        self.assertEqual(inv["phaseends"]["files"], ["docs/PhaseEnd_1.md", "phase-ends/PhaseEnd_2.md"])
        self.assertEqual([f["files"] for f in inv["transcripts"]["folders"]], [[], []])
        self.assertLessEqual(max(inv["days"]), "2026-02-12")

    def _install(self):
        os.makedirs(os.path.join(self.root, "docs", "old"))
        self._git("mv", "PHASE_PLAN.md", "docs/old/PHASE_PLAN.md")
        self._commit({"CURRENT_PHASE.md": "# Current phase\n" + "z" * 400 + "\nedited by the install\n",
                      ".claude/pa.json": "{}\n", "GENERATION_PLAN.md": "# Generated\n" + "g" * 4000,
                      "templates/PhaseEnd.template.md": "# Template\n"},
                     "2026-04-01T10:00:00+00:00", "PA3 install")
        self._commit({"phase-ends/phase-4/task_plan.md": "# Task plan\n" + "t" * 400},
                     "2026-04-10T10:00:00+00:00", "after the install")

    def test_install_commit(self):
        self._install()
        inv = self._inventory()
        d = {x["path"]: x for x in inv["docs"]}
        moved = d["docs/old/PHASE_PLAN.md"]
        self.assertEqual((moved["first"], moved["last"], moved["kind"]), ("2026-03-02", "2026-03-02", "plan"))
        self.assertEqual(d["CURRENT_PHASE.md"]["last"], "2026-02-10")   # the install's edit is no date
        self.assertEqual(d["GENERATION_PLAN.md"]["kind"], "install")
        self.assertEqual(d["templates/PhaseEnd.template.md"]["kind"], "install")
        self.assertEqual(d["phase-ends/phase-4/task_plan.md"]["last"], "2026-04-10")
        reading = [p for e in inv["days"].values() for p in e["paths"]]
        self.assertNotIn("GENERATION_PLAN.md", reading)
        self.assertNotIn("2026-04-01", inv["days"])
        self.assertIn("docs/old/PHASE_PLAN.md", inv["days"]["2026-03-02"]["paths"])
        self.assertIn("phase-ends/phase-4/task_plan.md", inv["days"]["2026-04-10"]["paths"])
        # an install after --until: its moved docs stay, its own are out
        self.run_tool("inventory", "--until", "2026-03-15")
        with open(os.path.join(self.root, ".run", "history", "inventory.json"), encoding="utf-8") as fh:
            paths = [x["path"] for x in json.load(fh)["docs"]]
        self.assertIn("docs/old/PHASE_PLAN.md", paths)
        self.assertNotIn("GENERATION_PLAN.md", paths)
        self.assertNotIn("phase-ends/phase-4/task_plan.md", paths)

    def test_install_commit_by_subject(self):
        """A project can keep pa.json out of the install commit: the installer's subject finds it, the older one wins."""
        self._commit({"HOW_WE_WORK.md": "# Card\n"}, "2026-04-01T10:00:00+00:00", "chore: install Project Architect 3.0")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip()
        self._commit({".claude/pa.json": "{}\n"}, "2026-04-02T10:00:00+00:00", "dry run")
        self.assertEqual(H.install_commit(self.root), (sha, "2026-04-01"))
        self._commit({"NOTES2.md": "# Later\n"}, "2026-04-03T10:00:00+00:00", "chore: Project Architect 3.0 migration (Phase 9.5)")
        self.assertEqual(H.install_commit(self.root), (sha, "2026-04-01"))

    def _index_text(self):
        with open(os.path.join(self.root, *H.INDEX_REL.split("/")), encoding="utf-8") as fh:
            return fh.read()

    def test_index(self):
        """One line per pre-PA3 document, oldest first; build output, PA3's own files and what came after the install out."""
        self._commit({"tools/README.md": "# Tool\n", "src/bin/x.md": "# Built\n", "src/obj/y.md": "# Built\n",
                      "templates/t.md": "# T\n", "docs/design.md": "# The design\n" + "t" * 100},
                     "2026-03-03T10:00:00+00:00", "more")
        self._git("mv", "docs/design.md", "docs/moved.md")
        os.makedirs(os.path.join(self.root, ".claude"))
        self._commit({"HOW_WE_WORK.md": "# Card\n", "GENERATION_PLAN.md": "# Generated\n", "CLAUDE.md": "# C\n",
                      "phase-ends/README.md": "# Readme\n", "rules/INDEX.md": "# Rules\n",
                      "phase-ends/current/PHASE_PLAN.md": "# Live\n", ".claude/pa.json": "{}\n"},
                     "2026-04-01T10:00:00+00:00", "chore: install Project Architect 3.0")
        self._commit({"rules/INDEX.md": "# Rules, more\n", "cookbook/C0009.md": "# After\n", "docs/history/later.md": "# Saved later\n",
                      "docs/PRE_PA3_STATE.md": "# State\n", "phase-ends/phase-4/task_plan.md": "# Plan\n"},
                     "2026-04-02T10:00:00+00:00", "after")
        self._put(".claude-state/memory/gen3.2.md", "# Archive\n")
        self._git("add", ".claude-state/memory/gen3.2.md")
        self._git("commit", "-q", "-m", "memory", date="2026-04-03T10:00:00+00:00")
        self._git("mv", ".claude-state/memory/gen3.2.md", "rules/gen3.2.md")     # the curator's move
        self._git("commit", "-q", "-m", "curate", date="2026-04-04T10:00:00+00:00")
        self.assertRegex(self.run_tool("index"), r"^index: [0-9]+ documents, 0 big folders, [0-9]+ chars: docs/PRE_PA3_INDEX.md\n$")
        text = self._index_text()
        self.assertTrue(text.startswith("# Documents from before PA3\n"))
        self.assertIn("- 2026-01-10 `README.md` Readme\n", text)
        self.assertIn("- 2026-03-03 `docs/moved.md` The design\n", text)       # the install's move keeps its dates
        self.assertIn("- 2026-04-02 `docs/history/later.md` Saved later\n", text)
        self.assertLess(text.index("`README.md`"), text.index("`docs/moved.md`"))
        for gone in ("tools/README.md", "src/bin/x.md", "src/obj/y.md", "templates/t.md", "`HOW_WE_WORK.md", "`GENERATION_PLAN.md",
                     "`CLAUDE.md", "phase-ends/README.md", "rules/INDEX.md", "cookbook/C0009.md", "phase-ends/current/",
                     "phase-ends/phase-", "PRE_PA3_STATE", "rules/gen3.2.md", "PRE_PA3_INDEX.md"):
            self.assertNotIn(gone, text)
        tail = text[text.index("## Elsewhere"):]
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(self.root))
        self.assertIn("- Claude Code transcripts: `%s/projects/%s`\n" % (self.cfg.replace("\\", "/"), name), tail)
        self.assertIn("- Claude Code transcripts: `%s/projects/%s-sub`\n" % (self.cfg.replace("\\", "/"), name), tail)
        self.assertNotIn("-other", tail)
        self.assertIn("- Transcript archive (not extracted): `.claude-state/archive/transcripts.7z`\n", tail)
        self.assertTrue(tail.endswith("- Memory archive: `.claude-state/memory/gen*.md`\n"))

    def test_index_history_dates_and_ignored(self):
        """docs/history/ docs carry their own dates; a .gitignore'd file and a big folder's files have no line of their own."""
        rel = H.CONV_DIR + "/2026-03-03-plan.md"
        self._commit({rel: '---\ntitle: "Plan the bag"\nfirst: 2026-03-03T12:00:00Z\nlast: 2026-03-04T12:00:00Z\n---\n\n'
                           "## 2026-03-03T12:00:00Z developer\n\nHello\n",
                      "docs/history/v1.0-notes.md": '---\ntitle: "v1.0 notes"\nfirst: 2021-08-07\nlast: 2024-09-18\n---\n\n# x\n'},
                     "2026-10-01T10:00:00+00:00", "chats")
        self._put(".gitignore", "secret/\nignored.md\n")
        self._put("secret/keys.md", "# no\n")
        self._put("ignored.md", "# no\n")
        for i in range(H.COLLAPSE):
            self._put("tools/out/%02d.txt" % i, "x")      # a big folder in a skipped path: no line either
            self._put("runs/%02d.txt" % i, "x")
        self.run_tool("index")
        text = self._index_text()
        d3, d4 = (H._local(t).date().isoformat() for t in ("2026-03-03T12:00:00Z", "2026-03-04T12:00:00Z"))
        self.assertIn("- %s..%s `%s` Plan the bag\n" % (d3, d4, rel), text)
        self.assertIn("- 2021-08-07..2024-09-18 `docs/history/v1.0-notes.md` v1.0 notes\n", text)
        self.assertIn("- `runs`: %d files\n" % H.COLLAPSE, text)
        for gone in ("secret/keys.md", "ignored.md", "tools/out"):
            self.assertNotIn(gone, text)

    def test_index_rerun_keeps_a_persons_line(self):
        self.run_tool("index")
        path = os.path.join(self.root, *H.INDEX_REL.split("/"))
        text = self._index_text()
        lines = text.replace("- 2026-01-10 `README.md` Readme\n", "- 2026-01-10 `README.md` an edited title\n")
        lines = lines.replace("\n## Elsewhere", "\n- The old wiki is at https://example.com/wiki\n"
                                                "- 2019-01-01 `gone.md` a moved-away file\n\n## Elsewhere")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(lines)
        self.run_tool("index")
        again = self._index_text()
        self.assertIn("## Added by hand\n\n- The old wiki is at https://example.com/wiki\n", again)
        self.assertIn("- 2026-01-10 `README.md` Readme\n", again)
        self.assertNotIn("an edited title", again)
        self.assertNotIn("gone.md", again)
        self.assertEqual(again.count("## Elsewhere"), 1)
        self.run_tool("index")
        self.assertEqual(self._index_text(), again)


if __name__ == "__main__":
    unittest.main()
