"""pa.config: defaults, merge, validation, role map, pinned models."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import config, paths  # noqa: E402


class DefaultsTest(unittest.TestCase):

    def setUp(self):
        self.cfg = config.defaults()

    def test_section_g_values(self):
        c = self.cfg
        self.assertEqual(c["schema"], 1)
        self.assertTrue(c["enabled"])
        self.assertIsNone(c["renewal_day"])                # 3.11 T9: no global day (was 21)
        self.assertEqual(c["extra_roots_mode"], "copy")
        self.assertEqual(c["handoff"]["threshold_tokens"], 350000)
        self.assertEqual(c["handoff"]["roles"], ["expert", "coder"])
        self.assertEqual(c["ttl_default"]["expert"], "1h")   # was "5m" (3.15 T4); was "1h" before
        self.assertEqual(c["ttl_default"]["coder"], "5m")    # was "1h"
        self.assertEqual(c["ttl_default"]["main"], "1h")
        self.assertEqual(c["ttl_default"]["retriever"], "5m")
        self.assertEqual(c["fit"]["n_min"], 8)
        self.assertEqual(c["fit"]["window_durations_s"]["five_hour"], 18000)
        self.assertEqual(c["sampling"]["heartbeat_s"], 600)
        for k in ("seed_old", "old_reset", "vanilla_seed"):  # T30: retired, plain 1M cycle
            self.assertNotIn(k, c["modeled"])
            self.assertIn("modeled." + k, config.SUPERSEDED)
        self.assertEqual(c["savings"]["kept_out_mode"], "results_share")
        self.assertEqual(c["statusline"]["ctx_red"], 300000)
        self.assertEqual(c["ledger"]["lock_inline_max_bytes"], 20000000)
        self.assertEqual(c["ledger"]["disk_min_free_mb"], 50)
        self.assertEqual(c["usage_api"]["enabled"], True)
        self.assertEqual(c["usage_api"]["poll_min"], 15)     # was 5
        self.assertEqual(c["resume"]["liveness_min"], 5)
        self.assertEqual(c["resume"]["unstop"], True)
        self.assertEqual(c["discussion_allow_scripts"],
                         ["tools/analysis/*.py", "tools/plan_show.py", "tools/discussion.py",
                          "tools/card.py slice|check", "tools/plan_edit.py show|grammar"])

    def test_machine_and_python_are_resolved(self):
        self.assertIn(self.cfg["machine"], ("win", "wsl", "linux", "mac"))
        self.assertEqual(self.cfg["machine"], paths.machine_tag())
        self.assertTrue(self.cfg["python"])

    def test_defaults_are_independent_copies(self):
        a = config.defaults()
        a["handoff"]["threshold_tokens"] = 1
        self.assertEqual(config.defaults()["handoff"]["threshold_tokens"], 350000)
        self.assertEqual(config.DEFAULTS["handoff"]["threshold_tokens"], 350000)

    def test_validate_accepts_defaults_and_reports_problems(self):
        self.assertEqual(config.validate(self.cfg), [])
        bad = config.merge(self.cfg, {"schema": 9, "renewal_day": 40,
                                      "ttl_default": {"expert": "2h"},
                                      "roles": {"prefix_map": {"x-": "wizard"}}})
        problems = config.validate(bad)
        self.assertTrue(any("schema" in p for p in problems))
        self.assertTrue(any("renewal_day" in p for p in problems))
        self.assertTrue(any("ttl_default" in p for p in problems))
        self.assertTrue(any("prefix_map" in p for p in problems))
        self.assertEqual(config.validate("nope"), ["config is not an object"])

    def test_validate_accepts_installed_at_string_or_null(self):
        self.assertIsNone(self.cfg["installed_at"])
        self.assertEqual(config.validate(self.cfg), [])
        cfg = config.merge(self.cfg, {"installed_at": "2026-09-12T00:00:00Z"})
        self.assertEqual(config.validate(cfg), [])
        bad = dict(self.cfg)
        bad["installed_at"] = 123
        self.assertTrue(any("installed_at" in p for p in config.validate(bad)))

    def test_validate_usage_api(self):
        self.assertEqual(config.validate(self.cfg), [])
        bad = config.merge(self.cfg, {"usage_api": {"enabled": "yes"}})
        problems = config.validate(bad)
        self.assertTrue(any("usage_api.enabled" in p for p in problems))
        bad2 = config.merge(self.cfg, {"usage_api": {"poll_min": -1}})
        problems2 = config.validate(bad2)
        self.assertTrue(any("usage_api.poll_min" in p for p in problems2))

    def test_install_card_guard_groups_in_defaults(self):
        """The install, card and guard groups exist in DEFAULTS with the expected keys."""
        d = config.DEFAULTS
        self.assertIn("install", d)
        self.assertEqual(d["install"]["source"],
                         "https://github.com/Druthulu/ProjectArchitect.git")
        self.assertEqual(d["install"]["clone_dir"], "pa3-src")
        self.assertEqual(d["install"]["update_check_hours"], 24)
        self.assertEqual(d["install"]["fetch_timeout_s"], 8)

        self.assertIn("card", d)
        self.assertEqual(d["card"]["max_chars"], 7000)

        self.assertIn("guard", d)
        self.assertTrue(d["guard"]["spilled_read"])
        self.assertEqual(d["guard"]["whole_plan_roles"], ["review", "critic"])
        self.assertEqual(d["guard"]["tool_source_roles"], ["expert"])

    def test_install_card_guard_groups_in_template(self):
        """The three groups exist in pa.json.template and parse as valid JSON."""
        import json as _json
        tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "templates", "pa.json.template")
        with open(tpl_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # The template has {{PY}} and {{PROJECT_NAME}} placeholders; fill them for parsing
        text = text.replace("{{PY}}", "python").replace("{{PROJECT_NAME}}", "test")
        data = _json.loads(text)
        self.assertIn("install", data)
        self.assertEqual(data["install"]["clone_dir"], "pa3-src")
        self.assertIn("card", data)
        self.assertEqual(data["card"]["max_chars"], 7000)
        self.assertIn("guard", data)
        self.assertTrue(data["guard"]["spilled_read"])

    def test_whole_read_chars_and_router_ctx_flag_in_defaults(self):
        """guard.whole_read_chars and audit.router_ctx_flag exist in DEFAULTS."""
        d = config.DEFAULTS
        self.assertEqual(d["guard"]["whole_read_chars"], 20000)
        self.assertEqual(d["audit"]["router_ctx_flag"], 300000)

    def test_warmer_cap_fallback_in_defaults(self):
        """3.15 T3: max_pings_5m is the ceiling; max_pings_5m_fallback 3 when cap inputs are missing."""
        w = config.DEFAULTS["warmer"]
        self.assertEqual((w["max_pings_5m"], w["max_pings_5m_fallback"]), (12, 3))

    def test_whole_read_chars_and_router_ctx_flag_in_template(self):
        """Both new keys exist in pa.json.template."""
        import json as _json
        tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "templates", "pa.json.template")
        with open(tpl_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("{{PY}}", "python").replace("{{PROJECT_NAME}}", "test")
        data = _json.loads(text)
        self.assertEqual(data["guard"]["whole_read_chars"], 20000)
        self.assertEqual(data["audit"]["router_ctx_flag"], 300000)

    def test_validate_rejects_bad_whole_read_chars(self):
        """validate rejects whole_read_chars: 0 and "20k"."""
        cfg = config.defaults()
        cfg0 = config.merge(cfg, {"guard": {"whole_read_chars": 0}})
        self.assertTrue(any("whole_read_chars" in p for p in config.validate(cfg0)))
        cfgs = config.merge(cfg, {"guard": {"whole_read_chars": "20k"}})
        self.assertTrue(any("whole_read_chars" in p for p in config.validate(cfgs)))

    def test_validate_rejects_bad_router_ctx_flag(self):
        """validate rejects router_ctx_flag: -1."""
        cfg = config.defaults()
        cfgn = config.merge(cfg, {"audit": {"router_ctx_flag": -1}})
        self.assertTrue(any("router_ctx_flag" in p for p in config.validate(cfgn)))

    def test_validate_accepts_defaults(self):
        """validate() accepts the defaults with the new keys."""
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])

    def test_validate_accepts_config_with_groups(self):
        """validate() accepts the full defaults including the three groups."""
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])
        self.assertIn("install", cfg)
        self.assertIn("card", cfg)
        self.assertIn("guard", cfg)


class LoadTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-cfg-")
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_file_gives_defaults(self):
        cfg = config.load(self.path, refresh=True)
        self.assertEqual(cfg["handoff"]["threshold_tokens"], 350000)
        self.assertEqual(config.validate(cfg), [])

    def test_file_merges_over_defaults(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"renewal_day": 3,
                       "extra_roots": ["//wsl.localhost/Ubuntu-24.04/home/you/.claude/usage-ledger"],
                       "handoff": {"roles": ["expert", "coder"]},
                       "pinned_models": {"expert-fable": "claude-opus-5"}}, fh)
        cfg = config.load(self.path, refresh=True)
        self.assertEqual(cfg["renewal_day"], 3)
        self.assertEqual(len(cfg["extra_roots"]), 1)
        self.assertEqual(cfg["handoff"]["roles"], ["expert", "coder"])
        self.assertEqual(cfg["handoff"]["threshold_tokens"], 350000)   # untouched key survives
        self.assertEqual(cfg["pinned_models"]["expert-fable"], "claude-opus-5")
        self.assertEqual(cfg["pinned_models"]["router"], "claude-sonnet-5-5")

    def test_user_layer_only_stored_keys(self):
        """fix-14: user_layer() is config.json as stored, no defaults merged."""
        self.assertEqual(config.user_layer(self.path), {})
        stored = {"statusline": {"fewer_numbers": True, "show": {"month": True}}}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(stored, fh)
        self.assertEqual(config.user_layer(self.path), stored)
        self.assertEqual(config.validate(config.load(self.path, refresh=True)), [])

    def test_save_round_trip(self):
        cfg = config.defaults()
        cfg["renewal_day"] = 11
        config.save(cfg, self.path)
        self.assertEqual(config.load(self.path, refresh=True)["renewal_day"], 11)

    def test_save_writes_slim_snapshot(self):
        """T12: a full snapshot keeps only user keys and overrides; the cache stays full."""
        cfg = config.defaults()
        cfg["renewal_day"] = 11
        cfg["accounts"] = {"a@example.com": {"label": "main"}}
        cfg["extra_roots"] = ["//wsl/usage-ledger"]
        cfg["pinned_models"]["planner-phase"] = "claude-fable-5-1"   # past default
        cfg["statusline"]["lines"] = 5                                # past default
        cfg["statusline"]["max_width"] = 120                          # override
        config.save(cfg, self.path)
        with open(self.path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertNotIn("pinned_models", raw)
        self.assertNotIn("roles", raw)
        self.assertNotIn("ttl_default", raw)
        self.assertEqual(raw["statusline"], {"max_width": 120})
        self.assertEqual(raw["renewal_day"], 11)
        self.assertEqual(raw["accounts"], {"a@example.com": {"label": "main"}})
        self.assertEqual(raw["extra_roots"], ["//wsl/usage-ledger"])
        for key in config.USER_KEYS:
            self.assertIn(key, raw)
        cached = config.load(self.path)
        self.assertEqual(cached["pinned_models"]["router"], "claude-sonnet-5-5")
        self.assertEqual(cached["statusline"]["max_width"], 120)

    def test_slim_drops_empty_dicts_and_reports(self):
        dropped = []
        out = config.slim({"renewal_day": 21, "notify": {}, "fit": {"n_min": 8},
                           "warmer": {"max_pings": 3}}, dropped)
        self.assertEqual(out, {"renewal_day": 21})
        self.assertEqual(sorted(dropped), ["fit.n_min", "notify", "warmer.max_pings"])

    def test_kill_switch(self):
        cfg = config.load(self.path, refresh=True)
        self.assertTrue(config.enabled(cfg))
        self.assertFalse(config.enabled(config.merge(cfg, {"enabled": False})))
        os.environ["PA_LEDGER_OFF"] = "1"
        try:
            self.assertFalse(config.enabled(cfg))
        finally:
            del os.environ["PA_LEDGER_OFF"]


class RoleMapTest(unittest.TestCase):

    def setUp(self):
        self.cfg = config.defaults()

    def test_prefix_map(self):
        cases = {
            "expert-fable": "expert",
            "expert-opus46": "expert",
            "coder-sonnet": "coder",
            "coder-opus46": "coder",
            "retriever-code": "retriever",
            "retriever-web": "retriever",
            "router": "router",
            "critic": "critic",
            "review": "review",
            "planner-gen": "planner",
            "planner-phase": "planner",
            "general-purpose": "other",
            "Explore": "other",
            "": "other",
        }
        for agent_type, role in cases.items():
            with self.subTest(agent_type=agent_type):
                self.assertEqual(config.role_from_agent_type(agent_type, self.cfg), role)
        self.assertEqual(config.role_from_agent_type(None, self.cfg), "other")

    def test_case_insensitive(self):
        self.assertEqual(config.role_from_agent_type("Expert-Fable", self.cfg), "expert")

    def test_pinned_model_for(self):
        self.assertEqual(config.pinned_model_for("expert-fable", self.cfg), "claude-fable-5-1")
        self.assertEqual(config.pinned_model_for("retriever-code", self.cfg), "claude-sonnet-5-5")
        self.assertEqual(config.pinned_model_for("coder-opus46", self.cfg), "claude-opus-4-6")
        self.assertIsNone(config.pinned_model_for("nobody", self.cfg))
        self.assertIsNone(config.pinned_model_for(None, self.cfg))

    def test_ttl_for_role(self):
        self.assertEqual(config.ttl_for_role("expert", self.cfg), "1h")   # was "5m" (3.15 T4); was "1h" before
        self.assertEqual(config.ttl_for_role("coder", self.cfg), "5m")    # was "1h"
        self.assertEqual(config.ttl_for_role("main", self.cfg), "1h")
        self.assertEqual(config.ttl_for_role("retriever", self.cfg), "5m")
        self.assertEqual(config.ttl_for_role("unknown-role", self.cfg), "5m")

    def test_dotted_get(self):
        self.assertEqual(config.get(self.cfg, "handoff.threshold_tokens"), 350000)
        self.assertIsNone(config.get(self.cfg, "handoff.nope"))
        self.assertEqual(config.get(self.cfg, "a.b.c", "fallback"), "fallback")


class LadderPinTest(unittest.TestCase):
    """3.10.6 T3: ladder agents without a user pin follow the process's tier."""

    def setUp(self):
        self.cfg = config.defaults()
        old = config._LADDER_TIER
        self.addCleanup(setattr, config, "_LADDER_TIER", old)

    def test_max20_equals_defaults(self):
        config._set_ladder_tier("max20")
        for agent in ("critic", "review", "expert-fable"):
            self.assertEqual(config.pinned_model_for(agent, self.cfg),
                             config.DEFAULTS["pinned_models"][agent])
        self.assertEqual(config.pinned_model_for("plain", self.cfg), "claude-fable-5-1")

    def test_pro_and_max5(self):
        config._set_ladder_tier("pro")
        self.assertEqual(config.pinned_model_for("critic", self.cfg), "claude-opus-5-5")
        self.assertEqual(config.pinned_model_for("Auditor", self.cfg), "claude-opus-5-5")
        self.assertEqual(config.pinned_model_for("expert-fable", self.cfg), "claude-fable-5-1")
        self.assertEqual(config.pinned_model_for("retriever-code", self.cfg), "claude-sonnet-5-5")
        config._set_ladder_tier("max5")
        self.assertEqual(config.pinned_model_for("review", self.cfg), "claude-fable-5-1")

    def test_user_pin_and_override(self):
        config._set_ladder_tier("pro")
        cfg = config.defaults()
        cfg["pinned_models"] = dict(cfg["pinned_models"], Critic="claude-sonnet-5")
        del cfg["pinned_models"]["critic"]
        self.assertEqual(config.pinned_model_for("critic", cfg), "claude-sonnet-5")
        config._set_ladder_tier("max5", {"critic": {"model": "claude-opus-5-5[1m]"}})
        self.assertEqual(config.pinned_model_for("critic", self.cfg), "claude-opus-5-5")

    def test_resolved_from_pa_json(self):
        tmp = tempfile.mkdtemp(prefix="pa3-ladpin-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, ".claude"))
        with open(os.path.join(tmp, ".claude", "pa.json"), "w") as fh:
            json.dump({"tier": "pro"}, fh)
        old_ledger, old_cwd = os.environ.get("PA_LEDGER_DIR"), os.getcwd()
        os.environ["PA_LEDGER_DIR"] = os.path.join(tmp, "ledger")
        os.chdir(tmp)
        try:
            config._set_ladder_tier(None)
            self.assertEqual(config.pinned_model_for("critic", self.cfg), "claude-opus-5-5")
            self.assertEqual(config._LADDER_TIER, ("pro", None))
        finally:
            os.chdir(old_cwd)
            if old_ledger is None:
                os.environ.pop("PA_LEDGER_DIR", None)
            else:
                os.environ["PA_LEDGER_DIR"] = old_ledger


class ExtraRootsTest(unittest.TestCase):
    """extra_roots entries: a bare path string or {path, account, label}."""

    def test_extra_root_entries_normalizes_both_forms(self):
        cfg = config.defaults()
        cfg["extra_roots"] = [
            "//wsl.localhost/Ubuntu-24.04/home/you/.claude/usage-ledger",
            {"path": "D:/other-ledger", "account": "a@b.c", "label": "other"},
            {"path": "", "account": "dropped"},
        ]
        entries = config.extra_root_entries(cfg)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], {
            "path": "//wsl.localhost/Ubuntu-24.04/home/you/.claude/usage-ledger",
            "account": None, "label": None})
        self.assertEqual(entries[1], {"path": "D:/other-ledger", "account": "a@b.c",
                                      "label": "other"})

    def test_validate_accepts_dict_entries(self):
        cfg = config.defaults()
        cfg["extra_roots"] = [{"path": "D:/other-ledger", "account": "a@b.c"}]
        self.assertEqual(config.validate(cfg), [])
        cfg["extra_roots"] = [{"account": "a@b.c"}]           # missing path
        self.assertTrue(any("extra_roots" in p for p in config.validate(cfg)))
        cfg["extra_roots"] = [123]                             # not a string or object
        self.assertTrue(any("extra_roots" in p for p in config.validate(cfg)))


