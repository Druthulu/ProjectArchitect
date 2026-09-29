"""pa.usage_api: OAuth usage endpoint polling and window mapping."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pa import config, paths, statusline, usage_api  # noqa: E402

# The probed API response shape (2026-09-21)
PROBED_BODY = {
    "five_hour": {"utilization": 30.0, "resets_at": "2026-09-22T01:49:59.568236+00:00"},
    "seven_day": {"utilization": 55.0, "resets_at": "2026-09-27T02:00:00+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 30,
         "severity": "normal", "resets_at": "2026-09-22T01:49:59.568236+00:00",
         "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 55,
         "severity": None, "resets_at": "2026-09-27T02:00:00+00:00",
         "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 77,
         "severity": "warning",
         "resets_at": "2026-09-27T02:00:00.096504+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
         "is_active": True},
    ],
}


# --------------------------------------------------------------------------- windows_from

class WindowsFromTest(unittest.TestCase):

    def test_probed_shape_maps_three_windows(self):
        w = usage_api.windows_from(PROBED_BODY)
        self.assertEqual(len(w), 3)
        self.assertIn("five_hour", w)
        self.assertIn("seven_day", w)
        self.assertIn("model_scoped:Fable", w)

    def test_fable_window_fields(self):
        w = usage_api.windows_from(PROBED_BODY)
        fable = w["model_scoped:Fable"]
        self.assertEqual(fable["pct"], 77.0)
        self.assertIsNotNone(fable["resets_at"])
        self.assertIsInstance(fable["resets_at"], float)
        self.assertEqual(fable["severity"], "warning")
        self.assertTrue(fable["is_active"])

    def test_session_maps_to_five_hour(self):
        w = usage_api.windows_from(PROBED_BODY)
        self.assertEqual(w["five_hour"]["pct"], 30.0)
        self.assertEqual(w["five_hour"]["severity"], "normal")
        self.assertFalse(w["five_hour"]["is_active"])

    def test_weekly_all_maps_to_seven_day(self):
        w = usage_api.windows_from(PROBED_BODY)
        self.assertEqual(w["seven_day"]["pct"], 55.0)

    def test_empty_body_returns_empty(self):
        self.assertEqual(usage_api.windows_from({}), {})
        self.assertEqual(usage_api.windows_from(None), {})
        self.assertEqual(usage_api.windows_from({"limits": "bad"}), {})

    def test_unknown_kind_skipped(self):
        body = {"limits": [{"kind": "unknown_bucket", "percent": 50}]}
        self.assertEqual(usage_api.windows_from(body), {})

    def test_missing_display_name_uses_unknown(self):
        body = {"limits": [{"kind": "weekly_scoped", "percent": 10,
                            "scope": {"model": {"id": None}}}]}
        w = usage_api.windows_from(body)
        self.assertIn("model_scoped:unknown", w)


# --------------------------------------------------------------------------- read_token

class ReadTokenTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-uapi-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_present(self):
        cred = {"claudeAiOauth": {"accessToken": "tok_abc123"}}
        with open(os.path.join(self.tmp, ".credentials.json"), "w") as f:
            json.dump(cred, f)
        self.assertEqual(usage_api.read_token(self.tmp), "tok_abc123")

    def test_absent(self):
        self.assertIsNone(usage_api.read_token(self.tmp))

    def test_no_key(self):
        with open(os.path.join(self.tmp, ".credentials.json"), "w") as f:
            json.dump({"other": "stuff"}, f)
        self.assertIsNone(usage_api.read_token(self.tmp))

    def test_empty_token(self):
        cred = {"claudeAiOauth": {"accessToken": ""}}
        with open(os.path.join(self.tmp, ".credentials.json"), "w") as f:
            json.dump(cred, f)
        self.assertIsNone(usage_api.read_token(self.tmp))

    def test_credentials_all_keys(self):
        cred = {"claudeAiOauth": {"accessToken": "tok_abc123", "subscriptionType": "max",
                                  "rateLimitTier": "default_claude_max_20x"}}
        with open(os.path.join(self.tmp, ".credentials.json"), "w") as f:
            json.dump(cred, f)
        self.assertEqual(usage_api.read_credentials(self.tmp),
                         {"token": "tok_abc123", "subscription_type": "max",
                          "rate_limit_tier": "default_claude_max_20x"})

    def test_credentials_missing(self):
        want = {"token": None, "subscription_type": None, "rate_limit_tier": None}
        self.assertEqual(usage_api.read_credentials(self.tmp), want)
        with open(os.path.join(self.tmp, ".credentials.json"), "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": "t"}}, f)
        self.assertEqual(usage_api.read_credentials(self.tmp), dict(want, token="t"))


# --------------------------------------------------------------------------- poll

class PollTest(unittest.TestCase):
    """poll() with urllib.request.urlopen monkeypatched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-uapi-poll-")
        self.ledger = os.path.join(self.tmp, "usage-ledger")
        self.config_dir = os.path.join(self.tmp, "claude-config")
        os.makedirs(self.ledger)
        os.makedirs(self.config_dir)
        self._ledger_env = os.environ.get("PA_LEDGER_DIR")
        self._config_env = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        os.environ["CLAUDE_CONFIG_DIR"] = self.config_dir
        # write a credentials file
        cred = {"claudeAiOauth": {"accessToken": "tok_test_secret"}}
        with open(os.path.join(self.config_dir, ".credentials.json"), "w") as f:
            json.dump(cred, f)
        self.cfg = config.defaults()
        self._orig_urlopen = None
        self._patch_urllib()

    def tearDown(self):
        self._unpatch_urllib()
        for key, val in [("PA_LEDGER_DIR", self._ledger_env),
                         ("CLAUDE_CONFIG_DIR", self._config_env)]:
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_urllib(self):
        import urllib.request
        self._orig_urlopen = urllib.request.urlopen
        self._call_count = 0

    def _unpatch_urllib(self):
        if self._orig_urlopen is not None:
            import urllib.request
            urllib.request.urlopen = self._orig_urlopen

    def _mock_urlopen(self, body_dict, status=200, retry_after=None):
        """Install a mock that returns body_dict as JSON on 200, raises HTTPError otherwise."""
        import urllib.request
        import urllib.error
        import io
        from email.message import Message

        call_count_ref = [0]

        def fake_urlopen(req, **kwargs):
            call_count_ref[0] += 1
            if status != 200:
                headers = Message()
                if retry_after is not None:
                    headers["Retry-After"] = str(retry_after)
                raise urllib.error.HTTPError(
                    req.full_url, status, "error", headers, io.BytesIO(b""))
            raw = json.dumps(body_dict).encode("utf-8")
            resp = io.BytesIO(raw)
            resp.read = resp.read
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        urllib.request.urlopen = fake_urlopen
        return call_count_ref

    def _mock_timeout(self):
        import urllib.request
        import socket

        def fake_urlopen(req, **kwargs):
            raise socket.timeout("timed out")

        urllib.request.urlopen = fake_urlopen

    def test_200_writes_cache_without_token(self):
        self._mock_urlopen(PROBED_BODY)
        now = time.time()
        result = usage_api.poll(self.cfg, now=now, force=True)
        self.assertIsNotNone(result)
        self.assertIn("model_scoped:Fable", result)
        # check cache file
        cache_path = os.path.join(self.ledger, "usage_api.json")
        self.assertTrue(os.path.exists(cache_path))
        with open(cache_path) as f:
            cache = json.load(f)
        # token must never appear in the cache
        with open(cache_path) as f:
            raw = f.read()
        self.assertNotIn("tok_test_secret", raw)
        self.assertIn("windows", cache)
        self.assertIsNone(cache.get("last_error"))

    def test_401_backoff_60_min(self):
        self._mock_urlopen(PROBED_BODY, status=401)
        now = 1000000.0
        result = usage_api.poll(self.cfg, now=now, force=True)
        # should return old windows (None since no prior cache)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertEqual(cache["last_error"], "401")
        self.assertAlmostEqual(cache["next_poll"], now + 3600, delta=1)

    def test_timeout_backoff_15_min(self):
        self._mock_timeout()
        now = 2000000.0
        result = usage_api.poll(self.cfg, now=now, force=True)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertIn("timeout", cache["last_error"].lower())
        self.assertAlmostEqual(cache["next_poll"], now + 900, delta=1)

    def test_cached_within_poll_min_no_second_request(self):
        counter = self._mock_urlopen(PROBED_BODY)
        now = 3000000.0
        usage_api.poll(self.cfg, now=now, force=True)
        self.assertEqual(counter[0], 1)
        # second poll within the interval: no request
        result = usage_api.poll(self.cfg, now=now + 60)
        self.assertEqual(counter[0], 1)
        self.assertIsNotNone(result)
        self.assertIn("model_scoped:Fable", result)

    def test_disabled(self):
        cfg = config.merge(self.cfg, {"usage_api": {"enabled": False}})
        self.assertIsNone(usage_api.poll(cfg, force=True))

    def test_429_with_retry_after(self):
        self._mock_urlopen(PROBED_BODY, status=429, retry_after=127)
        now = 4000000.0
        usage_api.poll(self.cfg, now=now, force=True)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertEqual(cache["last_error"], "429 retry-after 127")
        # next_poll = now + max(127, 120) + 30 = now + 157
        self.assertAlmostEqual(cache["next_poll"], now + 157, delta=1)

    def test_429_without_retry_after(self):
        self._mock_urlopen(PROBED_BODY, status=429)
        now = 5000000.0
        usage_api.poll(self.cfg, now=now, force=True)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertEqual(cache["last_error"], "429")
        # next_poll = now + max(0, 120) + 30 = now + 150
        self.assertAlmostEqual(cache["next_poll"], now + 150, delta=1)

    def test_200_after_429_clears_last_error(self):
        # first: a 429
        self._mock_urlopen(PROBED_BODY, status=429, retry_after=60)
        now = 6000000.0
        usage_api.poll(self.cfg, now=now, force=True)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertIsNotNone(cache["last_error"])
        # then: a 200 (force past the backoff)
        self._mock_urlopen(PROBED_BODY, status=200)
        usage_api.poll(self.cfg, now=now + 300, force=True)
        with open(cache_path) as f:
            cache = json.load(f)
        self.assertIsNone(cache["last_error"])
        self.assertIn("model_scoped:Fable", cache.get("windows", {}))

    def test_fallback_interval_15_min_when_key_absent(self):
        """When usage_api.poll_min is absent, the fallback is 15 min."""
        counter = self._mock_urlopen(PROBED_BODY)
        cfg = config.merge(self.cfg, {"usage_api": {"enabled": True}})
        # remove poll_min key entirely
        cfg["usage_api"].pop("poll_min", None)
        now = 7000000.0
        usage_api.poll(cfg, now=now, force=True)
        cache_path = os.path.join(self.ledger, "usage_api.json")
        with open(cache_path) as f:
            cache = json.load(f)
        # next_poll should be now + 15*60 = now + 900
        self.assertAlmostEqual(cache["next_poll"], now + 900, delta=1)
        # a poll at now+899 should use cache (no second request)
        usage_api.poll(cfg, now=now + 899)
        self.assertEqual(counter[0], 1)


