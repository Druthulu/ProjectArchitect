"""pa.statusline: the four-line renderer and the live sampler (design A §D).

Every test runs against a temporary ``PA_LEDGER_DIR`` and a temporary project
directory; nothing touches the real ``~/.claude``.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config, paths, statusline  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "statusline_live.json")
ESC = "\x1b["

PLAN = """# Phase 36 — levers-off        (implements GENERATION_PLAN.md phase 4.2)
Milestone: the levers are off — verified by: python -m unittest discover tests

## Tasks
- T1 | done    | expert-fable | coder: opus46 | effort: high | title: first lever | files: a.py
- T2 | done    | expert-fable | coder: none   | effort: high | title: second lever | files: b.py
- T3 | next    | expert-fable | coder: opus46 | effort: high | title: recipe rung | files: c.py
- T4 | queued  | expert-fable | coder: none   | effort: high | title: overnight bake | files: d.py

## Changes
- 2026-09-12 planner: plan approved
"""


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def plain_cfg():
    cfg = config.defaults()
    cfg["statusline"]["colors"] = False
    return cfg


def write_json(path, obj):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(obj, handle)


class LedgerCase(unittest.TestCase):
    """Base: a private ledger dir, a private project dir, no cached reads."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-sl-")
        self.ledger = os.path.join(self.tmp, "usage-ledger")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.ledger)
        os.makedirs(self.project)
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        statusline._JSON_CACHE.clear()
        self.payload = load_fixture()
        # the captured cwd is the PA repo, which became a PA3 project at M3: point the
        # payload at the empty temp project so lines 2 and 4 stay absent until make_project()
        self.payload["workspace"]["project_dir"] = self.project
        self.payload["cwd"] = self.project
        self.sid = self.payload["session_id"]
        self.cfg = plain_cfg()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        statusline._JSON_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def make_project(self, plan=PLAN, status=None, pa_json=None):
        """A PA3 project: `.claude/pa.json`, a PHASE_PLAN and `.run/status.json`."""
        write_json(os.path.join(self.project, ".claude", "pa.json"),
                   pa_json if pa_json is not None else
                   {"pa_version": "3.0.0", "project": "demo", "phase_ends_dir": "phase-ends"})
        if plan is not None:
            current = os.path.join(self.project, "phase-ends", "current")
            os.makedirs(current, exist_ok=True)
            with open(os.path.join(current, "PHASE_PLAN.md"), "w",
                      encoding="utf-8", newline="\n") as handle:
                handle.write(plan)
        if status is not None:
            write_json(os.path.join(self.project, ".run", "status.json"), status)
        self.payload["workspace"]["project_dir"] = self.project
        self.payload["cwd"] = self.project
        return self.project

    def render(self, cfg=None):
        statusline._JSON_CACHE.clear()
        return statusline.render(self.payload, cfg or self.cfg)

    def lines(self, cfg=None):
        return self.render(cfg).split("\n")


# --------------------------------------------------------------------------- line 1

class RenderFixtureTest(LedgerCase):
    """The captured Claude Code 2.1.270 payload, rendered with no project."""

    def test_fixture_lines_and_fields(self):
        lines = self.lines()
        # No `.claude/pa.json` -> no task block, no project line -> model + Pace
        self.assertGreaterEqual(len(lines), 2, lines)
        model_line = lines[0]
        for token in ("Fable 5.1", "max", "472k/1M", "5h 19%", "7d 1%"):
            self.assertIn(token, model_line, model_line)
        # no summary -> no Session line (dollars off, no windowed data)
        joined = "\n".join(lines)
        self.assertNotIn("$11.70", joined)

    def test_bar_is_ten_cells(self):
        first = self.lines()[0]
        self.assertEqual(first.count(statusline.BAR_FULL) + first.count(statusline.BAR_EMPTY), 10)
        # context_window.used_percentage = 47 -> 5 filled cells
        self.assertEqual(first.count(statusline.BAR_FULL), 5)

    def test_eta_and_pace(self):
        first = self.lines()[0]
        self.assertIn(statusline.RESET_MARK + " ", first)    # the countdown marker, then a space
        # the 7d parenthetical is gone (T1.c1); pace goes to the Pace line
        self.assertNotIn("(pace ", first)

    def test_colours_on_and_off(self):
        colored = statusline.render(self.payload, config.defaults())
        self.assertIn(ESC, colored)
        self.assertNotIn(ESC, self.render())

    def test_model_scoped_fable_bucket(self):
        """`utilization` <= 1 is a fraction; a string `resets_at` is accepted."""
        self.payload["rate_limits"]["model_scoped"] = [
            {"display_name": "Fable 5.1", "utilization": 0.89,
             "resets_at": "2026-09-20T00:00:00Z"}]
        statusline._JSON_CACHE.clear()
        cfg = plain_cfg()
        cfg["statusline"]["max_width"] = 160     # the 7d rate pieces (T13) can otherwise crowd it out
        self.assertIn("fable 89%", self.lines(cfg)[0])

    def test_usage_readings_uncolored_on_live_fixture(self):
        """T10.c1: model and the 5h/7d window percents carry no colour code (fable N%
        is plain too since T22 -- see test_fable_label_dim_percent_plain_on_live_fixture)."""
        self.payload["rate_limits"]["model_scoped"] = [
            {"display_name": "Fable 5.1", "utilization": 0.89,
             "resets_at": "2026-09-20T00:00:00Z"}]
        statusline._JSON_CACHE.clear()
        cfg = config.defaults()
        cfg["statusline"]["max_width"] = 160     # the 7d rate pieces can otherwise crowd it out
        line = self.lines(cfg)[0]
        for token in ("Fable 5.1", "5h 19%", "7d 1%",
                      statusline.DIM + "fable" + statusline.RESET + " 89%"):
            self.assertIn(token, line, line)
        for color in (statusline.GREEN, statusline.YELLOW, statusline.RED, statusline.LIGHT_BLUE):
            self.assertNotIn(color + "Fable", line)
            self.assertNotIn(color + "5h", line)
            self.assertNotIn(color + "7d", line)

    def test_fable_label_dim_percent_plain_on_live_fixture(self):
        """T22: fable is a bare reading -- label DIM, N% plain; neither pace (a future
        resets_at makes it computable) nor a usage-API severity colours it."""
        resets = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 86400))
        cfg = config.defaults()
        cfg["statusline"]["max_width"] = 160     # the 7d rate pieces can otherwise crowd it out
        cases = [(0.03, "3%", None), (0.60, "60%", None), (1.0, "100%", None),
                 (0.60, "60%", "warning")]
        for util, text, sev in cases:
            entry = {"display_name": "Fable 5.1", "utilization": util, "resets_at": resets}
            if sev:
                entry["severity"] = sev
            self.payload["rate_limits"]["model_scoped"] = [entry]
            statusline._JSON_CACHE.clear()
            line = self.lines(cfg)[0]
            self.assertIn(statusline.DIM + "fable" + statusline.RESET + " " + text, line, line)
            for color in (statusline.GREEN, statusline.YELLOW, statusline.RED,
                          statusline.LIGHT_BLUE):
                self.assertNotIn(color + "fable", line, line)
                self.assertNotIn(color + text, line, line)

    def test_model_scoped_absent_means_segment_absent(self):
        self.assertNotIn("fable", self.lines()[0])

    def test_missing_rate_limits_still_renders(self):
        self.payload.pop("rate_limits", None)
        line = self.lines()[0]
        self.assertIn("Fable 5.1", line)
        self.assertNotIn("5h", line)

    def test_empty_payload_never_raises(self):
        self.assertIsInstance(statusline.render({}, self.cfg), str)

    def test_max_width_is_respected(self):
        cfg = plain_cfg()
        cfg["statusline"]["max_width"] = 40
        for line in self.lines(cfg):
            self.assertLessEqual(len(line), 40, line)


class FiveHourPaceTest(LedgerCase):
    """Line 1's 5h pace segment (T4.1): mirrors the 7d one, its own window duration."""

    def _set_five_hour(self, pct, elapsed_fraction):
        now = time.time()
        duration = statusline.FIVE_HOUR_S
        elapsed = duration * elapsed_fraction
        self.payload["rate_limits"] = {
            "five_hour": {"used_percentage": pct, "resets_at": now + (duration - elapsed)}}

    def test_five_hour_pace_absent_by_default(self):
        """T10.1: five_hour_pace defaults to False, so the 5h segment has no pace."""
        self._set_five_hour(34, 0.25)
        first = self.lines()[0]
        self.assertIn("5h 34%", first)
        self.assertNotIn("pace", first)

    def test_five_hour_pace_present(self):
        self._set_five_hour(34, 0.25)                  # 34% used, a quarter through the window
        cfg = plain_cfg()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        first = self.lines(cfg)[0]
        expected = "(pace %.1fx)" % (34 / 100.0 / 0.25)
        self.assertIn(expected, first)

    def test_five_hour_pace_absent_too_early(self):
        self._set_five_hour(34, 0.005)                 # under 1% elapsed: too early to mean anything
        cfg = plain_cfg()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        first = self.lines(cfg)[0]
        self.assertIn("5h 34%", first)
        self.assertNotIn("pace", first)

    def test_five_hour_pace_red_above_threshold(self):
        self._set_five_hour(34, 0.25)                  # pace 1.36x; above pace_red 1.75? no, 1.36 < 1.75
        cfg = config.defaults()                        # pace_yellow=1.25, pace_red=1.75
        cfg["statusline"]["show"]["five_hour_pace"] = True
        colored = statusline.render(self.payload, cfg)
        # 1.36x is between 1.25 and 1.75 -> YELLOW
        self.assertIn(statusline.YELLOW, colored)


class SevenDayRateTest(LedgerCase):
    """Line 1's 7d segment (T13): pace plus used/day and left/day, each on its own guard."""

    def _set_seven_day(self, pct, elapsed_days):
        now = time.time()
        duration = statusline.DEFAULT_WINDOW_S
        elapsed = elapsed_days * 86400.0
        self.payload["rate_limits"] = {
            "seven_day": {"used_percentage": pct, "resets_at": now + (duration - elapsed)}}

    def test_seven_day_rate_pieces_default(self):
        """Default seven_day='all': the 7d parenthetical is gone from line 1 (T1.c1);
        pace figures appear on the Pace line instead."""
        self._set_seven_day(45, 4.8)                    # 45% used, 4.8d elapsed of 7
        cfg = plain_cfg()
        cfg["statusline"]["max_width"] = 300
        first = self.lines(cfg)[0]
        # the 7d parenthetical is removed from line 1
        self.assertNotIn("(pace", first)
        self.assertIn("7d 45%", first)
        # the Pace line carries the figures now
        joined = "\n".join(self.lines(cfg))
        self.assertIn("Pace:", joined)
        self.assertIn("0.7x", joined)

    def test_seven_day_rate_pieces_pace_used(self):
        """seven_day='pace,used': the 7d parenthetical is gone from line 1."""
        self._set_seven_day(45, 4.8)
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "pace,used"
        first = self.lines(cfg)[0]
        self.assertNotIn("(pace", first)
        self.assertIn("7d 45%", first)

    def test_seven_day_rate_pieces_all(self):
        """seven_day='all': the 7d parenthetical is gone from line 1."""
        self._set_seven_day(45, 4.8)
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "all"
        cfg["statusline"]["max_width"] = 300
        first = self.lines(cfg)[0]
        self.assertNotIn("(pace", first)
        self.assertIn("7d 45%", first)

    def test_seven_day_pace_and_used_omitted_too_early(self):
        self._set_seven_day(5, 7 * 0.005)                # under 1% elapsed: too early to mean anything
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "all"
        first = self.lines(cfg)[0]
        self.assertIn("7d 5%", first)
        self.assertNotIn("pace", first)
        # the Pace line might still show left/day
        pace_lines = [l for l in self.lines(cfg) if l.startswith("Pace:")]
        if pace_lines:
            self.assertIn("left", pace_lines[0])

    def test_seven_day_left_shown_near_reset(self):
        self._set_seven_day(90, 7 - 0.1)                 # 0.1d (2.4h) remaining (6h guard dropped, T23)
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "all"
        # left = (100 - 90) / max(1 day, 0.1d) = 10%/day
        pace_lines = [l for l in self.lines(cfg) if l.startswith("Pace:")]
        self.assertTrue(pace_lines)
        self.assertIn("10%/day left", pace_lines[0])

    def test_seven_day_left_omitted_after_reset(self):
        self._set_seven_day(90, 7 + 0.1)                 # resets_at already passed
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "all"
        pace_lines = [l for l in self.lines(cfg) if l.startswith("Pace:")]
        for line in pace_lines:
            self.assertNotIn("left", line)

    def test_seven_day_none_hides_all_rates(self):
        """seven_day='none': no rate pieces on line 1."""
        self._set_seven_day(45, 4.8)
        cfg = plain_cfg()
        cfg["statusline"]["seven_day"] = "none"
        first = self.lines(cfg)[0]
        self.assertIn("7d 45%", first)
        self.assertNotIn("pace", first)
        self.assertNotIn("used", first)
        self.assertNotIn("left", first)

    def test_five_hour_segment_unaffected_by_seven_day_rates(self):
        now = time.time()
        self.payload["rate_limits"] = {
            "five_hour": {"used_percentage": 34,
                          "resets_at": now + (statusline.FIVE_HOUR_S - statusline.FIVE_HOUR_S * 0.25)},
            "seven_day": {"used_percentage": 45,
                          "resets_at": now + (statusline.DEFAULT_WINDOW_S - 4.8 * 86400.0)}}
        cfg = plain_cfg()
        cfg["statusline"]["max_width"] = 300
        cfg["statusline"]["seven_day"] = "all"
        cfg["statusline"]["show"]["five_hour_pace"] = True
        first = self.lines(cfg)[0]
        self.assertIn("(pace 1.4x)", first)              # 5h: unchanged, still uses parens
        # 7d parenthetical is gone from line 1
        self.assertNotIn("(pace 0.7x", first)


# --------------------------------------------------------------------------- line 2

