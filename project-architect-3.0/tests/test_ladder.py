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
            if agent == "pa-planner":       # 3.24 T17: the file holds the safe default, Opus at high
                continue
            self.assertEqual(ladder.render(agent, pkg_text(agent), "max20"), pkg_text(agent), agent)

    def test_rows(self):
        self.assertIsNone(ladder.row("expert-fable", "max20"))       # 3.25 T6a: pa-expert@high replaced it
        self.assertIsNone(ladder.row("pa-expert@low", "max20"))      # a variant the table lacks
        self.assertEqual(ladder.row("auditor", "max5"),
                         {"model": "claude-fable-5-1[1m]", "effort": "medium"})
        self.assertEqual(ladder.row("memory-curator", "pro"),
                         {"model": "claude-opus-5-5[1m]", "effort": "medium"})
        # 3.24 T17e: the curator is Opus 5.5 at medium on every tier; the auditor keeps Fable on the Max tiers
        for preset in ("max20", "max5"):
            self.assertEqual(ladder.row("memory-curator", preset),
                             {"model": "claude-opus-5-5[1m]", "effort": "medium"}, preset)
            self.assertEqual(ladder.row("auditor", preset),
                             {"model": "claude-fable-5-1[1m]", "effort": "medium"}, preset)
        self.assertIsNone(ladder.row("coder-opus55", "max20"))
        self.assertIsNone(ladder.row("retriever-code@high", "max20"))  # an unmanaged type's variant

    def test_role_rows(self):
        """3.25 T6a: one agent per role; the rows carry the old agents' values."""
        opus, fable = "claude-opus-5-5[1m]", "claude-fable-5-1[1m]"
        for preset in ("max20", "max5"):
            self.assertEqual(ladder.row("pa-expert", preset), {"model": opus, "effort": "medium"}, preset)
            self.assertEqual(ladder.row("pa-coder", preset), {"model": opus, "effort": "medium"}, preset)
            self.assertEqual(ladder.row("pa-thinker", preset), {"model": opus, "effort": "max"}, preset)
        # 3.27 T11: Pro's own role rows
        self.assertEqual(ladder.row("pa-expert", "pro"), {"model": opus, "effort": "low"})
        self.assertEqual(ladder.row("pa-coder", "pro"), {"model": "claude-sonnet-5-5[1m]", "effort": "medium"})
        self.assertEqual(ladder.row("pa-thinker", "pro"), {"model": opus, "effort": "medium"})
        self.assertEqual(ladder.row("pa-expert@high", "max20"), {"model": fable, "effort": "medium"})
        self.assertEqual(ladder.row("pa-expert@high", "max5"), {"model": opus, "effort": "high"})
        self.assertEqual(ladder.row("pa-expert@high", "pro"), {"model": opus, "effort": "high"})
        self.assertEqual(ladder.row("pa-thinker@deep", "max5"), {"model": fable, "effort": "max"})
        self.assertIsNone(ladder.row("pa-thinker@deep", "pro"))
        ov = {"pa-thinker@deep": {"model": opus}}                       # an override may name a variant
        self.assertEqual(ladder.row("pa-thinker@deep", "pro", ov), {"model": opus, "effort": "max"})

    def test_render_touches_only_model_and_effort(self):
        for preset in ("max5", "pro"):
            for agent in ladder.MANAGED:
                text = pkg_text(agent)
                out = ladder.render(agent, text, preset)
                diffs, same_len = diff_lines(text, out)
                self.assertTrue(same_len, agent)
                for old, new in diffs:
                    self.assertTrue(old.startswith(("model:", "effort:")), (agent, old))
                    self.assertEqual(old.split(":", 1)[0], new.split(":", 1)[0])
                r = ladder.row(agent, preset)
                self.assertIn("\nmodel: %s\n" % r["model"], out)
                self.assertIn("\neffort: %s\n" % r["effort"], out)

    def test_unmanaged_passes_through(self):
        text = pkg_text("retriever-code")
        self.assertIs(ladder.render("retriever-code", text, "pro"), text)

    def test_override_wins(self):
        ov = {"auditor": {"effort": "high"}, "pa-coder": {"model": "claude-fable-5-1[1m]"}}
        self.assertEqual(ladder.row("auditor", "pro", ov),
                         {"model": "claude-opus-5-5[1m]", "effort": "high"})
        out = ladder.render("pa-coder", pkg_text("pa-coder"), "max5", ov)
        self.assertIn("\nmodel: claude-fable-5-1[1m]\neffort: medium\n", out)
        self.assertIn("\neffort: high\n", ladder.render("auditor", pkg_text("auditor"), "max20", ov))


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
        self.assertEqual(ladder.mismatches(self.dir, "max20"), ["pa-planner"])  # 3.24 T17f: planner max5 high, max20 max
        self.assertEqual(ladder.mismatches(self.dir, "pro"),   # 3.24 T17e: the curator is Opus at medium on every tier
                         ["pa-expert", "pa-coder", "pa-thinker", "auditor", "pa-planner"])  # 3.27 T11: Pro's role rows
        self.place("max20")
        self.assertEqual(ladder.mismatches(self.dir, "max5"), ["pa-planner"])
        os.remove(os.path.join(self.dir, "pa-coder.md"))
        self.assertEqual(ladder.mismatches(self.dir, "max20"), ["pa-coder"])   # a missing base-row file

    def test_gen3_agents_left(self):
        """3.24 T3.1: the Gen 3 agents have no rows; 3.25 T6a: nor the per-model role agents."""
        for agent in ("critic", "review", "plain", "expert-fable-5m",
                      "expert-fable", "expert-opus55", "coder-opus55", "thinker", "deep-thinker"):
            self.assertNotIn(agent, ladder.MANAGED)
            self.assertIsNone(ladder.row(agent, "max20"))
        self.assertEqual(ladder.MANAGED,
                         ("pa-expert", "pa-coder", "pa-thinker", "auditor", "memory-curator", "pa-planner"))


