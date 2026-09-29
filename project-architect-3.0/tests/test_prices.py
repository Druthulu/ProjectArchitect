"""pa.prices: T2 -- cost-state reconciliation and model-id normalization.

T2 (design doc F, section 0 V2): recompute every ``modelUsage[m].costUSD`` of
the two reference ``cost-state`` records from its own token counts.

What reconciles, measured on this machine:

  fixture  model                        1h writes   5m writes   asserted
  -------  ---------------------------  ----------  ----------  --------------------
  vantage  claude-fable-5-1 (main)      +0.000 %    -15.53 %    <= 0.1 % at 1h
  vantage  claude-sonnet-5 (subagents)  +20.61 %    +0.000 %    <= 5 % at 5m
  vantage  claude-haiku-4-5-2025…       +0.000 %    +0.000 %    <= 5 % (no cache use)
  td       claude-opus-5[1m] (main)     +0.000 %    -4.81 %     <= 0.1 % at 1h
  td       claude-haiku-4-5-2025…       +0.000 %    +0.000 %    <= 5 %
  td       claude-fable-5-1 (mixed)     +7.22 %     -12.05 %    bracketed only

The TD session ran Fable as *both* an expert model (1h writes) and subagent
traffic (5m writes), so no single TTL reproduces its row; the test asserts only
that the actual cost lies inside the 5m..1h bracket and documents the split.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import prices  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# fixture file -> (main model priced at 1h, models that are pure 5m subagent traffic)
CASES = {
    "cost_state_vantage_e2eed858.json": ("claude-fable-5-1",
                                         ("claude-sonnet-5", "claude-haiku-4-5-20251001")),
    "cost_state_td_76c977f4.json": ("claude-opus-5[1m]",
                                    ("claude-haiku-4-5-20251001",)),
}


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _pct(computed, actual):
    return abs(computed - actual) / actual * 100.0 if actual else 0.0


class CostStateReconciliationTest(unittest.TestCase):

    def test_main_model_reconciles_to_the_cent_at_1h(self):
        for name, (main, _) in CASES.items():
            with self.subTest(fixture=name, model=main):
                usage = _load(name)["modelUsage"][main]
                cost, parts = prices.cost_of(usage, main, ttl_default="1h")
                self.assertTrue(parts["priced"])
                self.assertLessEqual(_pct(cost, usage["costUSD"]), 0.1,
                                     "%s/%s: %r vs %r" % (name, main, cost, usage["costUSD"]))

    def test_subagent_models_reconcile_at_5m(self):
        for name, (_, subs) in CASES.items():
            for model in subs:
                with self.subTest(fixture=name, model=model):
                    usage = _load(name)["modelUsage"][model]
                    cost, _parts = prices.cost_of(usage, model, ttl_default="5m")
                    self.assertLessEqual(_pct(cost, usage["costUSD"]), 5.0,
                                         "%s/%s: %r vs %r" % (name, model, cost, usage["costUSD"]))

    def test_mixed_ttl_row_is_bracketed_not_asserted(self):
        # TD's Fable row mixes expert (1h) and subagent (5m) writes: 5m under-
        # counts by 12.0 %, 1h over-counts by 7.2 %, the truth is in between.
        usage = _load("cost_state_td_76c977f4.json")["modelUsage"]["claude-fable-5-1"]
        low, _ = prices.cost_of(usage, "claude-fable-5-1", ttl_default="5m")
        high, _ = prices.cost_of(usage, "claude-fable-5-1", ttl_default="1h")
        self.assertLess(low, usage["costUSD"])
        self.assertGreater(high, usage["costUSD"])

    def test_totals_cover_the_session_cost(self):
        # Sum of the per-model reconciliations is within 0.1 % of totalCostUSD
        # when each model is priced with its own TTL (mixed row excluded).
        cs = _load("cost_state_vantage_e2eed858.json")
        total = 0.0
        for model, usage in cs["modelUsage"].items():
            ttl = "1h" if model == "claude-fable-5-1" else "5m"
            total += prices.cost_of(usage, model, ttl_default=ttl)[0]
        self.assertLessEqual(_pct(total, cs["totalCostUSD"]), 0.1)

    def test_split_in_the_record_beats_the_ttl_default(self):
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 1000,
                 "cache_read_input_tokens": 0, "output_tokens": 0,
                 "cache_creation": {"ephemeral_5m_input_tokens": 400,
                                    "ephemeral_1h_input_tokens": 600}}
        cost, parts = prices.cost_of(usage, "claude-fable-5-1", ttl_default="5m")
        self.assertEqual(parts["ttl"], "split")
        self.assertAlmostEqual(cost, (400 * 12.5 + 600 * 20.0) / 1e6, places=12)

    def test_unknown_model_is_unpriced_not_fatal(self):
        cost, parts = prices.cost_of({"input_tokens": 10}, "gpt-5-turbo")
        self.assertEqual(cost, 0.0)
        self.assertFalse(parts["priced"])
        self.assertIsNone(prices.price_for("<synthetic>"))

    def test_long_context_tier_at_250k_context(self):
        # T8: 100k input + 100k cache write + 50k cache read = 250k context,
        # past LONG_CONTEXT_TOKENS, on a 1M-window model (opus-5).
        usage = {"input_tokens": 100000, "cache_creation_input_tokens": 100000,
                 "cache_read_input_tokens": 50000, "output_tokens": 1000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                    "ephemeral_1h_input_tokens": 100000}}
        self.assertGreater(100000 + 100000 + 50000, prices.LONG_CONTEXT_TOKENS)
        base, base_parts = prices.cost_of(usage, "claude-opus-5", ttl_default="1h")
        tiered, tiered_parts = prices.cost_of(usage, "claude-opus-5", ttl_default="1h",
                                              long_context=True)
        self.assertFalse(base_parts["long_context"])
        self.assertTrue(tiered_parts["long_context"])
        p = prices.price_for("claude-opus-5")
        want = (100000 * p["input"] * 2.0 + 100000 * p["write_1h"] * 2.0
                + 50000 * p["read"] * 2.0 + 1000 * p["output"] * 1.5) / 1e6
        self.assertAlmostEqual(tiered, want, places=9)
        self.assertGreater(tiered, base)

    def test_long_context_tier_needs_a_1m_window_model(self):
        # sonnet-4-6 has no 1M window: long_context=True is a no-op for it.
        usage = {"input_tokens": 300000, "output_tokens": 100}
        cost, parts = prices.cost_of(usage, "claude-sonnet-4-6", long_context=True)
        self.assertFalse(parts["long_context"])
        self.assertAlmostEqual(cost, prices.cost_of(usage, "claude-sonnet-4-6")[0], places=12)

    def test_long_context_never_auto_applies_to_a_pooled_total(self):
        # A cost-state modelUsage entry is a whole-session total, not one
        # request; cost_of must not infer the tier from its own summed ctx.
        pooled = {"inputTokens": 3406374, "cacheCreationInputTokens": 19894142,
                  "cacheReadInputTokens": 1588232885, "outputTokens": 6430309,
                  "cache_write_5m": 0, "cache_write_1h": 19894142}
        cost, parts = prices.cost_of(pooled, "claude-opus-5", ttl_default="1h")
        self.assertFalse(parts["long_context"])


class NormalizeModelTest(unittest.TestCase):

    def test_date_suffix_and_context_tag_are_stripped(self):
        cases = {
            "claude-haiku-4-5-20251001": "claude-haiku-4-5",
            "claude-opus-5[1m]": "claude-opus-5",
            "claude-fable-5-1[1m]": "claude-fable-5-1",
            "CLAUDE-SONNET-5": "claude-sonnet-5",
            "claude-opus-4-6-20260115": "claude-opus-4-6",
            "": "",
        }
        for raw, want in cases.items():
            with self.subTest(model=raw):
                self.assertEqual(prices.normalize_model(raw), want)
        self.assertEqual(prices.normalize_model(None), "")

    def test_most_specific_key_wins(self):
        self.assertEqual(prices.model_key("claude-fable-5-1"), "fable-5-1")
        self.assertEqual(prices.model_key("claude-fable-5"), "fable-5")
        self.assertEqual(prices.model_key("claude-sonnet-5-5"), "sonnet-5-5")
        self.assertEqual(prices.model_key("claude-sonnet-5-5[1m]"), "sonnet-5-5")
        self.assertEqual(prices.model_key("claude-sonnet-5"), "sonnet-5")
        self.assertEqual(prices.model_key("claude-sonnet-4-6"), "sonnet-4-6")
        self.assertEqual(prices.model_key("claude-opus-4-6"), "opus-4-6")
        self.assertEqual(prices.model_key("claude-opus-5-5"), "opus-5-5")
        self.assertEqual(prices.model_key("claude-opus-5[1m]"), "opus-5")
        self.assertEqual(prices.model_key("claude-haiku-4-5-20251001"), "haiku-4-5")
        self.assertIsNone(prices.model_key("llama-3"))

    def test_price_columns_match_the_spec_table(self):
        want = {
            "claude-opus-4-6": (5, 6.25, 10, 0.50, 25, "old"),
            "claude-opus-5-5": (4, 5.00, 8, 0.20, 20, "new"),
            "claude-opus-5": (5, 6.25, 10, 0.50, 25, "new"),
            "claude-sonnet-5-5": (2, 2.50, 4, 0.20, 10, "new"),
            "claude-sonnet-5": (2, 2.50, 4, 0.20, 10, "new"),
            "claude-sonnet-4-6": (3, 3.75, 6, 0.30, 15, "old"),
            "claude-haiku-4-5": (1, 1.25, 2, 0.10, 5, "old"),
            "claude-fable-5-1": (10, 12.50, 20, 0.25, 50, "new"),
            "claude-fable-5": (10, 12.50, 20, 1.00, 50, "new"),
        }
        for model, row in want.items():
            with self.subTest(model=model):
                p = prices.price_for(model)
                self.assertEqual((p["input"], p["write_5m"], p["write_1h"], p["read"],
                                  p["output"], p["tokenizer"]), row)

    def test_effective_from_selects_the_latest_row_at_or_before_at(self):
        p_now = prices.price_for("claude-fable-5-1")
        p_then = prices.price_for("claude-fable-5-1", at="2026-09-30")
        self.assertEqual(p_now["effective_from"], p_then["effective_from"])
        # a date before every row still prices (oldest row), never returns None
        self.assertIsNotNone(prices.price_for("claude-fable-5-1", at="2020-01-01"))

    def test_rows_for_db_shape(self):
        rows = prices.rows_for_db()
        self.assertEqual(len(rows), len(prices.PRICES))
        self.assertEqual(len(rows[0]), 9)


if __name__ == "__main__":
    unittest.main()
