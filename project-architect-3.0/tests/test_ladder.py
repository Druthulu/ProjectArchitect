"""pa.install.ladder: presets, render, mismatches; agents written only through render (3.10.6 T2).

    cd project-architect-3.0 && python -m unittest tests.test_ladder -v
"""

import glob
import os
import re
import shutil
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from pa.install import ladder  # noqa: E402


def pkg_text(agent):
    with open(os.path.join(PKG, "agents", agent + ".md"), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def diff_lines(a, b):
    la, lb = a.splitlines(), b.splitlines()
    return [(x, y) for x, y in zip(la, lb) if x != y], len(la) == len(lb)


class TestRender(unittest.TestCase):

    def test_max20_is_the_shipped_file(self):
        for agent in ladder.MANAGED:
            self.assertEqual(ladder.render(agent, pkg_text(agent), "max20"), pkg_text(agent), agent)

    def test_rows(self):
        self.assertIsNone(ladder.row("expert-fable", "max5"))
        self.assertIsNone(ladder.row("expert-fable", "pro"))
        self.assertEqual(ladder.row("plain", "max5"),
                         {"model": "claude-fable-5-1[1m]", "effort": "medium"})
        self.assertEqual(ladder.row("critic", "pro"),
                         {"model": "claude-opus-5-5[1m]", "effort": "medium"})
        self.assertIsNone(ladder.row("coder-opus55", "max20"))

    def test_render_touches_only_model_and_effort(self):
        for preset in ("max5", "pro"):
            for agent in ladder.MANAGED:
                text = pkg_text(agent)
                out = ladder.render(agent, text, preset)
                if agent in ("expert-fable", "expert-fable-5m"):   # was == "expert-fable" (3.15 T4 twin)
                    self.assertIsNone(out)
                    continue
                diffs, same_len = diff_lines(text, out)
                self.assertTrue(same_len, agent)
                for old, new in diffs:
                    self.assertTrue(old.startswith(("model:", "effort:")), (agent, old))
                    self.assertEqual(old.split(":", 1)[0], new.split(":", 1)[0])
                r = ladder.row(agent, preset)
                self.assertIn("\nmodel: %s\n" % r["model"], out)
                self.assertIn("\neffort: %s\n" % r["effort"], out)

    def test_unmanaged_passes_through(self):
        text = pkg_text("coder-opus55")
        self.assertIs(ladder.render("coder-opus55", text, "pro"), text)

    def test_override_wins(self):
        ov = {"critic": {"effort": "high"}, "expert-fable": {"model": "claude-opus-5-5[1m]"}}
        self.assertEqual(ladder.row("critic", "pro", ov),
                         {"model": "claude-opus-5-5[1m]", "effort": "high"})
        out = ladder.render("expert-fable", pkg_text("expert-fable"), "max5", ov)
        self.assertIn("\nmodel: claude-opus-5-5[1m]\neffort: medium\n", out)
        self.assertIn("\neffort: high\n", ladder.render("critic", pkg_text("critic"), "max20", ov))


class TestMismatches(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ladder-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def place(self, preset):
        for agent in ladder.MANAGED:
            out = ladder.render(agent, pkg_text(agent), preset)
            path = os.path.join(self.dir, agent + ".md")
            if out is None:
                if os.path.exists(path):
                    os.remove(path)
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)

    def test_mismatches(self):
        self.place("max5")
        self.assertEqual(ladder.mismatches(self.dir, "max5"), [])
        self.assertEqual(ladder.mismatches(self.dir, "max20"), ["expert-fable", "expert-fable-5m", "plain"])
        self.assertEqual(ladder.mismatches(self.dir, "pro"),
                         ["critic", "review", "auditor", "memory-curator", "plain"])
        self.place("max20")
        self.assertEqual(ladder.mismatches(self.dir, "max5"), ["expert-fable", "expert-fable-5m", "plain"])


class TestOnlyRenderWritesAgents(unittest.TestCase):

    def test_install_sources(self):
        """Only project.copy_agents names the agents destination; overlay only reads it."""
        hits = []
        for path in sorted(glob.glob(os.path.join(PKG, "pa", "install", "*.py"))):
            with open(path, "r", encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]
                    if re.search(r"""["']\.claude/agents["'/]|"\.claude",\s*"agents\"""", code):
                        hits.append("%s:%d" % (os.path.basename(path), n))
        with open(os.path.join(PKG, "pa", "install", "project.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotRegex(src, r"copy_dir\([^)]*\"agents\"")
        self.assertIn("ladder.render(", src)
        allowed = {"project.py": ("copy_agents", "RETIRED", "_stage_earlier_prunes", "_lf_only"),
                   "managed.py": ("PKG_MAP", "RESTART", "_compare", "_transform"),
                   "overlay.py": ("_project_agents", "extra_agents"),
                   "detect.py": ("pa3_agents",)}                      # isfile probe only
        for hit in hits:
            name, n = hit.split(":")
            self.assertIn(name, allowed, hit)
            with open(os.path.join(PKG, "pa", "install", name), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            ctx = "\n".join(lines[max(0, int(n) - 40):int(n)])
            self.assertTrue(any(k in ctx for k in allowed[name]), hit)
        with open(os.path.join(PKG, "pa", "install", "overlay.py"), "r", encoding="utf-8") as fh:
            overlay = fh.read()
        self.assertNotRegex(overlay, r"place\([^)]*agents|copy_dir\([^)]*agents")


if __name__ == "__main__":
    unittest.main()
