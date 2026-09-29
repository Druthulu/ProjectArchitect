"""Tests for phaseend_index.py: archived-phase assemble/lint, closer by title."""

import json
import os
import shutil
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(PKG, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, PKG)

import phaseend_index as PI  # noqa: E402


# --------------------------------------------------------------------------- fixture helpers

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


PLAN_TEXT = """\
# Phase 9.9 — test phase        (implements GENERATION_PLAN.md phase 9.9)
Milestone: everything green — verified by: `echo ok`
Approved: 2026-01-01   Planner: test   Plan-hash: 0000

## Tasks

- T7 | done | expert-fable | title: some work | done-when: all good
- T8 | done | expert-fable | title: phase close and records | done-when: milestone green
"""

T7_TEXT = """\
# T7 — some work

Status: done | commit: aaa1111
Done: the work
Verified: tests pass
"""

T8_TEXT = """\
# T8 — phase close and records

Status: done | commit: bbb2222
Done: closed
Verified: MILESTONE: green
"""

RECAP_TEXT = """\
## Recap
The test phase did what it set out to do.

## Binding lines
- binding: never break the fixture
"""


def _build_archive(root, phase="9.9"):
    """Create a minimal archived phase under phase-ends/phase-<N>/."""
    base = os.path.join(root, "phase-ends", "phase-%s" % phase)
    _write(os.path.join(base, "PHASE_PLAN.md"), PLAN_TEXT)
    _write(os.path.join(base, "tasks", "T7.md"), T7_TEXT)
    _write(os.path.join(base, "tasks", "T8.md"), T8_TEXT)
    _write(os.path.join(base, "RECAP.md"), RECAP_TEXT)
    # pa.json so find_root/cfg work
    _write(os.path.join(root, ".claude", "pa.json"), "{}\n")
    return root


# --------------------------------------------------------------------------- tests

class TestArchivedPhase(unittest.TestCase):
    """assemble/lint on an archived phase (no current/PHASE_PLAN.md)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _build_archive(self.tmp)
        self._orig_root_env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig_root_env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig_root_env

    def test_plan_bits_reads_archive(self):
        bits = PI.plan_bits(self.tmp, {}, phase="9.9")
        self.assertEqual(bits["phase"], "9.9")
        self.assertEqual(bits["name"], "test phase")
        self.assertEqual(len(bits["tasks"]), 2)

    def test_load_summaries_reads_archive(self):
        sums = PI.load_summaries(self.tmp, {}, phase="9.9")
        self.assertEqual(len(sums), 2)
        ids = [s["id"] for s in sums]
        self.assertIn("T7", ids)
        self.assertIn("T8", ids)

    def test_assemble_archived_phase(self):
        """assemble 9.9 produces a PhaseEnd from the archive."""
        import argparse
        a = argparse.Namespace(phase="9.9")
        rc = PI.cmd_assemble(a, self.tmp, {})
        self.assertEqual(rc, 0)
        pe = os.path.join(self.tmp, "phase-ends", "PhaseEnd_Phase9.9.md")
        self.assertTrue(os.path.isfile(pe))
        text = PI.PE.read_text(pe)
        self.assertIn("Phase 9.9", text)
        self.assertIn("test phase", text)
        # recap should be filled from archive
        self.assertIn("The test phase did what it set out to do", text)

    def test_lint_archived_phase(self):
        """lint 9.9 passes when the archive has all done tasks and a MILESTONE: green."""
        # assemble first so the PhaseEnd file exists
        import argparse
        a = argparse.Namespace(phase="9.9")
        PI.cmd_assemble(a, self.tmp, {})
        a2 = argparse.Namespace(phase="9.9", lint_file=None)
        rc = PI.cmd_lint(a2, self.tmp, {})
        self.assertEqual(rc, 0)


PLAN_35_TEXT = """\
# Phase 3.5 — README 3.0 + generation close        (implements GENERATION_PLAN.md phase 3.5)
Milestone: everything done
Approved: 2026-09-21   Planner: test   Plan-hash: 0000

## Tasks

- T1 | next | expert-fable | title: statusline | done-when: lines look right
- T8 | queued | expert-fable | title: phase close | done-when: milestone green
"""


class TestArchivedWithCurrentPlan(unittest.TestCase):
    """lint/assemble on an archived phase while a different current plan exists."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-cur-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # archived phase 9.9 (all done, MILESTONE: green)
        _build_archive(self.tmp)
        # current plan for a different phase (3.5)
        _write(os.path.join(self.tmp, "phase-ends", "current", "PHASE_PLAN.md"),
               PLAN_35_TEXT)
        self._orig_root_env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig_root_env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig_root_env

    def test_lint_archived_phase_with_current_present(self):
        """lint 9.9 reads the archive, not the current 3.5 plan."""
        import argparse
        a = argparse.Namespace(phase="9.9")
        PI.cmd_assemble(a, self.tmp, {})
        a2 = argparse.Namespace(phase="9.9", lint_file=None)
        rc = PI.cmd_lint(a2, self.tmp, {})
        self.assertEqual(rc, 0)

    def test_plan_bits_reads_archive_not_current(self):
        """plan_bits('9.9') returns 9.9's tasks, not 3.5's."""
        bits = PI.plan_bits(self.tmp, {}, phase="9.9")
        self.assertEqual(bits["phase"], "9.9")
        self.assertEqual(len(bits["tasks"]), 2)
        # all done
        for t in bits["tasks"]:
            self.assertEqual(t.status, "done")

    def test_plan_bits_current_phase_reads_current(self):
        """plan_bits('3.5') reads the current plan."""
        bits = PI.plan_bits(self.tmp, {}, phase="3.5")
        self.assertEqual(bits["phase"], "3.5")


