"""records.py: filings parse into outcomes with ids, statuses, the index, the seed and to-dos (3.31 T1)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(PKG, "tools", "records.py")
sys.path.insert(0, os.path.join(PKG, "tools"))

import records as R  # noqa: E402

J = ".claude-state/journal"

ALPHA = """topic: alpha/beta releases
idea: "let's ship alpha to the bench first, beta only after a week"
refinement: Confirmed the order; corrected the wait.
  A week is too long for the canary, three days covers it.
decision: Ship alpha to the bench first; beta follows after three days.
  overrides: the card line "release both together"
  binds work: yes
  landed: discussion only
finding: The canary catches install faults within two days.
  evidence: v3.30.7 and v3.30.8 canary logs, 7/7 both
open: a beta channel for users -> the 3.32 plan
headline: alpha ships first, beta three days later
"""


def _entry(ts, body, through=None):
    front = "---\nts: %s\n%ssession: s1\n---\n\n" % (ts, "through: %s\n" % through if through else "")
    return front + body


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa3-records-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.jdir = os.path.join(self.root, ".claude-state", "journal")
        os.makedirs(self.jdir)

    def _live(self, date, stem, ts, body, through=None):
        os.makedirs(os.path.join(self.jdir, date), exist_ok=True)
        with open(os.path.join(self.jdir, date, stem + ".md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_entry(ts, body, through))
        return "%s/%s/%s.md" % (J, date, stem)

    def _rolled(self, date, entries):
        text = "# Journal %s (rolled)\n" % date
        for stem, ts, body in entries:
            text += "## %s\n%s\n" % (stem, _entry(ts, body))
        text += "## 235959-099w-dev-msg\n---\nts: %sT23:59:59Z\n---\n\ntopic: not a record\n" % date
        with open(os.path.join(self.jdir, date + ".md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _run(self, *args):
        p = subprocess.run([sys.executable, TOOL, "--root", self.root] + list(args),
                           capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout

    def _three(self):
        """A rolled day (alpha, then a filing that supersedes and retires) and a live day."""
        self._rolled("2026-10-28", [
            ("100000-001r-claude-record", "2026-10-28T10:00:00Z", ALPHA),
            ("150000-004r-claude-record", "2026-10-28T15:00:00Z",
             "topic: Alpha/Beta releases\ndecision: Beta waits two days, not three.\n  supersedes: R1, R9\n"
             "  evidence: " + "the 3.30.8 bench, " * 20 + "\n"
             "retire: R2 - the canary changed\nretire: R7 - unknown\nheadline: beta after two days\n"),
        ])
        return self._live("2026-10-30", "090000-002r-claude-record", "2026-10-30T09:00:00Z",
                          "topic: journal records\ndecision: File records after 15 quiet minutes.\n"
                          "  binds work: yes\n  landed: plan T3\nheadline: records filed after quiet\n")

    # ---- parsing
    def test_alpha_beta_parses(self):
        p = R.parse_filing(ALPHA)
        self.assertEqual(p["warnings"], [])
        (t,) = p["topics"]
        self.assertEqual(t["title"], "alpha/beta releases")
        self.assertEqual(t["headline"], "alpha ships first, beta three days later")
        self.assertEqual(t["refinement"],
                         "Confirmed the order; corrected the wait. A week is too long for the canary, three days covers it.")
        d, f, o = t["items"]
        self.assertEqual((d["kind"], f["kind"], o["kind"]), ("decision", "finding", "open"))
        self.assertEqual(d["attrs"], {"overrides": 'the card line "release both together"', "binds work": "yes",
                                      "landed": "discussion only"})
        self.assertEqual(f["attrs"], {"evidence": "v3.30.7 and v3.30.8 canary logs, 7/7 both"})
        self.assertEqual(o["text"], "a beta channel for users -> the 3.32 plan")

    def test_continuations_bullets_bold_crlf(self):
        body = ("**Topic:** t1\r\n\r\n- **decision:** first part\r\n  second part\r\nthird part\r\n"
                "  - **Binds work**: yes\r\n  evidence: e1\r\n    more evidence\r\n- headline: h1\r\n")
        (t,) = R.parse_filing(body)["topics"]
        (d,) = t["items"]
        self.assertEqual(d["text"], "first part second part third part")
        self.assertEqual(d["attrs"], {"binds work": "yes", "evidence": "e1 more evidence"})
        self.assertEqual(t["headline"], "h1")

    def test_nothing_to_file(self):
        for body in ("nothing to file", "Nothing to file.", "(nothing to file)", "  NOTHING TO FILE  \n"):
            p = R.parse_filing(body)
            self.assertTrue(p["nothing"], body)
            self.assertEqual(p["topics"], [])

    def test_unparsed_becomes_note(self):
        p = R.parse_filing("We agreed to keep the cache.\nAnd more.")
        (t,) = p["topics"]
        self.assertEqual(t["title"], "(unparsed filing)")
        self.assertEqual(t["items"], [{"kind": "note", "text": "We agreed to keep the cache.\nAnd more.", "attrs": {}}])

    # ---- ids, statuses, index
    def test_ids_statuses_and_index(self):
        self._three()
        outcomes, topics = R.load(self.root)
        self.assertEqual([o["id"] for o in outcomes], ["R1", "R2", "R3", "R4", "R5"])
        self.assertEqual([o["kind"] for o in outcomes], ["decision", "finding", "open", "decision", "decision"])
        self.assertEqual(outcomes[0]["status"], "superseded by R4")
        self.assertEqual(outcomes[1]["status"],
                         "retired by %s/2026-10-28.md#150000-004r-claude-record (the canary changed)" % J)
        self.assertEqual([o["status"] for o in outcomes[2:]], ["in force"] * 3)
        self.assertEqual(len(topics), 2)
        self.assertEqual(topics["alpha/beta releases"]["headline"], "beta after two days")
        self.assertEqual(self._run("index"), (0, "indexed: 5 outcomes in 2 topics\n"))
        with open(os.path.join(self.jdir, "TOPICS.md"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[1], "# filed through: (none)")
        self.assertEqual(lines[3], "R1 | 2026-10-28 | decision | superseded by R4 | Alpha/Beta releases | "
                         "Ship alpha to the bench first; beta follows after three days. | "
                         "%s/2026-10-28.md#100000-001r-claude-record" % J)
        self.assertEqual(lines[7], "R5 | 2026-10-30 | decision | in force | journal records | "
                         "File records after 15 quiet minutes. | %s/2026-10-30/090000-002r-claude-record.md" % J)

    def test_index_filed_through(self):
        """3.31 T2b: the newest `through` of every record entry heads TOPICS.md, `nothing to file` ones included."""
        path = os.path.join(self.jdir, "TOPICS.md")
        self.assertEqual(self._run("index"), (0, "indexed: 0 outcomes in 0 topics\n"))
        self.assertFalse(os.path.exists(path))  # no record entry: no file
        self._live("2026-10-30", "090000-001r-claude-record", "2026-10-30T09:00:00.000Z", "(nothing to file)",
                   through="2026-10-30T08:50:00.000Z")
        self._run("index")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().splitlines()[1], "# filed through: 2026-10-30T08:50:00.000Z")
        self._live("2026-10-30", "100000-002r-claude-record", "2026-10-30T10:00:00.000Z",
                   "topic: t\ndecision: d.\nheadline: h\n", through="2026-10-30T09:40:00.000Z")
        self._live("2026-10-30", "110000-003r-claude-record", "2026-10-30T11:00:00.000Z", "(nothing to file)",
                   through="2026-10-30T10:55:00.000Z")
        self.assertEqual(self._run("index"), (0, "indexed: 1 outcomes in 1 topics\n"))
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[1], "# filed through: 2026-10-30T10:55:00.000Z")
        self.assertEqual(len(lines), 4)
        # unchanged: not rewritten
        before = os.stat(path).st_mtime_ns
        os.utime(path, ns=(before - 10**9, before - 10**9))
        self._run("index")
        self.assertEqual(os.stat(path).st_mtime_ns, before - 10**9)

    # ---- seed
    def test_seed_full_and_overflow(self):
        self._three()
        rc, out = self._run("seed")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("## Outcomes in force\n\n### journal records (2026-10-30) · records filed"))
        self.assertIn("- R3 open: a beta channel for users -> the 3.32 plan\n", out)
        self.assertNotIn("R1 ", out)
        self.assertNotIn("## Earlier topics", out)
        rc, out = self._run("seed", "--budget-chars", "240")
        self.assertEqual(out, "## Outcomes in force\n\n### journal records (2026-10-30) · records filed after quiet\n"
                         "- R5 decision: File records after 15 quiet minutes.\n  binds work: yes\n\n"
                         "## Earlier topics\n\n(1 more: .claude-state/journal/TOPICS.md)\n")
        rc, out = self._run("seed", "--budget-chars", "520")
        self.assertIn("## Earlier topics\n\n- 2026-10-28 Alpha/Beta releases: beta after two days "
                      "(in force; see TOPICS.md)\n", out)
        self.assertNotIn("more:", out)

    def _plan(self, text, ped="phase-ends"):
        os.makedirs(os.path.join(self.root, ped, "current"), exist_ok=True)
        with open(os.path.join(self.root, ped, "current", "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _phase_end(self, name, text, ped="phase-ends"):
        os.makedirs(os.path.join(self.root, ped), exist_ok=True)
        with open(os.path.join(self.root, ped, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_seed_keeps_this_phase_and_binding_outcomes(self):
        """3.32 B4: older outcomes that don't bind work leave the seed; an old `binds work` outcome stays."""
        self._three()
        self._live("2026-10-29", "090000-001r-claude-record", "2026-10-29T09:00:00Z",
                   "topic: the old rule\ndecision: Keep the bench apart.\n  binds work: yes\nheadline: bench apart\n")
        self._plan("# Phase 3.32\nMilestone: m\nApproved: 2026-10-30   Planner: p   Plan-hash: x\n")
        rc, out = self._run("seed")
        self.assertEqual(rc, 0)
        held, earlier = out.split("## Earlier topics\n\n")
        self.assertIn("- R6 decision: File records after 15 quiet minutes.\n", held)  # this phase
        self.assertIn("Keep the bench apart.\n", held)                                # old, binds work
        self.assertLess(held.index("journal records"), held.index("the old rule"))    # newest first
        for gone in ("a beta channel", "Beta waits two days"):                        # older, no binds work
            self.assertNotIn(gone, held)
        self.assertEqual(earlier, "- 2026-10-28 Alpha/Beta releases: beta after two days (in force; see TOPICS.md)\n")
        rc, out = self._run("seed", "--all")                                          # the planners keep everything
        self.assertIn("a beta channel", out)
        self.assertNotIn("(in force; see TOPICS.md)", out)

    def test_phase_start(self):
        """3.32 B4: the plan's approval, else the newest PhaseEnd's date under `phase_ends_dir`, else ''."""
        self.assertEqual(R.phase_start(self.root), "")
        ped = "Project Context Markdowns"
        os.makedirs(os.path.join(self.root, ".claude"))
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            fh.write('{"phase_ends_dir": "%s"}' % ped)
        self._phase_end("PhaseEnd_Phase1.md", "# PhaseEnd — Phase 1\n**Date:** 2026-03-17\n", ped)
        self._phase_end("PhaseEnd_Phase2.md", "# PhaseEnd — Phase 2\nApproved: 2026-04-01 | Closed: 2026-04-09 | x\n", ped)
        self.assertEqual(R.phase_start(self.root), "2026-04-09")
        self._plan("# Phase 3\n(not approved yet)\n", ped)
        self.assertEqual(R.phase_start(self.root), "2026-04-09")
        self._plan("# Phase 3\nApproved: 2026-05-02   Planner: p   Plan-hash: x\n", ped)
        self.assertEqual(R.phase_start(self.root), "2026-05-02")

    def test_stdout_lf(self):
        """3.31 T6: LF only, as bytes (text mode hid the CRLF a Windows stdout wrote into the seed)."""
        self._three()
        for args in (["seed"], ["inforce", "--binds-work"]):
            p = subprocess.run([sys.executable, TOOL, "--root", self.root] + args, capture_output=True)
            self.assertIn(b"\n", p.stdout)
            self.assertNotIn(b"\r", p.stdout)

    def test_state_page_until_this_phase_has_records(self):
        """3.32 B6: the page is seeded until an outcome is on record for the current phase, and stays on disk."""
        self.assertEqual(self._run("state"), (0, ""))                   # no page
        page = os.path.join(self.root, "docs", "PRE_PA3_STATE.md")
        os.makedirs(os.path.dirname(page))
        with open(page, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Pre-PA3 state\n\nGen5.4.6 is in flight.\nNext: the mesh pass.\n")
        self.assertEqual(self._run("state"), (0, "## Pre-PA3 state\n\nGen5.4.6 is in flight.\nNext: the mesh pass.\n"))
        self.assertEqual(self._run("state", "--budget-chars", "30")[1],
                         "## Pre-PA3 state\n\nGen5.4.6 is in flight.\n(more: docs/PRE_PA3_STATE.md)\n")
        self._live("2026-10-20", "090000-001r-claude-record", "2026-10-20T09:00:00Z", ALPHA)
        pdir = os.path.join(self.root, "phase-ends", "current")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "PHASE_PLAN.md"), "w", encoding="utf-8") as fh:
            fh.write("# Phase Gen5.4.6\nApproved: 2026-10-25\n")
        self.assertIn("## Pre-PA3 state", self._run("state")[1])        # an older phase's record replaces nothing
        self._live("2026-10-26", "090000-002r-claude-record", "2026-10-26T09:00:00Z", "topic: mesh\ndecision: Keep the pass.\n")
        self.assertEqual(self._run("state"), (0, ""))
        self.assertTrue(os.path.isfile(page))

    def test_seed_empty(self):
        self.assertEqual(self._run("seed"), (0, ""))
        self._live("2026-10-30", "090000-002r-claude-record", "2026-10-30T09:00:00Z", "(nothing to file)")
        self.assertEqual(self._run("seed"), (0, ""))

    # ---- inforce, todo, headlines
    def test_inforce(self):
        self._three()
        self.assertEqual(self._run("inforce", "--binds-work", "--ids"),
                         (0, "R5 decision: File records after 15 quiet minutes.\n"))
        rc, out = self._run("inforce")
        self.assertEqual(out.splitlines()[0], "- R5 decision: File records after 15 quiet minutes.")
        self.assertIn("  landed: plan T3\n", out)
        self.assertEqual(self._run("inforce", "--ids", "--max-tokens", "15"),
                         (0, "R5 decision: File records after 15 quiet minutes.\n"
                          "(2 more: .claude-state/journal/TOPICS.md)\n"))

    def test_todo_and_headlines(self):
        live = self._three()
        rolled = os.path.join(self.jdir, "2026-10-28.md") + "#100000-001r-claude-record"
        self.assertEqual(self._run("todo", rolled), (0,
                         "retire or narrow: the card line \"release both together\" (R1 overrides it)\n"
                         "R3 open: a beta channel for users -> the 3.32 plan\n"))
        self.assertEqual(self._run("todo", os.path.join(self.root, live)), (0, ""))
        self.assertEqual(self._run("headlines", live), (0, "journal records: records filed after quiet\n"))
        self.assertEqual(self._run("headlines", "%s/2026-10-28/100000-001r-claude-record.md" % J),
                         (0, "alpha/beta releases: alpha ships first, beta three days later\n"))
        self.assertEqual(self._run("todo", "nope.md")[0], 1)

    # ---- check
    def test_check(self):
        self._three()
        path = os.path.join(self.root, "reply.md")

        def check(body):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            return self._run("check", path)

        rc, out = check("topic: t\ndecision: d one. More.\n  supersedes: R9\nretire: R3 - done\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, 'topic: t\n  R6 decision: d one.\n  retire R3\nwarning: topic "t": no headline\n'
                         'warning: topic "t": R6 supersedes R9: not an earlier record\n')
        self.assertEqual(check("Nothing to file."), (0, "nothing to file\n"))
        rc, out = check("just prose")
        self.assertEqual(rc, 1)
        self.assertIn("warning: unparsed text kept as a note", out)
        self.assertEqual(check("topic: t\n")[0], 1)

    # ---- imported records (3.32)
        """3.32 T4b: --all holds what a small budget overflows, with no `more:` line."""
        self._three()
        rc, small = self._run("seed", "--budget-chars", "240")
        self.assertIn("more:", small)
        rc, out = self._run("seed", "--all")
        self.assertEqual(rc, 0)
        self.assertNotIn("more:", out)
        self.assertNotIn("(in force; see TOPICS.md)", out)
        for held in ("- R5 decision: File records after 15 quiet minutes.\n", "- R3 open: a beta channel for users"):
            self.assertIn(held, out)


if __name__ == "__main__":
    unittest.main()