if __name__ == "__main__":
    unittest.main()


class RenewalDayTest(unittest.TestCase):
    """3.1: the renewal day lives per account. 3.11 T9: only there; the global one and the default 21 are
    retired, and an account without its own day has none (the month figures show a dash)."""

    def test_per_account_renewal_day_and_no_fallback(self):
        cfg = config.defaults()
        cfg["renewal_day"] = 12                            # an old config's global: ignored
        cfg["accounts"] = {"a@x.com": {"label": "win", "renewal_day": 3},
                           "b@x.com": {"label": "wsl"},
                           "c@x.com": {"renewal_day": 31}}
        self.assertEqual(config.renewal_day_for(cfg, "a@x.com"), 3)
        self.assertIsNone(config.renewal_day_for(cfg, "b@x.com"))
        self.assertIsNone(config.renewal_day_for(cfg, None))
        self.assertIsNone(config.renewal_day_for({}, "a@x.com"))
        self.assertEqual(config.renewal_day_for(cfg, "c@x.com"), 28)                # clamped
        self.assertEqual(config.validate(cfg), [])
        cfg["renewal_day"] = None
        self.assertEqual(config.validate(cfg), [])                                   # null is valid
        cfg["accounts"]["a@x.com"]["renewal_day"] = 40
        self.assertTrue(any("accounts.a@x.com.renewal_day" in p for p in config.validate(cfg)))

    def test_period_start_follows_the_account(self):
        from pa import summary

        cfg = config.defaults()
        cfg["renewal_day"] = 12
        cfg["accounts"] = {"a@x.com": {"renewal_day": 3}}
        sept19 = 1789862400                                # 2026-09-19T20:00:00Z
        self.assertEqual(summary.period_start(cfg, now=sept19, account="a@x.com"), "2026-09-03")
        self.assertIsNone(summary.period_start(cfg, now=sept19, account="b@x.com"))
        self.assertIsNone(summary.period_start(cfg, now=sept19))