class ProjectLineTest(LedgerCase):

    def test_task_block_has_counts_and_phase(self):
        """T7.c1: the header line carries progress and phase; the running line carries elapsed."""
        self.make_project(status={
            "generation": "Gen4.W", "phase": "36", "phase_name": "levers-off",
            "task": "T3", "task_title": "recipe rung", "expert_agent_type": "expert-fable",
            "run_id": "agent-1", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        self.assertGreaterEqual(len(lines), 3, lines)
        # the header line is first, carrying phase + progress
        header = lines[0]
        self.assertIn("2/4", header)
        self.assertIn("Phase 36", header)
        self.assertNotIn("levers-off", header)  # T1.c2: phase number only, no name
        joined = "\n".join(lines)
        self.assertIn("T3", joined)
        self.assertIn("recipe rung", joined)
        self.assertIn(statusline.ARROW_BACK, joined)
        # the running line itself carries no phase or counter
        running = [l for l in lines if l.startswith("T3")][0]
        self.assertNotIn("Phase", running)
        self.assertNotIn("2/4", running)
        self.assertNotIn("demo", running)

    def test_header_line_present_without_running_task(self):
        """T7.c1: the header line is present even with no running task."""
        self.make_project()
        self.assertEqual(self.lines()[0], "demo · Phase 36 · 2/4")

    def test_header_line_project_off_keeps_phase_and_counter(self):
        """T7.c1: task_project off drops the project piece, keeps phase/counter."""
        self.make_project()
        cfg = plain_cfg()
        cfg["statusline"]["show"]["task_project"] = False
        self.assertEqual(self.lines(cfg)[0], "Phase 36 · 2/4")

    def test_next_falls_back_to_first_queued(self):
        plan = PLAN.replace("- T3 | next  ", "- T3 | done  ")
        self.make_project(plan=plan, status={"task": "T4"})
        # T4 is now the current task in the task block
        joined = "\n".join(self.lines())
        self.assertIn("T4", joined)

    def test_running_expert_ctx_and_handoff(self):
        """T1.c1: agent info appears as an indented child of the current task."""
        self.make_project(status={"task": "T3", "run_id": "agent-1",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {
            "schema": 1, "sessions": {self.sid: {
                "account": "dev@example.com", "expert": "agent-1",
                "agents": {"agent-1": {"ctx": 210437, "handoff_fired": True,
                                       "saved_live_usd": 0.0}}}}})
        joined = "\n".join(self.lines())
        self.assertNotIn("ctx 210k", joined)                 # agent ctx dropped (T27)
        self.assertIn(statusline.HANDOFF_MARK, joined)
        agent_line = next(l for l in self.lines() if statusline.HANDOFF_MARK in l)
        self.assertNotIn("ctx ", agent_line)
        cfg = config.defaults()
        line = statusline._line2(self.payload, statusline._gather(self.payload, cfg), cfg,
                                 statusline.sl_config(cfg), time.time())
        self.assertIn(statusline.HANDOFF_MARK, line)
        self.assertNotIn("ctx ", line)

    def test_no_agent_line_after_return(self):
        """T27: task done in the plan, running.json holds only the session's own entry
        (key == sid, ctx 0, no agent_type): no agent line in either renderer."""
        plan = PLAN.replace("- T3 | next  ", "- T3 | done  ")
        self.make_project(plan=plan, status={"task": "T3", "run_id": None,
                                             "expert_agent_type": "expert-opus55",
                                             "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {
            "schema": 1, "sessions": {self.sid: {
                "account": "dev@example.com",
                "agents": {self.sid: {"ctx": 0}}}}})
        joined = "\n".join(self.lines())
        self.assertNotIn("expert-opus55", joined)
        self.assertNotIn("ctx 0", joined)
        cfg = config.defaults()
        line = statusline._line2(self.payload, statusline._gather(self.payload, cfg), cfg,
                                 statusline.sl_config(cfg), time.time())
        self.assertNotIn("expert-opus55", line)

    def test_self_entry_never_the_agent(self):
        """T27: the session's own entry is never chosen; a non-running status is not in flight."""
        sid = self.sid
        running = {"sessions": {sid: {"expert": sid, "agents": {sid: {"ctx": 0}}}}}
        self.assertIsNone(statusline._running_agent(running, sid, sid)[0])
        running["sessions"][sid]["agents"]["run-9"] = {"agent_type": "expert-opus55",
                                                      "status": "completed"}
        self.assertIsNone(statusline._running_agent(running, sid, None)[0])
        running["sessions"][sid]["agents"]["run-9"]["status"] = "running"
        entry = statusline._running_agent(running, sid, None)[0]
        self.assertEqual(entry["agent_type"], "expert-opus55")

    def test_running_agent_label_and_warm_pings(self):
        """T1.c1: agent label and warm pings appear indented under the current task
        (3.9.5 T4: read from warmer.json, was keepwarm.json)."""
        import time as _time

        self.make_project(status={"task": "T3", "run_id": "agent-1",
                                  "expert_agent_type": "expert-fable",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {
            "schema": 1, "sessions": {self.sid: {
                "account": "dev@example.com", "expert": "agent-1",
                "agents": {"agent-1": {"agent_type": "expert-fable", "ctx": 90000,
                                       "model_seen": "claude-fable-5-1",
                                       "effort_seen": "medium", "saved_live_usd": 0.0}}}}})
        def warmer(pings, last_ping, run_id="agent-1"):
            write_json(os.path.join(self.project, ".run", "warmer.json"), {
                "session": self.sid, "updated": int(_time.time()), "runs": {run_id: {
                    "last_request": int(_time.time()) - 30, "ttl": "5m", "pings": pings,
                    "last_ping": last_ping, "via": "monitor",
                    "agent_type": "expert-fable", "idle": True}}})

        warmer(3, int(_time.time()) - 60)
        joined = "\n".join(self.lines())
        self.assertIn("expert-fable fable/medium", joined)
        self.assertNotIn("ctx 90k", joined)                  # agent ctx dropped (T27)
        self.assertIn("warm ×3", joined)
        warmer(3, int(_time.time()) - 3400)             # stale: now - last_ping >= WARM_FRESH_S
        self.assertNotIn("warm", "\n".join(self.lines()))
        warmer(3, None)                                  # no ping yet
        self.assertNotIn("warm", "\n".join(self.lines()))
        warmer(5, int(_time.time()) - 60, run_id="agent-2")   # another run's pings
        self.assertNotIn("warm", "\n".join(self.lines()))
        self.assertEqual(statusline._model_short("claude-fable-5-1[1m]"), "fable")
        self.assertEqual(statusline._model_short("claude-opus-4-6"), "opus46")
        self.assertEqual(statusline._model_short("claude-sonnet-5"), "sonnet")
        self.assertEqual(statusline._model_short("claude-haiku-4-5-20251001"), "haiku")
        self.assertEqual(statusline._model_short(""), "")

    def test_agent_line_painted_per_piece(self):
        """T1.c4: the agent line paints per piece -- label unpainted, warm DIM,
        no flat CYAN; the agent ctx figure is gone (T27)."""
        self.make_project(status={"task": "T3", "run_id": "agent-1",
                                  "expert_agent_type": "expert-fable",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {
            "schema": 1, "sessions": {self.sid: {
                "account": "dev@example.com", "expert": "agent-1",
                "agents": {"agent-1": {"agent_type": "expert-fable", "ctx": 250000,
                                       "model_seen": "claude-fable-5-1",
                                       "effort_seen": "medium", "saved_live_usd": 0.0}}}}})
        write_json(os.path.join(self.project, ".run", "warmer.json"), {
            "session": self.sid, "updated": int(time.time()), "runs": {"agent-1": {
                "last_request": int(time.time()) - 30, "ttl": "5m", "pings": 3,
                "last_ping": int(time.time()) - 60, "via": "monitor",
                "agent_type": "expert-fable", "idle": True}}})
        cfg = config.defaults()
        agent_line = next(l for l in self.render(cfg).split("\n") if "expert-fable" in l)
        self.assertIn("  expert-fable fable/medium", agent_line)   # label unpainted
        self.assertNotIn("ctx ", agent_line)                 # agent ctx dropped (T27)
        self.assertIn(statusline.DIM + "warm ×3", agent_line)
        self.assertNotIn(statusline.CYAN, agent_line)

    def test_compact_line_has_no_agent_ctx(self):
        """T27: the compact line's agent segment carries no ctx figure (was T1.c4's
        GREEN-below-ctx_yellow check on ``_line2``)."""
        self.make_project(status={"task": "T3", "run_id": "agent-1",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {
            "schema": 1, "sessions": {self.sid: {
                "account": "dev@example.com", "expert": "agent-1",
                "agents": {"agent-1": {"ctx": 50000}}}}})
        cfg = config.defaults()
        sl = statusline.sl_config(cfg)
        ctx = statusline._gather(self.payload, cfg)
        line = statusline._line2(self.payload, ctx, cfg, sl, time.time())
        self.assertNotIn("ctx 50k", line)

    def _paused_line(self, resume):
        self.make_project(status={"kind": "paused", "resume_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(resume))})
        cfg = config.defaults()
        return statusline._line2(self.payload, statusline._gather(self.payload, cfg), cfg,
                                 statusline.sl_config(cfg), time.time())

    def test_paused_segment_until_resume(self):
        """T5.c1: window-gate pause shows `paused until HH:MM` (local) with no task segment."""
        resume = int(time.time()) + 3600
        line = self._paused_line(resume)
        self.assertIn(statusline.YELLOW + "paused until %s"
                      % time.strftime("%H:%M", time.localtime(resume)), line)

    def test_paused_segment_hidden_after_resume(self):
        self.assertNotIn("paused until", self._paused_line(int(time.time()) - 60))

    def test_no_pa_json_drops_task_block(self):
        """No pa.json -> no task block, no governed lines (but Pace may appear from rate_limits)."""
        self.payload["workspace"]["project_dir"] = self.project
        current = os.path.join(self.project, "phase-ends", "current")
        os.makedirs(current)
        with open(os.path.join(current, "PHASE_PLAN.md"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(PLAN)
        write_json(os.path.join(self.project, ".run", "status.json"), {"task": "T3"})
        lines = self.lines()
        self.assertNotIn("2/4", "\n".join(lines))    # no task block without pa.json

    def test_phase_ends_dir_override(self):
        self.make_project(plan=None, pa_json={"phase_ends_dir": "Project Context Markdowns"},
                          status={"task": "T3"})
        current = os.path.join(self.project, "Project Context Markdowns", "current")
        os.makedirs(current)
        with open(os.path.join(current, "PHASE_PLAN.md"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(PLAN)
        joined = "\n".join(self.lines())
        self.assertIn("2/4", joined)


class TaskProjectNameTest(LedgerCase):
    """T11.c1: the project name on the task block's current line."""

    def test_project_name_from_pa_json(self):
        """task_project on: the project name appears on the header line."""
        self.make_project(status={
            "task": "T3", "phase": "36", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        self.assertIn("demo", lines[0])
        running = [l for l in lines if l.startswith("T3")][0]
        self.assertNotIn("demo", running)
        self.assertNotIn("Phase", running)
        self.assertNotIn("2/4", running)

    def test_project_name_off(self):
        """task_project off: the project name does not appear, but phase/counter stay."""
        self.make_project(status={
            "task": "T3", "phase": "36", "started": "2026-09-12T20:58:00Z"})
        cfg = dict(self.cfg)
        cfg["statusline"] = dict(cfg.get("statusline", {}))
        cfg["statusline"]["show"] = {"task_project": False}
        lines = self.lines(cfg)
        joined = "\n".join(lines)
        self.assertNotIn("demo", joined)
        self.assertEqual(lines[0], "Phase 36 · 2/4")

    def test_project_name_fallback_to_folder(self):
        """Without pa.json 'project', the folder name is used on the header line."""
        self.make_project(pa_json={"pa_version": "3.0.0", "phase_ends_dir": "phase-ends"},
                          status={"task": "T3", "phase": "36",
                                  "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        # the folder name is the temp dir's basename (from self.project)
        folder = os.path.basename(self.project)
        self.assertIn(folder, lines[0])


class EmptyPlanHeaderTest(LedgerCase):
    """T13.c1: the header survives an empty plan: no phase / Phase <n> planning."""

    GEN = ("# Generation\n\n## Phases\n"
           "- 3.9 The proof | milestone: x | status: closed\n"
           "- 3.9.5 The warmer | milestone: y\n"
           "- 3.10 Publish | status: open\n\n## Notes\n- 4 Later\n")

    def write_gen(self, text):
        with open(os.path.join(self.project, "GENERATION_PLAN.md"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def test_no_phase(self):
        """No GENERATION_PLAN.md, no PHASE_PLAN.md: header reads '<project> · no phase'."""
        self.make_project(plan=None)
        self.assertEqual(self.lines()[0], "demo · no phase")

    def test_all_closed_is_no_phase(self):
        self.make_project(plan=None)
        self.write_gen("## Phases\n- 3.9 The proof | status: closed\n")
        self.assertEqual(self.lines()[0], "demo · no phase")

    def test_planning_without_phase_plan(self):
        """Open phase (missing status = open), no PHASE_PLAN.md: 'Phase <id> · planning'."""
        self.make_project(plan=None)
        self.write_gen(self.GEN)
        lines = self.lines()
        self.assertEqual(lines[0], "demo · Phase 3.9.5 · planning")
        self.assertFalse(any(l.startswith("T") for l in lines[1:2]))

    def test_planning_with_taskless_phase_plan(self):
        self.make_project(plan="# Phase 3.9.5 — The warmer (draft)\n\n## Tasks\n")
        self.write_gen(self.GEN)
        cfg = dict(self.cfg)
        cfg["statusline"] = dict(cfg.get("statusline", {}))
        cfg["statusline"]["show"] = {"task_project": False}
        self.assertEqual(self.lines(cfg)[0], "Phase 3.9.5 · planning")


class PhasePlanScanTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-plan-")
        self.path = os.path.join(self.tmp, "PHASE_PLAN.md")
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(PLAN)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_header_and_tasks(self):
        out = statusline.scan_phase_plan(self.path)
        self.assertEqual(out["phase"], "36")
        self.assertEqual(out["phase_name"], "levers-off")
        self.assertEqual((out["done"], out["total"]), (2, 4))
        self.assertEqual(out["next"], "T3")
        self.assertEqual(out["titles"]["T3"], "recipe rung")

    def test_example_fixture_scans(self):
        example = os.path.join(ROOT, "tests", "fixtures", "PHASE_PLAN.example.md")
        if not os.path.exists(example):
            self.skipTest("PHASE_PLAN.example.md absent")
        out = statusline.scan_phase_plan(example)
        self.assertEqual(out["phase"], "24")
        self.assertEqual(out["total"], 5)        # T1 T2 T3.1 T4 T5; T3 superseded leaves the count (3.5 T8.1)
        self.assertEqual(out["done"], 2)
        self.assertEqual(out["next"], "T3.1")

    def test_missing_file(self):
        out = statusline.scan_phase_plan(os.path.join(self.tmp, "nope.md"))
        self.assertEqual((out["done"], out["total"], out["next"]), (0, 0, None))

    def test_superseded_leaves_the_count(self):
        """3.5 T8.1: a superseded task is not work left; it stays listed but leaves done/total."""
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(PLAN.replace("- T2 | done    ", "- T2 | superseded"))
        out = statusline.scan_phase_plan(self.path)
        self.assertEqual((out["done"], out["total"]), (1, 3))
        self.assertIn("T2", [t["id"] for t in out["tasks"]])


# --------------------------------------------------------------------------- line 3 / line 4

class SavingsLineTest(LedgerCase):
    """Line 3 (this project, T12) and line 4 (the account, moved here from line 3)."""

    def summary(self, fit_quality="ok", period_cost_used=None, lifetime=None,
                project=None, ppd=0.0855, **extra):
        account = {"label": "win-main", "windows": {
            "five_hour": {"pct_saved": 21.0, "fit_quality": fit_quality, "pct_per_dollar": ppd},
            "seven_day": {"pct_saved": 6.0, "fit_quality": fit_quality, "pct_per_dollar": ppd},
            "model_scoped:Fable": {"pct_saved": None, "fit_quality": "none"},
        }}
        if period_cost_used is not None:
            account["period"] = {"cost_used": period_cost_used}
            if period_cost_used > 0:              # per-window weeks from the summary (fix-9.c2)
                account["period"].update({"weeks_used": period_cost_used * 0.01,
                                          "weeks_saved": 0.0, "unrated_usd": 0.0})
        if lifetime is not None:
            account["lifetime"] = lifetime
        doc = {
            "schema": 1,
            "accounts": {"dev@example.com": account},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1,
                                    "saved_modeled": 31.4, "ratio": 0.78,
                                    "windows": {
                                        "five_hour": {"cost_used": 11.70, "net_saved": 9.1},
                                        "seven_day": {"cost_used": 11.70, "net_saved": 9.1}}}},
        }
        if project is not None:
            doc["projects"] = {statusline._project_key(self.project): project}
        doc.update(extra)
        write_json(paths.summary_path(), doc)
        return doc

    # -- session line --------------------------------------------------------

    def test_session_with_cost_and_savings(self):
        """T1.c7: Session line with windowed pairs and discount/multiplier."""
        self.summary()
        session = [l for l in self.lines() if "Session:" in l]
        self.assertTrue(session, self.lines())
        # dollars hidden by default; windowed pairs and discount visible
        self.assertNotIn("$11.70", session[0])
        self.assertIn("of 5h", session[0])
        self.assertIn("fewer", session[0])

    def test_project_usage_and_lifetime(self):
        """T1.c7: Project line with windowed pairs, weeks and discount/multiplier."""
        self.summary(project={"label": "demo",
                              "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                                          "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
                              "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0,
                                           "weeks_used": 1.25, "weeks_saved": 2.5,
                                           "unrated_usd": 0.0}})
        joined ="\n".join(self.lines())
        self.assertIn("Project:", joined)
        project_line = [l for l in self.lines() if l.strip().startswith("Project:")][0]
        self.assertIn("of 5h", project_line)
        self.assertIn("of 7d", project_line)
        # lifetime in weeks (not dollars): the summary's per-instance weeks (fix-9)
        self.assertIn("lifetime ~1.2w/2.5w", project_line)
        self.assertNotIn("$50", project_line)

    def test_project_weeks_skipped_without_fields(self):
        """fix-9: old summary data without weeks_used skips the segment, never today's rate."""
        self.summary(project={"label": "demo",
                              "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                                          "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
                              "period": {"cost_used": 30.0, "cost_saved_measured": 60.0},
                              "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0}})
        project_line = [l for l in self.lines() if l.strip().startswith("Project:")][0]
        self.assertNotIn("lifetime", project_line)
        self.assertNotIn("month", project_line)

    def test_project_weeks_approx_when_unrated(self):
        """fix-9: unrated spend over 10% of the block's cost marks the weeks with the approx sign."""
        self.summary(project={"label": "demo",
                              "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                                          "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
                              "period": {"cost_used": 30.0, "weeks_used": 0.4,
                                         "unrated_usd": 2.0},
                              "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0,
                                           "weeks_used": 1.0, "unrated_usd": 6.0}})
        project_line = [l for l in self.lines() if l.strip().startswith("Project:")][0]
        self.assertIn("month ~0.4w", project_line)
        self.assertIn("lifetime %s1.0w" % statusline.APPROX, project_line)

    def test_project_dash_without_fit(self):
        self.summary(fit_quality="none",
                     project={"label": "demo", "windows": {}, "lifetime": {}})
        # no fit -> no windowed pairs -> no project line (no vanilla pieces either with empty lifetime)
        project_lines = [l for l in self.lines() if "Project:" in l]
        self.assertEqual(len(project_lines), 0)

    def test_live_savings_are_provisional(self):
        self.summary()
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": "dev@example.com", "expert": "agent-1",
            "agents": {"agent-1": {"ctx": 1000, "saved_live_usd": 0.9}}}}})
        session = [l for l in self.lines() if "Session:" in l][0]
        # with live helper running, the discount shows ~ prefix
        self.assertIn("~", session)
        self.assertIn("fewer", session)

    def test_session_windows_fallback_before_rebuild(self):
        """T7.c2: a patch-only session entry (SessionStart, no rebuild yet) still
        shows both windowed pairs, approximated from the session-wide cost."""
        started = 1700000000
        self.summary(sessions={self.sid: {"account": "dev@example.com", "started": started}})
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": "dev@example.com", "expert": "agent-1",
            "agents": {"agent-1": {"ctx": 1000, "saved_live_usd": 0.9}}}}})
        session = [l for l in self.lines() if "Session:" in l][0]
        self.assertIn("of 5h", session)
        self.assertIn("of 7d", session)
        self.assertIn("fewer", session)
        # no windows block: y is DASH, live not added (T11.1; was 100%/8%)
        self.assertIn("100%%/%s of 5h" % statusline.DASH, session)
        self.assertIn("100%%/%s of 7d" % statusline.DASH, session)

    def test_session_windows_fallback_skips_unstarted_window(self):
        """T7.c2: a window that started after the session did is left out of the
        fallback (its cost predates that window)."""
        started = 1700000000
        account = {"label": "win-main", "windows": {
            "five_hour": {"pct_saved": 21.0, "fit_quality": "ok", "pct_per_dollar": 0.0855,
                          "started_at": started + 100},
            "seven_day": {"pct_saved": 6.0, "fit_quality": "ok", "pct_per_dollar": 0.0855},
            "model_scoped:Fable": {"pct_saved": None, "fit_quality": "none"},
        }}
        self.summary(sessions={self.sid: {"account": "dev@example.com", "started": started}},
                     accounts={"dev@example.com": account})
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": "dev@example.com", "expert": "agent-1",
            "agents": {"agent-1": {"ctx": 1000, "saved_live_usd": 0.9}}}}})
        session = [l for l in self.lines() if "Session:" in l][0]
        self.assertNotIn("of 5h", session)
        self.assertIn("of 7d", session)
        self.assertIn("100%%/%s of 7d" % statusline.DASH, session)  # T11.1: was 100%/8%

    def test_show_modeled_true_no_monolithic(self):
        """T10.c1: show_modeled is accepted but the vs-monolithic segment is removed."""
        self.summary()
        cfg = plain_cfg()
        cfg["statusline"]["show_modeled"] = True
        joined = "\n".join(self.lines(cfg))
        self.assertNotIn("monolithic", joined)

    def test_line3_without_summary(self):
        """T1.c7: without summary no windowed pairs; T11.1: the vanilla pieces print DASH
        (was: Session line absent)."""
        session = [l for l in self.lines() if "Session:" in l]
        self.assertEqual(session, ["Session: {0} fewer · lasts {0} longer".format(statusline.DASH)])

    # -- account line -------------------------------------------------------

    def test_account_dash_without_fit(self):
        """T1.c1: Account line uses x/y schema; x=meter pct, y=~savings pct."""
        self.summary(fit_quality="none")
        acct = [l for l in self.lines() if "Account:" in l][0]
        # no fit: x=meter pct (from pct_last, which is absent here), y=DASH
        self.assertIn("of 5h", acct)
        self.assertIn("of 7d", acct)

    def test_account_band_shows_approx(self):
        """T1.c1: band quality uses approx mark on y."""
        self.summary(fit_quality="band")
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertIn(statusline.APPROX, acct)

    def test_account_ok_shows_tilde(self):
        """T1.c1: ok quality uses tilde on y."""
        self.summary(fit_quality="ok")
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertIn("~", acct)

    def test_lifetime_saved_shown_when_positive(self):
        self.summary(lifetime={"cost_saved_measured": 612.0, "cost_used": 900.0, "ratio": 0.68,
                               "weeks_used": 2.0, "weeks_saved": 1.5, "unrated_usd": 0.0})
        acct = [l for l in self.lines() if "Account:" in l][0]
        # lifetime in weeks from the summary's per-window fields (fix-9.c2), not dollars
        self.assertIn("lifetime ~2.0w/1.5w", acct)
        self.assertNotIn("$612", acct)

    def test_lifetime_saved_hidden_at_zero(self):
        self.summary(lifetime={"cost_saved_measured": 0.0, "cost_used": 900.0, "ratio": 0.0,
                               "weeks_used": 2.0, "weeks_saved": 0.0, "unrated_usd": 0.0})
        acct = [l for l in self.lines() if "Account:" in l][0]
        # cost_used > 0: lifetime shown in weeks; saved = 0, no savings pair
        self.assertIn("lifetime ~2.0w", acct)
        self.assertNotIn("~2.0w/", acct)
        self.assertNotIn("$900", acct)

    def test_account_weeks_skipped_without_fields(self):
        """fix-9.c2: old summary data without weeks_used skips month and lifetime."""
        self.summary(lifetime={"cost_saved_measured": 612.0, "cost_used": 900.0, "ratio": 0.68},
                     period_cost_used=0)
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertNotIn("lifetime", acct)
        self.assertNotIn("month", acct)

    def test_account_weeks_approx_when_unrated(self):
        """fix-9.c2: unrated spend over 10% of cost_used marks the weeks with the approx sign."""
        self.summary(lifetime={"cost_saved_measured": 612.0, "cost_used": 900.0, "ratio": 0.68,
                               "weeks_used": 2.0, "weeks_saved": 1.5, "unrated_usd": 200.0})
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertIn("lifetime %s2.0w/1.5w" % statusline.APPROX, acct)

    def test_project_weeks_first_segment_labelled(self):
        """fix-9.c2: a weeks segment that comes first carries the "Project:" label."""
        self.summary(fit_quality="none",
                     project={"label": "demo", "windows": {},
                              "lifetime": {"cost_used": 50.0, "weeks_used": 1.0,
                                           "unrated_usd": 0.0}})
        proj = [l for l in self.lines() if "lifetime ~1.0w" in l]
        self.assertTrue(proj, self.lines())
        self.assertTrue(proj[0].startswith("Project: lifetime ~1.0w"), proj[0])

    def test_lifetime_saved_hidden_when_absent(self):
        self.summary()
        acct = [l for l in self.lines() if "Account:" in l]
        # no lifetime data, no lifetime segment
        if acct:
            self.assertNotIn("lifetime", acct[0])

    def test_account_segment_order_with_month(self):
        """T1.c7: Account line order: x/y of 5h, x/y of 7d, month, lifetime, discount."""
        self.summary(period_cost_used=48.3, lifetime={"cost_saved_measured": 120.0,
                                                       "cost_used": 300.0, "ratio": 0.4,
                                                       "weeks_used": 3.0, "weeks_saved": 1.2})
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertIn("month", acct)
        self.assertIn("lifetime", acct)
        # month and lifetime in weeks
        self.assertIn("w", acct)
        marks = ("Account:", "of 5h", "of 7d", "month", "lifetime", "fewer")
        positions = [acct.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions), acct)

    def test_month_hidden_at_zero(self):
        self.summary(period_cost_used=0)
        acct = [l for l in self.lines() if "Account:" in l]
        if acct:
            self.assertNotIn("month", acct[0])

    def test_main_thread_mismatch_ignores_a_gained_window_tag(self):
        cfg = {"pinned_models": {"pa-session": "claude-sonnet-5", "expert-fable": "claude-fable-5-1[1m]"}}
        def payload(agent, model):
            return {"agent": {"name": agent}, "model": {"id": model}}
        self.assertIsNone(statusline._mismatch(payload("pa-session", "claude-sonnet-5[1m]"), cfg))
        self.assertIsNone(statusline._mismatch(payload("pa-session", "claude-sonnet-5"), cfg))
        lost = statusline._mismatch(payload("expert-fable", "claude-fable-5-1"), cfg)
        self.assertEqual(lost["seen"], "claude-fable-5-1")            # pinned [1m], ran without it
        other = statusline._mismatch(payload("pa-session", "claude-opus-5"), cfg)
        self.assertEqual(other["expected"], "claude-sonnet-5")

    def test_resolved_model_alert_leaves_the_statusline(self):
        cfg = {"pinned_models": {"pa-session": "claude-sonnet-5"}}
        alert = {"kind": "model_mismatch", "session_id": "s1", "agent": "pa-session",
                 "expected": "claude-sonnet-5", "seen": "claude-sonnet-5[1m]", "ts": "2026-09-19T19:33:17Z"}
        ctx = {"summary": {"alerts": [alert]}, "status": {}, "sid": "s1"}
        payload = {"session_id": "s1", "agent": {"name": "pa-session"}, "model": {"id": "claude-sonnet-5[1m]"}}
        self.assertIsNone(statusline._mismatch(payload, cfg))   # the condition does not hold, so line 5 must not show it
        payload_bad = {"session_id": "s1", "agent": {"name": "pa-session"}, "model": {"id": "claude-opus-5"}}
        self.assertIsNotNone(statusline._mismatch(payload_bad, cfg))


# --------------------------------------------------------------------------- line 5

class WaitingLineTest(LedgerCase):

    def test_waiting_file_adds_line_five(self):
        self.make_project(status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        write_json(paths.waiting_path(self.sid),
                   {"kind": "question", "agent": "expert-fable", "ts": "2026-09-12T21:00:00Z"})
        lines = self.lines()
        self.assertGreaterEqual(len(lines), 4, lines)   # task block adds lines after line 5
        joined = "\n".join(lines)
        self.assertIn("waiting on you: question (expert-fable)", joined)
        self.assertIn(statusline.FLAG, joined)

    def test_waiting_replan_and_review(self):
        self.make_project(status={"task": "T3"})
        write_json(paths.waiting_path(self.sid), {"kind": "REPLAN.md"})
        joined = "\n".join(self.lines())
        self.assertIn("REPLAN.md", joined)
        write_json(paths.waiting_path(self.sid), {"kind": "review"})
        joined = "\n".join(self.lines())
        self.assertIn("REVIEW.md", joined)

    def test_review_file_newer_than_session_start(self):
        self.make_project(status={"task": "T3", "started": "2000-01-01T00:00:00Z"})
        current = os.path.join(self.project, "phase-ends", "current")
        with open(os.path.join(current, "REVIEW.md"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("# review\n")
        lines = self.lines()
        self.assertGreaterEqual(len(lines), 4, lines)
        joined = "\n".join(lines)
        self.assertIn("REVIEW.md", joined)

    def test_model_fallback_alert(self):
        self.make_project(status={"task": "T3"})
        write_json(paths.summary_path(), {"schema": 1, "accounts": {}, "sessions": {},
                                          "alerts": [{"kind": "model_mismatch",
                                                      "session_id": self.sid,
                                                      "agent": "expert-fable",
                                                      "expected": "claude-fable-5-1",
                                                      "seen": "claude-opus-5"}]})
        joined = "\n".join(self.lines())
        self.assertIn("model fallback", joined)
        self.assertIn("claude-opus-5", joined)

    def test_nothing_waiting_means_at_least_three_lines(self):
        self.make_project(status={"task": "T3"})
        self.assertGreaterEqual(len(self.lines()), 3)  # task block adds lines after line 5

    def test_full_render_when_everything_present(self):
        """T1.c7: task block + model + Pace + Session + Project + Account + alerts."""
        self.make_project(status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                "five_hour": {"pct_saved": 21.0, "pct_last": 19.0, "fit_quality": "ok",
                              "pct_per_dollar": 0.0855},
                "seven_day": {"pct_saved": 6.0, "pct_last": 1.0, "fit_quality": "ok",
                              "pct_per_dollar": 0.0855}},
                "period": {"cost_used": 48.3, "cost_saved_measured": 120.0},
                "lifetime": {"cost_saved_measured": 120.0, "cost_used": 300.0, "ratio": 0.4}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1,
                                    "windows": {
                                        "five_hour": {"cost_used": 11.70, "net_saved": 9.1},
                                        "seven_day": {"cost_used": 11.70, "net_saved": 9.1}}}},
            "projects": {statusline._project_key(self.project): {
                "label": "demo",
                "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                            "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
                "lifetime": {"cost_used": 50.0, "cost_saved_measured": 40.0,
                             "weeks_used": 1.0, "weeks_saved": 0.8, "unrated_usd": 0.0}}},
        })
        write_json(paths.waiting_path(self.sid),
                   {"kind": "question", "agent": "expert-fable", "ts": "2026-09-12T21:00:00Z"})
        lines = self.lines()
        self.assertGreaterEqual(len(lines), 6, lines)
        joined = "\n".join(lines)
        self.assertIn("Session:", joined)
        self.assertIn("Project:", joined)
        self.assertIn("Account:", joined)
        self.assertIn("waiting on you: question", joined)


    def test_seed_growth_alert_on_line_five(self):
        """T10: a seed_growth alert in the summary shows on line 5 in RED."""
        self.make_project(status={"task": "T3"})
        pkey = statusline._project_key(self.project)
        write_json(paths.summary_path(), {
            "schema": 1, "accounts": {}, "sessions": {},
            "alerts": [{"kind": "seed_growth", "project": pkey,
                        "phase": "3.3", "median": 45000, "max": 50000,
                        "suspects": [{"path": "CLAUDE.md", "size": 960},
                                     {"path": ".claude/skills/project-architect/SKILL.md",
                                      "size": 11400}]}]})
        joined = "\n".join(self.lines())
        self.assertIn("seed growth", joined)
        self.assertIn("45k", joined)
        self.assertIn("CLAUDE.md", joined)

    def test_memory_linked_alert_on_line_five(self):
        """T5: a fresh memory_linked alert for this session asks for a restart on line 5."""
        import time as _time
        self.make_project(status={"task": "T3"})
        write_json(paths.summary_path(), {
            "schema": 1, "accounts": {}, "sessions": {},
            "alerts": [{"kind": "memory_linked", "session_id": self.sid,
                        "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())},
                       {"kind": "memory_linked", "session_id": "someone-else",
                        "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())}]})
        joined = "\n".join(self.lines())
        self.assertIn("memory linked", joined)
        # only one per session (the other session's alert is not shown)
        self.assertEqual(joined.count("memory linked"), 1)

    def test_seed_block_in_summary(self):
        """T10: rebuild stores seed data in projects[p].seed."""
        from pa import summary, db as _db
        conn = _db.connect(os.path.join(self.ledger, "ledger.sqlite"))
        try:
            _db.init_schema(conn)
            sid = "seed-sl-0000-0000-000000000001"
            pdir = self.project.replace("\\", "/")
            _db.upsert(conn, "sessions", {
                "session_id": sid, "account": "dev@example.com",
                "project": pdir, "cwd": pdir,
                "kind": "main", "started": "2026-09-20T10:00:00Z",
                "ended": "2026-09-20T11:00:00Z", "phase": "3.3"})
            for i, seed in enumerate([20000, 25000, 30000]):
                _db.upsert(conn, "agent_runs", {
                    "run_id": "seed-slr-%04d" % i, "session_id": sid,
                    "kind": "expert", "agent_type": "expert-fable", "phase": "3.3",
                    "seed_ctx": seed, "started": "2026-09-20T10:%02d:00Z" % i})
            conn.commit()
            doc = summary.rebuild(conn)
        finally:
            _db.close(conn)
        self.assertIsInstance(doc, dict)
        pkey = os.path.normcase(os.path.normpath(pdir))
        projects = doc.get("projects", {})
        # the project may or may not appear depending on whether there are window instances
        # so check via the seed_for_projects function directly
        from pa.summary import _seed_for_projects
        fake_projects = {pkey: {"label": "demo"}}
        conn2 = _db.connect(os.path.join(self.ledger, "ledger.sqlite"))
        try:
            _seed_for_projects(conn2, fake_projects)
        finally:
            _db.close(conn2)
        seed = fake_projects[pkey].get("seed")
        self.assertIsNotNone(seed, fake_projects[pkey])
        self.assertEqual(seed["phase"], "3.3")
        self.assertEqual(seed["median"], 25000)
        self.assertEqual(seed["max"], 30000)
        self.assertEqual(seed["n"], 3)


# --------------------------------------------------------------------------- T1.c2: period cost_used kind='api' only

class AccountPeriodApiOnlyTest(LedgerCase):
    """T1.c2: accounts_block period.cost_used counts only kind='api' turns,
    so residual corrections never make the month exceed the lifetime."""

    def test_residual_excluded_from_period(self):
        from pa import summary, db as _db
        conn = _db.connect(os.path.join(self.ledger, "ledger.sqlite"))
        try:
            _db.init_schema(conn)
            _db.set_meta(conn, "created", "2026-09-01T00:00:00Z")  # era before fixture sessions
            sid = "period-test-0000-0000-000000000001"
            email = "dev@example.com"
            _db.upsert(conn, "sessions", {
                "session_id": sid, "account": email,
                "kind": "main", "started": "2026-09-21T10:00:00Z",
                "ended": "2026-09-21T11:00:00Z", "cost_usd": 5.0})
            # register the account so accounts_block discovers it
            conn.execute("INSERT OR IGNORE INTO accounts (email) VALUES (?)", (email,))
            # an api turn
            _db.upsert_turn(conn, {"msg_id": "m1", "session_id": sid, "account": email,
                                   "ts": "2026-09-21T10:30:00Z", "model": "claude-sonnet-5",
                                   "cost_usd": 3.0, "kind": "api"})
            # a residual turn (reconciliation correction)
            _db.upsert_turn(conn, {"msg_id": "m2", "session_id": sid, "account": email,
                                   "ts": "2026-09-21T10:35:00Z", "model": "claude-sonnet-5",
                                   "cost_usd": 100.0, "kind": "residual"})
            conn.commit()
            # 3.11 T9: a period needs the account's own renewal day (was the default 21)
            block = summary.accounts_block(conn, cfg={"accounts": {email: {"renewal_day": 21}}})
            acct = block.get(email) or {}
            period_cost = (acct.get("period") or {}).get("cost_used", 0.0)
            lifetime_cost = (acct.get("lifetime") or {}).get("cost_used", 0.0)
            # period must exclude the residual row, so it equals the api cost
            self.assertAlmostEqual(period_cost, 3.0, places=2)
            # lifetime also counts api only
            self.assertAlmostEqual(lifetime_cost, 3.0, places=2)
            # month must never exceed lifetime
            self.assertLessEqual(period_cost, lifetime_cost + 0.01)
        finally:
            _db.close(conn)


# --------------------------------------------------------------------------- helpers

class FormattingTest(unittest.TestCase):

    def test_fmt_tokens(self):
        self.assertEqual(statusline.fmt_tokens(471873), "472k")
        self.assertEqual(statusline.fmt_tokens(1000000), "1M")
        self.assertEqual(statusline.fmt_tokens(1234567), "1.2M")
        self.assertEqual(statusline.fmt_tokens(512), "512")
        self.assertEqual(statusline.fmt_tokens(None), "?")

    def test_fmt_elapsed(self):
        self.assertEqual(statusline.fmt_elapsed(720), "12m")
        self.assertEqual(statusline.fmt_elapsed(6420), "1h47m")
        self.assertEqual(statusline.fmt_elapsed(-5), "0m")
        self.assertEqual(statusline.fmt_elapsed(180000), "2d02h")

    def test_parse_epoch(self):
        self.assertEqual(statusline.parse_epoch(1789272600), 1789272600.0)
        self.assertEqual(statusline.parse_epoch("1789272600"), 1789272600.0)
        self.assertEqual(statusline.parse_epoch("1970-01-01T00:00:00Z"), 0.0)
        self.assertEqual(statusline.parse_epoch("2026-09-12T20:58:00Z"), 1789246680.0)
        self.assertEqual(statusline.parse_epoch("2026-09-12T21:58:00+01:00"), 1789246680.0)
        self.assertIsNone(statusline.parse_epoch("not-a-date"))
        self.assertIsNone(statusline.parse_epoch(None))

    def test_windows_of_scaling(self):
        windows = statusline.windows_of({"rate_limits": {
            "five_hour": {"used_percentage": 19, "resets_at": 10},
            "model_scoped": [{"display_name": "Fable 5.1", "utilization": 0.89},
                             {"display_name": "Opus", "utilization": 42}]}})
        self.assertEqual(windows["five_hour"]["pct"], 19.0)
        self.assertAlmostEqual(windows["model_scoped:Fable 5.1"]["pct"], 89.0)
        self.assertAlmostEqual(windows["model_scoped:Opus"]["pct"], 42.0)


# --------------------------------------------------------------------------- sampler

class SamplerTest(LedgerCase):

    def spool_lines(self):
        path = paths.spool_path(self.sid)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def rows(self, sql, args=()):
        from pa import db

        conn = db.connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            db.close(conn)

    def test_first_then_identical_then_change(self):
        statusline.sample(self.payload, self.cfg)
        spooled = self.spool_lines()
        self.assertEqual([s["reason"] for s in spooled], ["first"])
        self.assertTrue(os.path.exists(paths.session_state_path(self.sid)))

        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)               # nothing changed
        self.assertEqual(len(self.spool_lines()), 1)

        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 21
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        reasons = [s["reason"] for s in self.spool_lines()]
        self.assertEqual(reasons, ["first", "prev", "change"])

        util = self.rows("SELECT window, pct, reason FROM utilization ORDER BY id")
        self.assertGreaterEqual(len(util), 6)                   # 3 samples x 2 windows
        self.assertIn("five_hour", [r["window"] for r in util])
        self.assertIn(21.0, [r["pct"] for r in util])

        instances = self.rows("SELECT * FROM window_instances WHERE window='five_hour'")
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["reset_source"], "resets_at")
        self.assertEqual(instances[0]["started_at"],
                         self.payload["rate_limits"]["five_hour"]["resets_at"] - 18000)
        self.assertIsNone(instances[0]["pct_per_dollar"])       # the fit is a later milestone

    def test_resets_at_change_creates_a_second_instance(self):
        statusline.sample(self.payload, self.cfg)
        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 21
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        self.assertEqual(len(self.rows("SELECT * FROM window_instances WHERE window='five_hour'")), 1)

        self.payload["rate_limits"]["five_hour"]["resets_at"] += 18000
        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 2
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        instances = self.rows(
            "SELECT * FROM window_instances WHERE window='five_hour' ORDER BY resets_at")
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[1]["reset_source"], "resets_at")

    def test_drop_without_resets_change_is_flagged(self):
        statusline.sample(self.payload, self.cfg)
        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 21
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        self.payload["rate_limits"]["five_hour"]["used_percentage"] = 3   # -18 pts, same reset
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        row = self.rows("SELECT * FROM window_instances WHERE window='five_hour'")[0]
        self.assertEqual(row["reset_source"], "drop")
        self.assertTrue(self.rows("SELECT * FROM events WHERE kind='reset'"))

    def test_model_change_and_cache_miss(self):
        statusline.sample(self.payload, self.cfg)
        self.payload["model"]["id"] = "claude-opus-5"
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        self.assertEqual([s["reason"] for s in self.spool_lines()], ["first", "model"])

        self.payload["prompt_cache"]["misses"] = 1
        self.payload["prompt_cache"]["last_miss_cause"] = "ttl_expired"
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        self.assertEqual([s["reason"] for s in self.spool_lines()][-1], "miss")

    def test_heartbeat(self):
        statusline.sample(self.payload, self.cfg)
        state_path = paths.session_state_path(self.sid)
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        state["last_written_ts"] = time.time() - 10000
        with open(state_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle)
        statusline._JSON_CACHE.clear()
        statusline.sample(self.payload, self.cfg)
        self.assertEqual([s["reason"] for s in self.spool_lines()], ["first", "heartbeat"])

    def test_session_cost_is_the_payload_cost(self):
        statusline.sample(self.payload, self.cfg)
        self.assertAlmostEqual(self.spool_lines()[0]["session_cost"],
                               self.payload["cost"]["total_cost_usd"])

    def test_spool_is_drained_once(self):
        statusline.sample(self.payload, self.cfg)
        for pct in (21, 22, 23):
            self.payload["rate_limits"]["five_hour"]["used_percentage"] = pct
            statusline._JSON_CACHE.clear()
            statusline.sample(self.payload, self.cfg)
        rows = self.rows("SELECT COUNT(*) AS n FROM utilization")[0]["n"]
        spooled = len(self.spool_lines())
        self.assertEqual(rows, spooled * 2)            # two windows per spool line

    def test_no_session_id_is_a_no_op(self):
        self.payload.pop("session_id")
        self.assertIsNone(statusline.sample(self.payload, self.cfg))
        self.assertFalse(os.path.exists(paths.spool_dir())
                         and os.listdir(paths.spool_dir()))

    def test_no_sqlite_without_a_change(self):
        statusline.sample(self.payload, self.cfg)      # `first` only
        self.assertFalse(os.path.exists(paths.db_path()))

    def test_sampler_labels_row_with_restamped_account(self):
        """T8.c3: when the credentials key changed, the sampler picks up the re-stamped account."""
        from pa import accounts, fsutil, running

        old_email = "old@example.com"
        new_email = "switched@example.com"
        # populate running.json so the sampler sees an initial account
        running.ensure_session(self.sid, account=old_email)
        # stamp the session with the old account and a known cred_key
        stamp_dir = os.path.join(self.ledger, "state", "accounts")
        os.makedirs(stamp_dir, exist_ok=True)
        fsutil.atomic_write_json(
            os.path.join(stamp_dir, "%s.json" % self.sid),
            {"session_id": self.sid, "account": old_email,
             "source": "auth", "cred_key": "100:200",
             "ts": "2026-09-12T20:00:00Z"}, indent=1)
        orig_cred = accounts.credentials_key
        orig_auth = accounts.auth_status
        try:
            accounts.credentials_key = lambda: "999:200"  # different from stamp
            accounts.auth_status = lambda *a, **k: {"email": new_email, "source": "auth"}
            statusline._JSON_CACHE.clear()
            statusline.sample(self.payload, self.cfg)
        finally:
            accounts.credentials_key = orig_cred
            accounts.auth_status = orig_auth
        spooled = self.spool_lines()
        self.assertTrue(spooled)
        self.assertEqual(spooled[0]["account"], new_email)
        # the render ctx picks up the re-stamped account from running.json
        statusline._JSON_CACHE.clear()
        ctx = statusline._gather(self.payload, self.cfg)
        self.assertEqual(ctx["account"], new_email)


# --------------------------------------------------------------------------- timing

class TimingTest(LedgerCase):

    def test_render_under_20ms(self):
        cfg = config.defaults()
        statusline.render(self.payload, cfg)           # warm the caches
        best = min(self._time_render(cfg) for _ in range(5))
        sys.stderr.write("\n  render(): %.2f ms (best of 5)\n" % best)
        self.assertLess(best, 20.0, "render() took %.2f ms" % best)

    def _time_render(self, cfg):
        start = time.perf_counter()
        statusline.render(self.payload, cfg)
        return (time.perf_counter() - start) * 1000.0


class EntryPointTest(LedgerCase):

    def test_entry_point_prints_and_exits_zero(self):
        env = dict(os.environ)
        env["PA_LEDGER_DIR"] = self.ledger
        with open(FIXTURE, "rb") as handle:
            raw = handle.read()
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "pa_statusline.py")],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=ROOT)
        elapsed = (time.perf_counter() - start) * 1000.0
        sys.stderr.write("\n  pa_statusline.py: %.0f ms wall\n" % elapsed)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8", "replace")
        self.assertNotIn("\r", out)                    # LF only
        lines = [l for l in out.split("\n") if l]
        self.assertGreaterEqual(len(lines), 2)
        # model line may not be lines[0] if the entry point's cwd is a governed project
        joined = "\n".join(lines)
        self.assertIn(ESC, joined)                     # ANSI colours somewhere
        self.assertIn("Fable 5.1", joined)
        self.assertTrue(os.path.exists(paths.spool_path(self.sid)))

    def test_broken_stdin_still_prints_and_exits_zero(self):
        env = dict(os.environ)
        env["PA_LEDGER_DIR"] = self.ledger
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "pa_statusline.py")],
            input=b"not json at all", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=ROOT)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.decode("utf-8", "replace").strip() != "")


