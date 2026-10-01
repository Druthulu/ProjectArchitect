"""Unit tests for tools/outline.py (T1).

    cd project-architect-3.0 && python -m unittest tests.test_outline -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "tools")
FIXTURES = os.path.join(HERE, "fixtures", "outline")
OUTLINE = os.path.join(TOOLS, "outline.py")
# the credit notes outline.py writes land here, never in the repo's .run/credit
NOTES_ROOT = tempfile.mkdtemp(prefix="pa3-outline-")


def tearDownModule():
    shutil.rmtree(NOTES_ROOT, ignore_errors=True)


def run_outline(*args, cwd=None):
    """Run outline.py via subprocess, returning the CompletedProcess."""
    cmd = [sys.executable, "-X", "utf8", OUTLINE] + [str(a) for a in args]
    env = dict(os.environ)
    env["PA_SKIP_PREFLIGHT"] = "1"
    env["PA_PROJECT_ROOT"] = NOTES_ROOT
    p = subprocess.run(cmd, cwd=cwd or PKG, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    p.out = (p.stdout or "") + (p.stderr or "")
    return p


def symbol_rows(stdout):
    """Symbol rows only: no header line, header block, range rows or intents."""
    rows = []
    for l in stdout.strip().splitlines()[1:]:
        head = l.split(":", 1)[0]
        if l.startswith("    ") or not head.isdigit():
            continue
        rows.append(l.split("  # ", 1)[0])
    return rows


class TestOutlineImports(unittest.TestCase):
    """Module-level imports must be exactly re, os, sys, argparse."""

    def test_imports(self):
        with open(OUTLINE, encoding="utf-8") as f:
            source = f.read()
        import_lines = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") and not line.startswith(" "):
                import_lines.append(stripped)
            elif stripped.startswith("from ") and not line.startswith(" "):
                import_lines.append(stripped)
        expected = {"import re", "import os", "import sys", "import argparse"}
        self.assertEqual(set(import_lines), expected,
                         "Only re, os, sys, argparse allowed as module-level imports")


class TestOutlineMd(unittest.TestCase):
    """Markdown outline: headings, fences skipped, --depth."""

    def test_sample_md(self):
        p = run_outline(os.path.join(FIXTURES, "sample.md"))
        self.assertEqual(p.returncode, 0, p.out)
        lines = p.stdout.strip().splitlines()
        # header
        self.assertIn("sample.md:", lines[0])
        self.assertIn("lines", lines[0])
        self.assertIn("chars", lines[0])
        # expected rows (the heading inside the fence must NOT appear)
        rows = symbol_rows(p.stdout)
        self.assertEqual(rows[0], "3:# Top Level")
        self.assertEqual(rows[1], "7:  ## Section One")
        self.assertEqual(rows[2], "11:    ### Subsection")
        self.assertEqual(rows[3], "25:  ## Section Two")
        self.assertEqual(len(rows), 4, "fence heading must not appear")

    def test_depth(self):
        p = run_outline(os.path.join(FIXTURES, "sample.md"), "--depth", "1")
        self.assertEqual(p.returncode, 0, p.out)
        rows = symbol_rows(p.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], "3:# Top Level")


class TestOutlinePy(unittest.TestCase):
    """Python outline: class, def, async def, nesting by indent."""

    def test_sample_py(self):
        p = run_outline(os.path.join(FIXTURES, "sample.py"))
        self.assertEqual(p.returncode, 0, p.out)
        rows = symbol_rows(p.stdout)
        self.assertEqual(rows[0], "7:class Widget")
        self.assertEqual(rows[1], "10:  def render(self, ctx)")
        self.assertEqual(rows[2], "17:  def update(self, value)")
        self.assertEqual(rows[3], "22:async def fetch_data(url, timeout=30)")
        self.assertEqual(rows[4], "27:def helper(x)")
        self.assertEqual(len(rows), 5)


class TestOutlineCs(unittest.TestCase):
    """C# outline: namespace, class, interface, methods."""

    def test_sample_cs(self):
        p = run_outline(os.path.join(FIXTURES, "sample.cs"))
        self.assertEqual(p.returncode, 0, p.out)
        rows = symbol_rows(p.stdout)
        self.assertEqual(rows[0], "5:namespace ProjectArchitect.Core")
        self.assertEqual(rows[1], "7:  class Engine")
        self.assertEqual(rows[2], "10:    Start(string mode)")
        self.assertEqual(rows[3], "19:    Calculate(int a, int b)")
        self.assertEqual(rows[4], "25:  interface IPlugin")
        self.assertEqual(rows[5], "27:    Initialize()")
        self.assertEqual(len(rows), 6)


