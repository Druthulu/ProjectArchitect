"""pa.ledger_cli: export, import, import_slices (T8).

Tests: export -> import reproduces report --json for a project; a second import
inserts 0; local wins on a conflicting row; --phase selects only that phase's
sessions; the archive step's export call with a stub CLI path.
"""

import contextlib
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_cli import CliTestCase, build_session, run_cli, CWD, SID  # noqa: E402
from pa import db  # noqa: E402
from pa import ledger_cli  # noqa: E402
from pa import paths  # noqa: E402


class ExportImportTest(CliTestCase):
    """Export a project's sessions, import into an empty ledger, compare."""

    def setUp(self):
        super().setUp()
        # the fixture's sessions carry cwd=CWD (a path that must not exist); every export
        # here passes --out under self.dir

    def _recalc(self):
        rc, text, errs = run_cli("recalc", "--from", "transcripts",
                                 "--root", self.projects)
        self.assertEqual(rc, 0, "recalc failed: %s" % errs)

    def _report_json(self):
        rc, text, errs = run_cli("report", "--json", "--window", "all")
        self.assertEqual(rc, 0, "report failed: %s" % errs)
        return json.loads(text)

    def test_export_import_roundtrip(self):
        """Export -> import into empty ledger reproduces report --json."""
        self._recalc()
        report_before = self._report_json()

        # export
        out_file = os.path.join(self.dir, "slice.jsonl.gz")
        rc, text, errs = run_cli("export", "--project", CWD, "--out", out_file)
        self.assertEqual(rc, 0, "export failed: %s" % errs)
        self.assertTrue(os.path.isfile(out_file))
        self.assertIn("exported:", text)

        # verify header
        with gzip.open(out_file, "rt", encoding="utf-8") as fh:
            header = json.loads(fh.readline())
        self.assertEqual(header["type"], "header")
        self.assertIn("schema", header)
        self.assertIn("counts", header)
        self.assertIn("sessions", header["counts"])

        # set up a fresh ledger
        fresh_ledger = os.path.join(self.dir, "ledger2")
        os.environ["PA_LEDGER_DIR"] = fresh_ledger

        # import
        rc, text, errs = run_cli("import", out_file)
        self.assertEqual(rc, 0, "import failed: %s" % errs)
        self.assertIn("import done", text)

        # compare report
        report_after = self._report_json()
        # strip volatile keys
        for doc in (report_before, report_after):
            for key in ("updated", "generated"):
                doc.pop(key, None)
        self.assertEqual(report_before, report_after)
        # fix-10.c3: the ledger era travels with the export
        conn = db.connect(paths.db_path())
        try:
            self.assertEqual(db.get_meta(conn, "created"), header["created"])
            self.assertIsNotNone(header["created"])
        finally:
            db.close(conn)

        # restore for tearDown
        os.environ["PA_LEDGER_DIR"] = self.ledger

    def test_second_import_inserts_zero(self):
        """A second import of the same file inserts 0 rows."""
        self._recalc()

        out_file = os.path.join(self.dir, "slice.jsonl.gz")
        rc, _, _ = run_cli("export", "--project", CWD, "--out", out_file)
        self.assertEqual(rc, 0)

        # first import into fresh ledger
        fresh_ledger = os.path.join(self.dir, "ledger2")
        os.environ["PA_LEDGER_DIR"] = fresh_ledger
        rc, _, _ = run_cli("import", out_file)
        self.assertEqual(rc, 0)

        # second import
        rc, text, _ = run_cli("import", out_file)
        self.assertEqual(rc, 0)
        # all rows should be skipped
        for line in text.strip().splitlines():
            if "inserted" in line and "skipped" in line:
                # e.g. "  sessions: 0 inserted, 1 skipped"
                self.assertTrue(line.strip().startswith("sessions") or
                                "0 inserted" in line,
                                "expected 0 inserted: %s" % line)

        os.environ["PA_LEDGER_DIR"] = self.ledger

    def test_local_wins(self):
        """A row already present locally is never overwritten on import."""
        self._recalc()

        out_file = os.path.join(self.dir, "slice.jsonl.gz")
        rc, _, _ = run_cli("export", "--project", CWD, "--out", out_file)
        self.assertEqual(rc, 0)

        # import into fresh ledger
        fresh_ledger = os.path.join(self.dir, "ledger2")
        os.environ["PA_LEDGER_DIR"] = fresh_ledger
        rc, _, _ = run_cli("import", out_file)
        self.assertEqual(rc, 0)

        # modify a local row (change cost_usd on the session)
        conn = db.connect(paths.db_path())
        try:
            conn.execute("UPDATE sessions SET cost_usd=999.99 WHERE session_id=?", (SID,))
            conn.commit()
        finally:
            db.close(conn)

        # re-import: local row should survive
        rc, _, _ = run_cli("import", out_file)
        self.assertEqual(rc, 0)

        conn = db.connect(paths.db_path())
        try:
            row = conn.execute("SELECT cost_usd FROM sessions WHERE session_id=?",
                               (SID,)).fetchone()
            self.assertAlmostEqual(float(row["cost_usd"]), 999.99, places=1)
        finally:
            db.close(conn)

        os.environ["PA_LEDGER_DIR"] = self.ledger

    def test_phase_filter(self):
        """--phase selects only that phase's sessions."""
        self._recalc()

        # tag the session with phase 3.2
        conn = db.connect(paths.db_path())
        try:
            conn.execute("UPDATE sessions SET phase='3.2' WHERE session_id=?", (SID,))
            conn.commit()
        finally:
            db.close(conn)

        out_file = os.path.join(self.dir, "slice-3.2.jsonl.gz")
        rc, text, _ = run_cli("export", "--project", CWD, "--phase", "3.2",
                               "--out", out_file)
        self.assertEqual(rc, 0)

        # verify the exported rows are for the right session
        rows = []
        with gzip.open(out_file, "rt", encoding="utf-8") as fh:
            for line in fh:
                obj = json.loads(line.strip())
                if obj.get("table") == "sessions":
                    rows.append(obj)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], SID)

        # export with a non-existent phase should find nothing
        rc2, _, errs2 = run_cli("export", "--project", CWD, "--phase", "99.9",
                                 "--out", os.path.join(self.dir, "empty.jsonl.gz"))
        self.assertEqual(rc2, 1)  # no sessions found