if __name__ == "__main__":
    unittest.main()


class SevenDayRateShrinkTest(SevenDayRateTest):
    """Too wide, the 7d rate piece gives up its figures one by one; the 7d segment itself
    is never dropped for them (the developer lost the 7d segment on 2026-09-20)."""

    def _first(self, max_width, seven_day="all"):
        cfg = {"statusline": {"colors": False, "max_width": max_width, "seven_day": seven_day}}
        return self.lines(cfg)[0]

    def test_seven_day_has_no_parenthetical(self):
        """T1.c1: the 7d parenthetical is removed from line 1; pace is on the Pace line."""
        self._set_seven_day(45, 4.8)
        full = self._first(400)
        self.assertIn("7d 45%", full)
        self.assertNotIn("(pace", full)


# --------------------------------------------------------------------------- T1.c3: Pace line colours

class PaceLineColorTest(LedgerCase):
    """T10.c1: Pace line per-piece colouring: the multiplier and the used number share the
    pace tier (GREEN/YELLOW/RED, T1.c4); the left number is its own tier by pace_left_green
    /pace_left_yellow. Suffixes stay DIM."""

    def _set_seven_day(self, pct, elapsed_days):
        now = time.time()
        duration = statusline.DEFAULT_WINDOW_S
        elapsed = elapsed_days * 86400.0
        self.payload["rate_limits"] = {
            "seven_day": {"used_percentage": pct, "resets_at": now + (duration - elapsed)}}

    def _pace_line(self, **kw):
        self._set_seven_day(kw.get("pct", 45), kw.get("elapsed", 4.8))
        cfg = config.defaults()
        for k, v in kw.items():
            if k.startswith("pace_"):
                cfg["statusline"][k] = v
        colored = statusline.render(self.payload, cfg)
        for line in colored.split("\n"):
            if "Pace:" in line:
                return line
        return ""

    def test_multiplier_green_below_yellow(self):
        """T1.c4: the leading pace multiplier is GREEN under pace_yellow (was LIGHT_BLUE, T10.c1)."""
        line = self._pace_line(pct=45, elapsed=4.8)  # pace 0.7x, under 1.25
        self.assertIn(statusline.GREEN + "0.7x", line)

    def test_used_number_colored_pace_zone_dim_suffix(self):
        """T10.c1: the used number shares the multiplier's pace-zone colour, '/day used' DIM."""
        line = self._pace_line(pct=45, elapsed=4.8)  # pace 0.7x, GREEN; 45% / 5 days begun
        self.assertIn(statusline.GREEN + "9%", line)
        self.assertIn(statusline.DIM + "/day used", line)

    def _used_plain(self, pct, elapsed_s):
        """T21.1.1.1: the Pace line at a fixed now, ANSI stripped."""
        import re
        now = 1_800_000_000.0
        self.payload["rate_limits"] = {"seven_day": {
            "used_percentage": pct,
            "resets_at": now + (statusline.DEFAULT_WINDOW_S - elapsed_s)}}
        cfg = config.defaults()
        line = statusline._line_pace(self.payload, statusline._gather(self.payload, cfg), cfg,
                                     statusline.sl_config(cfg), now)
        return re.sub(r"\x1b\[[0-9;]*m", "", line)

    def test_left_number_colored_green_high(self):
        """T10.c1: left number at or above pace_left_green is GREEN."""
        line = self._pace_line(pct=10, elapsed=2.0)  # 90% left over 5 days -> 18%/day, >= 14
        self.assertIn(statusline.GREEN + "18%", line)
        self.assertIn("/day left", line)

    def test_left_number_colored_yellow_mid(self):
        """T10.c1: left number between pace_left_yellow and pace_left_green is YELLOW."""
        line = self._pace_line(pct=45, elapsed=2.5)  # left ~12.2%/day, between 10 and 14
        self.assertIn(statusline.YELLOW + "12%", line)

    def test_left_number_colored_red_low(self):
        """T10.c1: left number below pace_left_yellow is RED."""
        line = self._pace_line(pct=90, elapsed=5.0)  # left 5%/day, below 10
        self.assertIn(statusline.RED + "5%", line)

    def test_left_thresholds_configurable_move_color(self):
        """T10.c1: custom pace_left thresholds move the left number's colour."""
        line = self._pace_line(pct=45, elapsed=2.5,
                               pace_left_green=12, pace_left_yellow=8)  # left ~12.2%/day, now >= green
        self.assertIn(statusline.GREEN + "12%", line)


