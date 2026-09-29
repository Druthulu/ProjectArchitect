"""Poll the Anthropic OAuth usage endpoint for model-scoped utilization.

The access token is read from ``<config_dir>/.credentials.json`` and used
once per poll; it is a local variable and is never written to the cache file,
the ledger directory, or any file under ``.run/``.

The endpoint rate-limits after a handful of calls per hour (HTTP 429 with a
``Retry-After`` header in seconds).  ``poll`` honours the header and backs off
at least 150 s on a 429.  The default poll interval is 15 minutes
(``usage_api.poll_min``).

Public:
    read_credentials(config_dir)      -> {token, subscription_type, rate_limit_tier}
    read_token(config_dir)            -> str | None
    fetch(token, timeout=5.0)         -> (dict, None) | (None, {status, retry_after})
    windows_from(body)                -> {window: {pct, resets_at, severity, is_active}}
    poll(cfg, now=None, force=False, account=None)  -> dict | None
"""

import json
import os
import sys
import time

_URL = "https://api.anthropic.com/api/oauth/usage"
_KIND_MAP = {"session": "five_hour", "weekly_all": "seven_day"}
_BACKOFF_S = 900       # 15 min on transient failures
_BACKOFF_AUTH_S = 3600  # 60 min on 401/403
_BACKOFF_429_MIN = 120  # minimum backoff on 429 before adding Retry-After
_BACKOFF_429_PAD = 30   # added after max(Retry-After, _BACKOFF_429_MIN)
_FALLBACK_POLL_MIN = 15  # minutes; used when usage_api.poll_min is absent