class TestThinkerRows(unittest.TestCase):

    def test_rows(self):
        fable = {"model": "claude-fable-5-1[1m]", "effort": "max"}
        self.assertEqual(ladder.row("pa-thinker@deep", "max20"), fable)    # 3.22 T8f's deep-thinker
        self.assertEqual(ladder.row("pa-thinker@deep", "max5"), fable)
        self.assertIsNone(ladder.row("pa-thinker@deep", "pro"))           # absent at pro
        self.assertEqual(ladder.render("pa-thinker", pkg_text("pa-thinker"), "pro"),     # 3.27 T11: Pro thinks at medium
                         pkg_text("pa-thinker").replace("\neffort: max\n", "\neffort: medium\n"))
        self.assertIsNone(ladder.row("pa-claude", "max20"))      # model: inherit, no row


class TestPlannerRows(unittest.TestCase):
    """3.24 T17f: Opus 5.5 at max on Max 20x, high on Max 5x, medium on Pro (developer 2026-10-12); the file's own
    default is Opus at high."""

    def test_rows(self):
        opus = "claude-opus-5-5[1m]"
        self.assertEqual(ladder.row("pa-planner", "max20"), {"model": opus, "effort": "max"})
        self.assertEqual(ladder.row("pa-planner", "max5"), {"model": opus, "effort": "high"})
        self.assertEqual(ladder.row("pa-planner", "pro"), {"model": opus, "effort": "medium"})
        text = pkg_text("pa-planner")
        self.assertIn("\nmodel: %s\neffort: high\n" % opus, text)
        self.assertEqual(ladder.render("pa-planner", text, "max5"), text)
        diffs, same = diff_lines(text, ladder.render("pa-planner", text, "max20"))
        self.assertTrue(same)
        self.assertEqual(diffs, [("effort: high", "effort: max")])