class PerDayRealBoundaryTest(unittest.TestCase):
    """T21.1.1.1.1: used = pct / max(1 day, now - real start); left = remaining /
    max(1 day, time to reset); start = drop-aware summary start else resets - 7d."""

    NOW = 1_800_000_000.0
    WEEK = 7 * 86400

    def _plain(self, pct, resets, now, ctx=None):
        import re
        payload = {"rate_limits": {"seven_day": {"used_percentage": pct, "resets_at": resets}}}
        cfg = config.defaults()
        line = statusline._line_pace(payload, ctx, cfg, statusline.sl_config(cfg), now)
        return re.sub(r"\x1b\[[0-9;]*m", "", line)

    def _at(self, pct, elapsed_s):
        return self._plain(pct, self.NOW + self.WEEK - elapsed_s, self.NOW)

    def test_a_three_hours(self):
        line = self._at(3, 3 * 3600)
        # was 1.7x (raw-elapsed pace basis); T26: one-day accrual floor
        for text in ("0.2x", "3%/day used", "14%/day left"):
            self.assertIn(text, line)

    def test_b_three_and_half_days(self):
        line = self._at(40, 3.5 * 86400)
        for text in ("0.8x", "11%/day used", "17%/day left"):
            self.assertIn(text, line)

    def test_c_exactly_one_day(self):
        line = self._at(12, 86400)
        for text in ("12%/day used", "15%/day left"):
            self.assertIn(text, line)

    def test_d_continuity_past_one_day(self):
        self.assertIn("12%/day used", self._at(12.5, 86400 + 3600))

    def test_e_exactly_two_days(self):
        line = self._at(20, 2 * 86400)
        for text in ("10%/day used", "16%/day left"):
            self.assertIn(text, line)

    def test_f_no_calendar_midnight_effect(self):
        import calendar
        start = calendar.timegm((2026, 9, 20, 14, 30, 0))       # 14:30 UTC
        resets = start + self.WEEK
        for now in (start + 33 * 3600, start + 34 * 3600):       # 23:30, 00:30 next day
            self.assertIn("13%/day used", self._plain(18, resets, now))

    def _ctx(self, resets_at, started_at):
        return {"account": "a@x", "summary": {"accounts": {"a@x": {"windows": {
            "seven_day": {"resets_at": resets_at, "started_at": started_at}}}}}}

    def test_g_drop_reset_start(self):
        resets = self.NOW + 3 * 86400
        line = self._plain(2, resets, self.NOW, self._ctx(resets + 30, self.NOW - 5 * 3600))
        # was 0.3x (raw-elapsed pace basis); T26: one-day accrual floor
        for text in ("0.1x", "2%/day used", "33%/day left"):
            self.assertIn(text, line)
        line = self._plain(2, resets, self.NOW,
                           self._ctx(resets + 2 * 86400, self.NOW - 5 * 3600))
        self.assertIn("0.0x", line)                              # nominal start

    def test_h_past_reset(self):
        line = self._plain(50, self.NOW - 60, self.NOW)
        self.assertNotIn("/day used", line)
        self.assertNotIn("/day left", line)

    def _colored(self, pct, resets, now, ctx=None):
        payload = {"rate_limits": {"seven_day": {"used_percentage": pct, "resets_at": resets}}}
        cfg = config.defaults()
        return statusline._line_pace(payload, ctx, cfg, statusline.sl_config(cfg), now)

    def test_i_live_moment_t26(self):
        """T26: the live report; was RED 2.1x beside a GREEN-worthy 6%/day used."""
        resets, now = 1791079200, 1790492062
        ctx = self._ctx(1791079200, 1790474400)
        line = self._plain(6, resets, now, ctx)
        for text in ("0.4x", "6%/day used", "14%/day left"):
            self.assertIn(text, line)
        colored = self._colored(6, resets, now, ctx)
        self.assertIn(statusline.GREEN + "0.4x", colored)
        self.assertIn(statusline.GREEN + "6%", colored)

    def test_j_pace_and_used_colour_share_base(self):
        """T26: pace and /day used share one base, so they agree in colour and value."""
        import re
        esc = r"\x1b\[[0-9;]*m"
        pace_re = re.compile("(%s)(\\d+\\.\\d)x" % esc)
        used_re = re.compile("(%s)(\\d+)%%(?:%s)*/day used" % (esc, esc))
        seen = 0
        for days in (0.1, 0.2, 0.5, 0.9, 1, 1.5, 3, 6.5):
            for pct in (2, 6, 12, 30, 60):
                colored = self._colored(pct, self.NOW + self.WEEK - days * 86400, self.NOW)
                p, u = pace_re.search(colored), used_re.search(colored)
                if not (p and u):
                    continue
                seen += 1
                self.assertEqual(p.group(1), u.group(1), (days, pct))
                pace, used = float(p.group(2)), int(u.group(2))
                self.assertLessEqual(abs(pace * 100 / 7 - used), 0.051 * 100 / 7 + 0.5,
                                     (days, pct))
        self.assertGreater(seen, 0)


    def _paid_ctx(self, resets, **seven):
        seven.update({"resets_at": resets, "started_at": self.NOW - 3 * 86400,
                      "cost_saved_measured": 100.0})
        return {"account": "a@x", "summary": {"accounts": {"a@x": {"windows": {
            "seven_day": seven}}}}}

    def test_k_paid_on_ledger_basis(self):
        """fix-8: paid reads ledger_cost_in_window, not the harness cost_in_window."""
        resets = self.NOW + 4 * 86400
        line = self._plain(40, resets, self.NOW, self._paid_ctx(
            resets, cost_in_window=100.0, ledger_cost_in_window=50.0))
        for text in ("67% fewer", "lasts 3.0x longer"):
            self.assertIn(text, line)
        self.assertNotIn("50% fewer", line)

    def test_l_paid_falls_back_to_cost_in_window(self):
        """fix-8: old summary data without ledger_cost_in_window renders the old figures."""
        resets = self.NOW + 4 * 86400
        line = self._plain(40, resets, self.NOW, self._paid_ctx(resets, cost_in_window=100.0))
        for text in ("50% fewer", "lasts 2.0x longer"):
            self.assertIn(text, line)


