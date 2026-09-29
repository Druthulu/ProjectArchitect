"""Tests for curate.py: ops --reindex refreshes line counts and appends missing rows."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PKG, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, PKG)

import curate as CU  # noqa: E402


# --------------------------------------------------------------------------- fixture helpers

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


HEADER = "<!-- Ops index: <file> | <title> | <lines>. One line per topic; grep it, never read it whole. -->"
RETIRED = "- [Old topic](../retired/ops/old.md) — retired gen 2"
INDEX_TEXT = HEADER + "\n" + "alpha.md | Alpha as written | 2\n" + "\n" + RETIRED + "\n"

ALPHA = "# Alpha heading\n\nline 3\nline 4\n"          # 4 lines, row says 2
BETA = "intro\n# Beta topic\nbody\n"                    # 3 lines, no row


class OpsReindexTest(unittest.TestCase):
    def setUp(self):
        scratch = os.path.join(os.path.dirname(PKG), ".run")
        os.makedirs(scratch, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="pa3-curate-test-", dir=scratch)
        self.ops = os.path.join(self.tmp, "docs", "ops")
        self.index = os.path.join(self.ops, "INDEX.md")
        _write(self.index, INDEX_TEXT)
        _write(os.path.join(self.ops, "alpha.md"), ALPHA)
        _write(os.path.join(self.ops, "beta.md"), BETA)
        self._env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CU.main(["ops", "--reindex"] + list(extra))
        return rc, buf.getvalue()

    def test_reindex_updates_and_appends(self):
        rc, _ = self._run()
        self.assertEqual(rc, 0)
        lines = _read(self.index).decode("utf-8").split("\n")
        self.assertEqual(lines[0], HEADER)
        self.assertEqual(lines[1], "alpha.md | Alpha as written | 4")
        self.assertEqual(lines[2], "beta.md | Beta topic | 3")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], RETIRED)
        self.assertEqual(lines[5:], [""])

    def test_dry_run_writes_nothing(self):
        before = _read(self.index)
        rc, out = self._run("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(_read(self.index), before)
        self.assertEqual(out.splitlines(),
                         ["alpha.md: 2 -> 4 lines", "add beta.md | Beta topic | 3"])


if __name__ == "__main__":
    unittest.main()