class TestLadderRow(unittest.TestCase):
    """3.24 T17: ``pa_ledger.py ladder-row``, one source each: credentials, preset, default."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-ladder-row-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cfg = os.path.join(self.dir, "cfg")
        self.proj = os.path.join(self.dir, "proj")
        os.makedirs(os.path.join(self.cfg, "agents"))
        os.makedirs(os.path.join(self.proj, ".claude", "agents"))
        for agent in ("pa-planner", "pa-thinker", "retriever-code"):
            with open(os.path.join(self.proj, ".claude", "agents", agent + ".md"), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(pkg_text(agent))

    def creds(self, rate_tier, sub="max"):
        import json
        with open(os.path.join(self.cfg, ".credentials.json"), "w", encoding="utf-8") as fh:
            json.dump({"claudeAiOauth": {"accessToken": "x", "subscriptionType": sub,
                                         "rateLimitTier": rate_tier}}, fh)

    def pa(self, doc):
        import json
        with open(os.path.join(self.proj, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def resolve(self, agent, effort=None):
        from pa import ladder_row
        return ladder_row.resolve(agent, self.proj, self.cfg, effort)

    def test_credentials(self):
        self.creds("default_claude_max_20x")
        self.pa({"preset": "pro"})
        self.assertEqual(self.resolve("pa-planner"),
                         {"agent": "pa-planner", "managed": True, "model": "claude-opus-5-5[1m]",
                          "effort": "max", "tier": "max20", "source": "credentials"})
        self.creds(None, sub="pro")
        self.assertEqual(self.resolve("pa-planner")["effort"], "medium")  # 3.24 T17f

    def test_preset(self):
        self.pa({"preset": "pro", "ladder": {"pa-planner": {"effort": "medium"}}})
        got = self.resolve("pa-planner")
        self.assertEqual((got["tier"], got["source"], got["effort"]), ("pro", "preset", "medium"))

    def test_default(self):
        got = self.resolve("pa-planner")                       # no credentials, no pa.json
        self.assertEqual((got["tier"], got["source"], got["model"], got["effort"]),
                         (None, "default", "claude-opus-5-5[1m]", "high"))
        self.creds("default_claude_max_20x")
        got = self.resolve("retriever-code")                    # unmanaged: its own file
        self.assertEqual((got["managed"], got["source"], got["tier"]), (False, "default", "max20"))
        own = ladder._installed(os.path.join(PKG, "agents", "retriever-code.md"))
        self.assertEqual((got["model"], got["effort"]), (own["model"], own["effort"]))

    def test_effort_hint(self):
        """3.25 T6a: ``--effort``: the tier's variant row, else the base row lowered (never raised)."""
        fable, opus = "claude-fable-5-1[1m]", "claude-opus-5-5[1m]"
        self.creds("default_claude_max_20x")
        got = self.resolve("pa-expert", "high")
        self.assertEqual((got["agent"], got["managed"], got["model"], got["effort"], got["source"]),
                         ("pa-expert@high", True, fable, "medium", "credentials"))
        self.creds(None, sub="pro")
        got = self.resolve("pa-expert", "high")
        self.assertEqual((got["agent"], got["model"], got["effort"]), ("pa-expert@high", opus, "high"))
        got = self.resolve("pa-thinker", "deep")                # no variant at pro: the base thinker row
        self.assertEqual((got["agent"], got["model"], got["effort"]), ("pa-thinker@deep", opus, "medium"))  # 3.27 T11
        self.assertEqual(self.resolve("pa-thinker", "medium")["effort"], "medium")   # lowered
        self.assertEqual(self.resolve("pa-expert", "low")["effort"], "low")
        self.pa({"preset": "pro", "ladder": {"pa-thinker": {"effort": "high"}}})
        got = self.resolve("pa-thinker", "max")                 # never above a high base row
        self.assertEqual((got["agent"], got["effort"]), ("pa-thinker@max", "high"))
        self.assertEqual(self.resolve("pa-thinker")["agent"], "pa-thinker")          # no hint: the type

    def test_cli_line(self):
        import json
        import subprocess
        self.creds("default_claude_max_5x")
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.cfg)
        out = subprocess.run([sys.executable, os.path.join(PKG, "pa_ledger.py"), "ladder-row", "--agent",
                              "pa-planner", "--project", self.proj, "--json"], env=env,
                             stdout=subprocess.PIPE, timeout=60).stdout.decode("utf-8")
        self.assertEqual(len(out.splitlines()), 1)
        doc = json.loads(out)
        self.assertEqual(list(doc), ["agent", "managed", "model", "effort", "tier", "source"])
        self.assertEqual((doc["tier"], doc["source"], doc["effort"]), ("max5", "credentials", "high"))  # 3.24 T17f
        out = subprocess.run([sys.executable, os.path.join(PKG, "pa_ledger.py"), "ladder-row", "--agent",
                              "pa-thinker", "--effort", "deep", "--project", self.proj, "--json"], env=env,
                             stdout=subprocess.PIPE, timeout=60).stdout.decode("utf-8")
        doc = json.loads(out)                                   # 3.25 T6a: the hint's variant row
        self.assertEqual((doc["agent"], doc["model"], doc["effort"]),
                         ("pa-thinker@deep", "claude-fable-5-1[1m]", "max"))

    def gen(self, *lines):
        with open(os.path.join(self.proj, "GENERATION_PLAN.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Plan\n\n## Phases\n" + "".join("- %s\n" % ln for ln in lines))

    def test_planner_effort(self):
        """3.25 T5: the open phase's ``planner-effort`` lowers the planner row, never raises it."""
        self.creds("default_claude_max_20x")                    # planner row: max
        self.gen("3.1 A | milestone: m | status: closed | planner-effort: low",
                 "3.2 B | milestone: m | status: open | planner-effort: medium",
                 "3.3 C | milestone: m | status: open | planner-effort: high")
        self.assertEqual(self.resolve("pa-planner")["effort"], "medium")   # the first open phase
        self.gen("3.2 B | milestone: m | status: open")
        self.assertEqual(self.resolve("pa-planner")["effort"], "max")      # no field
        self.gen("3.2 B | milestone: m | status: open | planner-effort: lots")
        self.assertEqual(self.resolve("pa-planner")["effort"], "max")      # bad value
        self.creds("default_claude_max_5x")                     # planner row: high
        self.gen("3.2 B | milestone: m | status: open | planner-effort: max")
        self.assertEqual(self.resolve("pa-planner")["effort"], "high")     # never above the row


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
                   "managed.py": ("PKG_MAP", "RESTART", "_compare", "_transform", "_gone_what"),  # 3.24 T5: a step text
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