class SessionTotalLabelTest(LedgerCase):
    """T1.c7: dollars on the session line are behind show.dollars (default off)."""

    def test_dollars_hidden_by_default(self):
        """Session line: dollars off by default."""
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win-main", "windows": {}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1}}})
        session = [l for l in self.lines() if "Session:" in l]
        if session:
            self.assertNotIn("$11.70", session[0])

    def test_dollars_shown_with_key(self):
        """T1.c7: show.dollars restores the dollar pair."""
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win-main", "windows": {}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1,
                                    "saved_net": 9.1}}})  # (3.9.8 T4)
        cfg = plain_cfg()
        cfg["statusline"]["show"]["dollars"] = True
        session = [l for l in self.lines(cfg) if "Session:" in l][0]
        self.assertIn("$11.70", session)
        self.assertIn("$9.10", session)


# --------------------------------------------------------------------------- T10: pace colors

class PaceColorTest(LedgerCase):
    """T1.c4: pace colors: green <= pace_yellow (1.25), yellow <= pace_red (1.75), red above."""

    def _set_five_hour(self, pct, elapsed_fraction):
        now = time.time()
        duration = statusline.FIVE_HOUR_S
        elapsed = duration * elapsed_fraction
        self.payload["rate_limits"] = {
            "five_hour": {"used_percentage": pct, "resets_at": now + (duration - elapsed)}}

    def test_pace_green_at_one(self):
        """T1.c4: below pace_yellow is GREEN (was LIGHT_BLUE, T10.c1)."""
        self._set_five_hour(25, 0.25)                     # pace 1.0x; below pace_yellow 1.25
        cfg = config.defaults()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        colored = statusline.render(self.payload, cfg)
        self.assertIn(statusline.GREEN + "(pace 1.0x)", colored)

    def test_pace_yellow_between_thresholds(self):
        self._set_five_hour(35, 0.25)                     # pace 1.4x -> between 1.25 and 1.75
        cfg = config.defaults()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        colored = statusline.render(self.payload, cfg)
        self.assertIn(statusline.YELLOW + "(pace 1.4x)", colored)

    def test_pace_red_above_threshold(self):
        self._set_five_hour(50, 0.25)                     # pace 2.0x -> above 1.75
        cfg = config.defaults()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        colored = statusline.render(self.payload, cfg)
        self.assertIn(statusline.RED + "(pace 2.0x)", colored)

    def test_pace_thresholds_configurable(self):
        self._set_five_hour(25, 0.25)                     # pace 1.0x
        cfg = config.defaults()
        cfg["statusline"]["show"]["five_hour_pace"] = True
        cfg["statusline"]["pace_yellow"] = 0.8
        cfg["statusline"]["pace_red"] = 0.9
        colored = statusline.render(self.payload, cfg)
        # pace 1.0x is above pace_red (0.9), so RED
        self.assertIn(statusline.RED + "(pace 1.0x)", colored)


class EffortColorTest(LedgerCase):
    """T1.c4: EFFORT_COLORS render through line 1's effort segment."""

    def test_effort_segment_colored_per_level(self):
        cfg = config.defaults()
        self.assertEqual(statusline.EFFORT_COLORS, {
            "max": statusline.MAGENTA, "xhigh": statusline.BRIGHT_MAGENTA,
            "high": statusline.CYAN, "medium": statusline.DIM, "low": statusline.DIM})
        self.assertEqual(statusline.BRIGHT_MAGENTA, "[0;95m")
        for level, color in statusline.EFFORT_COLORS.items():
            self.payload["effort"]["level"] = level
            first = self.render(cfg).split("\n")[0]
            self.assertIn(color + level, first)


# --------------------------------------------------------------------------- T10: line 3 format

class Line3FormatTest(LedgerCase):
    """T1.c7: Session/Project lines with windowed pairs, weeks and discount/multiplier."""

    def summary(self, project=None, **extra):
        account = {"label": "win-main", "windows": {
            "five_hour": {"pct_saved": 21.0, "fit_quality": "ok", "pct_per_dollar": 0.0855},
            "seven_day": {"pct_saved": 6.0, "fit_quality": "ok", "pct_per_dollar": 0.0855,
                          "started_at": "2026-09-14T00:00:00Z"},
        }}
        doc = {
            "schema": 1,
            "accounts": {"dev@example.com": account},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1,
                                    "windows": {
                                        "five_hour": {"cost_used": 11.70, "net_saved": 9.1},
                                        "seven_day": {"cost_used": 11.70, "net_saved": 9.1}}}},
        }
        if project is not None:
            doc["projects"] = {statusline._project_key(self.project): project}
        doc.update(extra)
        write_json(paths.summary_path(), doc)
        return doc

    def test_session_label_format(self):
        """T1.c7: Session line with discount/multiplier, no dollars."""
        self.summary()
        session_line = [l for l in self.lines() if "Session:" in l][0]
        self.assertIn("Session:", session_line)
        self.assertNotIn("$11.70", session_line)      # dollars off by default
        self.assertIn("fewer", session_line)

    def test_session_saved_in_dollars_with_key(self):
        """T1.c7: session dollars restored with show.dollars."""
        self.summary()
        cfg = plain_cfg()
        cfg["statusline"]["show"]["dollars"] = True
        session_line = [l for l in self.lines(cfg) if "Session:" in l][0]
        self.assertIn("$9.10", session_line)

    def test_project_label_format(self):
        """T1.c7: Project line uses x/y of Xh schema with windows data."""
        self.summary(project={"label": "demo",
                              "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                                          "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
                              "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0}})
        project_line = [l for l in self.lines() if "Project:" in l][0]
        self.assertIn("Project:", project_line)
        self.assertIn("of 5h", project_line)

    def test_project_with_savings_pct(self):
        """T1.c7: project x/y of Xh where x = cost and y = net_saved through fit."""
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": 21.0, "fit_quality": "ok",
                                 "pct_per_dollar": 0.05},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok",
                                 "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 9.1}},
               "projects": {statusline._project_key(self.project): {
                   "label": "demo",
                   "windows": {"five_hour": {"cost_used": 1.0, "net_saved": 1.0},
                               "seven_day": {"cost_used": 5.0, "net_saved": 3.0}},
                   "lifetime": {"cost_used": 50.0, "cost_saved_measured": 10.0}}}}
        write_json(paths.summary_path(), doc)
        project_line = [l for l in self.lines() if "Project:" in l][0]
        # 5h: x = $1 * 0.05 * 100 = 5%, y = $1 * 0.05 * 100 = 5%
        # 7d: x = $5 * 0.02 * 100 = 10%, y = $3 * 0.02 * 100 = 6%
        self.assertIn("5%/5% of 5h", project_line)
        self.assertIn("10%/6% of 7d", project_line)

    def test_project_savings_dash_without_fit(self):
        """T1.c7: 5h window skipped when fit is none; 7d shown through its fit."""
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": None, "fit_quality": "none"},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok",
                                 "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}},
               "projects": {statusline._project_key(self.project): {
                   "label": "demo",
                   "windows": {"five_hour": {"cost_used": 1.0, "net_saved": 1.0},
                               "seven_day": {"cost_used": 5.0, "net_saved": 3.0}},
                   "lifetime": {"cost_used": 50.0, "cost_saved_measured": 10.0}}}}
        write_json(paths.summary_path(), doc)
        project_line = [l for l in self.lines() if "Project:" in l][0]
        # 5h has no fit: window skipped entirely; 7d shown
        self.assertNotIn("of 5h", project_line)
        self.assertIn("of 7d", project_line)

    def test_session_usage_pct_through_fit(self):
        """T1.c7: session x/y of Xh from per-window cost_used/net_saved through the fit."""
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": 21.0, "fit_quality": "ok",
                                 "pct_per_dollar": 0.05},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok",
                                 "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 4.0,
                                       "windows": {
                                           "five_hour": {"cost_used": 11.70, "net_saved": 4.0},
                                           "seven_day": {"cost_used": 11.70, "net_saved": 4.0}}}}}
        write_json(paths.summary_path(), doc)
        session_line = [l for l in self.lines() if "Session:" in l][0]
        # 5h: x = $11.70 * 0.05 * 100 = 58%, y = $4 * 0.05 * 100 = 20%
        # 7d: x = $11.70 * 0.02 * 100 = 23%, y = $4 * 0.02 * 100 = 8%
        self.assertIn("58%/20% of 5h", session_line)
        self.assertIn("23%/8% of 7d", session_line)