class TierRegimeTest(unittest.TestCase):
    """T32.1: accounts carry a tier and a usage-limit regime boundary for the pooled fit."""

    def test_tier_for_default_and_override(self):
        cfg = config.defaults()
        cfg["accounts"] = {"a@x.com": {"tier": "pro"}, "b@x.com": {"label": "wsl"}}
        self.assertEqual(config.tier_for(cfg, "a@x.com"), "pro")
        self.assertEqual(config.tier_for(cfg, "b@x.com"), "max")
        self.assertEqual(config.tier_for(cfg, "c@x.com"), "max")
        self.assertEqual(config.tier_for({}, None), "max")
        self.assertEqual(config.validate(cfg), [])
        cfg["accounts"]["a@x.com"]["tier"] = 3
        self.assertTrue(any("accounts.a@x.com.tier" in p for p in config.validate(cfg)))

    def test_regime_since_for_precedence(self):
        cfg = config.defaults()
        self.assertIsNone(config.get(cfg, "fit.regime_since"))
        self.assertEqual(config.get(cfg, "fit.pool_min_instance_samples"), 8)
        cfg["accounts"] = {"a@x.com": {"regime_since": "2026-09-22T00:00:00Z"}, "b@x.com": {}}
        self.assertIsNone(config.regime_since_for(cfg, "b@x.com"))       # no boundary anywhere
        self.assertEqual(config.regime_since_for(cfg, "a@x.com"), "2026-09-22T00:00:00Z")
        cfg["fit"]["regime_since"] = "2026-09-20T00:00:00Z"
        self.assertEqual(config.regime_since_for(cfg, "a@x.com"), "2026-09-22T00:00:00Z")  # wins
        self.assertEqual(config.regime_since_for(cfg, "b@x.com"), "2026-09-20T00:00:00Z")
        self.assertEqual(config.regime_since_for(cfg, None), "2026-09-20T00:00:00Z")
        self.assertEqual(config.validate(cfg), [])
        cfg["fit"]["regime_since"] = 5
        cfg["accounts"]["a@x.com"]["regime_since"] = 7
        probs = config.validate(cfg)
        self.assertTrue(any(p.startswith("fit.regime_since") for p in probs))
        self.assertTrue(any("accounts.a@x.com.regime_since" in p for p in probs))