def read_credentials(config_dir):
    """Read ``<config_dir>/.credentials.json`` once.

    Returns ``{"token", "subscription_type", "rate_limit_tier"}`` from the
    ``claudeAiOauth`` keys ``accessToken``, ``subscriptionType`` and
    ``rateLimitTier``; each is None when the file is missing, unreadable, or
    lacks the key.  The token is never logged or written anywhere.
    """
    out = {"token": None, "subscription_type": None, "rate_limit_tier": None}
    path = os.path.join(config_dir, ".credentials.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return out
    for key, src in (("token", "accessToken"), ("subscription_type", "subscriptionType"),
                     ("rate_limit_tier", "rateLimitTier")):
        val = oauth.get(src)
        out[key] = val if isinstance(val, str) and val else None
    return out


def read_token(config_dir):
    """Read the OAuth access token from ``<config_dir>/.credentials.json``.

    Returns the token string or None when the file is missing, unreadable,
    or lacks the ``claudeAiOauth.accessToken`` key.
    """
    return read_credentials(config_dir)["token"]


def fetch(token, timeout=5.0):
    """GET the usage endpoint.

    On success returns ``(body_dict, None)``.
    On an HTTP error returns ``(None, {"status": int, "retry_after": int|None})``.
    On any other failure returns ``(None, {"status": None, "error": class_name})``.
    """
    import urllib.request
    import urllib.error

    req = urllib.request.Request(_URL, method="GET", headers={
        "Authorization": "Bearer %s" % token,
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "pa3-usage-api/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            body = json.loads(raw)
            return (body if isinstance(body, dict) else None, None)
    except urllib.error.HTTPError as exc:
        retry_after = None
        try:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            if ra is not None:
                retry_after = int(ra)
        except (TypeError, ValueError):
            pass
        return (None, {"status": exc.code, "retry_after": retry_after})
    except Exception as exc:
        return (None, {"status": None, "error": type(exc).__name__})


def _parse_epoch(value):
    """Epoch seconds from a number or ISO-8601 string (mirrors statusline.parse_epoch)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip() if value else ""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        offset = 0.0
        if text.endswith("Z") or text.endswith("z"):
            text = text[:-1]
        else:
            for sign in ("+", "-"):
                pos = text.rfind(sign)
                if pos > 9:
                    tz = text[pos + 1:]
                    parts = tz.replace(":", "")
                    if parts.isdigit() and len(parts) in (2, 4):
                        hours = int(parts[:2])
                        minutes = int(parts[2:]) if len(parts) == 4 else 0
                        offset = (hours * 3600 + minutes * 60) * (1 if sign == "+" else -1)
                        text = text[:pos]
                    break
        date_part, sep, clock = text.partition("T")
        if not sep:
            date_part, sep, clock = text.partition(" ")
        ymd = date_part.split("-")
        if len(ymd) != 3:
            return None
        year, month, day = int(ymd[0]), int(ymd[1]), int(ymd[2])
        h = m = 0
        s = 0.0
        if clock:
            cp = clock.split(":")
            h = int(cp[0])
            if len(cp) > 1:
                m = int(cp[1])
            if len(cp) > 2:
                s = float(cp[2])
        year -= 1 if month <= 2 else 0
        era = (year if year >= 0 else year - 399) // 400
        yoe = year - era * 400
        doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        days = era * 146097 + doe - 719468
        return days * 86400.0 + h * 3600 + m * 60 + s - offset
    except (ValueError, IndexError):
        return None


def windows_from(body):
    """Map ``limits[]`` from the usage endpoint to the statusline vocabulary.

    ``session`` -> ``five_hour``, ``weekly_all`` -> ``seven_day``,
    ``weekly_scoped`` -> ``model_scoped:<display_name>``.
    """
    out = {}
    if not isinstance(body, dict):
        return out
    limits = body.get("limits")
    if not isinstance(limits, list):
        return out
    for item in limits:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if not isinstance(kind, str):
            continue
        pct = item.get("percent")
        if pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        resets_at = _parse_epoch(item.get("resets_at"))
        severity = item.get("severity")
        severity = severity if isinstance(severity, str) else None
        is_active = bool(item.get("is_active", False))

        if kind in _KIND_MAP:
            name = _KIND_MAP[kind]
        elif kind == "weekly_scoped":
            scope = item.get("scope")
            display = None
            if isinstance(scope, dict):
                model = scope.get("model")
                if isinstance(model, dict):
                    display = model.get("display_name")
            name = "model_scoped:%s" % (display if isinstance(display, str) and display
                                        else "unknown")
        else:
            continue

        out[name] = {"pct": pct, "resets_at": resets_at,
                     "severity": severity, "is_active": is_active}
    return out


def _write_cache(path, data):
    """Write the cache file atomically (never contains the token)."""
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def poll(cfg, now=None, force=False, account=None):
    """Poll the usage API with caching and backoff.

    Honours ``usage_api.enabled`` (default True) and ``usage_api.poll_min``
    (default 15 minutes).  Reads the token once; the token is a local variable,
    never written to the cache.

    ``account`` is stamped on the cache on success; a cache stamped with a
    different account is refetched regardless of ``next_poll`` but still
    honours a 429/auth backoff (``backoff_until``).

    Returns the cached windows dict or None.
    """
    if now is None:
        now = time.time()

    ua = cfg.get("usage_api") if isinstance(cfg, dict) else {}
    ua = ua if isinstance(ua, dict) else {}
    if not ua.get("enabled", True):
        return None

    poll_min = _FALLBACK_POLL_MIN
    try:
        from . import config as _config
        val = _config.get(cfg, "usage_api.poll_min", None)
        if val is not None:
            poll_min = float(val)
    except Exception:
        pass
    poll_interval = poll_min * 60

    from . import paths as _paths

    cache_path = os.path.join(_paths.ledger_dir(), "usage_api.json")

    # read existing cache
    cache = None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = None
    if not isinstance(cache, dict):
        cache = {}

    # a cache from another account is stale for this one (T18)
    other = account is not None and cache.get("account") != account
    if other and not force:
        try:
            if now < float(cache.get("backoff_until") or 0):
                return None
        except (TypeError, ValueError):
            pass

    # return cached windows when within the poll interval
    next_poll = cache.get("next_poll")
    if not force and not other and next_poll is not None:
        try:
            if now < float(next_poll):
                return cache.get("windows")
        except (TypeError, ValueError):
            pass

    # determine config dir for the token
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        config_dir = _paths.claude_dir()

    token = read_token(config_dir)
    if token is None:
        cache["last_error"] = "no_token"
        cache["next_poll"] = now + _BACKOFF_S
        _write_cache(cache_path, cache)
        return None if other else cache.get("windows")

    # fetch (token is a local variable, used only for this request)
    body, err = fetch(token, timeout=5.0)

    if body is None or not isinstance(body, dict):
        # determine backoff from the error
        backoff = _BACKOFF_S
        error_text = "fetch_failed"
        if isinstance(err, dict):
            status = err.get("status")
            retry_after = err.get("retry_after")
            if status == 429:
                ra = max(retry_after or 0, _BACKOFF_429_MIN)
                backoff = ra + _BACKOFF_429_PAD
                if retry_after is not None:
                    error_text = "429 retry-after %d" % retry_after
                else:
                    error_text = "429"
            elif status in (401, 403):
                backoff = _BACKOFF_AUTH_S
                error_text = str(status)
            elif status is not None:
                error_text = str(status)
            else:
                error_text = err.get("error") or "fetch_failed"
        cache["last_error"] = error_text
        cache["next_poll"] = now + backoff
        if isinstance(err, dict) and err.get("status") in (429, 401, 403):
            cache["backoff_until"] = now + backoff
        _write_cache(cache_path, cache)
        return None if other else cache.get("windows")

    # success
    windows = windows_from(body)
    cache["ts"] = now
    cache["account"] = account
    cache["windows"] = windows
    cache["next_poll"] = now + poll_interval
    cache["last_error"] = None
    _write_cache(cache_path, cache)
    return windows
