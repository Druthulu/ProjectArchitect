"""3.3 T4: the hooks' child mode -- an external coder's own ``claude -p`` (``PA_EXT`` set) only
guards and records: kind ``ext``, account ``ext:<config>``, cash prices, the model-mismatch alarm,
and none of the main session's duties.  Uses test_hooks' temp ledger and fake home.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_hooks import LedgerCase, usage_line  # noqa: E402

from pa import accounts, paths, prices  # noqa: E402
from pa import hooks  # noqa: E402

PARENT = "parent-main-0001"
SID = "ext-child-0001"
MODEL = "deepseek/deepseek-v4.1-flash-1017"
_ENV = ("PA_EXT", "PA_EXT_MODEL", "PA_EXT_PARENT", "PA_EXT_LABEL")


class ExtChildTest(LedgerCase):

    def setUp(self):
        super().setUp()
        accounts.auth_status = self._auth          # the real one: child mode needs no subprocess
        self._old_ext = {k: os.environ.get(k) for k in _ENV}
        os.environ.update({"PA_EXT": "openrouter/deepseek", "PA_EXT_MODEL": MODEL,
                           "PA_EXT_PARENT": PARENT, "PA_EXT_LABEL": "T4.c1"})
        with open(os.path.join(self.root, ".claude", "pa.json"), "w", encoding="utf-8") as fh:
            json.dump({"pa_version": "3.0.0", "project": "hooks-test", "mod": "on"}, fh)
        os.makedirs(os.path.join(self.root, ".claude-state", "memory"))
        self.path = os.path.join(self.projects, "slug", SID + ".jsonl")

    def tearDown(self):
        for key, old in self._old_ext.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        super().tearDown()

    def _inp(self, **extra):
        inp = {"session_id": SID, "cwd": self.root, "transcript_path": self.path,
               "agent_type": "pa-coder", "model": MODEL}
        inp.update(extra)
        return inp

    def _start(self):
        from pa.hooks import session_start

        return session_start.run(self._inp(source="startup"), self.cfg)

    def _transcript(self, model=MODEL, cost_state=False):
        lines = [usage_line("msg_x1", model=model, inp=20000, write=0, read=0, out=800,
                            ts="2026-10-20T10:00:00.000Z"),
                 usage_line("msg_x2", model=model, inp=500, write=0, read=19000, out=600,
                            ts="2026-10-20T10:00:30.000Z")]
        if cost_state:
            lines.append(json.dumps({"type": "cost-state", "timestamp": "2026-10-20T10:01:00.000Z",
                                     "totalCostUSD": 2.5}))
        return self.write_transcript(self.path, lines)

    def _stop(self, message="Shall I go on?"):
        from pa.hooks import stop

        return stop.run(self._inp(last_assistant_message=message), self.cfg)

    def test_session_start_only_records(self):
        self.assertIsNone(self._start())
        mod = os.path.join(self.root, ".run", "mod")
        self.assertFalse(os.path.exists(os.path.join(mod, "hook.json")))
        self.assertFalse(os.path.exists(os.path.join(mod, "start-check.json")))
        slug = paths.project_slug(self.root.replace("\\", "/"))
        self.assertFalse(os.path.exists(os.path.join(paths.projects_dir(), slug, "memory")))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM accounts"), [(0,)])
        self.assertEqual(self.rows("SELECT kind, account FROM sessions WHERE session_id=?", (SID,)),
                         [("ext", "ext:openrouter")])
        self.assertEqual(self.rows("SELECT parent_run_id, parent_source, kind, model_pinned "
                                   "FROM agent_runs WHERE run_id=?", (SID,)),
                         [(PARENT, "ext", "coder", MODEL)])
        self.assertEqual(self.toasts, [])

    def test_stop_books_cash_turns_and_never_waits(self):
        self._start()
        self._transcript()
        self.assertIsNone(self._stop())
        rows = self.rows("SELECT account, cost_usd FROM turns WHERE session_id=? AND kind='api'",
                         (SID,))
        self.assertEqual(len(rows), 2)
        expected = (20000 * 0.098 + 800 * 0.294 + 500 * 0.098 + 19000 * 0.00294 + 600 * 0.294) / 1e6
        self.assertEqual({r[0] for r in rows}, {"ext:openrouter"})
        self.assertGreater(sum(r[1] for r in rows), 0)
        self.assertAlmostEqual(sum(r[1] for r in rows), expected, places=9)
        self.assertNotIn("waiting", hooks.status_read(self.root) or {})
        self.assertFalse(os.path.exists(paths.state_path("waiting", SID + ".json")))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM savings"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events WHERE kind='model_mismatch'"), [(0,)])
        self.assertEqual(self.toasts, [])

    def test_claude_model_in_child_raises_mismatch(self):
        self._start()
        self._transcript(model="claude-opus-5-5")
        self._stop("done")
        events = [json.loads(r[0]) for r in self.rows(
            "SELECT detail_json FROM events WHERE kind='model_mismatch'")]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["expected"], events[0]["seen"]), (MODEL, "claude-opus-5-5"))

    def test_session_end_no_residual_no_killed_at(self):
        from pa.hooks import session_end

        self._start()
        self._transcript(cost_state=True)
        self.assertIsNone(session_end.run(self._inp(reason="other"), self.cfg))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM turns WHERE kind='residual'"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM turns WHERE kind='api' AND session_id=?",
                                   (SID,)), [(2,)])
        self.assertNotIn("killed_at", hooks.status_read(self.root) or {})

    def test_prices_map_both_ids(self):
        self.assertEqual(prices.model_key(MODEL), "deepseek-v4.1-flash")
        self.assertEqual(prices.model_key("z-ai/glm-5.3-flash"), "glm-5.3-flash")
        self.assertEqual(prices.price_for(MODEL)["read"], 0.00294)
        self.assertEqual(prices.price_for("z-ai/glm-5.3-flash")["output"], 0.50)   # Z.AI fp8 (6433026c)

    def test_no_pa_ext_keeps_main_session(self):
        os.environ.pop("PA_EXT")
        self.assertEqual(hooks.ext_child(), "")
        self.assertNotEqual(hooks.pinned_for({"agent_type": "pa-coder"}, self.cfg), MODEL)


if __name__ == "__main__":
    unittest.main()