class StatuslineShowTest(unittest.TestCase):
    """T10: statusline.show dict and pace thresholds in config."""

    def test_show_dict_defaults(self):
        """Every show key is True except five_hour_pace, dollars, task_helpers (False)."""
        cfg = config.defaults()
        show = cfg["statusline"]["show"]
        self.assertIsInstance(show, dict)
        self.assertFalse(show["five_hour_pace"])
        self.assertFalse(show["dollars"])
        self.assertFalse(show["task_helpers"])
        rest = {k: v for k, v in show.items() if k not in ("five_hour_pace", "dollars", "task_helpers")}
        self.assertTrue(all(v is True for v in rest.values()), rest)

    def test_show_dict_keys_complete(self):
        """Every segment key exists in the defaults."""
        cfg = config.defaults()
        show = cfg["statusline"]["show"]
        expected = {"model", "effort", "ctx",
                    "five_hour", "five_hour_pace", "seven_day_meter", "fable",
                    "pace_line",
                    "generation", "phase", "task", "agent", "progress", "next_task",
                    "session_cost", "session_saved", "session_window_saved",
                    "session_usage",
                    "project_usage", "project_window_saved", "project_window_saved_pct",
                    "project_lifetime", "project_month", "project_accounts",
                    "account_saved", "fable_saved", "month", "account_lifetime",
                    "account_usage",
                    "dollars", "pace_vanilla", "session_vanilla",
                    "project_vanilla", "account_vanilla",
                    "alerts", "task_block", "task_helpers",
                    "task_project",
                    "pace_used", "project_five_hour", "project_multiplier"}  # fix-14
        self.assertEqual(set(show.keys()), expected)

    def test_pace_thresholds_defaults(self):
        cfg = config.defaults()
        self.assertEqual(cfg["statusline"]["pace_yellow"], 1.25)  # was 1.0
        self.assertEqual(cfg["statusline"]["pace_red"], 1.75)     # was 1.2

    def test_validate_rejects_unknown_show_key(self):
        cfg = config.defaults()
        cfg["statusline"]["show"]["nonexistent_segment"] = True
        problems = config.validate(cfg)
        self.assertTrue(any("statusline.show.nonexistent_segment" in p for p in problems))

    def test_validate_accepts_valid_show_keys(self):
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])