class TestCloserByTitle(unittest.TestCase):
    """The summary with 'phase close' in its title wins first."""

    def test_phase_close_title_wins(self):
        summaries = [
            {"id": "T8", "title": "phase close and records",
             "sec": {"Verified": ["MILESTONE: green"]}, "path": "T8.md"},
            {"id": "T9", "title": "resume test",
             "sec": {"Verified": ["tests pass"]}, "path": "T9.md"},
            {"id": "T10", "title": "statusline updates",
             "sec": {"Verified": ["tests pass"]}, "path": "T10.md"},
            {"id": "T11", "title": "seed audit",
             "sec": {"Verified": ["tests pass"]}, "path": "T11.md"},
        ]
        cl = PI.closer(summaries)
        self.assertIsNotNone(cl)
        self.assertEqual(cl["id"], "T8")

    def test_phase_close_title_case_insensitive(self):
        summaries = [
            {"id": "T5", "title": "Phase Close: wrap up",
             "sec": {}, "path": "T5.md"},
            {"id": "T6", "title": "other work",
             "sec": {"Verified": ["ok"]}, "path": "T6.md"},
        ]
        cl = PI.closer(summaries)
        self.assertEqual(cl["id"], "T5")

    def test_fallback_to_non_t_id(self):
        """When no title matches, a non-T<n> id wins (existing behaviour)."""
        summaries = [
            {"id": "T1", "title": "work",
             "sec": {}, "path": "T1.md"},
            {"id": "PHASE-END", "title": "close",
             "sec": {}, "path": "PE.md"},
        ]
        cl = PI.closer(summaries)
        self.assertEqual(cl["id"], "PHASE-END")

    def test_fallback_to_last_verified(self):
        """When no title or non-T<n> id matches, the last Verified: wins."""
        summaries = [
            {"id": "T1", "title": "work",
             "sec": {"Verified": ["ok"]}, "path": "T1.md"},
            {"id": "T2", "title": "more work",
             "sec": {"Verified": ["also ok"]}, "path": "T2.md"},
        ]
        cl = PI.closer(summaries)
        self.assertEqual(cl["id"], "T2")


INBOX_ROWS = [("I1", "3.11 wiki: move the pages", "deferred:3.11"),
              ("I2", "tidy the launcher -> 3.11", "deferred:3.11"),
              ("I3", "later: revisit the ops sunset", "deferred:3.11"),
              ("I4", "Gen 4: a hosted mode", "deferred:gen4"),
              ("I5", "check the bake host after 22:00", "now")]