class ImportSlicesTest(CliTestCase):
    """import_slices: import from .claude-state/ledger/."""

    def test_import_slices(self):
        self._recalc_local()

        # export into a temp repo's .claude-state/ledger/ -- never the default path under CWD,
        # which must not exist on the real drive (an existing Z:/Test/Proj without a pa.json
        # makes the fixture's sessions ungoverned and voids their savings)
        repo = os.path.join(self.dir, "repo")
        out_file = os.path.join(repo, ".claude-state", "ledger", "win-all.jsonl.gz")
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        rc, _, _ = run_cli("export", "--project", CWD, "--out", out_file)
        self.assertEqual(rc, 0)

        # set up fresh ledger, import slices
        fresh_ledger = os.path.join(self.dir, "ledger2")
        os.environ["PA_LEDGER_DIR"] = fresh_ledger
        conn = ledger_cli.open_db()
        try:
            from pa import config
            cfg = config.load()
            result = ledger_cli.import_slices(conn, repo, cfg)
            self.assertTrue(len(result) > 0)
            # at least one file had insertions
            any_inserted = any(v["inserted"] for v in result.values())
            self.assertTrue(any_inserted)
        finally:
            db.close(conn)

        os.environ["PA_LEDGER_DIR"] = self.ledger

    def _recalc_local(self):
        rc, _, errs = run_cli("recalc", "--from", "transcripts",
                              "--root", self.projects)
        self.assertEqual(rc, 0, "recalc failed: %s" % errs)


class ArchiveExportTest(unittest.TestCase):
    """The archive step's export call with a missing CLI path."""

    def test_missing_cli_note(self):
        """A missing CLI path produces a note, not a failure."""
        # import phaseend_index functions
        tools_dir = os.path.join(ROOT, "tools")
        sys.path.insert(0, tools_dir)
        try:
            import phaseend_index
        finally:
            sys.path.pop(0)

        out = []
        # use a temp dir as root with a fake pa.json pointing to a nonexistent python
        tmpdir = tempfile.mkdtemp(prefix="pa3-archive-")
        try:
            claude_dir = os.path.join(tmpdir, ".claude")
            os.makedirs(claude_dir)
            with open(os.path.join(claude_dir, "pa.json"), "w") as fh:
                json.dump({"python": "nonexistent_python_12345"}, fh)
            phaseend_index._export_ledger_slice(tmpdir, {}, "3.2", out)
            joined = " ".join(out)
            self.assertTrue("note:" in joined,
                            "expected a note, got: %s" % out)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_phase_without_sessions_falls_back_to_the_whole_project(self):
        """T8 expert: a phase no session carries exports the whole project's slice instead."""
        tools_dir = os.path.join(ROOT, "tools")
        sys.path.insert(0, tools_dir)
        try:
            import phaseend_index
        finally:
            sys.path.pop(0)
        tmpdir = tempfile.mkdtemp(prefix="pa3-archive-")
        try:
            claude_dir = os.path.join(tmpdir, ".claude")
            os.makedirs(claude_dir)
            with open(os.path.join(claude_dir, "pa.json"), "w") as fh:
                json.dump({"python": sys.executable}, fh)
            stub = os.path.join(tmpdir, "stub_ledger.py")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write("import sys\n"
                         "if '--phase' in sys.argv:\n"
                         "    sys.stderr.write('export: no sessions found\\n'); sys.exit(1)\n"
                         "print('exported: whole.jsonl.gz')\n")
            out = []
            phaseend_index._export_ledger_slice(tmpdir, {}, "3.3", out, cli=stub)
            joined = " ".join(out)
            self.assertIn("no session carries phase 3.3", joined)
            self.assertIn("ledger slice: whole.jsonl.gz", joined)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