class SupersededTest(unittest.TestCase):
    """T5: superseded-default detection from the SUPERSEDED table."""

    def test_one_superseded_one_handset_one_current(self):
        """A config with one superseded value, one hand-set value and one current default."""
        cfg = config.defaults()
        # superseded: lines = 6 (old default, now 7)
        cfg["statusline"]["lines"] = 6
        # hand-set: max_width = 120 (not a superseded default)
        cfg["statusline"]["max_width"] = 120
        # current default: pace_yellow stays at 1.25
        result = config.superseded(cfg)
        dotted_keys = [r[0] for r in result]
        self.assertIn("statusline.lines", dotted_keys)
        self.assertNotIn("statusline.max_width", dotted_keys)
        self.assertNotIn("statusline.pace_yellow", dotted_keys)
        # check the tuple shape
        entry = [r for r in result if r[0] == "statusline.lines"][0]
        self.assertEqual(entry[1], 6)
        self.assertEqual(entry[2], 7)

    def test_current_defaults_return_nothing(self):
        cfg = config.defaults()
        self.assertEqual(config.superseded(cfg), [])

    def test_superseded_table_exists(self):
        self.assertIsInstance(config.SUPERSEDED, dict)
        self.assertIn("statusline.lines", config.SUPERSEDED)

    def test_superseded_ttl_default_expert_5m(self):
        """A config with ttl_default.expert = '5m' (superseded, 3.15 T4; was '1h') is detected."""
        cfg = config.defaults()
        cfg["ttl_default"]["expert"] = "5m"
        result = config.superseded(cfg)
        dotted_keys = [r[0] for r in result]
        self.assertIn("ttl_default.expert", dotted_keys)
        entry = [r for r in result if r[0] == "ttl_default.expert"][0]
        self.assertEqual(entry[1], "5m")
        self.assertEqual(entry[2], "1h")

    def test_superseded_ttl_default_coder_1h(self):
        """A config with ttl_default.coder = '1h' (superseded) is detected."""
        cfg = config.defaults()
        cfg["ttl_default"]["coder"] = "1h"
        result = config.superseded(cfg)
        dotted_keys = [r[0] for r in result]
        self.assertIn("ttl_default.coder", dotted_keys)
        entry = [r for r in result if r[0] == "ttl_default.coder"][0]
        self.assertEqual(entry[1], "1h")
        self.assertEqual(entry[2], "5m")

    def test_superseded_usage_api_poll_min_5(self):
        """A config with usage_api.poll_min = 5 (superseded) is detected."""
        cfg = config.defaults()
        cfg["usage_api"]["poll_min"] = 5
        result = config.superseded(cfg)
        dotted_keys = [r[0] for r in result]
        self.assertIn("usage_api.poll_min", dotted_keys)
        entry = [r for r in result if r[0] == "usage_api.poll_min"][0]
        self.assertEqual(entry[1], 5)
        self.assertEqual(entry[2], 15)