class TestDiscussionAssembleAndArchive(unittest.TestCase):
    """A fixture phase with two discussion records and routed inbox rows in INDEX.md."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-disc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _build_archive(self.tmp)
        cur = os.path.join(self.tmp, "phase-ends", "current")
        # build a current plan so assemble works
        _write(os.path.join(cur, "PHASE_PLAN.md"),
               "# Phase 3.7 — test discussions        (implements GENERATION_PLAN.md phase 3.7)\n"
               "Milestone: everything green — verified by: `echo ok`\n"
               "Approved: 2026-09-20   Planner: test   Plan-hash: 0000\n\n"
               "## Tasks\n\n"
               "- T1 | done | expert-fable | title: card design | done-when: card looks right\n")
        _write(os.path.join(cur, "tasks", "T1.md"),
               "# T1 — card design\n\nStatus: done | commit: ccc3333\nDone: card\nVerified: tests pass\n")
        _write(os.path.join(cur, "logs", "T1.md"),
               "# T1 full log\n\n## Timeline\n- ran the gate\n")
        # two discussion records: D1 deferred:3.8, D2 executed
        disc = os.path.join(cur, "discussions")
        _write(os.path.join(disc, "D1.md"),
               "# D1 — card format (2026-09-20, session router)\n"
               "Status: deferred:3.8\n\n## Decisions\n- keep it short\n\n"
               "## Plan edits\n- (none)\n\n## Open\n- (none)\n\n"
               "## Deferred\n- deferred: slice format review → 3.8\n")
        _write(os.path.join(disc, "D2.md"),
               "# D2 — seed lines (2026-09-21, session router)\n"
               "Status: executed\n\n## Decisions\n- add deferred\n\n"
               "## Plan edits\n- (none)\n\n## Open\n- (none)\n\n"
               "## Deferred\n")
        _write(os.path.join(disc, "INDEX.md"),
               "# Discussions -- one line per record\n"
               "# id | topic | date | status | path\n"
               "D1 | card format | 2026-09-20 | deferred:3.8 | phase-ends/current/discussions/D1.md\n"
               "D2 | seed lines | 2026-09-21 | executed | phase-ends/current/discussions/D2.md\n"
               + "".join("%s | %s | 2026-09-26 | %s | phase-ends/current/discussions/inbox-x.md\n"
                         % r for r in INBOX_ROWS))
        self._orig_root_env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig_root_env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig_root_env

    def test_assemble_discussions_section(self):
        """assemble writes ## Discussions with both records' statuses."""
        import argparse
        a = argparse.Namespace(phase="3.7")
        PI.cmd_assemble(a, self.tmp, {})
        pe = os.path.join(self.tmp, "phase-ends",
                          "PhaseEnd_Phase3.7.md")
        text = open(pe, encoding="utf-8").read()
        self.assertIn("## Discussions", text)
        self.assertIn("D1 | card format | deferred:3.8", text)
        self.assertIn("D2 | seed lines | executed", text)

    def test_assemble_deferred_from_records_and_inbox(self):
        """## Deferred has the record's deferred: line and the four deferred: I rows, no now row."""
        import argparse
        a = argparse.Namespace(phase="3.7")
        PI.cmd_assemble(a, self.tmp, {})
        pe = os.path.join(self.tmp, "phase-ends",
                          "PhaseEnd_Phase3.7.md")
        text = open(pe, encoding="utf-8").read()
        # from D1's deferred section
        self.assertIn("from D1: slice format review", text)
        # routed inbox rows
        block = text.split("## Deferred", 1)[1].split("\n## ", 1)[0]
        for rid, topic, status in INBOX_ROWS:
            line = "- %s (%s): %s" % (rid, status, topic)
            if status == "now":
                self.assertNotIn(rid + " ", block)
            else:
                self.assertIn(line, block)

    def test_archive_appends_to_discussion_index(self):
        """archive appends the two discussion lines to DISCUSSION_INDEX.md."""
        import argparse
        a = argparse.Namespace(phase="3.7")
        PI.cmd_assemble(a, self.tmp, {})
        # write recap so lint passes
        _write(os.path.join(self.tmp, "phase-ends", "current", "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        # mock card.py check to exit 0 by creating a small HOW_WE_WORK.md
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        a2 = argparse.Namespace(phase="3.7")
        PI.cmd_archive(a2, self.tmp, {})
        di = os.path.join(self.tmp, "phase-ends", "DISCUSSION_INDEX.md")
        self.assertTrue(os.path.isfile(di))
        text = open(di, encoding="utf-8").read()
        self.assertIn("3.7 | D1", text)
        self.assertIn("3.7 | D2", text)
        self.assertIn("deferred:3.8", text)
        self.assertIn("executed", text)

    def _archive_out(self):
        import argparse
        import contextlib
        import io
        _write(os.path.join(self.tmp, "phase-ends", "current", "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_archive(argparse.Namespace(phase="3.7"), self.tmp, {})
        return buf.getvalue()

    def test_archive_reindexes_ops_index(self):
        """archive refreshes a stale docs/ops/INDEX.md row count and names it (fix-16)."""
        ops = os.path.join(self.tmp, "docs", "ops")
        _write(os.path.join(ops, "setup.md"), "# Setup\none\ntwo\n")
        _write(os.path.join(ops, "INDEX.md"),
               "# Ops topics -- file | title | lines\nsetup.md | Setup | 99\n")
        out = self._archive_out()
        self.assertIn("setup.md | Setup | 3\n",
                      open(os.path.join(ops, "INDEX.md"), encoding="utf-8").read())
        self.assertIn("ops index", out)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "phase-ends", "phase-3.7")))

    def test_archive_without_ops_index(self):
        """no docs/ops/INDEX.md: archive runs as before, no ops line."""
        out = self._archive_out()
        self.assertNotIn("ops index", out)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "phase-ends", "phase-3.7")))

    def test_archive_clears_router_session(self):
        """fix-18: archive nulls router_session in .run/status.json and says so."""
        spath = os.path.join(self.tmp, ".run", "status.json")
        _write(spath, json.dumps({"phase": "3.7", "router_session": "sess-1"}))
        out = self._archive_out()
        self.assertIsNone(json.load(open(spath, encoding="utf-8"))["router_session"])
        self.assertIn("owner: cleared", out)

    def test_archive_without_status_file(self):
        """fix-18: no .run/status.json: archive runs as before, no owner line, no file made."""
        out = self._archive_out()
        self.assertNotIn("owner: cleared", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".run", "status.json")))

    def test_archive_refuses_card_over_cap(self):
        """archive refuses when card.py check exits 1 (over cap or placeholders)."""
        import argparse
        a = argparse.Namespace(phase="3.7")
        PI.cmd_assemble(a, self.tmp, {})
        _write(os.path.join(self.tmp, "phase-ends", "current", "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        # create a HOW_WE_WORK.md with placeholders so card check fails
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"),
               "# How we work\n\n## Developer\n{{DEVELOPER_NAME}}\n"
               "## Standing decisions\n- decision\n")
        a2 = argparse.Namespace(phase="3.7")
        with self.assertRaises(SystemExit):
            PI.cmd_archive(a2, self.tmp, {})

    INBOX_TMPL = ("# INBOX\n\n<!-- The developer writes here. -->\n\n"
                  "- <note, correction, priority change, or question>\n")

    def _ready_to_archive(self, inbox):
        """assemble with a recap and a passing card; write current/INBOX.md."""
        import argparse
        _write(os.path.join(self.tmp, "phase-ends", "current", "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        _write(os.path.join(self.tmp, "phase-ends", "current", "INBOX.md"), inbox)

    def test_archive_refuses_unconsumed_inbox(self):
        """fix-2: a real bullet in current/INBOX.md blocks archive; the refusal names inbox-consume."""
        import argparse, io, contextlib
        self._ready_to_archive(self.INBOX_TMPL + "- developer: route me\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            with self.assertRaises(SystemExit):
                PI.cmd_archive(argparse.Namespace(phase="3.7"), self.tmp, {})
        self.assertIn("inbox-consume", buf.getvalue())
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "phase-ends", "current")))

    def test_archive_proceeds_with_template_inbox(self):
        """fix-2: header plus placeholder only is not pending; a fresh current gets the template."""
        import argparse
        _write(os.path.join(self.tmp, "templates", "INBOX.template.md"), self.INBOX_TMPL)
        self._ready_to_archive(self.INBOX_TMPL)
        PI.cmd_archive(argparse.Namespace(phase="3.7"), self.tmp, {})
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "phase-ends", "phase-3.7")))

    def test_fresh_current_gets_inbox_from_template(self):
        """fix-2: after archive, current/INBOX.md exists and equals templates/INBOX.template.md."""
        import argparse
        _write(os.path.join(self.tmp, "templates", "INBOX.template.md"), self.INBOX_TMPL)
        self._ready_to_archive(self.INBOX_TMPL)
        PI.cmd_archive(argparse.Namespace(phase="3.7"), self.tmp, {})
        inbox = os.path.join(self.tmp, "phase-ends", "current", "INBOX.md")
        self.assertTrue(os.path.isfile(inbox))
        with open(inbox, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.INBOX_TMPL)

    def test_assemble_writes_cumulative_discussion_index(self):
        """assemble alone creates DISCUSSION_INDEX.md with this phase's two rows."""
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        di = os.path.join(self.tmp, "phase-ends", "DISCUSSION_INDEX.md")
        self.assertTrue(os.path.isfile(di))
        text = open(di, encoding="utf-8").read()
        self.assertIn("3.7 | D1", text)
        self.assertIn("3.7 | D2", text)

    def test_assemble_twice_is_idempotent(self):
        """a second assemble leaves DISCUSSION_INDEX.md byte-identical."""
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        di = os.path.join(self.tmp, "phase-ends", "DISCUSSION_INDEX.md")
        first = open(di, encoding="utf-8").read()
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        second = open(di, encoding="utf-8").read()
        self.assertEqual(first, second)

    def test_archive_after_assemble_is_idempotent(self):
        """archive after assemble already wrote the cumulative index changes nothing."""
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        di = os.path.join(self.tmp, "phase-ends", "DISCUSSION_INDEX.md")
        before = open(di, encoding="utf-8").read()
        _write(os.path.join(self.tmp, "phase-ends", "current", "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        PI.cmd_assemble(argparse.Namespace(phase="3.7"), self.tmp, {})
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        PI.cmd_archive(argparse.Namespace(phase="3.7"), self.tmp, {})
        after = open(di, encoding="utf-8").read()
        self.assertEqual(before, after)


class TestDiscussionCumulativeNoRecords(unittest.TestCase):
    """A phase with no discussion records: assemble writes the header only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-disc-empty-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _build_archive(self.tmp)
        cur = os.path.join(self.tmp, "phase-ends", "current")
        _write(os.path.join(cur, "PHASE_PLAN.md"),
               "# Phase 3.8 — no discussions        (implements GENERATION_PLAN.md phase 3.8)\n"
               "Milestone: everything green — verified by: `echo ok`\n"
               "Approved: 2026-09-22   Planner: test   Plan-hash: 0000\n\n"
               "## Tasks\n\n"
               "- T1 | done | expert-fable | title: work | done-when: works\n")
        _write(os.path.join(cur, "tasks", "T1.md"),
               "# T1 — work\n\nStatus: done | commit: ddd4444\nDone: work\nVerified: tests pass\n")
        self._orig_root_env = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig_root_env is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig_root_env

    def test_assemble_no_records_writes_header_only(self):
        """A phase with no discussion records yields the header-only cumulative file."""
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        di = os.path.join(self.tmp, "phase-ends", "DISCUSSION_INDEX.md")
        self.assertTrue(os.path.isfile(di))
        text = open(di, encoding="utf-8").read()
        self.assertEqual(text, PI.DISC_HEADER)


class TestVerifyOutput(unittest.TestCase):
    """verify prints only red clauses + summary; --verbose prints all."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-verify-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _write(os.path.join(self.tmp, ".claude", "pa.json"), "{}\n")
        os.makedirs(os.path.join(self.tmp, ".run", "logs"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "phase-ends", "current", "tasks"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "tools"), exist_ok=True)
        # create a minimal run.sh that just runs its command
        _write(os.path.join(self.tmp, "tools", "run.sh"),
               '#!/usr/bin/env bash\nNAME="$1"; shift\n'
               'while [ "$1" != "--" ] && [ $# -gt 0 ]; do shift; done; shift\n'
               'LOG=".run/logs/${NAME}.log"\nmkdir -p "$(dirname "$LOG")"\n'
               '"$@" > "$LOG" 2>&1; RC=$?; cat "$LOG"; exit $RC\n')
        self._orig = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig

    def _plan(self, clauses_str):
        _write(os.path.join(self.tmp, "phase-ends", "current", "PHASE_PLAN.md"),
               "# Phase 99 — verify test\n"
               "Milestone: all good — verified by: %s\n"
               "Approved: 2026-01-01   Planner: test   Plan-hash: 0000\n\n"
               "## Tasks\n\n"
               "- T1 | done | expert-fable | title: work | done-when: works\n" % clauses_str)
        _write(os.path.join(self.tmp, "phase-ends", "current", "tasks", "T1.md"),
               "# T1 — work\n\nStatus: done | commit: aaa1111\nDone: ok\n"
               "Verified: MILESTONE: green\n")

    def test_all_green_prints_summary_only(self):
        self._plan("`echo ok`; `echo fine`")
        import argparse
        a = argparse.Namespace(phase=None, verbose=False)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_verify(a, self.tmp, {})
        self.assertEqual(rc, 0)
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^VERIFY: GREEN \(2/2\)$")

    def test_one_red_prints_clause_and_summary(self):
        self._plan("`echo ok`; `false`")
        import argparse
        a = argparse.Namespace(phase=None, verbose=False)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_verify(a, self.tmp, {})
        self.assertEqual(rc, 1)
        out = buf.getvalue().strip()
        lines = [l for l in out.split("\n") if l.strip()]
        # should have the RED clause + summary
        self.assertTrue(any(l.startswith("RED ") for l in lines))
        self.assertTrue(lines[-1].startswith("VERIFY: RED"))
        self.assertRegex(lines[-1], r"^VERIFY: RED \(1/2\)$")
        # should NOT have the GREEN clause
        self.assertFalse(any(l.startswith("GREEN ") for l in lines))

    def test_prints_clause_judged_on_output_not_exit(self):
        # 3.8 T8: `grep -c` exits 1 on a zero count; a `prints` clause is judged on the output
        self._plan("`bash -c 'echo 0 && exit 1'` prints `0`; `bash -c 'echo 0 && exit 1'` exits 0")
        import argparse
        a = argparse.Namespace(phase=None, verbose=True)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_verify(a, self.tmp, {})
        self.assertEqual(rc, 1)
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        self.assertTrue(lines[0].startswith("GREEN 1:"))
        self.assertTrue(any(l.startswith("RED 2:") for l in lines))
        self.assertRegex(lines[-1], r"^VERIFY: RED \(1/2\)$")

    def test_verbose_prints_every_clause(self):
        self._plan("`echo ok`; `false`")
        import argparse
        a = argparse.Namespace(phase=None, verbose=True)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_verify(a, self.tmp, {})
        self.assertEqual(rc, 1)
        out = buf.getvalue().strip()
        lines = [l for l in out.split("\n") if l.strip()]
        # both GREEN and RED clauses should be present
        self.assertTrue(any(l.startswith("GREEN ") for l in lines))
        self.assertTrue(any(l.startswith("RED ") for l in lines))
        self.assertRegex(lines[-1], r"^VERIFY: RED \(1/2\)$")

    def test_non_utf8_clause_log_does_not_abort(self):
        # 3.10: a clause log holding cp1252 0xb1 (`±`) is read tolerantly
        self._plan("`printf 'x \\261 ok\\n'` prints `ok`; `false`")
        import argparse
        a = argparse.Namespace(phase=None, verbose=True)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_verify(a, self.tmp, {})
        with open(os.path.join(self.tmp, ".run", "logs", "verify1.log"), "rb") as fh:
            self.assertIn(b"\xb1", fh.read())
        out = buf.getvalue()
        self.assertNotIn("UnicodeDecodeError", out)
        lines = [l for l in out.strip().split("\n") if l.strip()]
        self.assertTrue(lines[0].startswith("GREEN 1:"))
        self.assertTrue(any(l.startswith("RED 2:") for l in lines))
        self.assertEqual(rc, 1)


class TestPhaseendOutputLimits(unittest.TestCase):
    """assemble/lint/archive line counts."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-limits-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _build_archive(self.tmp)
        cur = os.path.join(self.tmp, "phase-ends", "current")
        _write(os.path.join(cur, "PHASE_PLAN.md"),
               "# Phase 3.8 — output test        (implements GENERATION_PLAN.md phase 3.8)\n"
               "Milestone: everything green — verified by: `echo ok`\n"
               "Approved: 2026-09-22   Planner: test   Plan-hash: 0000\n\n"
               "## Tasks\n\n"
               "- T1 | done | expert-fable | title: work | done-when: works\n")
        _write(os.path.join(cur, "tasks", "T1.md"),
               "# T1 — work\n\nStatus: done | commit: ddd4444\nDone: work\n"
               "Verified: MILESTONE: green\n")
        _write(os.path.join(cur, "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        _write(os.path.join(self.tmp, "GENERATION_PLAN.md"),
               "# Generation 3 — test\n\n## Phases\n"
               "- 3.8 output test | milestone: gate | scope: s | depends: — "
               "| status: open | phase-end: -\n")
        self._orig = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig

    def test_assemble_at_most_five_lines(self):
        import argparse, io, contextlib
        a = argparse.Namespace(phase="3.8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_assemble(a, self.tmp, {})
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        self.assertLessEqual(len(lines), 5)

    def test_lint_at_most_five_lines(self):
        import argparse, io, contextlib
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        a = argparse.Namespace(phase="3.8", lint_file=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_lint(a, self.tmp, {})
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        self.assertLessEqual(len(lines), 5)

    def test_archive_at_most_five_plus_run(self):
        import argparse, io, contextlib
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        # git init so archive's git mv works
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.tmp,
                       capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmp,
                       capture_output=True)
        a = argparse.Namespace(phase="3.8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_archive(a, self.tmp, {})
        out = buf.getvalue().strip()
        all_lines = [l for l in out.split("\n") if l.strip()]
        run_lines = [l for l in all_lines if l.startswith("RUN:")]
        info_lines = [l for l in all_lines if not l.startswith(("RUN:", "stage:"))]
        self.assertLessEqual(len(info_lines), 5)
        # archive of a generation phase should have a RUN: line
        self.assertGreaterEqual(len(run_lines), 1)
        # 3.11 T11: the generation's last phase ends with the generation end as a RUN: line after
        # gen-close, never cut by the info cap
        self.assertIn("tools/plan_edit.py gen-close 3.8", run_lines[0])
        self.assertTrue(run_lines[-1].endswith("tools/genend_index.py assemble 3"), run_lines)


class TestMilestoneClauses(unittest.TestCase):
    """3.11 T11: verified-by clauses split at ';' outside backticks only."""

    def test_a_commands_own_semicolons_stay_in_its_clause(self):
        m = ('Milestone: tests pass — verified by: `PY -m unittest discover -s tests && '
             'PY -c "import json; n=1; print(n)"` exits 0')
        gate, clauses = PI.milestone_clauses(m)
        self.assertEqual(gate, "tests pass")
        self.assertEqual(len(clauses), 1, clauses)
        self.assertTrue(clauses[0].endswith('print(n)"` exits 0'), clauses)

    def test_clauses_between_commands_still_split(self):
        m = 'Milestone: g — verified by: `PY a.py` exits 0; `PY b.py` exits 0;  `PY -c "x; y"` exits 0'
        self.assertEqual(PI.milestone_clauses(m)[1],
                         ["`PY a.py` exits 0", "`PY b.py` exits 0", '`PY -c "x; y"` exits 0'])


FAKE_LEDGER = """\
import os, sys
root = sys.argv[sys.argv.index("--project") + 1]
d = os.path.join(root, ".claude-state", "ledger")
os.makedirs(d, exist_ok=True)
p = os.path.join(d, "phase-3.8.jsonl.gz")
open(p, "wb").write(b"x")
print("exported: %s" % p)
"""


class TestLedgerSliceStaged(TestPhaseendOutputLimits):
    """3.10 T1: archive stages the ledger slice; lint fails on an untracked slice."""

    def setUp(self):
        super().setUp()
        import subprocess
        home = os.path.join(self.tmp, "_home")
        _write(os.path.join(home, ".claude", "pa3", "pa_ledger.py"), FAKE_LEDGER)
        _write(os.path.join(self.tmp, ".claude", "pa.json"),
               json.dumps({"python": sys.executable}) + "\n")
        _write(os.path.join(self.tmp, ".gitignore"), "_home/\n")
        for k in ("HOME", "USERPROFILE"):
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None
                            else os.environ.__setitem__(k, old))
            os.environ[k] = home
        for cmd in (["init", "-q"], ["config", "user.email", "t@t.t"], ["config", "user.name", "T"],
                    ["config", "commit.gpgsign", "false"], ["add", "-A"],
                    ["commit", "-q", "-m", "init"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)

    def _git_out(self, *args):
        import subprocess
        return subprocess.run(("git",) + args, cwd=self.tmp, capture_output=True,
                              text=True).stdout

    def test_archive_stages_slice(self):
        import argparse, io, contextlib
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_archive(argparse.Namespace(phase="3.8"), self.tmp, {})
        out = buf.getvalue()
        self.assertIn("stage: .claude-state/ledger/phase-3.8.jsonl.gz", out)
        self.assertIn("ledger slice:", out)
        self.assertIn(".claude-state/ledger/phase-3.8.jsonl.gz",
                      self._git_out("diff", "--cached", "--name-only"))

    def test_lint_fails_on_untracked_slice(self):
        import argparse, io, contextlib
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        rel = ".claude-state/ledger/x.jsonl.gz"
        _write(os.path.join(self.tmp, rel), "x")
        a = argparse.Namespace(phase="3.8", lint_file=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_lint(a, self.tmp, {})
        self.assertNotEqual(rc, 0)
        self.assertIn(rel, buf.getvalue())
        self._git_out("add", "-A")
        self._git_out("commit", "-q", "-m", "slice")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_lint(a, self.tmp, {})
        self.assertEqual(rc, 0, buf.getvalue())


class TestAssembleAdoptsPending(unittest.TestCase):
    """assemble adopts pending research reports first, within the five-line cap."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-pe-adopt-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _build_archive(self.tmp)
        cur = os.path.join(self.tmp, "phase-ends", "current")
        _write(os.path.join(cur, "PHASE_PLAN.md"),
               "# Phase 3.8 — adopt test        (implements GENERATION_PLAN.md phase 3.8)\n"
               "Milestone: everything green — verified by: `echo ok`\n"
               "Approved: 2026-09-22   Planner: test   Plan-hash: 0000\n\n"
               "## Tasks\n\n"
               "- T1 | done | expert-fable | title: work | done-when: works\n")
        _write(os.path.join(cur, "tasks", "T1.md"),
               "# T1 — work\n\nStatus: done | commit: ddd4444\nDone: work\n"
               "Verified: MILESTONE: green\n")
        _write(os.path.join(cur, "RECAP.md"),
               "## Recap\nDone.\n\n## Binding lines\n- (none)\n")
        _write(os.path.join(self.tmp, "HOW_WE_WORK.md"), "# How we work\nShort.\n")
        _write(os.path.join(self.tmp, "GENERATION_PLAN.md"),
               "# Generation 3 — test\n\n## Phases\n"
               "- 3.8 adopt test | milestone: gate | scope: s | depends: — "
               "| status: open | phase-end: -\n")
        # A pending research report
        pdir = os.path.join(cur, "research", "pending")
        os.makedirs(pdir, exist_ok=True)
        _write(os.path.join(pdir, "retriever-web-python-release.md"),
               "# python release\ntask: T1\nagent: retriever-web\ntags: python\n\n"
               "## Answer\nPython 3.14\n\n## Findings\nfound\n\n"
               "## Dead ends\nnone\nsources:\n- python.org (fetched 2026-09-23)\n")
        self._orig = os.environ.get("PA_PROJECT_ROOT")
        os.environ["PA_PROJECT_ROOT"] = self.tmp

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("PA_PROJECT_ROOT", None)
        else:
            os.environ["PA_PROJECT_ROOT"] = self._orig

    def test_assemble_adopts_first_within_five_lines(self):
        import argparse, io, contextlib
        a = argparse.Namespace(phase="3.8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_assemble(a, self.tmp, {})
        out = buf.getvalue()
        lines = [l for l in out.strip().split("\n") if l.strip()]
        # The adopt line should be present
        self.assertTrue(any("adopted:" in l for l in lines))
        # Total lines (excluding RUN:) should be at most 5
        info_lines = [l for l in lines if not l.startswith("RUN:")]
        self.assertLessEqual(len(info_lines), 5)
        # The pending file should be gone
        self.assertFalse(os.path.isfile(
            os.path.join(self.tmp, "phase-ends", "current", "research",
                         "pending", "retriever-web-python-release.md")))


def _agent(version, body="Body.\n"):
    return "---\nname: x\ndescription: d\nversion: %s\n---\n%s" % (version, body)


class TestAgentVersionRule(unittest.TestCase):
    """3.10 T14: a changed agent body bumps <phase>.<N+1>; N never resets."""

    def check(self, old, new, body_changed, phase="4.2"):
        return PI.agent_version_problem("x", _agent(old),
                                        _agent(new, "Body 2.\n" if body_changed else "Body.\n"), phase)

    def test_user_marks_skipped(self):
        self.assertIsNone(self.check("3.10.5+u1", "9", True))
        self.assertIsNone(self.check("3.10.5", "3.10.5+u2", False))
        self.assertIsNone(self.check("user/3", "user/3", True))

    def test_added_or_deleted(self):
        self.assertIsNone(PI.agent_version_problem("x", None, _agent("1"), "4.2"))
        self.assertIsNone(PI.agent_version_problem("x", _agent("1"), None, "4.2"))

    def test_body_unchanged(self):
        self.assertIsNone(self.check("3.10.5", "3.10.5", False))
        self.assertIsNone(self.check("5", "3.10.5", False, phase="3.10"))
        p = self.check("3.10.5", "4.2.6", False)
        self.assertIn("agent x: ", p)
        self.assertIn("(expected 3.10.5)", p)
        self.assertIsNotNone(self.check("5", "3.10.6", False, phase="3.10"))

    def test_body_changed(self):
        self.assertIsNone(self.check("3.10.5", "4.2.6", True))
        self.assertIsNone(self.check("2", "3", True))
        self.assertIn("(expected 4.2.6)", self.check("3.10.5", "4.2.1", True))
        self.assertIn("(expected 4.2.6)", self.check("3.10.5", "3.10.6", True))
        self.assertIn("(expected 4.2.6)", self.check("3.10.5", "3.10.5", True))
        self.assertIsNotNone(self.check("2", "4", True))

    def test_line_endings_normalised(self):
        self.assertIsNone(PI.agent_version_problem(
            "x", _agent("3.10.5").replace("\n", "\r\n"), _agent("3.10.5"), "4.2"))


class TestLintAgentVersions(TestLedgerSliceStaged):
    """3.10 T14: lint reports a bad bump in a commit since approval, and in the working tree."""

    def test_lint_reports_bad_bump(self):
        import argparse, io, contextlib
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        rel = os.path.join(self.tmp, "project-architect-3.0", "agents", "coder.md")
        _write(rel, _agent("3.8.1"))
        _write(os.path.join(self.tmp, "project-architect-3.0", "CHANGES.md"),
               "# PA3 changes\n\n## 3.8 — test\nAgent added.\n")
        self._git_out("add", "-A")
        self._git_out("commit", "-q", "-m", "add agent")
        a = argparse.Namespace(phase="3.8", lint_file=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_lint(a, self.tmp, {})
        self.assertEqual(rc, 0, buf.getvalue())
        _write(rel, _agent("3.8.1", "Body 2.\n"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PI.cmd_lint(a, self.tmp, {})
        self.assertIn("agent coder at worktree: ", buf.getvalue())
        self._git_out("commit", "-q", "-am", "bad bump")
        sha = self._git_out("rev-parse", "--short=7", "HEAD").strip()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_lint(a, self.tmp, {})
        self.assertNotEqual(rc, 0)
        self.assertIn("agent coder at %s: " % sha, buf.getvalue())
        self.assertIn("(expected 3.8.2)", buf.getvalue())


class TestLintChanges(TestLedgerSliceStaged):
    """3.10 T15: a phase that changed the package needs a '## <phase>' section in CHANGES.md."""

    def lint(self):
        import argparse, io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = PI.cmd_lint(argparse.Namespace(phase="3.8", lint_file=None), self.tmp, {})
        return rc, buf.getvalue()

    def test_changes_section_required(self):
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        changes = os.path.join(self.tmp, "project-architect-3.0", "CHANGES.md")
        _write(changes, "# PA3 changes\n\n## 3.7 — older\nOld.\n")
        self._git_out("add", "-A")
        self._git_out("commit", "-q", "-m", "changes file")
        rc, out = self.lint()
        self.assertEqual(rc, 0, out)                 # package unchanged since approval: silent
        _write(os.path.join(self.tmp, "project-architect-3.0", "tools", "x.py"), "x = 1\n")
        self._git_out("add", "-A")
        self._git_out("commit", "-q", "-m", "package change")
        rc, out = self.lint()
        self.assertNotEqual(rc, 0)
        self.assertIn("package changed in phase 3.8; project-architect-3.0/CHANGES.md "
                      "has no '## 3.8' section", out)
        _write(changes, "# PA3 changes\n\n## 3.8.5 — not it\nX.\n\n## 3.7 — older\nOld.\n")
        rc, out = self.lint()
        self.assertIn("has no '## 3.8' section", out)
        _write(changes, "# PA3 changes\n\n## 3.8 — new\nNew.\n\n## 3.7 — older\nOld.\n")
        rc, out = self.lint()
        self.assertEqual(rc, 0, out)


class TestLintInboxRows(TestLintChanges):
    """T29: an inbox row still 'now' must be named by a task or a Changes line."""

    def test_unreferenced_now_row_fails(self):
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        cur = os.path.join(self.tmp, "phase-ends", "current")
        _write(os.path.join(cur, "discussions", "INDEX.md"),
               "# Discussions -- one line per record\n# id | topic | date | status | path\n"
               "I7 | tidy the launcher | 2026-09-01 | now | x.md\n"
               "I8 | later work | 2026-09-01 | deferred:3.9 | x.md\n")
        self._git_out("add", "-A")                   # assemble's ledger slice, tracked
        self._git_out("commit", "-q", "-m", "index")
        rc, out = self.lint()
        self.assertNotEqual(rc, 0)
        self.assertIn("inbox row I7 still 'now' with no task or Changes line naming it "
                      "(tidy the launcher)", out)
        self.assertNotIn("I8", out)
        plan = os.path.join(cur, "PHASE_PLAN.md")
        with open(plan, encoding="utf-8") as fh:
            text = fh.read()
        _write(plan, text + "\n## Changes\n- 2026-09-02 add T2 for I7\n")
        rc, out = self.lint()
        self.assertEqual(rc, 0, out)

    def test_unconsumed_inbox_fails_current_lint(self):
        """fix-2: a real bullet in current/INBOX.md is a lint problem for the current phase."""
        import argparse
        PI.cmd_assemble(argparse.Namespace(phase="3.8"), self.tmp, {})
        self._git_out("add", "-A")
        self._git_out("commit", "-q", "-m", "assembled")
        _write(os.path.join(self.tmp, "phase-ends", "current", "INBOX.md"),
               "# INBOX\n\n- <placeholder>\n- developer: route me\n")
        rc, out = self.lint()
        self.assertNotEqual(rc, 0)
        self.assertIn("current/INBOX.md holds 1 unconsumed bullet(s)", out)


if __name__ == "__main__":
    unittest.main()