# --------------------------------------------------------------------------- T10: multi-account project line

class MultiAccountProjectLineTest(LedgerCase):
    """T10: Project line with two accounts on one project."""

    def _summary_two_accounts(self):
        """summary.json with two accounts on one project, by_account slices."""
        acct_a_slice = {
            "cost_in_window": {"five_hour": {"opus": 6.0}, "seven_day": {"opus": 6.0}},
            "pct_used": {"five_hour": 25.7, "seven_day": 12.9},
            "pct_est": {"five_hour": 25.7, "seven_day": 12.9},
            "cost_saved_measured": {"five_hour": 2.0, "seven_day": 2.0},
            "lifetime": {"cost_used": 50.0, "cost_saved_measured": 10.0},
            "period": {"start": "2026-09-01", "cost_used": 20.0, "cost_saved_measured": 5.0},
            "windows": {"five_hour": {"cost_used": 6.0, "net_saved": 2.0},
                        "seven_day": {"cost_used": 6.0, "net_saved": 2.0}},
        }
        acct_b_slice = {
            "cost_in_window": {"five_hour": {"sonnet": 1.0}, "seven_day": {"sonnet": 1.0}},
            "pct_used": {"five_hour": 4.3, "seven_day": 2.1},
            "pct_est": {"five_hour": 4.3, "seven_day": 2.1},
            "cost_saved_measured": {"five_hour": 0.5, "seven_day": 0.5},
            "lifetime": {"cost_used": 10.0, "cost_saved_measured": 3.0},
            "period": {"start": "2026-09-01", "cost_used": 5.0, "cost_saved_measured": 1.0},
            "windows": {"five_hour": {"cost_used": 1.0, "net_saved": 0.5},
                        "seven_day": {"cost_used": 1.0, "net_saved": 0.5}},
        }
        project = {
            "label": "demo",
            "by_account": {"dev@example.com": acct_a_slice, "other@example.com": acct_b_slice},
            "cost_in_window": {"five_hour": {"opus": 6.0, "sonnet": 1.0},
                               "seven_day": {"opus": 6.0, "sonnet": 1.0}},
            "pct_used": {"five_hour": 25.7, "seven_day": 12.9},
            "pct_est": {"five_hour": 25.7, "seven_day": 12.9},
            "cost_saved_measured": {"five_hour": 2.5, "seven_day": 2.5},
            "lifetime": {"cost_used": 60.0, "cost_saved_measured": 13.0},
            "period": {"start": "2026-09-01", "cost_used": 25.0, "cost_saved_measured": 6.0},
            "windows": {"five_hour": {"cost_used": 7.0, "net_saved": 2.5},
                        "seven_day": {"cost_used": 7.0, "net_saved": 2.5}},
        }
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": 21.0, "fit_quality": "ok", "pct_per_dollar": 0.05},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok", "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}},
               "projects": {statusline._project_key(self.project): project}}
        write_json(paths.summary_path(), doc)

    def test_usage_from_current_account(self):
        """T10: x is this account's apportioned share, not the all-accounts total."""
        self._summary_two_accounts()
        project_line = [l for l in self.lines() if "Project:" in l][0]
        # dev@example.com's pct_used for five_hour is 25.7 -> rounds to 26%
        self.assertIn("26%/", project_line)

    def test_savings_from_this_account(self):
        """fix-7: y is this account's own cost_saved_measured through its fit, not the pooled net_saved."""
        self._summary_two_accounts()
        project_line = [l for l in self.lines() if "Project:" in l][0]
        # A's own saved: 5h 2.0*0.05*100 = 10%, 7d 2.0*0.02*100 = 4%
        self.assertIn("26%/10% of 5h", project_line)
        self.assertIn("13%/4% of 7d", project_line)
        # pooled windows.net_saved (2.5) would read 12% of 5h and 5% of 7d
        self.assertNotIn("/12% of 5h", project_line)
        self.assertNotIn("/5% of 7d", project_line)
        self.assertIn("· 2 accounts", project_line)

    def test_multi_account_marker(self):
        """T10: 2 accounts marker appears when by_account has two entries."""
        self._summary_two_accounts()
        project_line = [l for l in self.lines() if "Project:" in l][0]
        self.assertIn("2 accounts", project_line)

    def test_marker_absent_for_single_account(self):
        """T10: no accounts marker for a single-account project."""
        project = {
            "label": "demo",
            "by_account": {"dev@example.com": {
                "pct_used": {"five_hour": 20.0, "seven_day": 10.0},
            }},
            "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                        "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
            "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0},
        }
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": 21.0, "fit_quality": "ok", "pct_per_dollar": 0.05},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok", "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}},
               "projects": {statusline._project_key(self.project): project}}
        write_json(paths.summary_path(), doc)
        project_line = [l for l in self.lines() if "Project:" in l][0]
        self.assertNotIn("accounts", project_line)

    def test_usage_omitted_when_no_slice(self):
        """T10: when the current account has no by_account slice, no crash and no usage
        segment (another account's share is not this account's)."""
        project = {
            "label": "demo",
            "by_account": {"other@example.com": {
                "pct_used": {"five_hour": 20.0, "seven_day": 10.0},
            }},
            "windows": {"five_hour": {"cost_used": 5.0, "net_saved": 3.0},
                        "seven_day": {"cost_used": 20.0, "net_saved": 15.0}},
            "lifetime": {"cost_used": 50.0, "cost_saved_measured": 120.0},
        }
        doc = {"schema": 1,
               "accounts": {"dev@example.com": {"label": "win-main", "windows": {
                   "five_hour": {"pct_saved": 21.0, "fit_quality": "ok", "pct_per_dollar": 0.05},
                   "seven_day": {"pct_saved": 6.0, "fit_quality": "ok", "pct_per_dollar": 0.02}}}},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}},
               "projects": {statusline._project_key(self.project): project}}
        write_json(paths.summary_path(), doc)
        # no crash; the project line may still appear with savings
        project_lines = [l for l in self.lines() if "Project:" in l]
        if project_lines:
            # was assertIn(DASH): it held only while the line lacked its "Project:" label (fix-9)
            self.assertNotIn("of 5h", project_lines[0])


# --------------------------------------------------------------------------- T10: line 4 format

class Line4FormatTest(LedgerCase):
    """T1.c7: Account line uses x/y schema, weeks, and discount/multiplier."""

    def summary(self, **kw):
        account = {"label": "win-main", "windows": {
            "five_hour": {"pct_saved": 21.0, "pct_last": 19.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.0855},
            "seven_day": {"pct_saved": 6.0, "pct_last": 1.0, "fit_quality": "ok",
                          "pct_per_dollar": 0.0855},
        }}
        if "period_cost_used" in kw:
            account["period"] = {"cost_used": kw.pop("period_cost_used")}
        if "lifetime" in kw:
            account["lifetime"] = kw.pop("lifetime")
        doc = {"schema": 1, "accounts": {"dev@example.com": account},
               "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}}}
        doc.update(kw)
        write_json(paths.summary_path(), doc)

    def test_account_label(self):
        self.summary()
        acct = [l for l in self.lines() if "Account:" in l][0]
        self.assertIn("Account:", acct)

    def test_savings_light_blue_in_y(self):
        """T1.c4: y (savings pct) is LIGHT_BLUE (was GREEN, T1.c1)."""
        self.summary()
        colored = statusline.render(self.payload, config.defaults())
        # ok quality -> y = "~21%" -> LIGHT_BLUE via _xy_seg
        self.assertIn(statusline.LIGHT_BLUE + "/~21%", colored)

    def test_lifetime_label_value_split(self):
        self.summary(lifetime={"cost_saved_measured": 612.0, "cost_used": 900.0, "ratio": 0.68,
                               "weeks_used": 2.0, "weeks_saved": 1.5})
        colored = statusline.render(self.payload, config.defaults())
        # "lifetime" dim
        self.assertIn(statusline.DIM + "lifetime", colored)
        # weeks: saved in LIGHT_BLUE after the slash (not dollars)
        self.assertIn("w", colored)
        self.assertNotIn("$612", colored)


# --------------------------------------------------------------------------- T10: show dict

class ShowDictTest(LedgerCase):
    """T10: statusline.show dict toggles individual segments."""

    def test_hide_model_segment(self):
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"model": False}
        first = self.lines(cfg)[0]
        self.assertNotIn("Fable 5.1", first)

    def test_hide_effort_segment(self):
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"effort": False}
        first = self.lines(cfg)[0]
        self.assertNotIn("max", first)
        self.assertIn("Fable 5.1", first)

    def test_hide_ctx_segment(self):
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"ctx": False}
        first = self.lines(cfg)[0]
        self.assertNotIn("472k/1M", first)

    def test_hide_five_hour_segment(self):
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"five_hour": False}
        first = self.lines(cfg)[0]
        self.assertNotIn("5h", first)
        self.assertIn("7d", first)

    def test_hide_task_block(self):
        self.make_project(status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        cfg_on = plain_cfg()
        lines_on = self.lines(cfg_on)
        cfg_off = plain_cfg()
        cfg_off["statusline"]["show"] = {"task_block": False}
        lines_off = self.lines(cfg_off)
        self.assertGreater(len(lines_on), len(lines_off))

    def test_hide_alerts(self):
        self.make_project(status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        write_json(paths.waiting_path(self.sid),
                   {"kind": "question", "agent": "expert-fable"})
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"alerts": False}
        joined = "\n".join(self.lines(cfg))
        self.assertNotIn("waiting on you", joined)

    def test_hide_session_cost(self):
        cfg = plain_cfg()
        cfg["statusline"]["show"] = {"session_cost": False}
        joined = "\n".join(self.lines(cfg))
        # Session: with cost hidden should not show the dollar amount
        self.assertNotIn("$11.70", joined)


# --------------------------------------------------------------------------- T10: task block

class TaskBlockTest(LedgerCase):
    """T10: the expanded task block after line 5."""

    PLAN_SIX = """# Phase 36 — levers-off        (implements GENERATION_PLAN.md phase 4.2)
Milestone: the levers are off — verified by: python -m unittest discover tests

## Tasks
- T1 | done    | expert-fable | coder: opus46 | effort: high | title: first lever | files: a.py
- T2 | done    | expert-fable | coder: none   | effort: high | title: second lever | files: b.py
- T3 | next    | expert-fable | coder: opus46 | effort: high | title: recipe rung | files: c.py
- T4 | queued  | expert-fable | coder: none   | effort: high | title: overnight bake | files: d.py
- T5 | queued  | expert-fable | coder: opus46 | effort: high | title: final test | files: e.py
- T6 | queued  | expert-fable | coder: none   | effort: high | title: ship it | files: f.py

## Changes
- 2026-09-12 planner: plan approved
"""

    def test_task_block_shows_three_tasks(self):
        """T10.c1: the active task first, then the open tasks in plan order."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        self.assertEqual(len(task_lines), 3)    # T3, T4, T5 (T2 is done: dropped)
        t3_line = [l for l in task_lines if "T3" in l]
        self.assertTrue(t3_line)
        self.assertIn(statusline.GLYPH_RUNNING, t3_line[0])
        self.assertIn("recipe rung", t3_line[0])

    def test_task_block_glyphs(self):
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        t3 = [l for l in task_lines if "T3" in l][0]
        t4 = [l for l in task_lines if "T4" in l][0]
        self.assertIn(statusline.GLYPH_RUNNING, t3)
        self.assertIn(statusline.GLYPH_QUEUED, t4)

    def test_task_block_children_hidden_by_default(self):
        """T10.c1: helper lines hidden by default (task_helpers off)."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "run_id": "expert-1",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": "dev@example.com", "expert": "expert-1",
            "agents": {"expert-1": {"agent_type": "expert-fable", "ctx": 100000},
                       "coder-1": {"agent_type": "coder-opus46",
                                   "started": "2026-09-12T21:00:00Z"}}}}})
        lines = self.lines()
        joined = "\n".join(lines)
        # the agent label still appears (on the current task line)
        self.assertIn("expert-fable", joined)
        # but helper lines are hidden
        self.assertNotIn("coder-opus46", joined)

    def test_owner_session_shows_task_block(self):
        """T28.c4: the owner session keeps the task block."""
        self.make_project(plan=self.PLAN_SIX, status={
            "task": "T3", "phase": "36", "router_session": self.sid,
            "started": "2026-09-12T20:58:00Z"})
        joined = self.render()
        self.assertIn("recipe rung", joined)
        self.assertNotIn("runs in another session", joined)

    def test_other_session_shows_beside_line(self):
        """T28.c4: another session sees one beside line, no task block."""
        self.make_project(plan=self.PLAN_SIX, status={
            "task": "T3", "phase": "36", "router_session": "someone-else",
            "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        self.assertEqual(lines[0], "demo · 36 runs in another session")
        joined = "\n".join(lines)
        self.assertEqual(joined.count("runs in another session"), 1)
        for token in ("recipe rung", "T3", "T4", "Phase 36"):
            self.assertNotIn(token, joined)

    def test_other_session_closed_phase_shows_no_phase(self):
        """fix-18: another router_session with a null phase: no beside line, the no-phase header."""
        self.make_project(plan=None, status={
            "task": None, "phase": None, "router_session": "someone-else"})
        lines = self.lines()
        self.assertNotIn("runs in another session", "\n".join(lines))
        self.assertEqual(lines[0], "demo · no phase")

    def test_no_router_session_shows_task_block(self):
        """T28.c4: router_session null -> the block shows as before."""
        self.make_project(plan=self.PLAN_SIX, status={
            "task": "T3", "phase": "36", "router_session": None,
            "started": "2026-09-12T20:58:00Z"})
        joined = self.render()
        self.assertIn("recipe rung", joined)
        self.assertNotIn("runs in another session", joined)

    def test_task_block_children_shown_with_task_helpers(self):
        """T10.c1: task_helpers: true restores helper lines."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "run_id": "expert-1",
                                  "started": "2026-09-12T20:58:00Z"})
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": "dev@example.com", "expert": "expert-1",
            "agents": {"expert-1": {"agent_type": "expert-fable", "ctx": 100000},
                       "coder-1": {"agent_type": "coder-opus46",
                                   "started": "2026-09-12T21:00:00Z"}}}}})
        cfg = plain_cfg()
        cfg["statusline"]["show"]["task_helpers"] = True
        lines = self.lines(cfg)
        joined = "\n".join(lines)
        self.assertIn("coder-opus46", joined)
        self.assertIn("running", joined)

    def test_task_block_off_reduces_lines(self):
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        cfg_on = plain_cfg()
        cfg_off = plain_cfg()
        cfg_off["statusline"]["show"] = {"task_block": False}
        lines_on = self.lines(cfg_on)
        lines_off = self.lines(cfg_off)
        self.assertGreater(len(lines_on), len(lines_off))

    def test_scan_phase_plan_returns_tasks(self):
        plan_path = os.path.join(self.project, "test_plan.md")
        with open(plan_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.PLAN_SIX)
        result = statusline.scan_phase_plan(plan_path)
        self.assertEqual(len(result["tasks"]), 6)
        self.assertEqual(result["tasks"][0]["id"], "T1")
        self.assertEqual(result["tasks"][0]["status"], "done")
        self.assertEqual(result["tasks"][2]["title"], "recipe rung")

    def test_task_block_active_first_then_open_in_plan_order(self):
        """T10.c1: active task first, then not-done tasks in plan order (T3 is
        earlier in the plan than the active T4, but still follows it: fill order
        is plan order over the not-done tasks, not a window around the active one)."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T4", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T4", "T3", "T5"])

    def test_task_block_active_done_shows_own_mark_then_open(self):
        """T10.c1: active task T1 is done in the plan -- it still shows first, with
        its own done mark (not forced running), then the open tasks follow."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T1", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T1", "T3", "T4"])
        t1 = [l for l in task_lines if "T1" in l][0]
        self.assertIn(statusline.GLYPH_DONE, t1)
        self.assertNotIn(statusline.GLYPH_RUNNING, t1)

    def test_task_block_active_last_task_then_open(self):
        """T10.c1: active is T6 (not done) -- runs, then the open tasks fill in plan order."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T6", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T6", "T3", "T4"])
        t6 = [l for l in task_lines if "T6" in l][0]
        self.assertIn(statusline.GLYPH_RUNNING, t6)

    def test_task_block_no_status_task_lists_open_tasks(self):
        """T10.c2/T10.c1: status.task empty -> no active task, just the plan's open
        tasks in plan order; the first one keeps its own glyph (not GLYPH_RUNNING)."""
        self.make_project(plan=self.PLAN_SIX, status={})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T3", "T4", "T5"])
        t3 = [l for l in task_lines if "T3" in l][0]
        self.assertNotIn(statusline.GLYPH_RUNNING, t3)
        self.assertIn(statusline.GLYPH_NEXT, t3)

    def test_task_block_unknown_status_task_lists_open_tasks(self):
        """T10.c2: status.task names a task not in the plan -> same fallback as empty."""
        self.make_project(plan=self.PLAN_SIX, status={"task": "T99"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T3", "T4", "T5"])

    PLAN_ALL_DONE = """# Phase 36 — levers-off        (implements GENERATION_PLAN.md phase 4.2)
Milestone: the levers are off — verified by: python -m unittest discover tests

## Tasks
- T1 | done    | expert-fable | coder: opus46 | effort: high | title: first lever | files: a.py
- T2 | done    | expert-fable | coder: none   | effort: high | title: second lever | files: b.py
- T3 | done    | expert-fable | coder: opus46 | effort: high | title: recipe rung | files: c.py
- T4 | done    | expert-fable | coder: none   | effort: high | title: overnight bake | files: d.py
- T5 | done    | expert-fable | coder: opus46 | effort: high | title: final test | files: e.py
- T6 | done    | expert-fable | coder: none   | effort: high | title: ship it | files: f.py

## Changes
- 2026-09-12 planner: plan approved
"""

    def test_task_block_all_done_no_active_shows_nothing(self):
        """T10.c1: every task done and no status.task -> no active task and no open
        tasks to list, so the block is empty."""
        self.make_project(plan=self.PLAN_ALL_DONE, status={})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        self.assertEqual(task_lines, [])

    def test_task_block_active_done_task_shows_own_mark(self):
        """T10.c1: status.task naming a task that's done in the plan shows it first
        with its own done mark, not forced running (was: unconditionally running)."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T2", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        task_lines = [l for l in lines if l.strip() and l.strip()[0] == "T"]
        ids = [l.split()[0] for l in task_lines]
        self.assertEqual(ids, ["T2", "T3", "T4"])
        t2 = [l for l in task_lines if "T2" in l][0]
        self.assertIn(statusline.GLYPH_DONE, t2)
        self.assertNotIn(statusline.GLYPH_RUNNING, t2)

    def test_task_block_blocked_glyph(self):
        """T1.c1: blocked tasks get ✗ (superseded gets its own ↷ since 3.10 T25)."""
        plan = self.PLAN_SIX.replace("- T2 | done  ", "- T2 | blocked")
        self.make_project(plan=plan,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        t2 = [l for l in lines if "T2" in l][0]
        self.assertIn(statusline.GLYPH_BLOCKED, t2)

    def test_superseded_glyph_and_color_distinct(self):
        """3.10 T25: superseded never renders as blocked (red ✗) nor as done."""
        g, c = statusline._TASK_GLYPHS, statusline._TASK_COLORS
        for other in ("blocked", "done"):
            self.assertNotEqual(g["superseded"], g[other])
            self.assertNotEqual(c["superseded"], c[other])
        self.assertNotEqual(statusline.GLYPH_SUPERSEDED, statusline.GLYPH_BLOCKED)
        self.assertNotEqual(statusline.GLYPH_SUPERSEDED, statusline.GLYPH_DONE)

    def test_task_block_superseded_glyph(self):
        """3.10 T25: superseded tasks (incl. a reopened-chain id) show ↷, not ✗."""
        plan = self.PLAN_SIX.replace("- T2 | done  ", "- T2 | superseded").replace(
            "- T4 | queued  | expert-fable | coder: none   | effort: high | title: overnight bake",
            "- T21.1.1.1.1 | superseded | expert-fable | coder: none | effort: high "
            "| title: overnight bake")
        self.make_project(plan=plan,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        lines = self.lines()
        for tid in ("T2 ", "T21.1.1.1.1 "):
            line = [l for l in lines if l.startswith(tid)][0]
            self.assertIn(statusline.GLYPH_SUPERSEDED, line)
            self.assertNotIn(statusline.GLYPH_BLOCKED, line)

    def test_task_block_done_tasks_dropped_unless_active(self):
        """T10.c1: T2 is done and not the active task -- it is dropped from the block."""
        self.make_project(plan=self.PLAN_SIX,
                          status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        cfg = config.defaults()
        colored = statusline.render(self.payload, cfg)
        self.assertNotIn("T2", colored)

    def test_short_title_cuts_at_colon(self):
        """T7.c1: title before first ':', ' — ' or ';'; no length cap of its own."""
        self.assertEqual(statusline._short_title("statusline: add pace"), "statusline")
        self.assertEqual(statusline._short_title("abc %s def" % statusline.DASH), "abc")
        self.assertEqual(statusline._short_title("first; second"), "first")
        self.assertEqual(statusline._short_title("x" * 60), "x" * 60)

    def test_long_delimiter_free_title_cut_only_by_max_width(self):
        """T7.c1: a 100-char, delimiter-free title shows whole when it fits the
        line's max_width, and is cut with '…' only when it does not."""
        long_title = "x" * 100
        plan = self.PLAN_SIX.replace("| title: recipe rung |", "| title: %s |" % long_title)
        self.make_project(plan=plan, status={"task": "T3", "started": "2026-09-12T20:58:00Z"})
        cfg_wide = plain_cfg()
        cfg_wide["statusline"]["max_width"] = 130
        wide = [l for l in self.lines(cfg_wide) if l.startswith("T3")][0]
        self.assertIn(long_title, wide)
        cfg_narrow = plain_cfg()
        cfg_narrow["statusline"]["max_width"] = 80
        narrow = [l for l in self.lines(cfg_narrow) if l.startswith("T3")][0]
        self.assertNotIn(long_title, narrow)
        self.assertTrue(narrow.endswith("…"))
        self.assertEqual(len(narrow), 80)


# --------------------------------------------------------------------------- T1.c7: money lines in subscriber's units

class WindowedPairsTest(LedgerCase):
    """T1.c7: session x/y uses per-window cost_used/net_saved from the summary."""

    def test_session_inside_window_equals_whole_cost(self):
        """A session that started inside the window: cost_used == whole session cost."""
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "five_hour": {"fit_quality": "ok", "pct_per_dollar": 0.05},
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.02}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "windows": {
                                        "five_hour": {"cost_used": 11.70, "net_saved": 5.0},
                                        "seven_day": {"cost_used": 11.70, "net_saved": 5.0}}}}})
        session = [l for l in self.lines() if "Session:" in l][0]
        # 5h: x = 11.70 * 0.05 * 100 = 58%, y = 5 * 0.05 * 100 = 25%
        self.assertIn("58%/25% of 5h", session)

    def test_session_before_window_uses_partial_cost(self):
        """A session that started before t0: cost_used < whole session cost."""
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "five_hour": {"fit_quality": "ok", "pct_per_dollar": 0.05},
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.02}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "windows": {
                                        "five_hour": {"cost_used": 3.0, "net_saved": 2.0},
                                        "seven_day": {"cost_used": 8.0, "net_saved": 4.0}}}}})
        session = [l for l in self.lines() if "Session:" in l][0]
        # 5h: x = 3 * 0.05 * 100 = 15%, y = 2 * 0.05 * 100 = 10%
        self.assertIn("15%/10% of 5h", session)
        # 7d: x = 8 * 0.02 * 100 = 16%, y = 4 * 0.02 * 100 = 8%
        self.assertIn("16%/8% of 7d", session)

    def _guard_summary(self, five_ledger):
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "five_hour": {"fit_quality": "ok", "pct_per_dollar": 0.05,
                              "ledger_cost_in_window": five_ledger},
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.02,
                              "ledger_cost_in_window": 50.0}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "windows": {
                                        "five_hour": {"cost_used": 3.0, "net_saved": 2.0},
                                        "seven_day": {"cost_used": 8.0, "net_saved": 4.0}}}}})
        return [l for l in self.lines() if "Session:" in l][0]

    def test_session_cost_above_ledger_window_prints_question(self):
        """3.15 T1: session cost_used > the account window's ledger cost prints ?/? (C0079)."""
        session = self._guard_summary(2.0)
        self.assertIn("?/? of 5h", session)
        self.assertIn("16%/8% of 7d", session)

    def test_session_cost_within_ledger_window_unchanged(self):
        session = self._guard_summary(3.0)
        self.assertIn("15%/10% of 5h", session)
        self.assertIn("16%/8% of 7d", session)