# --------------------------------------------------------------------------- render merge

class RenderMergeTest(unittest.TestCase):
    """Payload without model_scoped + cache file -> line 1 shows fable N%."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pa3-uapi-render-")
        self.ledger = os.path.join(self.tmp, "usage-ledger")
        os.makedirs(self.ledger)
        self._env = os.environ.get("PA_LEDGER_DIR")
        os.environ["PA_LEDGER_DIR"] = self.ledger
        statusline._JSON_CACHE.clear()
        self.cfg = config.defaults()
        self.cfg["statusline"]["colors"] = False

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PA_LEDGER_DIR", None)
        else:
            os.environ["PA_LEDGER_DIR"] = self._env
        statusline._JSON_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cache(self, windows):
        cache = {"ts": time.time(), "windows": windows,
                 "next_poll": time.time() + 300, "last_error": None}
        path = os.path.join(self.ledger, "usage_api.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cache, f)

    def _payload(self, model_scoped=None):
        """Minimal payload with rate_limits but no model_scoped."""
        p = {
            "session_id": "test-sid",
            "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
            "context_window": {"total_input_tokens": 100000,
                               "context_window_size": 1000000},
            "rate_limits": {
                "five_hour": {"used_percentage": 19, "resets_at": int(time.time()) + 3600},
                "seven_day": {"used_percentage": 1, "resets_at": int(time.time()) + 86400},
            },
            "workspace": {"current_dir": self.tmp},
        }
        if model_scoped is not None:
            p["rate_limits"]["model_scoped"] = model_scoped
        return p

    def test_cache_merged_shows_fable(self):
        resets = time.time() + 86400
        self._write_cache({"model_scoped:Fable": {"pct": 77.0, "resets_at": resets,
                                                   "severity": "warning", "is_active": True}})
        text = statusline.render(self._payload(), self.cfg)
        line1 = text.split("\n")[0]
        self.assertIn("fable 77%", line1)

    def test_payload_model_scoped_wins(self):
        """When payload has model_scoped, the cache is not used."""
        resets = time.time() + 86400
        self._write_cache({"model_scoped:Fable": {"pct": 77.0, "resets_at": resets,
                                                   "severity": "warning", "is_active": True}})
        payload = self._payload(model_scoped=[
            {"display_name": "Fable", "used_percentage": 50,
             "resets_at": int(time.time()) + 86400}
        ])
        text = statusline.render(payload, self.cfg)
        line1 = text.split("\n")[0]
        self.assertIn("fable 50%", line1)
        self.assertNotIn("fable 77%", line1)

    def test_no_cache_no_fable(self):
        text = statusline.render(self._payload(), self.cfg)
        line1 = text.split("\n")[0]
        self.assertNotIn("fable", line1)


# --------------------------------------------------------------------------- config

class ConfigTest(unittest.TestCase):

    def test_defaults_contain_usage_api(self):
        cfg = config.defaults()
        ua = cfg.get("usage_api")
        self.assertIsNotNone(ua)
        self.assertTrue(ua["enabled"])
        self.assertIn("poll_min", ua)

    def test_validate_accepts_usage_api(self):
        cfg = config.defaults()
        self.assertEqual(config.validate(cfg), [])

    def test_validate_rejects_bad_usage_api(self):
        cfg = config.defaults()
        cfg["usage_api"]["enabled"] = "yes"
        problems = config.validate(cfg)
        self.assertTrue(any("usage_api.enabled" in p for p in problems))

    def test_validate_rejects_bad_poll_min(self):
        cfg = config.defaults()
        cfg["usage_api"]["poll_min"] = -1
        problems = config.validate(cfg)
        self.assertTrue(any("usage_api.poll_min" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