class TestOutlineJs(unittest.TestCase):
    """JS outline: function, class, method, arrow const, export default."""

    def test_sample_js(self):
        p = run_outline(os.path.join(FIXTURES, "sample.js"))
        self.assertEqual(p.returncode, 0, p.out)
        rows = symbol_rows(p.stdout)
        self.assertEqual(rows[0], "3:function greet(name)")
        self.assertEqual(rows[1], "7:class Router")
        self.assertEqual(rows[2], "9:  handle(req, res)")
        self.assertEqual(rows[3], "18:const transform = (data) =>")
        self.assertEqual(rows[4], "22:export default Router;")
        self.assertEqual(len(rows), 5)


class TestOutlineTs(unittest.TestCase):
    """TS outline: interface, type, enum, class."""

    def test_sample_ts(self):
        p = run_outline(os.path.join(FIXTURES, "sample.ts"))
        self.assertEqual(p.returncode, 0, p.out)
        rows = symbol_rows(p.stdout)
        self.assertEqual(rows[0], "4:interface Config")
        self.assertEqual(rows[1], "9:type Result")
        self.assertEqual(rows[2], "14:enum Status")
        self.assertEqual(rows[3], "20:class Service")
        self.assertEqual(rows[4], "21:  run(args: string[])")
        self.assertEqual(len(rows), 5)


def is_range(line):
    """A comment-block row: ``start-end:``."""
    head = line.split(":", 1)[0]
    return "-" in head and head.replace("-", "").isdigit()


class TestOutlineCommentary(unittest.TestCase):
    """Header block, intent lines, comment-block range rows (3.9.5 T3)."""

    def _out(self, name):
        p = run_outline(os.path.join(FIXTURES, name))
        self.assertEqual(p.returncode, 0, p.out)
        return p.stdout.strip().splitlines()

    def test_header_block(self):
        heads = {
            "sample.py": ['    """Sample Python file for outline tests.', "    ",
                          "    Header block: the outline prints these lines.", '    """'],
            "sample.md": ["    <!-- Sample Markdown for outline tests.",
                          "    Header block: the outline prints these lines. -->"],
            "sample.cs": ["    // Sample C# file for outline tests.",
                          "    // Header block: the outline prints these lines."],
            "sample.js": ["    /* Sample JS file for outline tests.",
                          "       Header block: the outline prints these lines. */"],
            "sample.ts": ["    // Sample TS file for outline tests.",
                          "    // Header block: the outline prints these lines."],
        }
        for name, want in heads.items():
            with self.subTest(name=name):
                lines = self._out(name)
                self.assertEqual(lines[1:1 + len(want)], want)
                self.assertFalse(lines[1 + len(want)].startswith("    "))

    def test_intent_lines(self):
        want = {
            "sample.py": ["7:class Widget  # A sample class.",
                          "27:def helper(x)  # Documented helper: its first line is the intent."],
            "sample.cs": ["10:    Start(string mode)  # Start the engine in the given mode."],
            "sample.js": ["9:  handle(req, res)  # Handle one request."],
            "sample.ts": ["20:class Service  # The service that runs the args."],
        }
        for name, rows in want.items():
            with self.subTest(name=name):
                lines = self._out(name)
                for r in rows:
                    self.assertIn(r, lines)
        # no intent → no suffix
        self.assertIn("19:    Calculate(int a, int b)", self._out("sample.cs"))
        self.assertIn("4:interface Config", self._out("sample.ts"))
        self.assertFalse([l for l in self._out("sample.cs") if l.endswith("  # ")])

    def test_comment_blocks(self):
        want = {
            "sample.py": ("14-16:  # A three-line comment block:", 2),
            "sample.md": ("21-23:<!-- A three-line comment block:", 3),
            "sample.cs": ("15-17:    // A three-line comment block:", 3),
            "sample.js": ("14-16:// A three-line comment block:", 3),
            "sample.ts": ("11-13:/*", 2),
        }
        for name, (row, pos) in want.items():
            with self.subTest(name=name):
                lines = self._out(name)
                ranges = [l for l in lines[1:] if is_range(l)]
                self.assertEqual(ranges, [row], "one range row, the header not repeated")
                # in line order among the symbol rows
                body = [l for l in lines[1:] if not l.startswith("    ")]
                self.assertEqual(body.index(row), pos)

    def test_credit_py_commentary(self):
        credit = os.path.join(PKG, "pa", "credit.py")
        p = run_outline(credit)
        self.assertEqual(p.returncode, 0, p.out)
        lines = p.stdout.splitlines()
        with open(credit, encoding="utf-8") as f:
            src = f.read().splitlines()
        self.assertEqual(lines[1:15], ["    " + l for l in src[:14]])
        intents = [l for l in lines if "  # " in l]
        names = ["consume", "tokens", "result_chars", "_rel_path",
                 "rows_for_call", "read_row", "carry_tokens"]
        self.assertEqual(len(intents), 7)
        for n, l in zip(names, intents):
            self.assertIn("def %s(" % n, l)
        self.assertFalse([l for l in lines[1:] if is_range(l)])