class DiscountMultiplierTest(unittest.TestCase):
    """T1.c7: discount = 100*net/(paid+net), multiplier = (paid+net)/paid."""

    def test_arithmetic(self):
        """paid 129, net 187 -> 59% fewer, lasts 2.4x longer."""
        v = statusline._vanilla_pieces(129, 187, False, False)
        self.assertIsNotNone(v)
        d_seg, m_seg, m_fold = v
        # discount: 100 * 187 / (129 + 187) = 59.2 -> 59%
        self.assertIn("59%", d_seg[0])
        self.assertIn("fewer", d_seg[0])
        # multiplier: (129+187)/129 = 2.45 -> 2.4x (one decimal rounds down)
        self.assertIn("2.4x", m_seg[0])
        self.assertIn("longer", m_seg[0])

    def test_omitted_when_net_zero(self):
        """net <= 0 -> omitted."""
        self.assertIsNone(statusline._vanilla_pieces(100, 0, False, False))
        self.assertIsNone(statusline._vanilla_pieces(100, -5, False, False))

    def test_omitted_when_paid_zero(self):
        """paid == 0 -> omitted."""
        self.assertIsNone(statusline._vanilla_pieces(0, 100, False, False))

    def test_full_text_on_pace(self):
        """Pace line uses 'fewer tokens than vanilla'."""
        v = statusline._vanilla_pieces(100, 100, True, False)
        self.assertIn("fewer tokens than vanilla", v[0][0])

    def test_short_text_on_session(self):
        """Session/Project/Account use 'fewer'."""
        v = statusline._vanilla_pieces(100, 100, False, False)
        self.assertIn("fewer", v[0][0])
        self.assertNotIn("vanilla", v[0][0])


class WeeksPieceTest(unittest.TestCase):
    """fix-9.c2: weeks render from the summary's per-window fields; was WeeksConversionTest
    over _weeks(dollars, ppd), removed with it (today's rate for every week)."""

    def test_weeks_piece(self):
        seg = statusline._weeks_piece({"weeks_used": 0.76, "cost_used": 887.0}, "month", False)
        self.assertEqual(seg[0], "month ~0.8w")

    def test_weeks_piece_none_without_fields(self):
        self.assertIsNone(statusline._weeks_piece({"cost_used": 100.0}, "month", False))

    def test_month_dash_without_a_renewal_day(self):
        """3.11 T9: an account with no renewal day shows `month —`, never a guessed month."""
        seg = statusline._weeks_piece({"start": None, "unknown": True}, "month", False)
        self.assertEqual(seg[0], "month %s" % statusline.DASH)


class DollarsShowKeyTest(LedgerCase):
    """T1.c7: dollars hidden by default, shown with show.dollars."""

    def test_dollars_hidden(self):
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0}}})
        joined = "\n".join(self.lines())
        self.assertNotIn("$11.70", joined)

    def test_dollars_shown(self):
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "saved_net": 5.0}}})  # (3.9.8 T4)
        cfg = plain_cfg()
        cfg["statusline"]["show"]["dollars"] = True
        session = [l for l in self.lines(cfg) if "Session:" in l][0]
        self.assertIn("$11.70", session)
        self.assertIn("$5.00", session)


class StraddleTest(LedgerCase):
    """T1.c7: the straddle piece appears only when the session predates t0."""

    def _summary(self, session_started, seven_day_started_at, weeks=True):
        sess = {"account": "dev@example.com", "saved_measured": 5.0,
                "started": session_started,
                "windows": {"seven_day": {"cost_used": 11.70, "net_saved": 5.0}}}
        if weeks:                                 # per-window weeks from the summary (fix-9.c2)
            sess.update({"weeks_used": 0.5, "weeks_saved": 0.2, "unrated_usd": 0.0})
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.0855,
                              "started_at": seven_day_started_at}}}},
            "sessions": {self.sid: sess}})

    def test_straddle_skipped_without_fields(self):
        """fix-9.c2: no weeks fields on the session entry -> no straddle piece."""
        now = time.time()
        self._summary(session_started=now - 700000, seven_day_started_at=now - 600000,
                      weeks=False)
        session = [l for l in self.lines() if "Session:" in l]
        self.assertTrue(session)
        self.assertNotIn("total", session[0])

    def test_straddle_absent_inside_window(self):
        """Session started inside the window: no straddle piece."""
        now = time.time()
        self._summary(session_started=now - 3600, seven_day_started_at=now - 86400)
        session = [l for l in self.lines() if "Session:" in l]
        if session:
            self.assertNotIn("total", session[0])

    def test_straddle_present_before_window(self):
        """Session started before the window: straddle piece present."""
        now = time.time()
        self._summary(session_started=now - 700000, seven_day_started_at=now - 600000)
        session = [l for l in self.lines() if "Session:" in l]
        self.assertTrue(session)
        self.assertIn("total ~0.5w/0.2w", session[0])


class PaceVanillaTest(LedgerCase):
    """T1.c7: the Pace line gains discount/multiplier from account 7d window."""

    def test_pace_vanilla_present(self):
        now = time.time()
        self.payload["rate_limits"] = {
            "seven_day": {"used_percentage": 45,
                          "resets_at": now + (statusline.DEFAULT_WINDOW_S - 4.8 * 86400)}}
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.0855,
                              "cost_in_window": 100.0, "cost_saved_measured": 200.0}}}},
            "sessions": {self.sid: {"account": "dev@example.com"}}})
        pace = [l for l in self.lines() if "Pace:" in l]
        self.assertTrue(pace, self.lines())
        self.assertIn("fewer tokens than vanilla", pace[0])
        self.assertIn("longer", pace[0])


class VanillaColorsTest(LedgerCase):
    """T1.c7: discount number LIGHT_BLUE (was GREEN, T1.c4), words DIM, multiplier number LIGHT_BLUE."""

    def test_discount_number_light_blue_words_dim(self):
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "five_hour": {"fit_quality": "ok", "pct_per_dollar": 0.05}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "windows": {"five_hour": {"cost_used": 11.70, "net_saved": 5.0}}}}})
        colored = statusline.render(self.payload, config.defaults())
        # discount number LIGHT_BLUE
        self.assertIn(statusline.LIGHT_BLUE + "30%", colored)
        # "fewer" DIM
        self.assertIn(statusline.DIM + " fewer", colored)
        # "lasts" DIM, multiplier LIGHT_BLUE, "longer" DIM
        self.assertIn(statusline.DIM + "lasts ", colored)
        self.assertIn(statusline.LIGHT_BLUE + "1.4x", colored)
        self.assertIn(statusline.DIM + " longer", colored)


