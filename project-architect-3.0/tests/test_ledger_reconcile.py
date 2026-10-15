"""3.24 T13l: a tool-call message's output is estimated; each model reconciles to the cost-state.

Fixture ``fixtures/specimen_d76796c5.json.gz``: the specimen session d76796c5 (pa3-calc-g5, CC 2.1.296,
2026-10-10), every subagent's assistant records (thinking signatures dropped) and the session's last
``cost-state``.  Its messages that end in a tool call carry only the ``message_start`` usage
(``stop_reason`` null, output 2-68): before T13l the ledger kept that placeholder (curator 193 tokens
for 15 messages) and booked the whole gap as one model-less residual on the main run.
"""

import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pa import config, db, ledger_cli, prices, runs  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "specimen_d76796c5.json.gz")


def _fixture_ids():
    """The curator's and pa-claude's agent ids and the exact message (pa-claude, stop_reason tool_use,
    output 452), read from the fixture: the publish scrub rewrites id literals in this file but not
    inside the gzip (3.28 T4b)."""
    with gzip.open(FIX) as fh:
        agents = json.loads(fh.read().decode("utf-8"))["agents"]
    by_type = {a["agentType"]: aid for aid, a in agents.items()}
    exact = next(m["id"] for m in (json.loads(ln)["message"] for ln in agents[by_type["pa-claude"]]["lines"])
                 if m.get("stop_reason") == "tool_use" and m["usage"]["output_tokens"] == 452)
    return by_type["memory-curator"], by_type["pa-claude"], exact


CURATOR, PA_CLAUDE, EXACT = _fixture_ids()


class SpecimenReconcileTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with gzip.open(FIX) as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        cls.sid = doc["session_id"]
        cls.state = doc["cost_state"]
        cls.dir = tempfile.mkdtemp(prefix="pa3-t13l-")
        sub = os.path.join(cls.dir, cls.sid, "subagents")
        os.makedirs(sub)
        cfg = config.defaults()
        cls.conn = db.connect(os.path.join(cls.dir, "ledger.sqlite"))
        db.init_schema(cls.conn)
        cls.before = {}
        for aid, agent in sorted(doc["agents"].items()):
            path = os.path.join(sub, "agent-%s.jsonl" % aid)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(agent["lines"]) + "\n")
            role = config.role_from_agent_type(agent["agentType"], cfg)
            agg = ledger_cli.ingest_turns(cls.conn, path, aid, cls.sid, "dev@example.com",
                                          config.ttl_for_role(role, cfg))
            db.upsert_agent_run(cls.conn, ledger_cli._run_row(aid, cls.sid, agg, path,
                                                              agent_type=agent["agentType"]))
            # the placeholder output the old keep-LAST rule booked, per run
            cls.before[aid] = sum(json.loads(line)["message"]["usage"]["output_tokens"]
                                  for line in agent["lines"]
                                  if _last_of_message(agent["lines"], line))
        cls.estimated = dict(cls._run_outputs())
        cls.result = runs.reconcile_models(cls.conn, cls.sid, cls.state,
                                           account="dev@example.com", ts="2026-10-10T06:48:00Z")
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        db.close(cls.conn)
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def _run_outputs(cls):
        return cls.conn.execute("SELECT run_id, SUM(output) FROM turns WHERE kind='api'"
                                " GROUP BY run_id").fetchall()

    def _by_model(self, kind):
        out = {}
        for r in self.conn.execute("SELECT model, cost_usd, output FROM turns WHERE session_id=?"
                                   " AND kind=?", (self.sid, kind)).fetchall():
            nk = prices.normalize_model(r["model"])
            cost, toks = out.get(nk, (0.0, 0))
            out[nk] = (cost + float(r["cost_usd"] or 0.0), toks + int(r["output"] or 0))
        return out

    def test_each_model_reconciles_to_the_cost_state_within_a_cent(self):
        api, residual = self._by_model("api"), self._by_model("residual")
        usage = self.state["modelUsage"]
        self.assertEqual(len(usage), 3)
        for model, u in usage.items():
            nk = prices.normalize_model(model)
            booked = api[nk][0] + residual.get(nk, (0.0, 0))[0]
            self.assertAlmostEqual(booked, u["costUSD"], delta=0.01, msg=model)
            self.assertEqual(api[nk][1], u["outputTokens"], model)     # output meets the cost-state
        total = sum(c for c, _ in api.values()) + sum(c for c, _ in residual.values())
        self.assertAlmostEqual(total, self.state["totalCostUSD"], delta=0.01)

    def test_curator_and_pa_claude_output_rises(self):
        after = dict(self._run_outputs())
        self.assertEqual(self.before[CURATOR], 193)            # 15 placeholders, the old booking
        self.assertEqual(after[CURATOR], 12612)                 # Fable's whole cost-state output
        self.assertGreater(self.estimated[CURATOR], 193)        # the estimate alone already rises
        self.assertGreater(after[PA_CLAUDE], self.before[PA_CLAUDE])
        self.assertGreater(self.estimated[PA_CLAUDE], self.before[PA_CLAUDE])
        n_est = self.conn.execute("SELECT COUNT(*) FROM turns WHERE run_id=? AND output_est=1",
                                  (CURATOR,)).fetchone()[0]
        self.assertEqual(n_est, 15)
        run = self.conn.execute("SELECT output FROM agent_runs WHERE run_id=?",
                                (CURATOR,)).fetchone()
        self.assertEqual(run["output"], 12612)                  # the run row follows its turns

    def test_an_exact_message_keeps_its_usage(self):
        row = self.conn.execute("SELECT output, output_est, input, cache_read, stop_reason"
                                " FROM turns WHERE msg_id=?", (EXACT,)).fetchone()
        self.assertEqual(tuple(row), (452, 0, 2, 52330, "tool_use"))

    def test_residual_rows_are_labeled_unattributed_per_model(self):
        rows = self.conn.execute("SELECT msg_id, run_id, model, output FROM turns"
                                 " WHERE kind='residual' ORDER BY msg_id").fetchall()
        # Sonnet's gap was output alone (T13x): scaled, under half a cent remains, so no row
        self.assertEqual([r["msg_id"] for r in rows],
                         ["residual:%s:claude-%s" % (self.sid, m) for m in ("fable-5-1", "opus-5-5")])
        self.assertLess(abs(self.result["models"]["claude-sonnet-5-5"]["residual_usd"]), 0.005)
        self.assertEqual({r["run_id"] for r in rows}, {"unattributed:%s" % self.sid})
        self.assertNotIn(self.sid, {r["run_id"] for r in rows})        # never the main loop
        self.assertEqual([r["model"] for r in rows], ["claude-fable-5-1", "claude-opus-5-5[1m]"])
        self.assertEqual(len(self.result["rows"]), 2)


def _last_of_message(lines, line):
    """True when ``line`` is its message's last line in ``lines`` (the keep-LAST row)."""
    mid = json.loads(line)["message"]["id"]
    last = None
    for other in lines:
        if json.loads(other)["message"]["id"] == mid:
            last = other
    return last is line


if __name__ == "__main__":
    unittest.main()