class TestOutlineEdgeCases(unittest.TestCase):
    """Unknown extension, missing file, real-file checks."""

    def test_txt_file(self):
        txt = os.path.join(FIXTURES, "sample.txt")
        os.makedirs(os.path.dirname(txt), exist_ok=True)
        try:
            with open(txt, "w") as f:
                f.write("hello\n")
            p = run_outline(txt)
            self.assertEqual(p.returncode, 0, p.out)
            lines = p.stdout.strip().splitlines()
            self.assertIn("lines", lines[0])
            self.assertEqual(lines[1], "no outline for .txt")
        finally:
            if os.path.isfile(txt):
                os.remove(txt)

    def test_missing_file(self):
        p = run_outline("does_not_exist.xyz")
        self.assertEqual(p.returncode, 0, p.out)
        self.assertIn("not found", p.stdout)

    def test_guard_py_decide(self):
        guard = os.path.join(PKG, "pa", "guard.py")
        p = run_outline(guard)
        self.assertEqual(p.returncode, 0, p.out)
        found = [l for l in p.stdout.splitlines() if "def decide" in l]
        self.assertTrue(found, "def decide must appear")
        line_no = int(found[0].split(":")[0])
        self.assertEqual(line_no, 563, "def decide should be at line 563")  # was 536 (3.15 T18 handback_deny); was 533 (fix-17 added two imports); was 525 (3.9.7 T9)

    def test_ledger_cli_speed(self):
        ledger_cli = os.path.join(PKG, "pa", "ledger_cli.py")
        t0 = time.time()
        p = run_outline(ledger_cli)
        elapsed = time.time() - t0
        self.assertEqual(p.returncode, 0, p.out)
        self.assertLess(elapsed, 0.5, "outline should finish in under 0.5 s")

    def test_kind_flag(self):
        """--kind forces a language regardless of extension."""
        py_fixture = os.path.join(FIXTURES, "sample.py")
        p = run_outline(py_fixture, "--kind", "md")
        self.assertEqual(p.returncode, 0, p.out)
        # py file forced to md: no headings, so only the header line
        rows = p.stdout.strip().splitlines()
        self.assertEqual(len(rows), 1)  # just the header


if __name__ == "__main__":
    unittest.main()