class AuditTest(unittest.TestCase):
    """T12: audit() over the raw config.json."""

    def _kinds(self, raw):
        return {(kind, dotted) for kind, dotted, _v, _d in config.audit(raw)}

    def test_clean_slim_config(self):
        raw = {"renewal_day": 3, "accounts": {"x@y": {"label": "l"}},
               "pinned_models": {"my-agent": "claude-opus-5-5[1m]"},
               "roles": {"prefix_map": {"mine-": "coder"}}, "statusline": {"max_width": 120}}
        self.assertEqual(config.audit(raw), [])

    def test_retired_model_pin(self):
        found = self._kinds({"pinned_models": {"coder-opus55": "Claude-Opus-4-1[1m]"}})
        self.assertEqual(found, {("retired_model", "pinned_models.coder-opus55")})

    def test_unknown_key(self):
        found = self._kinds({"statusline": {"old_knob": 1}, "gone_section": {"a": 1}})
        self.assertEqual(found, {("unknown_key", "statusline.old_knob"),
                                 ("unknown_key", "gone_section")})

    def test_past_default_incl_retired_key(self):
        found = self._kinds({"warmer": {"max_pings": 3}, "archive_transcripts": False,
                             "handoff": {"roles": ["expert"]}})
        self.assertEqual(found, {("past_default", "warmer.max_pings"),
                                 ("past_default", "archive_transcripts"),
                                 ("past_default", "handoff.roles")})

    def test_retired_key_other_value_is_unknown(self):
        self.assertEqual(self._kinds({"warmer": {"max_pings": 7}}),
                         {("unknown_key", "warmer.max_pings")})


class ResumeConfigTest(unittest.TestCase):
    """T4: resume block validation."""

    def test_resume_defaults(self):
        cfg = config.defaults()
        self.assertEqual(cfg["resume"]["liveness_min"], 5)
        self.assertEqual(cfg["resume"]["unstop"], True)

    def test_validate_accepts_resume(self):
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])

    def test_validate_rejects_bad_liveness_min(self):
        cfg = config.defaults()
        cfg["resume"]["liveness_min"] = -1
        problems = config.validate(cfg)
        self.assertTrue(any("resume.liveness_min" in p for p in problems))

    def test_validate_rejects_non_bool_unstop(self):
        cfg = config.defaults()
        cfg["resume"]["unstop"] = "yes"
        problems = config.validate(cfg)
        self.assertTrue(any("resume.unstop" in p for p in problems))