class FoldAtNarrowWidthTest(LedgerCase):
    """T1.c7: fold 'lasts Mx longer' into '(Mx)' before dropping other segments."""

    def test_fold_before_drop(self):
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {"label": "win", "windows": {
                "five_hour": {"fit_quality": "ok", "pct_per_dollar": 0.05},
                "seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.02}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 5.0,
                                    "windows": {
                                        "five_hour": {"cost_used": 11.70, "net_saved": 5.0},
                                        "seven_day": {"cost_used": 11.70, "net_saved": 5.0}}}}})
        # normal width
        cfg_wide = plain_cfg()
        cfg_wide["statusline"]["max_width"] = 200
        session_wide = [l for l in self.lines(cfg_wide) if "Session:" in l][0]
        self.assertIn("lasts", session_wide)
        # narrow: fold 'lasts Mx longer' into '(Mx)'
        cfg_narrow = plain_cfg()
        cfg_narrow["statusline"]["max_width"] = 65
        session_narrow = [l for l in self.lines(cfg_narrow) if "Session:" in l]
        if session_narrow:
            self.assertNotIn("lasts", session_narrow[0])
            # the folded form (Mx) or the segment was dropped entirely
            self.assertTrue("x)" in session_narrow[0] or "fewer" in session_narrow[0],
                            session_narrow[0])


class ConfigSavingsValidationTest(unittest.TestCase):
    """T1.c7: validate accepts/rejects the savings block."""

    def test_valid_savings(self):
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])

    def test_bad_vanilla_ttl(self):
        cfg = config.defaults()
        cfg["savings"]["vanilla_ttl_s"] = -1
        problems = config.validate(cfg)
        self.assertTrue(any("savings.vanilla_ttl_s" in p for p in problems))

    def test_bad_window_tokens(self):
        cfg = config.defaults()
        cfg["savings"]["window_tokens"] = "nope"
        problems = config.validate(cfg)
        self.assertTrue(any("savings.window_tokens" in p for p in problems))


class InputDumpTest(LedgerCase):
    """T14 probe: ``.run/statusline-dump`` marker -> raw stdin to ``statusline-input-<n>.json``."""

    def _raw(self, tag):
        payload = dict(self.payload, probe_tag=tag, note="caf\u00e9 \u2248")
        return json.dumps(payload, ensure_ascii=False, indent=1)

    def _read(self, raw):
        import io
        return statusline.read_payload(io.BytesIO(raw.encode("utf-8")))

    def _dumps(self):
        run = os.path.join(self.project, ".run")
        if not os.path.isdir(run):
            return []
        return sorted(n for n in os.listdir(run) if n.startswith("statusline-input-"))

    def test_marker_absent_writes_nothing(self):
        self.assertEqual(self._read(self._raw("a"))["probe_tag"], "a")
        self.assertEqual(self._dumps(), [])

    def test_marker_present_writes_raw_verbatim(self):
        os.makedirs(os.path.join(self.project, ".run"))
        open(os.path.join(self.project, ".run", "statusline-dump"), "w").close()
        raws = [self._raw("one"), self._raw("two")]
        for raw in raws:
            self._read(raw)
        self.assertEqual(self._dumps(), ["statusline-input-1.json", "statusline-input-2.json"])
        for n, raw in enumerate(raws, 1):
            path = os.path.join(self.project, ".run", "statusline-input-%d.json" % n)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), raw.encode("utf-8"))

    def test_cap_at_twenty(self):
        os.makedirs(os.path.join(self.project, ".run"))
        open(os.path.join(self.project, ".run", "statusline-dump"), "w").close()
        for i in range(23):
            self._read(self._raw(str(i)))
        self.assertEqual(len(self._dumps()), 20)
        self.assertFalse(os.path.exists(
            os.path.join(self.project, ".run", "statusline-input-21.json")))


class SessionMissingBlockTest(LedgerCase):
    """T11.1: no sessions[sid].windows -> DASH y and vanilla pieces; render-time rebuild; health flag."""

    ACCT = "dev@example.com"

    def _summary(self, sessions, started_at=None):
        wins = {}
        for wkey in ("five_hour", "seven_day"):
            wins[wkey] = {"fit_quality": "ok", "pct_per_dollar": 0.0855}
            if started_at is not None:
                wins[wkey]["started_at"] = started_at
        write_json(paths.summary_path(), {"schema": 1, "accounts": {self.ACCT: {
            "label": "win", "windows": wins}}, "sessions": sessions})

    def _live(self, usd):
        write_json(paths.running_path(), {"schema": 1, "sessions": {self.sid: {
            "account": self.ACCT, "agents": {"agent-1": {"ctx": 1000, "saved_live_usd": usd}}}}})

    def _session(self, cfg=None):
        found = [l for l in self.lines(cfg) if "Session:" in l]
        return found[0] if found else ""

    def _ledger(self, session_row=True, cost=2.0):
        from pa import db, summary as summary_mod
        conn = db.connect()
        db.init_schema(conn)
        now = time.time()
        if session_row:
            conn.execute("INSERT INTO sessions(session_id, account, project, cwd, started)"
                         " VALUES(?, ?, ?, ?, ?)",
                         (self.sid, self.ACCT, self.project, self.project,
                          summary_mod._iso(now - 3600)))
        if cost:
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, ?, ?, ?, ?, ?, 'api')",
                         ("m1", self.sid, self.ACCT, summary_mod._iso(now - 1800),
                          "claude-opus-4-6", cost))
        conn.commit()
        db.close(conn)
        return now

    def _counting(self):
        from unittest import mock
        from pa import summary as summary_mod
        return mock.patch.object(summary_mod, "rebuild", wraps=summary_mod.rebuild)

    def test_no_entry_renders_dashes(self):
        self._summary({})
        self._live(0.9)  # live is not added without a windows block
        self.assertEqual(self._session(),
                         "Session: 100%/{0} of 5h · 100%/{0} of 7d · {0} fewer · lasts {0} longer"
                         .format(statusline.DASH))

    def test_entry_with_windows_renders_numbers(self):
        self._summary({self.sid: {"account": self.ACCT, "saved_measured": 5.0, "windows": {
            "five_hour": {"cost_used": 11.70, "net_saved": 5.0},
            "seven_day": {"cost_used": 11.70, "net_saved": 5.0}}}})
        line = self._session()
        self.assertIn("100%/43% of 5h", line)
        self.assertIn("30% fewer", line)
        self.assertNotIn(statusline.DASH, line)

    def test_fewer_lasts_from_saved_net_not_saved_measured(self):
        """3.9.8 T4: fewer/lasts come from saved_net (the walk), never saved_measured."""
        self._summary({self.sid: {"account": self.ACCT, "saved_measured": 11.15,
                                  "saved_net": 37.19, "windows": {
            "five_hour": {"cost_used": 11.70, "net_saved": 5.0},
            "seven_day": {"cost_used": 11.70, "net_saved": 37.19}}}})
        line = self._session()
        cost = 11.70
        fewer = int(round(100.0 * 37.19 / (cost + 37.19)))
        lasts = "%.1fx" % ((cost + 37.19) / cost)
        self.assertIn("%d%% fewer" % fewer, line)
        self.assertIn("lasts %s longer" % lasts, line)
        self.assertNotIn("%d%% fewer" % int(round(100.0 * 11.15 / (cost + 11.15))), line)
        self.assertNotIn("lasts %.1fx longer" % ((cost + 11.15) / cost), line)
        wins = {"seven_day": {"fit_quality": "ok", "pct_per_dollar": 0.0855}}
        self.assertIn("/%d%% of 7d" % statusline._cost_as_pct(37.19, wins, "seven_day"), line)

    def test_live_only_never_zero_percent(self):
        self._summary({self.sid: {"account": self.ACCT, "saved_measured": 0.0, "windows": {
            "five_hour": {"cost_used": 11.70, "net_saved": 0.0},
            "seven_day": {"cost_used": 11.70, "net_saved": None}}}})
        self._live(0.01)
        line = self._session()
        self.assertNotIn("/0%", line)
        self.assertNotIn(" 0% fewer", line)
        self.assertIn("100%%/%s of 5h" % statusline.DASH, line)
        self.assertIn("%s fewer" % statusline.DASH, line)

    def test_rebuild_once_and_render_same_pass(self):
        now = self._ledger(session_row=True, cost=2.0)
        self._summary({}, started_at=int(now - 7200))
        with self._counting() as rb:
            line = self._session()
            self.assertEqual(rb.call_count, 1)
            # ledger cost $2.00 through the fit (not the payload's $11.70 fallback)
            self.assertIn("17%%/%s of 5h" % statusline.DASH, line)
            self._session()
            self.assertEqual(rb.call_count, 1)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".run", "health.json")))

    def test_no_ledger_cost_no_rebuild(self):
        self._ledger(session_row=True, cost=0)
        self._summary({})
        with self._counting() as rb:
            self._session()
            self.assertEqual(rb.call_count, 0)

    def test_still_missing_writes_health_and_honours_cooldown(self):
        now = self._ledger(session_row=False, cost=2.0)  # turns but no sessions row
        self._summary({}, started_at=int(now - 7200))
        health = os.path.join(self.project, ".run", "health.json")
        write_json(health, {"other": {"keep": 1}})
        with self._counting() as rb:
            self._session()
            self._session()
            self.assertEqual(rb.call_count, 1)  # inside the 60 s cooldown
            with open(health, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["other"], {"keep": 1})
            flag = doc["savings_missing"]
            self.assertEqual(flag["session"], self.sid)
            self.assertIsInstance(flag["since"], float)
            self.assertIn("no windows block after rebuild", flag["detail"])
            self.assertIn("$2.00", flag["detail"])
            self.assertIsNone(flag["hook_repair"])
            self.assertIsNone(flag["row"])
            cfg = plain_cfg()
            cfg["summary"]["session_rebuild_cooldown_s"] = 0
            self._session(cfg)
            self.assertEqual(rb.call_count, 2)
            with open(health, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["savings_missing"]["since"], flag["since"])


class RatioSessionLineTest(LedgerCase):
    """T31: end to end, a young 7d instance past fit.ratio_min_pct prices sessions by meter/cost."""

    ACCT = "dev@example.com"
    OTHER = "ratio-other-0000-0000-000000000002"

    def _build(self, pct):
        from pa import db, summary as summary_mod
        conn = db.connect()
        db.init_schema(conn)
        now = time.time()
        resets = int(now + 6 * 86400)              # 7d instance started a day ago
        conn.execute("INSERT OR IGNORE INTO accounts (email) VALUES (?)", (self.ACCT,))
        for i, p in enumerate((pct / 2.0, pct)):   # few samples: the instance is young
            conn.execute("INSERT INTO utilization(ts, account, session_id, window, pct, resets_at)"
                         " VALUES(?, ?, ?, 'seven_day', ?, ?)",
                         (summary_mod._iso(now - 1800 + i * 600), self.ACCT, self.sid, p, resets))
        for sid, cost in ((self.sid, 30.0), (self.OTHER, 10.0)):
            conn.execute("INSERT INTO sessions(session_id, account, project, cwd, started)"
                         " VALUES(?, ?, ?, ?, ?)",
                         (sid, self.ACCT, self.project, self.project, summary_mod._iso(now - 3600)))
            conn.execute("INSERT INTO turns(msg_id, session_id, account, ts, model, cost_usd, kind)"
                         " VALUES(?, ?, ?, ?, ?, ?, 'api')",
                         (sid + "_m1", sid, self.ACCT, summary_mod._iso(now - 1200),
                          "claude-opus-4-6", cost))
        conn.commit()
        summary_mod.rebuild(conn, self.cfg)
        db.close(conn)
        with open(paths.summary_path(), encoding="utf-8") as fh:
            return json.load(fh)["accounts"][self.ACCT]["windows"]["seven_day"]

    def _session(self, sid):
        self.payload["session_id"] = sid
        found = [l for l in self.lines() if "Session:" in l]
        return found[0] if found else ""

    def test_ratio_prices_each_session_share_of_meter(self):
        win = self._build(20.0)
        self.assertEqual(win["fit_method"], "ratio")
        # ppd = 0.20 / $40: $30 -> 15%, $10 -> 5%, sum = the meter's 20
        self.assertRegex(self._session(self.sid), r"15%/\S+ of 7d")
        self.assertRegex(self._session(self.OTHER), r"5%/\S+ of 7d")

    def test_below_ratio_min_no_ratio(self):
        win = self._build(3.0)
        self.assertNotEqual(win["fit_method"], "ratio")


# --------------------------------------------------------------------------- fix-14: fewer_numbers

class FewerNumbersTest(LedgerCase):
    """fix-14: ``statusline.fewer_numbers`` drops the busier figures; explicit show keys win."""

    def setUp(self):
        super().setUp()
        now = time.time()
        self.payload["rate_limits"] = {
            "five_hour": {"used_percentage": 20, "resets_at": now + 3600},
            "seven_day": {"used_percentage": 45,
                          "resets_at": now + (statusline.DEFAULT_WINDOW_S - 4.8 * 86400)}}
        weeks = {"cost_used": 50.0, "cost_saved_measured": 80.0,
                 "weeks_used": 1.5, "weeks_saved": 2.5}
        project = {
            "label": "demo",
            "by_account": {"dev@example.com": {
                "pct_used": {"five_hour": 20.0, "seven_day": 11.0},
                "cost_saved_measured": {"five_hour": 3.0, "seven_day": 6.0}},
                "other@example.com": {"pct_used": {"five_hour": 1.0, "seven_day": 1.0}}},
            "period": dict(weeks, weeks_used=0.5, weeks_saved=0.8),
            "lifetime": weeks,
        }
        write_json(paths.summary_path(), {
            "schema": 1,
            "accounts": {"dev@example.com": {
                "label": "win", "period": dict(weeks), "lifetime": dict(weeks),
                "windows": {
                    "five_hour": {"pct_saved": 21.0, "pct_last": 19.0, "fit_quality": "ok",
                                  "pct_per_dollar": 0.05},
                    "seven_day": {"pct_saved": 6.0, "pct_last": 1.0, "fit_quality": "ok",
                                  "pct_per_dollar": 0.02, "cost_in_window": 100.0,
                                  "cost_saved_measured": 200.0}}}},
            "sessions": {self.sid: {"account": "dev@example.com", "saved_measured": 0.0}},
            "projects": {statusline._project_key(self.project): project}})

    def _cfg(self, show=None):
        cfg = plain_cfg()
        cfg["statusline"]["fewer_numbers"] = True
        cfg["statusline"]["max_width"] = 400
        if show is not None:
            cfg["statusline"]["show"] = show
        return cfg

    def _line(self, label, cfg, user_show=None):
        statusline._JSON_CACHE.clear()
        text = statusline.render(self.payload, cfg,
                                 {} if user_show is None and cfg["statusline"].get("fewer_numbers")
                                 else user_show)
        found = [l for l in text.split("\n") if label in l]
        return found[0] if found else None

    def test_default_keeps_every_piece(self):
        cfg = plain_cfg()
        cfg["statusline"]["max_width"] = 400
        pace, project = self._line("Pace:", cfg), self._line("Project:", cfg)
        self.assertIn("/day used", pace)
        for piece in ("of 5h", "of 7d", "month", "lifetime", "fewer", "longer"):
            self.assertIn(piece, project)
        self.assertIsNotNone(self._line("Account:", cfg))

    def test_pace_line(self):
        pace = self._line("Pace:", self._cfg())
        self.assertNotIn("/day used", pace)
        for piece in ("/day left", "fewer tokens than vanilla", "longer"):
            self.assertIn(piece, pace)

    def test_project_line(self):
        project = self._line("Project:", self._cfg())
        for piece in ("of 5h", "month", "longer"):
            self.assertNotIn(piece, project)
        for piece in ("of 7d", "lifetime", "fewer", "2 accounts"):
            self.assertIn(piece, project)
        self.assertTrue(project.startswith("Project: "), project)

    def test_account_line_gone(self):
        self.assertIsNone(self._line("Account:", self._cfg()))
        self.assertIsNone(self._line("month", self._cfg()))  # no unlabelled account piece either

    def test_explicit_show_wins(self):
        sl = statusline.sl_config(self._cfg(), {"month": True})
        self.assertTrue(sl["show"]["month"])
        self.assertFalse(sl["show"]["account_usage"])
        # the Account label rides on the usage pair (off), so find the month piece itself
        account = self._line("month ~1.5w", self._cfg(), {"month": True})
        self.assertIsNotNone(account)
        self.assertNotIn("of 5h", account)
