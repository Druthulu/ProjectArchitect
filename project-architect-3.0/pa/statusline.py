"""The PA3 statusline: a five-line renderer and the ledger's live sampler.

Public:
    render(payload, cfg=None, user_show=None) -> str        the printable lines (no trailing "\\n")
    sample(payload, cfg=None) -> None       spool + (on a change) sqlite
    main(argv=None) -> int                  stdin -> print -> sample; always 0

    fmt_tokens(n), fmt_eta(epoch, now), fmt_elapsed(seconds)
    parse_epoch(value)                      epoch number | numeric string | ISO 8601
    scan_phase_plan(path)                   {phase, phase_name, done, total, next, titles}
    build_sample(payload, cfg)              the sample dict ``S`` of design D step 1
    windows_of(payload)                     {window: {pct, resets_at}}

Design: ``docs/pa3-build/design/A-ledger-statusline-hooks.md`` §D (renderer + sampler),
§B.2/§B.3/§B.4 (``running.json`` / ``summary.json`` / ``.run/status.json``),
§E.3-E.4 (the rows the sampler stores -- this milestone stores, never fits),
§G (``statusline``, ``sampling``, ``fit.drop_reset_pct``); layout: spec §3.7.

Hot path: this module imports ``json, os, sys, time`` plus ``pa.paths`` and
``pa.fsutil`` only.  ``sqlite3`` (via ``pa.db``), ``shutil`` (via
``fsutil.disk_free_mb``) and ``pa.summary`` are imported lazily on the ``change``
path, which happens a few times an hour.  ``re`` and ``datetime`` are never
imported: the PHASE_PLAN scan and the ISO parser are hand-rolled.

No transcript is ever opened.  Every file read is a small JSON document or one
linear scan of ``PHASE_PLAN.md``.
"""

import json
import os
import sys
import time

from . import fsutil
from . import paths

# --------------------------------------------------------------------------- ansi

RESET = "\033[0m"
GREEN = "\033[0;32m"
LIGHT_BLUE = "\033[0;94m"
BRIGHT_MAGENTA = "\033[0;95m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
MAGENTA = "\033[0;35m"
DIM = "\033[0;90m"
BOLD_YELLOW = "\033[1;33m"
BOLD_RED = "\033[1;31m"

SEP = " · "                      # " · "
BAR_FULL = "█"                   # █
BAR_EMPTY = "░"                  # ░
ARROW_BACK = "◀"                 # ◀
RESET_MARK = "↻"                 # ↻
FLAG = "⚑"                       # ⚑
WARN = "⚠"                       # ⚠
HANDOFF_MARK = "⇢handoff"        # ⇢handoff
DASH = "—"                       # —
APPROX = "≈"                     # ≈

EFFORT_COLORS = {"max": MAGENTA, "xhigh": BRIGHT_MAGENTA, "high": CYAN,  # was xhigh LIGHT_BLUE
                 "medium": DIM, "low": DIM}  # was YELLOW

STATUSLINE_DEFAULTS = {
    "lines": 7, "colors": True, "ctx_yellow": 200000, "ctx_red": 300000,  # was 6, was 5
    "win_yellow": 50, "win_red": 80, "show_modeled": False, "show_pace": True,  # was: show_modeled rendered "vs monolithic"; retired T10.c1
    "max_width": 130, "seven_day": "all",  # was the 7d parenthetical's level on line 1; retired to the Pace line (T1.c1)  # was "pace,used"
    "pace_yellow": 1.25, "pace_red": 1.75,  # was 1.0  # was 1.2
    "pace_left_green": 14, "pace_left_yellow": 10,
    "show": {
        "model": True, "effort": True, "ctx": True,
        "five_hour": True, "five_hour_pace": False, "seven_day_meter": True, "fable": True,
        "pace_line": True,
        "generation": True, "phase": True, "task": True,  # was on _line2; folded into the task block
        "agent": True, "progress": True, "next_task": True,  # was on _line2
        "session_cost": True, "session_saved": True, "session_window_saved": True,
        "session_usage": True,
        "project_usage": True, "project_window_saved": True,
        "project_window_saved_pct": True, "project_lifetime": True,
        "project_month": True, "project_accounts": True,
        "account_saved": True, "fable_saved": True, "month": True, "account_lifetime": True,
        "account_usage": True,
        "dollars": False, "pace_vanilla": True, "session_vanilla": True,
        "project_vanilla": True, "account_vanilla": True,
        "alerts": True, "task_block": True, "task_helpers": False,
        "task_project": True,
        "pace_used": True, "project_five_hour": True, "project_multiplier": True,  # fix-14
    },
    "fewer_numbers": False,  # fix-14: preset in sl_config, explicit show keys still win
}
# fix-14: show keys the ``fewer_numbers`` preset turns off
FEWER_NUMBERS_OFF = ("pace_used", "project_five_hour", "project_month", "project_multiplier",
                     "month", "account_usage", "account_saved", "account_lifetime",
                     "account_vanilla")
HEARTBEAT_S = 600
WARM_FRESH_S = 3300  # warmer.fire_1h_s: a last_ping older than this is stale (was 330: the retired loop's state file)
DROP_RESET_PCT = 2
FIVE_HOUR_S = 18000
DEFAULT_WINDOW_S = 604800
DISK_MIN_FREE_MB = 50

_JSON_CACHE = {}


# --------------------------------------------------------------------------- tiny helpers

def _num(value, default=None):
    """A float if ``value`` is a number or a numeric string, else ``default``."""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dig(obj, *keys):
    """``_dig(payload, "model", "id")`` without KeyError/TypeError."""
    node = obj
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _read_json(path, default=None):
    """``fsutil.read_json`` with a per-process (mtime, size) cache.

    render() and sample() run back to back in one process and read the same
    ``summary.json`` / ``running.json``; the cache halves those reads.
    """
    if not path:
        return default
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return default
    hit = _JSON_CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = fsutil.read_json(path, default)
    _JSON_CACHE[path] = (stamp, data)
    return data


def _log(event, **fields):
    """Log without importing ``pa.log`` on the happy path."""
    try:
        from . import log as _logmod

        _logmod.log(event, **fields)
    except Exception:
        pass


# --------------------------------------------------------------------------- formatting

def fmt_tokens(n):
    """1234567 -> ``1.2M``, 471873 -> ``472k``, 512 -> ``512`` (PA2 convention)."""
    value = _num(n, None)
    if value is None:
        return "?"
    value = float(value)
    if value >= 1000000:
        scaled = value / 1000000.0
        if abs(scaled - int(scaled)) < 0.05:
            return "%dM" % int(round(scaled))
        return "%.1fM" % scaled
    if value >= 1000:
        return "%dk" % int(value / 1000.0 + 0.5)
    return "%d" % int(value)


def fmt_elapsed(seconds):
    """Seconds -> ``12m`` / ``1h47m`` / ``2d03h`` (never negative)."""
    total = int(max(_num(seconds, 0) or 0, 0))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%dd%02dh" % (days, hours)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    return "%dm" % minutes


def fmt_eta(resets_at, now=None):
    """Countdown to a reset stamp (epoch or ISO); ``""`` when unknown."""
    epoch = parse_epoch(resets_at)
    if epoch is None:
        return ""
    return fmt_elapsed(epoch - (now if now is not None else time.time()))


def fmt_money(value, decimals=2):
    amount = _num(value, None)
    if amount is None:
        return ""
    return "$%.*f" % (decimals, amount)


def _days_from_civil(year, month, day):
    """Days since 1970-01-01 (Howard Hinnant's civil calendar algorithm)."""
    year -= 1 if month <= 2 else 0
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def parse_epoch(value):
    """Epoch seconds from a number, a numeric string or an ISO-8601 string.

    ``rate_limits[*].resets_at`` is an integer today but the ``model_scoped``
    bucket is documented as a string (design §0 V3, §H.2), so both shapes are
    accepted.  ``datetime`` stays out of the hot path.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
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
                if pos > 9:                     # after the date part
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
        hours = minutes = 0
        seconds = 0.0
        if clock:
            cp = clock.split(":")
            hours = int(cp[0])
            if len(cp) > 1:
                minutes = int(cp[1])
            if len(cp) > 2:
                seconds = float(cp[2])
        return (_days_from_civil(year, month, day) * 86400.0
                + hours * 3600 + minutes * 60 + seconds - offset)
    except (ValueError, IndexError):
        return None


def utc_stamp(now=None):
    """``2026-09-12T21:10:03.412Z`` -- the stamp a sample is keyed by.

    Milliseconds, not whole seconds: ``utilization`` is UNIQUE(session_id, ts,
    window) and the statusline refreshes on every API response, so a
    second-resolution stamp would silently drop refreshes -- including the
    ``prev``/``change`` pair that brackets a percent crossing, which is written
    by a single call.  Every other ledger writer keeps whole seconds; both
    forms sort and parse identically.
    """
    now = time.time() if now is None else now
    return "%s.%03dZ" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
                         int((now % 1) * 1000))


# --------------------------------------------------------------------------- config

def sl_config(cfg, user_show=None):
    """``config.statusline`` merged over :data:`STATUSLINE_DEFAULTS`.

    ``user_show``: the show keys the user set (config.json as stored); None treats
    ``statusline.show`` as the explicit layer. Under ``fewer_numbers`` the preset applies,
    then the explicit keys win (fix-14).
    """
    node = cfg.get("statusline") if isinstance(cfg, dict) else None
    out = dict(STATUSLINE_DEFAULTS)
    out["show"] = dict(STATUSLINE_DEFAULTS["show"])
    fewer = isinstance(node, dict) and bool(node.get("fewer_numbers"))
    if fewer and user_show is None:
        for sk in FEWER_NUMBERS_OFF:  # preset first; explicit show keys below still win
            out["show"][sk] = False
    if isinstance(node, dict):
        for key, default in STATUSLINE_DEFAULTS.items():
            if key not in node or node[key] is None:
                continue
            if key == "show":
                if isinstance(node[key], dict):
                    for sk, sv in node[key].items():
                        if sk in out["show"]:
                            out["show"][sk] = bool(sv)
                continue
            if isinstance(default, bool):
                out[key] = bool(node[key])
            elif isinstance(default, str):
                out[key] = str(node[key])
            else:
                out[key] = _num(node[key], default)
    if fewer and isinstance(user_show, dict):
        for sk in FEWER_NUMBERS_OFF:  # the merged show holds defaults: re-apply, then the user's keys
            out["show"][sk] = False
        for sk, sv in user_show.items():
            if sk in out["show"]:
                out["show"][sk] = bool(sv)
    out["max_width"] = int(out["max_width"] or 110)
    out["lines"] = int(out["lines"] or 4)
    return out


def _cfg_num(cfg, dotted, default):
    node = cfg if isinstance(cfg, dict) else {}
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    value = _num(node, None)
    return default if value is None else value


# --------------------------------------------------------------------------- painting

def _paint(text, color, colors):
    if not colors or not color or not text:
        return text
    return "%s%s%s" % (color, text, RESET)


def _seg(text, color, colors):
    """One segment: ``(visible text, color, painted text)``.

    Width arithmetic always uses the *visible* half, so ANSI escapes can never
    make a line look wider than it is.
    """
    return (text, color, _paint(text, color, colors))


def _seg_of(pieces, colors):
    """A composite segment built from ``[(text, color), ...]`` joined by a space."""
    pieces = [(t, c) for t, c in pieces if t]
    return (" ".join(t for t, _ in pieces), None,
            " ".join(_paint(t, c, colors) for t, c in pieces))


def _visible(segments):
    if not segments:
        return 0
    return sum(len(s[0]) for s in segments) + 3 * (len(segments) - 1)


def _join(segments, colors):
    sep = " %s " % _paint(SEP.strip(), DIM, colors) if colors else SEP
    return sep.join(s[2] for s in segments if s[0])


def _fit(segments, max_width, colors, shrink=None):
    """Trim a line to ``max_width`` visible characters.

    ``shrink`` is the index of the segment that gives up characters first (the
    task title on line 2); after that, trailing segments are dropped.
    """
    if max_width <= 0 or _visible(segments) <= max_width:
        return segments
    segments = list(segments)
    if shrink is not None and 0 <= shrink < len(segments):
        over = _visible(segments) - max_width
        text, color, _painted = segments[shrink]
        keep = len(text) - over - 1
        if keep >= 8:
            cut = text[:keep].rstrip() + "…"
            segments[shrink] = _seg(cut, color, colors)
            if _visible(segments) <= max_width:
                return segments
    while len(segments) > 1 and _visible(segments) > max_width:
        segments.pop()
    if _visible(segments) > max_width and segments:
        text, color, _painted = segments[0]
        segments[0] = _seg(text[:max(max_width - 1, 1)] + "…", color, colors)
    return segments


# --------------------------------------------------------------------------- payload readers

def _pct_of(node):
    """``used_percentage`` (0-100) or ``utilization`` (<=1 means a fraction)."""
    if not isinstance(node, dict):
        return None
    pct = _num(node.get("used_percentage"), None)
    if pct is not None:
        return pct
    util = _num(node.get("utilization"), None)
    if util is None:
        return None
    return util * 100.0 if util <= 1.0 else util


def windows_of(payload):
    """``{window: {"pct": float, "resets_at": epoch|None}}`` from ``rate_limits``.

    Window names follow the ``utilization.window`` vocabulary of design §B.1:
    the ``rate_limits`` keys verbatim (``five_hour``, ``seven_day``, ...) plus
    ``model_scoped:<display_name>`` for each entry of ``model_scoped``.
    """
    out = {}
    limits = _dig(payload, "rate_limits")
    if not isinstance(limits, dict):
        return out
    for key, node in limits.items():
        if key == "model_scoped" or not isinstance(node, dict):
            continue
        pct = _pct_of(node)
        if pct is None:
            continue
        out[str(key)] = {"pct": pct, "resets_at": parse_epoch(node.get("resets_at"))}
    scoped = limits.get("model_scoped")
    if isinstance(scoped, list):
        for item in scoped:
            if not isinstance(item, dict):
                continue
            pct = _pct_of(item)
            if pct is None:
                continue
            name = _text(item.get("display_name")) or _text(item.get("name")) or "unknown"
            out["model_scoped:%s" % name] = {"pct": pct,
                                             "resets_at": parse_epoch(item.get("resets_at"))}
    return out


def _fable_window(windows):
    """The ``model_scoped:*`` key whose display name mentions Fable, or None."""
    for key in windows:
        if key.startswith("model_scoped:") and "fable" in key.lower():
            return key
    return None


def project_dir_of(payload):
    """``workspace.project_dir`` else ``cwd`` else the process cwd."""
    for candidate in (_dig(payload, "workspace", "project_dir"),
                      payload.get("cwd") if isinstance(payload, dict) else None,
                      _dig(payload, "workspace", "current_dir")):
        text = _text(candidate)
        if text:
            return os.path.abspath(text)
    return os.path.abspath(os.getcwd())


# --------------------------------------------------------------------------- PHASE_PLAN

def scan_phase_plan(path):
    """One linear scan of ``PHASE_PLAN.md`` (design §D line 2).

    Returns ``{"phase", "phase_name", "done", "total", "next", "titles", "tasks"}``.
    ``tasks`` is an ordered list of ``{"id", "status", "title"}`` for the task block.
    Header grammar: ``# Phase <id> — <name> (…)``; task grammar:
    ``- T<id> | <status> | …`` with an optional ``| title: … |`` field.
    Hand-rolled so the hot path never imports ``re``.
    """
    out = {"phase": None, "phase_name": None, "done": 0, "total": 0,
           "next": None, "titles": {}, "tasks": []}
    first_queued = None
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return out
    try:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if out["phase"] is None and line.startswith("# Phase "):
                rest = line[8:].strip()
                head = rest.split(None, 1)
                out["phase"] = head[0] if head else None
                tail = head[1] if len(head) > 1 else ""
                tail = tail.lstrip("—-– ").strip()
                cut = tail.find("(")
                if cut >= 0:
                    tail = tail[:cut]
                out["phase_name"] = tail.strip() or None
                continue
            if not line.startswith("- T") or "|" not in line:
                continue
            fields = line.split("|")
            task_id = fields[0].strip()[2:].strip()
            if len(task_id) < 2 or not task_id[1].isdigit():
                continue
            status = fields[1].strip().lower() if len(fields) > 1 else ""
            if status != "superseded":          # a reopened or moved task's parent is not work left
                out["total"] += 1
            title = ""
            for field in fields[2:]:
                stripped = field.strip()
                if stripped[:6].lower() == "title:":
                    title = stripped[6:].strip()
                    out["titles"][task_id] = title
                    break
            out["tasks"].append({"id": task_id, "status": status, "title": title})
            if status == "done":
                out["done"] += 1
            elif status == "next" and out["next"] is None:
                out["next"] = task_id
            elif status == "queued" and first_queued is None:
                first_queued = task_id
    except (OSError, ValueError):
        pass
    finally:
        handle.close()
    if out["next"] is None:
        out["next"] = first_queued
    return out


# --------------------------------------------------------------------------- context

def _model_short(model):
    """``claude-fable-5-1[1m]`` -> ``fable``, ``claude-sonnet-5`` -> ``sonnet``, ``claude-opus-4-6`` ->
    ``opus55`` (the agent-name convention); anything else comes back as it is."""
    text = _text(model).split("[")[0].strip()
    parts = [p for p in text.split("-") if p]
    if parts and parts[0] == "claude":
        parts = parts[1:]
    if not parts:
        return text
    family = parts[0]
    digits = "".join(p for p in parts[1:] if p.isdigit() and len(p) <= 2)
    return family + digits if family == "opus" else family


def _agent_label(status, agent):
    """``expert-opus55 opus55/medium`` for the running subagent (design A line 2, 3.1 T10)."""
    who = _text(agent.get("agent_type")) or _text(status.get("expert_agent_type"))
    model = _model_short(_text(agent.get("model_seen")) or _text(agent.get("model_pinned")))
    effort = _text(agent.get("effort_seen"))
    if not (who or model or effort):
        return ""
    if model or effort:
        return ("%s %s/%s" % (who, model or "?", effort or "?")).strip()
    return who


def _warm_pings(project, now, run_id):
    """``pings`` of the running expert run in ``.run/warmer.json`` while its
    ``last_ping`` is fresh (3.9.5 T4; was the retired loop's state file), else 0."""
    if not project or not run_id:
        return 0
    doc = _read_json(os.path.join(project, ".run", "warmer.json"), None)
    runs = doc.get("runs") if isinstance(doc, dict) else None
    run = runs.get(run_id) if isinstance(runs, dict) else None
    if not isinstance(run, dict):
        return 0
    stamp = _num(run.get("last_ping"), None)
    count = _num(run.get("pings"), None)
    if stamp is None or count is None or now - stamp >= WARM_FRESH_S:
        return 0
    return int(count)


def _running_agent(running, sid, run_id):
    """``running.json`` -> (agent entry, expert flag) for this session.

    The session's own entry (key == sid) is never the agent, and an entry whose
    ``status`` is present and not ``running`` is not in flight (T27)."""
    session = _dig(running, "sessions", sid)
    if not isinstance(session, dict):
        return None, False
    agents = session.get("agents")
    agents = agents if isinstance(agents, dict) else {}
    agents = {k: v for k, v in agents.items()
              if k != sid and isinstance(v, dict)
              and v.get("status") in (None, "running")}
    expert = session.get("expert")
    key = run_id if run_id and run_id in agents else (expert if expert in agents else None)
    if key is None and len(agents) == 1:
        key = next(iter(agents))
    entry = agents.get(key) if key else None
    live = bool(expert) or bool(agents)
    return (entry if isinstance(entry, dict) else None), live


def _gather(payload, cfg):
    """Every file the renderer needs, read once (all small, none a transcript)."""
    sid = _text(payload.get("session_id")) if isinstance(payload, dict) else ""
    project = project_dir_of(payload)
    pj = paths.project_paths(project)
    pa_json = _read_json(os.path.join(project, ".claude", "pa.json"), None)
    phase_plan, replan, review = pj["phase_plan"], pj["replan"], pj["review"]
    if isinstance(pa_json, dict):
        # `pa.json.phase_ends_dir` is not always "phase-ends" (design B §G.14).
        phase_dir = _text(pa_json.get("phase_ends_dir")) or _text(pa_json.get("phase_dir"))
        if phase_dir:
            current = os.path.join(project, phase_dir, "current")
            phase_plan = os.path.join(current, "PHASE_PLAN.md")
            replan = os.path.join(current, "REPLAN.md")
            review = os.path.join(current, "REVIEW.md")
    pj = dict(pj, phase_plan=phase_plan, replan=replan, review=review)
    summary = _read_json(paths.summary_path(), None)
    running = _read_json(paths.running_path(), None)
    status = _read_json(pj["status_json"], None)
    account = (_dig(summary, "sessions", sid, "account")
               or _dig(running, "sessions", sid, "account"))
    # usage API cache: model_scoped windows from the OAuth endpoint (T1.c4)
    usage_api_windows = None
    try:
        ua_cache = _read_json(os.path.join(paths.ledger_dir(), "usage_api.json"), None)
        if isinstance(ua_cache, dict):
            usage_api_windows = ua_cache.get("windows")
    except Exception:
        pass
    return {
        "sid": sid, "project": project, "paths": pj, "pa_json": pa_json,
        "phase_plan": phase_plan, "summary": summary, "running": running,
        "status": status if isinstance(status, dict) else None, "account": account,
        "usage_api_windows": usage_api_windows if isinstance(usage_api_windows, dict) else None,
    }


# --------------------------------------------------------------------------- line 1

def _bar(pct, colors):
    """PA2's 10-cell gradient bar: cells 0-3 green, 4-6 yellow, 7-9 red."""
    filled = int((_num(pct, 0) or 0) / 100.0 * 10 + 0.5)
    filled = min(max(filled, 0), 10)
    plain = []
    painted = []
    for i in range(10):
        zone = GREEN if i < 4 else (YELLOW if i < 7 else RED)  # was LIGHT_BLUE
        cell = BAR_FULL if i < filled else BAR_EMPTY
        plain.append(cell)
        painted.append(_paint(cell, zone if i < filled else DIM, colors))
    return "".join(plain), "".join(painted)


def _window_color(pct, sl):
    """Tier colour for a window's percent-used judgement (not the rendered number itself)."""
    value = _num(pct, 0) or 0
    if value >= sl["win_red"]:
        return RED
    if value >= sl["win_yellow"]:
        return YELLOW
    return GREEN  # was LIGHT_BLUE


def _pace(resets_at, pct, now, duration, started=None):
    """Consumption pace: share of the window used / share of the window elapsed.

    With ``started`` (the window's real start, T21.1.1.1.1) the elapsed share is
    max(1 day, now - started) / (resets - started): T26, the same one-day accrual
    floor as _day_rates, so pace == used/day / (100 / span days) (was: forward
    basis over the raw elapsed, T21.1.1.1.1). Without it, the nominal duration.
    """
    resets = parse_epoch(resets_at)
    if resets is None or not duration:
        return None
    if started is not None:
        span = resets - started
        elapsed = now - started
        if span <= 0 or elapsed <= span * 0.01:   # too early in the window to mean anything
            return None
        # was: ... / (elapsed / float(span))
        return (_num(pct, 0) or 0) / 100.0 / (max(86400.0, elapsed) / float(span))
    elapsed = duration - (resets - now)
    if elapsed <= duration * 0.01:        # too early in the window to mean anything
        return None
    return (_num(pct, 0) or 0) / 100.0 / (elapsed / float(duration))


def _window_start(payload_resets, account_window, duration, now):
    """The 7d window's real start (T21.1.1.1.1): resets - duration, or the summary's
    drop-aware ``started_at`` when that window's resets_at is within 3600 s of the
    payload's and it started before now (a later start wins)."""
    resets = parse_epoch(payload_resets)
    if resets is None:
        return None
    start = resets - duration
    if isinstance(account_window, dict):
        acc_resets = parse_epoch(account_window.get("resets_at"))
        acc_start = parse_epoch(account_window.get("started_at"))
        if (acc_resets is not None and acc_start is not None
                and abs(acc_resets - resets) <= 3600 and acc_start < now):
            start = max(start, acc_start)
    return start


def _day_rates(pct, start, resets, now):
    """``(used, left)`` per day (T21.1.1.1.1): used = pct / max(1 day, now - start);
    left = (100 - pct) / max(1 day, resets - now); used None when now >= resets or
    now <= start. Replaces _days_passed (was: pct / days begun, ceil)."""
    pct = _num(pct, 0) or 0
    used = None
    if start is not None and start < now < resets:
        used = pct / max(1.0, (now - start) / 86400.0)
    left = (100 - pct) / max(1.0, (resets - now) / 86400.0)
    return used, left


def _fmt_per_day(value):
    """Whole percents at every magnitude (T3 / was: one decimal below 10)."""
    return "%d%%/day" % int(round(value))


def _pace_color(pace, sl):
    """GREEN at or under pace_yellow, YELLOW up to pace_red, RED above."""
    if pace <= sl.get("pace_yellow", 1.0):
        return GREEN  # was LIGHT_BLUE
    if pace <= sl.get("pace_red", 1.2):
        return YELLOW
    return RED


_SEVEN_DAY_CAPS = {"all": 3, "pace,used": 2, "pace": 1, "none": 0}


def _seven_day_cap(sl):
    """Config ``statusline.seven_day`` -> initial level for _rate_pieces."""
    return _SEVEN_DAY_CAPS.get(sl.get("seven_day", "pace,used"), 2)


def _account_seven_day(ctx):
    """The summary's account ``windows.seven_day`` block for ctx, or None (ctx may be None)."""
    if not isinstance(ctx, dict):
        return None
    account = _account_block(ctx.get("summary"), ctx.get("account"))
    account_windows = account.get("windows") if account else None
    seven = account_windows.get("seven_day") if isinstance(account_windows, dict) else None
    return seven if isinstance(seven, dict) else None


def _rate_pieces(pace, resets_at, pct, now, duration, level=2, sl=None, ctx=None):  # was level=3
    """7d segment's ``(pace Nx · used/day · left/day)`` piece (T13): the pace as
    `_pace` computes it, plus average used per day and allowance left per day,
    both from this window's own pct and resets_at; each omitted on its own guard.
    ``level`` shrinks the piece when line 1 is too wide: 3 = all three, 2 = pace
    and used, 1 = pace only, 0 = nothing (the 7d segment is never dropped for it).
    The start is `_window_start`'s drop-aware one; rates from `_day_rates` (T21.1.1.1.1).
    """
    resets = parse_epoch(resets_at)
    parts = []
    if pace is not None and level >= 1:
        color = _pace_color(pace, sl) if sl else (RED if pace > 1.2 else DIM)
        parts.append(("pace %.1fx" % pace, color))
    if resets is not None and duration:
        start = _window_start(resets, _account_seven_day(ctx), duration, now)
        elapsed = now - start
        remaining = resets - now
        used, left = _day_rates(pct, start, resets, now)
        if (level >= 2 and used is not None                # was level >= 3
                and elapsed > (resets - start) * 0.01):    # same guard as _pace
            parts.append(("%s used" % _fmt_per_day(used), DIM))
        if level >= 3 and remaining > 0:                 # was remaining >= 21600 (6h guard, dropped T23)  # was level >= 2
            parts.append(("%s left" % _fmt_per_day(left), DIM))
    if not parts:
        return []
    last = len(parts) - 1
    return [(("(" if i == 0 else "· ") + text + (")" if i == last else ""), color)
            for i, (text, color) in enumerate(parts)]


def _line1(payload, ctx, cfg, sl, now):
    colors = sl["colors"]
    show = sl["show"]
    segments = []
    model = _text(_dig(payload, "model", "display_name")) or _text(_dig(payload, "model", "id"))
    effort = _text(_dig(payload, "effort", "level"))
    if model and show.get("model", True):
        segments.append(_seg(model, None, colors))
    if effort and show.get("effort", True):
        segments.append(_seg(effort, EFFORT_COLORS.get(effort.lower(), CYAN), colors))

    used = _num(_dig(payload, "context_window", "used_percentage"), None)
    if used is None:
        remaining = _num(_dig(payload, "context_window", "remaining_percentage"), None)
        used = (100.0 - remaining) if remaining is not None else None
    total_in = _num(_dig(payload, "context_window", "total_input_tokens"), None)
    size = _num(_dig(payload, "context_window", "context_window_size"), None)
    if total_in is not None and size and show.get("ctx", True):
        ctx_text = "%s/%s" % (fmt_tokens(total_in), fmt_tokens(size))
        if used is None:
            segments.append(_seg(ctx_text, MAGENTA, colors))
        else:
            bar_plain, bar_painted = _bar(used, colors)
            segments.append(("%s %s" % (ctx_text, bar_plain), None,
                             "%s %s" % (_paint(ctx_text, MAGENTA, colors), bar_painted)))

    windows = windows_of(payload)
    # merge model_scoped windows from usage API cache when payload lacks them (T1.c4)
    if ctx is not None and not any(k.startswith("model_scoped:") for k in windows):
        api_windows = ctx.get("usage_api_windows") if isinstance(ctx, dict) else None
        if isinstance(api_windows, dict):
            for _ak, _av in api_windows.items():
                if _ak.startswith("model_scoped:") and isinstance(_av, dict):
                    windows[_ak] = _av
    seven = None                          # (segment index, base pieces, pace, resets_at, pct, duration)
    show_key_map = {"five_hour": "five_hour", "seven_day": "seven_day_meter"}
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = windows.get(key)
        if not window or not show.get(show_key_map[key], True):
            continue
        pct = window["pct"]
        pieces = [("%s %d%%" % (label, int(round(pct))), None)]   # usage reading: no colour (T10.c1)
        eta = fmt_eta(window.get("resets_at"), now)
        if eta:
            pieces.append(("%s %s" % (RESET_MARK, eta), DIM))
        # 5h pace: still on line 1 behind show.five_hour_pace; 7d pace moved to the Pace line (T1.c1)
        if key == "five_hour" and show.get("five_hour_pace", False) and sl["show_pace"]:
            duration = _window_duration(cfg, key)
            pace = _pace(window.get("resets_at"), pct, now, duration)
            if pace is not None:
                pieces.append(("(pace %.1fx)" % pace, _pace_color(pace, sl)))
        segments.append(_seg_of(pieces, colors))

    fable = _fable_window(windows)
    if fable and show.get("fable", True):
        fpct = windows[fable]["pct"]
        # fable: label DIM, reading plain; no pace/severity colour (T22)
        segments.append(_seg_of([("fable", DIM), ("%d%%" % int(round(fpct)), None)], colors))
    return _join(_fit(segments, sl["max_width"], colors), colors)


# --------------------------------------------------------------------------- Pace line

def _left_color(per_day, sl):
    """GREEN at or above pace_left_green, YELLOW at or above pace_left_yellow, RED below.

    Colors the Pace line's ``/day left`` number (T10.c1)."""
    if per_day >= sl.get("pace_left_green", 14):
        return GREEN  # was LIGHT_BLUE
    if per_day >= sl.get("pace_left_yellow", 10):
        return YELLOW
    return RED


def _pace_rate_seg(number_text, suffix, number_color, colors):
    """A rate segment: the number in ``number_color``, the suffix DIM."""
    vis = "%s%s" % (number_text, suffix)
    if not colors:
        return (vis, None, vis)
    painted = "%s%s%s" % (_paint(number_text, number_color, True), RESET,
                          _paint(suffix, DIM, True))
    return (vis, None, painted)


def _line_pace(payload, ctx, cfg, sl, now):
    """Pace: 1.9x · 27%/day used · 10%/day left · N% fewer tokens than vanilla · lasts Mx longer."""
    colors = sl["colors"]
    if not sl["show"].get("pace_line", True):
        return ""
    windows = windows_of(payload)
    window = windows.get("seven_day")
    if not window:
        return ""
    pct = window["pct"]
    duration = _window_duration(cfg, "seven_day")
    resets = parse_epoch(window.get("resets_at"))
    # drop-aware real start (T21.1.1.1.1); None when resets_at is missing
    start = _window_start(resets, _account_seven_day(ctx), duration, now) if duration else None
    pace = _pace(window.get("resets_at"), pct, now, duration, started=start)
    parts = []
    if pace is not None:
        color = _pace_color(pace, sl)
        parts.append(_seg("%.1fx" % pace, color, colors))
    if resets is not None and duration:
        elapsed = now - start
        remaining = resets - now
        used, left = _day_rates(pct, start, resets, now)
        if (used is not None and elapsed > (resets - start) * 0.01 and pace is not None
                and sl["show"].get("pace_used", True)):
            num = "%d%%" % int(round(used))  # was pct / days begun (T21.1.1.1)
            parts.append(_pace_rate_seg(num, "/day used", _pace_color(pace, sl), colors))
        if remaining > 0:  # was remaining >= 21600 (6h guard, dropped T23)
            left_val = int(round(left))
            num = "%d%%" % left_val
            parts.append(_pace_rate_seg(num, "/day left", _left_color(left_val, sl), colors))
    if not parts:
        return ""
    # attach label to the first piece with a space, not a separator
    first = parts[0]
    label_seg = _seg("Pace:", DIM, colors)
    vis = "%s %s" % (label_seg[0], first[0])
    painted = "%s %s" % (label_seg[2], first[2])
    segments = [(vis, None, painted)] + parts[1:]
    # discount and multiplier: account 7d window scope (T1.c7); paid on the ledger price basis,
    # like the saving and the fit (fix-8)
    fold_idx = fold_seg = None
    if sl["show"].get("pace_vanilla", True) and ctx is not None:
        summary = ctx.get("summary")
        account = _account_block(summary, ctx.get("account"))
        account_windows = account.get("windows") if account else None
        if isinstance(account_windows, dict):
            seven = account_windows.get("seven_day")
            if isinstance(seven, dict):
                paid = _num(seven.get("ledger_cost_in_window"), None)
                if paid is None:  # old summary data
                    paid = _num(seven.get("cost_in_window"), None)
                net = _num(seven.get("cost_saved_measured"), None)
                v = _vanilla_pieces(paid, net, True, colors)
                if v:
                    segments.append(v[0])
                    fold_idx = len(segments)
                    segments.append(v[1])
                    fold_seg = v[2]
    return _join(_fit_fold(segments, sl["max_width"], colors, fold_idx, fold_seg), colors)


# --------------------------------------------------------------------------- line 2 (retired)

def _line2(payload, ctx, cfg, sl, now):
    # was: the phase line; folded into the task block (T1.c1)
    colors = sl["colors"]
    show = sl["show"]
    status = ctx["status"] or {}
    plan = scan_phase_plan(ctx["phase_plan"])
    segments = []
    shrink = None

    generation = _text(status.get("generation"))
    if generation and show.get("generation", True):
        segments.append(_seg(generation, CYAN, colors))

    phase = _text(status.get("phase")) or (plan["phase"] or "")
    phase_name = _text(status.get("phase_name")) or (plan["phase_name"] or "")
    if (phase or phase_name) and show.get("phase", True):
        segments.append(_seg(("Phase %s %s" % (phase, phase_name)).strip(), None, colors))
    elif not _text(status.get("task")):
        # between phases (planning a generation or a phase): say so instead of showing nothing
        launch = _read_json(os.path.join(ctx.get("project") or "", ".run", "launch.json"), None)
        mode = _text(_dig(launch, "mode"))
        if mode and mode != "router":
            segments.append(_seg("%s (planning)" % mode if mode.startswith("planner") else mode, DIM, colors))

    task = _text(status.get("task"))
    if task and show.get("task", True):
        title = _text(status.get("task_title")) or _text(plan["titles"].get(task))
        text = ("%s %s" % (task, title)).strip()
        started = parse_epoch(status.get("started"))
        if started is not None:
            text = "%s %s %s" % (text, ARROW_BACK, fmt_elapsed(now - started))
        shrink = len(segments)
        segments.append(_seg(text, None, colors))

    run_id = _text(status.get("run_id")) or None
    agent, _live = _running_agent(ctx["running"], ctx["sid"], run_id)
    task_done = any(t["id"] == task and t["status"] == "done" for t in plan["tasks"])
    if agent and not task_done and show.get("agent", True):
        # agent ctx figure dropped (T27): label, handoff mark, warm count only
        label = _agent_label(status, agent)
        if label:
            segments.append(_seg(label, CYAN, colors))
        if agent.get("handoff_fired"):
            segments.append(_seg(HANDOFF_MARK, YELLOW, colors))

    # window-gate pause (status.py window-gate): shown until the scheduled resume passes
    resume = parse_epoch(status.get("resume_at")) if _text(status.get("kind")) == "paused" else None
    if resume is not None and now < resume:
        segments.append(_seg("paused until %s" % time.strftime("%H:%M", time.localtime(resume)),
                             YELLOW, colors))

    warm = _warm_pings(ctx.get("project"), now, run_id)
    if warm:
        segments.append(_seg("warm ×%d" % warm, DIM, colors))

    if plan["total"] and show.get("progress", True):
        segments.append(_seg("%d/%d" % (plan["done"], plan["total"]), None, colors))
    if plan["next"] and show.get("next_task", True):
        segments.append(_seg("next %s" % plan["next"], DIM, colors))

    waiting = _text(status.get("waiting"))
    note = _text(status.get("note"))
    if not segments and (waiting or note):
        segments.append(_seg(waiting or note, DIM, colors))
    if not segments:
        return ""
    return _join(_fit(segments, sl["max_width"], colors, shrink), colors)


# --------------------------------------------------------------------------- line 3

def _account_block(summary, account):
    """``summary.accounts[account]``, or the summary's only account when ``account`` is unknown."""
    block = _dig(summary, "accounts", account) if account else None
    if not isinstance(block, dict):
        accounts = _dig(summary, "accounts")
        if isinstance(accounts, dict) and len(accounts) == 1:
            block = next(iter(accounts.values()))
    return block if isinstance(block, dict) else None


def _window_saved_segment(window, label, prefix_ok):
    """``~21% of 5h`` (ok) / ``≈21% of 5h`` (band) / ``— of 5h`` (none)."""
    if not isinstance(window, dict):
        return None
    quality = _text(window.get("fit_quality")).lower() or "none"
    pct = _num(window.get("pct_saved"), None)
    if quality == "none" or pct is None:
        return "%s of %s" % (DASH, label)
    mark = APPROX if quality == "band" else prefix_ok
    return "%s%d%% of %s" % (mark, int(round(pct)), label)


def _fable_saved_segment(windows):
    key = _fable_window(windows) if isinstance(windows, dict) else None
    if not key:
        return None
    window = windows.get(key) or {}
    quality = _text(window.get("fit_quality")).lower() or "none"
    pct = _num(window.get("pct_saved"), None)
    if quality == "none" or pct is None:
        return "fable %s" % DASH
    mark = APPROX if quality == "band" else "~"
    return "fable %s%d%%" % (mark, int(round(pct)))


def _live_saved(running, sid):
    """Σ ``saved_live_usd`` of this session's running agents (design §B.2)."""
    session = _dig(running, "sessions", sid)
    if not isinstance(session, dict):
        return 0.0, False
    agents = session.get("agents")
    if not isinstance(agents, dict):
        return 0.0, bool(session.get("expert"))
    total = 0.0
    for entry in agents.values():
        if isinstance(entry, dict):
            total += _num(entry.get("saved_live_usd"), 0.0) or 0.0
    return total, bool(agents) or bool(session.get("expert"))


def _project_key(path):
    """Normalize a session's ``project``/``cwd`` path into the ``summary.json``
    ``projects`` key (T12); mirrors ``pa.summary._project_key`` and
    ``pa.savings._norm_project`` (kept a separate three-line copy so this module's
    imports stay ``os, sys, time`` on the hot path)."""
    text = str(path or "").strip()
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text))


def _project_use_text(pct, quality, label):
    """``12% of 5h`` (a fit exists) or ``— of 5h`` (none) -- no quality mark (T12):
    the project's usage share is a plain number, the ``~``/``≈`` marks stay on the
    account's own saved-share segments (line 4)."""
    if pct is None or (_text(quality) or "none").lower() == "none":
        return "%s of %s" % (DASH, label)
    return "%d%% of %s" % (int(round(pct)), label)


def _saved_pct_text(saved_by_window, account_windows):
    """``(3% of 5h · 1% of 7d)`` -- saved dollars as usage percent through the fit.

    ``saved_by_window``: a number (used for all windows) or ``{window: usd}``.
    Omits a window whose fit_quality is ``"none"`` or whose saved amount is <= 0.
    """
    parts = []
    if not isinstance(account_windows, dict):
        return ""
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        win = account_windows.get(key)
        if not isinstance(win, dict):
            continue
        quality = (_text(win.get("fit_quality")) or "none").lower()
        ppd = _num(win.get("pct_per_dollar"), None)
        if quality == "none" or ppd is None:
            continue
        if isinstance(saved_by_window, dict):
            usd = _num(saved_by_window.get(key), None)
        else:
            usd = _num(saved_by_window, None)
        if usd is None or usd <= 0:
            continue
        pct = usd * ppd * 100.0
        parts.append("%d%% of %s" % (int(round(pct)), label))
    if not parts:
        return ""
    return "(%s)" % " · ".join(parts)


def _line_project(payload, ctx, cfg, sl, now):
    """Project: x/y of 5h · x/y of 7d · month ~Xw/Yw · lifetime ~Xw/Yw · N% fewer · lasts Mx longer."""
    colors = sl["colors"]
    show = sl["show"]
    segments = []
    summary = ctx["summary"]
    account = _account_block(summary, ctx["account"])
    account_windows = account.get("windows") if account else None
    pkey = _project_key(ctx.get("project"))
    project = _dig(summary, "projects", pkey) if pkey else None
    if not isinstance(project, dict):
        return ""                         # no project data -> skip the line entirely
    proj_windows = project.get("windows") or {}
    by_account = project.get("by_account")
    acct_slice = by_account.get(ctx.get("account")) if isinstance(by_account, dict) else None

    # windowed pairs: x = this account's usage share, y = this account's own saved share (T10, fix-7)
    if isinstance(account_windows, dict) and account_windows and show.get("project_usage", True):
        first = True
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            if key not in account_windows:
                continue
            if key == "five_hour" and not show.get("project_five_hour", True):
                continue
            pw = proj_windows.get(key) or {}
            # usage: this account's apportioned share (T10); fall back to cost through fit
            # when by_account is absent (backward compat with old summary data)
            x_pct = None
            if isinstance(acct_slice, dict):
                x_raw = _num((acct_slice.get("pct_used") or {}).get(key), None)
                if x_raw is not None:
                    x_pct = int(round(x_raw))
            elif not isinstance(by_account, dict):
                cu = _num(pw.get("cost_used"), None)
                x_pct = _cost_as_pct(cu, account_windows, key)
            # savings: this account's own saved in its window, through its fit (fix-7); fall
            # back to the project's net_saved when by_account is absent (old summary data)
            if isinstance(acct_slice, dict):
                ns = _num((acct_slice.get("cost_saved_measured") or {}).get(key), None)
            elif not isinstance(by_account, dict):
                ns = _num(pw.get("net_saved"), None)
            else:
                ns = None
            y_pct = _cost_as_pct(ns, account_windows, key) if ns and ns > 0 else None
            if x_pct is None and y_pct is None:
                continue
            x_str = "%d%%" % x_pct if x_pct is not None else DASH
            y_str = "%d%%" % y_pct if y_pct is not None else DASH
            seg = _xy_seg(x_str, y_str, " of %s" % label, colors)
            if first:
                label_seg = _seg("Project:", DIM, colors)
                vis = "%s %s" % (label_seg[0], seg[0])
                painted = "%s %s" % (label_seg[2], seg[2])
                segments.append((vis, None, painted))
                first = False
            else:
                segments.append(seg)

    # month and lifetime in weeks of allowance (T1.c7): the summary's per-window weeks (fix-9);
    # a weeks segment that comes first carries the "Project:" label (fix-9.c2)
    for show_key, blk, name in (
            ("project_month", project.get("period") if isinstance(project, dict) else None, "month"),
            ("project_lifetime", project.get("lifetime") if isinstance(project, dict) else None,
             "lifetime")):
        if not show.get(show_key, True):
            continue
        if (name == "month" and isinstance(blk, dict)
                and ctx.get("account") in (blk.get("unknown_accounts") or ())):
            blk = {"unknown": True}                         # 3.11 T9: the active account has no renewal day
        seg = _weeks_piece(blk, name, colors)
        if seg and not segments:
            label_seg = _seg("Project:", DIM, colors)
            seg = ("%s %s" % (label_seg[0], seg[0]), None, "%s %s" % (label_seg[2], seg[2]))
        if seg:
            segments.append(seg)
    lifetime = project.get("lifetime") if isinstance(project, dict) else None

    # discount and multiplier: project lifetime scope (T1.c7)
    fold_idx = fold_seg = None
    if show.get("project_vanilla", True) and isinstance(lifetime, dict):
        lt_cost = _num(lifetime.get("cost_used"), None)
        lt_saved = _num(lifetime.get("cost_saved_measured"), None)
        v = _vanilla_pieces(lt_cost, lt_saved, False, colors)
        if v:
            if not segments:
                label_seg = _seg("Project:", DIM, colors)
                vis = "%s %s" % (label_seg[0], v[0][0])
                painted = "%s %s" % (label_seg[2], v[0][2])
                segments.append((vis, None, painted))
            else:
                segments.append(v[0])
            if show.get("project_multiplier", True):
                fold_idx = len(segments)
                segments.append(v[1])
                fold_seg = v[2]

    # multi-account marker (T10)
    if show.get("project_accounts", True) and isinstance(by_account, dict) and len(by_account) > 1:
        if segments:
            segments.append(_seg("· %d accounts" % len(by_account), DIM, colors))

    if not segments:
        return ""
    return _join(_fit_fold(segments, sl["max_width"], colors, fold_idx, fold_seg), colors)


def _cost_as_pct(cost, account_windows, wkey):
    """Cost as usage percent of a window through the fit, or None."""
    if not isinstance(account_windows, dict) or cost is None or cost <= 0:
        return None
    win = account_windows.get(wkey)
    if not isinstance(win, dict):
        return None
    ppd = _num(win.get("pct_per_dollar"), None)
    quality = (_text(win.get("fit_quality")) or "none").lower()
    if quality == "none" or ppd is None:
        return None
    return int(round(cost * ppd * 100.0))


def _xy_seg(x_str, y_str, suffix, colors):
    """Segment for ``x/y of Xh``: x white, y LIGHT_BLUE (was GREEN), suffix DIM."""
    vis = "%s/%s%s" % (x_str, y_str, suffix)
    if not colors:
        return (vis, None, vis)
    painted = "%s%s%s%s%s" % (x_str, RESET, _paint("/%s" % y_str, LIGHT_BLUE, True),
                              RESET, _paint(suffix, DIM, True))
    return (vis, None, painted)


def _xy_money_seg(x_str, y_str, colors):
    """Segment for ``$cost/$saved``: x white, y LIGHT_BLUE (was GREEN)."""
    vis = "%s/%s" % (x_str, y_str)
    if not colors:
        return (vis, None, vis)
    painted = "%s%s%s" % (x_str, RESET, _paint("/%s" % y_str, LIGHT_BLUE, True))
    return (vis, None, painted)


def _vanilla_pieces(paid, net, full_text, colors, approx=""):
    """Discount and multiplier segments for the vanilla comparison.

    Returns ``(discount_seg, mult_seg, mult_fold_seg)`` or None when net <= 0 or
    paid == 0.  ``full_text`` selects 'fewer tokens than vanilla' vs 'fewer'.
    """
    if not paid or paid <= 0 or net is None or net <= 0:
        return None
    discount = int(round(100.0 * net / (paid + net)))
    mult = (paid + net) / paid
    d_num = "%s%d%%" % (approx, discount)
    d_suffix = " fewer tokens than vanilla" if full_text else " fewer"
    d_seg = _pace_rate_seg(d_num, d_suffix, LIGHT_BLUE, colors)  # was GREEN
    m_num = "%.1fx" % mult
    m_vis = "lasts %s longer" % m_num
    if not colors:
        m_seg = (m_vis, None, m_vis)
    else:
        m_seg = (m_vis, None,
                 "%s%s%s" % (_paint("lasts ", DIM, True),
                             _paint(m_num, LIGHT_BLUE, True),  # was GREEN
                             _paint(" longer", DIM, True)))
    m_fold = _seg("(%s)" % m_num, LIGHT_BLUE, colors)  # was GREEN
    return d_seg, m_seg, m_fold


def _vanilla_dash(colors):
    """``— fewer`` / ``lasts — longer`` / ``(—)``: the vanilla pieces when no savings figure is known (T11.1)."""
    d_seg = _pace_rate_seg(DASH, " fewer", LIGHT_BLUE, colors)
    m_vis = "lasts %s longer" % DASH
    if not colors:
        m_seg = (m_vis, None, m_vis)
    else:
        m_seg = (m_vis, None,
                 "%s%s%s" % (_paint("lasts ", DIM, True), _paint(DASH, LIGHT_BLUE, True),
                             _paint(" longer", DIM, True)))
    return d_seg, m_seg, _seg("(%s)" % DASH, LIGHT_BLUE, colors)


def _weeks_piece(blk, name, colors, cost_key="cost_used"):
    """``<name> ~Xw/Yw`` from ``blk``'s ``weeks_used``/``weeks_saved`` (fix-9: each seven-day
    instance at its own rate), ``≈`` when ``unrated_usd`` exceeds 10% of ``blk[cost_key]``;
    None when the fields are absent (old summary data), never today's rate."""
    if not isinstance(blk, dict):
        return None
    if blk.get("unknown"):                                  # 3.11 T9: no renewal day, no month
        label_s = _seg(name, DIM, colors)
        return ("%s %s" % (label_s[0], DASH), None, "%s %s" % (label_s[2], DASH))
    wu = _num(blk.get("weeks_used"), None)
    if wu is None or wu <= 0:
        return None
    ws = _num(blk.get("weeks_saved"), None)
    unrated = _num(blk.get("unrated_usd"), 0.0) or 0.0
    cost = _num(blk.get(cost_key), 0.0) or 0.0
    mark = APPROX if cost > 0 and unrated > 0.1 * cost else "~"
    uw = "%s%.1fw" % (mark, wu)
    if ws is not None and ws > 0:
        seg = _xy_seg(uw, "%.1fw" % ws, "", colors)
    else:
        seg = _seg(uw, None, colors)
    label_s = _seg(name, DIM, colors)
    return ("%s %s" % (label_s[0], seg[0]), None, "%s %s" % (label_s[2], seg[2]))


def _fit_fold(segments, max_width, colors, fold_idx=None, fold_seg=None):
    """``_fit`` with one foldable segment: compress it before dropping others."""
    if max_width <= 0 or _visible(segments) <= max_width:
        return segments
    segments = list(segments)
    if fold_idx is not None and fold_seg is not None and 0 <= fold_idx < len(segments):
        segments[fold_idx] = fold_seg
        if _visible(segments) <= max_width:
            return segments
    while len(segments) > 1 and _visible(segments) > max_width:
        segments.pop()
    if _visible(segments) > max_width and segments:
        text, color, _painted = segments[0]
        segments[0] = _seg(text[:max(max_width - 1, 1)] + "…", color, colors)
    return segments


def _flag_savings_missing(project, sid, detail, now):
    """Read-modify-write ``<project>/.run/health.json`` key ``savings_missing`` (T11.1).

    Other top-level keys are kept; ``since`` (and the reader's ``hook_repair``/``row``)
    survive while the flagged session is the same.
    """
    path = os.path.join(project, ".run", "health.json")
    doc = fsutil.read_json(path, None)
    doc = doc if isinstance(doc, dict) else {}
    prev = doc.get("savings_missing")
    same = isinstance(prev, dict) and prev.get("session") == sid
    since = _num(prev.get("since"), None) if same else None
    doc["savings_missing"] = {
        "session": sid, "since": since if since is not None else float(now), "detail": detail,
        "hook_repair": prev.get("hook_repair") if same else None,
        "row": prev.get("row") if same else None}
    fsutil.atomic_write_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")


def _repair_session(ctx, cfg, now):
    """Render-time repair (T11.1): no ``sessions[sid].windows`` in summary.json but the
    ledger has cost for this sid -> ``summary.rebuild(sessions_only=True)`` and re-read,
    at most once per sid per ``summary.session_rebuild_cooldown_s``; a block still missing
    after the rebuild writes the ``savings_missing`` health flag.  Never raises.
    """
    sid = ctx.get("sid")
    row = _dig(ctx.get("summary"), "sessions", sid) if sid else None
    if not sid or (isinstance(row, dict) and "windows" in row):
        return
    try:
        if not os.path.isfile(paths.db_path()):
            return
        cooldown = _cfg_num(cfg, "summary.session_rebuild_cooldown_s", 60)
        stamp = paths.state_path("session_rebuild", "%s.stamp" % sid)
        try:
            with open(stamp, encoding="utf-8") as fh:
                last = _num(fh.read().strip(), None)
        except OSError:
            last = None
        if last is not None and 0 <= now - last < cooldown:
            return
        fsutil.atomic_write_text(stamp, "%.3f\n" % now)
        from . import db
        from . import summary as summary_mod

        conn = db.connect(create=False)
        try:
            crow = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM turns WHERE session_id=?",
                                (sid,)).fetchone()
            ledger_cost = float(crow[0] or 0.0) if crow else 0.0
            srow = conn.execute("SELECT cost_usd FROM sessions WHERE session_id=?",
                                (sid,)).fetchone()
            if srow and _num(srow[0], None):
                ledger_cost = max(ledger_cost, float(srow[0]))
            if ledger_cost <= 0:
                return
            doc = summary_mod.rebuild(conn, cfg, sessions_only=True)
        finally:
            db.close(conn)
        if not isinstance(doc, dict):
            doc = _read_json(paths.summary_path(), None)
        ctx["summary"] = doc
        if not ctx.get("account"):
            ctx["account"] = _dig(doc, "sessions", sid, "account") or ctx.get("account")
        row = _dig(doc, "sessions", sid)
        _log("statusline.session_rebuild", sid=sid, ok=isinstance(row, dict) and "windows" in row)
        if not (isinstance(row, dict) and "windows" in row):
            _flag_savings_missing(ctx["project"], sid,
                                  "no windows block after rebuild; ledger cost $%.2f" % ledger_cost,
                                  now)
    except Exception as exc:
        _log("statusline.session_repair_failed", sid=sid, err=repr(exc))


def _line_session(payload, ctx, cfg, sl, now):
    """Session: x/y of 5h · x/y of 7d [· total ~Xw/Yw] [· $cost/$saved] · N% fewer · lasts Mx longer."""
    colors = sl["colors"]
    show = sl["show"]
    segments = []
    _repair_session(ctx, cfg, now)  # T11.1: rebuild a missing sessions[sid].windows once per cooldown
    summary = ctx["summary"]
    account = _account_block(summary, ctx["account"])
    account_windows = account.get("windows") if account else None

    cost = _num(_dig(payload, "cost", "total_cost_usd"), None)
    session_row = _dig(summary, "sessions", ctx["sid"]) or {}
    # session-wide vanilla-net walk; older summary.json: largest windows net (3.9.8 T4)
    measured = _num(session_row.get("saved_net"), None)
    if measured is None:
        nets = [_num(w.get("net_saved"), None) for w in (session_row.get("windows") or {}).values()
                if isinstance(w, dict)]
        nets = [n for n in nets if n is not None]
        measured = max(nets) if nets else None
    live, expert_running = _live_saved(ctx["running"], ctx["sid"])
    saved = (measured or 0.0) + live
    approx = "~" if expert_running else ""

    # windowed pairs from per-session data in summary (T1.c7)
    session_windows = session_row.get("windows") or {}
    no_block = "windows" not in session_row  # T11.1: no entry / no windows -> y and fewer/lasts print DASH
    if no_block and cost is not None and cost > 0:
        # summary.patch_session (SessionStart, pa/summary.py) writes account/started/
        # cost but no windows; until the first full rebuild this session's whole cost
        # sits inside the account's current windows, so approximate cost_used from the
        # session-wide cost (net_saved 0: the live-provisional add below still carries
        # the running savings) -- but only for a window that had already started by
        # the time this session did (T7.c2).
        started = _num(session_row.get("started"), None)
        fallback = {}
        for wkey in ("five_hour", "seven_day"):
            win = account_windows.get(wkey) if isinstance(account_windows, dict) else None
            w_started = _num(win.get("started_at"), None) if isinstance(win, dict) else None
            if w_started is None or started is None or w_started <= started:
                fallback[wkey] = {"cost_used": cost, "net_saved": 0.0}
        session_windows = fallback
    if show.get("session_usage", True) and isinstance(account_windows, dict):
        first = True
        for wkey, wlabel in (("five_hour", "5h"), ("seven_day", "7d")):
            sw = session_windows.get(wkey) or {}
            cu = _num(sw.get("cost_used"), None)
            ns = _num(sw.get("net_saved"), None)
            ns_windowed = ns
            if ns is not None and not no_block:
                ns = ns + live                    # add live provisional (not without a windows block, T11.1)
            x_pct = _cost_as_pct(cu, account_windows, wkey)
            y_pct = _cost_as_pct(ns, account_windows, wkey) if ns and ns > 0 and not no_block else None
            if y_pct == 0 and not ns_windowed:
                y_pct = None                      # live-only y rounding to 0 prints DASH, never 0% (T11.1)
            aw = account_windows.get(wkey)
            ledger_cost = _num(aw.get("ledger_cost_in_window"), None) if isinstance(aw, dict) else None
            if cu is not None and ledger_cost is not None and cu > ledger_cost:
                # session cost above its account's window cost: mixed accounts or windows (C0079)
                xy = ("?", "?")
            elif x_pct is None and y_pct is None:
                continue
            else:
                xy = ("%d%%" % x_pct if x_pct is not None else DASH,
                               "%d%%" % y_pct if y_pct is not None else DASH)
            x_str, y_str = xy
            seg = _xy_seg(x_str, y_str, " of %s" % wlabel, colors)
            if first:
                label_seg = _seg("Session:", DIM, colors)
                vis = "%s %s" % (label_seg[0], seg[0])
                painted = "%s %s" % (label_seg[2], seg[2])
                segments.append((vis, None, painted))
                first = False
            else:
                segments.append(seg)

    # straddle piece: total ~Xw/Yw when session predates the 7d window (T1.c7); the session
    # entry's per-window weeks (fix-9.c2), skipped without them
    # was _weeks(cost, _ppd_7d(account_windows)): earlier weeks shown in today's weeks
    if isinstance(account_windows, dict):
        seven = account_windows.get("seven_day")
        if isinstance(seven, dict):
            t0 = seven.get("started_at")
            session_started = _num(session_row.get("started"), None)
            if session_started is None:
                session_started = parse_epoch(_dig(ctx.get("status"), "started"))
            if t0 is not None and session_started is not None and session_started < t0:
                seg = _weeks_piece(session_row, "total", colors, cost_key="cost_usd")
                if seg:
                    vis, painted = seg[0], seg[2]
                    if not segments:
                        label_seg = _seg("Session:", DIM, colors)
                        vis = "%s %s" % (label_seg[0], vis)
                        painted = "%s %s" % (label_seg[2], painted)
                    segments.append((vis, None, painted))

    # dollars: $cost/$net behind show.dollars (default off, T1.c7)
    if show.get("dollars", False) and cost is not None:
        cost_str = "%s%s" % (approx, fmt_money(cost))
        if saved > 0:
            saved_str = "%s%s" % (approx, fmt_money(saved))
            seg = _xy_money_seg(cost_str, saved_str, colors)
        else:
            seg = _seg(cost_str, None, colors)
        if not segments:
            label_seg = _seg("Session:", DIM, colors)
            vis = "%s %s" % (label_seg[0], seg[0])
            painted = "%s %s" % (label_seg[2], seg[2])
            segments.append((vis, None, painted))
        else:
            segments.append(seg)

    # discount and multiplier: whole session scope (T1.c7)
    fold_idx = fold_seg = None
    if show.get("session_vanilla", True):
        v = None if no_block else _vanilla_pieces(cost, saved, False, colors, approx)
        if cost is not None and cost > 0 and (no_block or (not measured and (
                v is None or int(round(100.0 * saved / (cost + saved))) == 0))):
            v = _vanilla_dash(colors)  # no windows block, or live-only rounding to 0% fewer (T11.1)
        if v:
            if not segments:
                label_seg = _seg("Session:", DIM, colors)
                vis = "%s %s" % (label_seg[0], v[0][0])
                painted = "%s %s" % (label_seg[2], v[0][2])
                segments.append((vis, None, painted))
            else:
                segments.append(v[0])
            fold_idx = len(segments)
            segments.append(v[1])
            fold_seg = v[2]

    if not segments:
        return ""
    return _join(_fit_fold(segments, sl["max_width"], colors, fold_idx, fold_seg), colors)


# --------------------------------------------------------------------------- line 4

def _line3(payload, ctx, cfg, sl, now):
    """The ``--line3`` output: Pace, Session, Project, Account on separate lines."""
    parts = []
    pace = _line_pace(payload, ctx, cfg, sl, now)
    if pace:
        parts.append(pace)
    s = _line_session(payload, ctx, cfg, sl, now)
    if s:
        parts.append(s)
    p = _line_project(payload, ctx, cfg, sl, now)
    if p:
        parts.append(p)
    a = _line4(payload, ctx, cfg, sl, now)
    if a:
        parts.append(a)
    return "\n".join(parts) if parts else ""


def _line4(payload, ctx, cfg, sl, now):
    """Account: x/~y of 5h · x/~y of 7d · month ~Xw/Yw · lifetime ~Xw/Yw · N% fewer · lasts Mx longer."""
    colors = sl["colors"]
    show = sl["show"]
    segments = []
    summary = ctx["summary"]
    account = _account_block(summary, ctx["account"])
    windows = account.get("windows") if account else None

    # x = meter pct repeated, y = savings pct (fitted, marked ~)
    if isinstance(windows, dict) and show.get("account_usage", True):
        first = True
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            win = windows.get(key)
            if not isinstance(win, dict):
                continue
            # x = the meter's own reading
            pct_last = _num(win.get("pct_last"), None)
            x_str = "%d%%" % int(round(pct_last)) if pct_last is not None else DASH
            # y = savings pct (fitted), marked with ~
            quality = (_text(win.get("fit_quality")) or "none").lower()
            pct_saved = _num(win.get("pct_saved"), None)
            if quality == "none" or pct_saved is None:
                y_str = DASH
            else:
                mark = APPROX if quality == "band" else "~"
                y_str = "%s%d%%" % (mark, int(round(pct_saved)))
            seg = _xy_seg(x_str, y_str, " of %s" % label, colors)
            if first:
                label_seg = _seg("Account:", DIM, colors)
                vis = "%s %s" % (label_seg[0], seg[0])
                painted = "%s %s" % (label_seg[2], seg[2])
                segments.append((vis, None, painted))
                first = False
            else:
                segments.append(seg)

    # month and lifetime in weeks of allowance (T1.c7): the summary's per-window weeks (fix-9.c2)
    # was _weeks(cost, _ppd_7d(windows)): earlier weeks shown in today's weeks
    if show.get("month", True) and account:
        seg = _weeks_piece(account.get("period"), "month", colors)
        if seg:
            segments.append(seg)

    lifetime = account.get("lifetime") if account else None
    if show.get("account_lifetime", True):
        seg = _weeks_piece(lifetime, "lifetime", colors)
        if seg:
            segments.append(seg)

    # discount and multiplier: account lifetime scope (T1.c7)
    fold_idx = fold_seg = None
    if show.get("account_vanilla", True) and isinstance(lifetime, dict):
        lt_cost = _num(lifetime.get("cost_used"), None)
        lt_saved = _num(lifetime.get("cost_saved_measured"), None)
        v = _vanilla_pieces(lt_cost, lt_saved, False, colors)
        if v:
            segments.append(v[0])
            fold_idx = len(segments)
            segments.append(v[1])
            fold_seg = v[2]

    if not segments:
        return ""
    return _join(_fit_fold(segments, sl["max_width"], colors, fold_idx, fold_seg), colors)


# --------------------------------------------------------------------------- line 5

def _line5(payload, ctx, cfg, sl, now):
    """The alerts (T12, moved from line 4): waiting-on-you flags, model fallback, disk low."""
    if not sl["show"].get("alerts", True):
        return ""
    colors = sl["colors"]
    sid = ctx["sid"]
    items = []

    waiting = _read_json(paths.waiting_path(sid), None) if sid else None
    kind = agent = ""
    if isinstance(waiting, dict):
        kind = _text(waiting.get("kind") or waiting.get("reason") or waiting.get("what"))
        agent = _text(waiting.get("agent") or waiting.get("agent_type"))
    elif isinstance(waiting, str):
        kind = _text(waiting)
    low = kind.lower()
    if "replan" in low:
        items.append(_seg(FLAG + " waiting on you: REPLAN.md", BOLD_YELLOW, colors))
    elif "review" in low:
        items.append(_seg(FLAG + " waiting on you: REVIEW.md", BOLD_YELLOW, colors))
    elif kind:
        text = FLAG + " waiting on you: question"
        if agent:
            text += " (%s)" % agent
        items.append(_seg(text, BOLD_YELLOW, colors))

    if not items:
        started = parse_epoch(_dig(ctx["status"], "started")) if ctx["status"] else None
        if started is None:
            started = parse_epoch(_dig(ctx["summary"], "sessions", sid, "started")) if sid else None
        for name, path in (("REPLAN.md", ctx["paths"]["replan"]),
                           ("REVIEW.md", ctx["paths"]["review"])):
            stamp = fsutil.mtime(path)
            if stamp and (started is None or stamp >= started):
                items.append(_seg(FLAG + " waiting on you: " + name, BOLD_YELLOW, colors))
                break

    alerts = _dig(ctx["summary"], "alerts")
    if isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            if _text(alert.get("session_id")) != sid:
                continue
            if "model" not in _text(alert.get("kind")).lower():
                continue
            # a fallback alert is shown only while it still holds (the session still runs on the
            # wrong model) and only for an hour after it was recorded; resolved ones stay in the
            # ledger for reports but leave the statusline
            main_agent = _text(_dig(payload, "agent", "name"))
            if _text(alert.get("agent")) == main_agent and _mismatch(payload, cfg) is None:
                continue                      # about this main thread, and no longer true
            age = parse_epoch(alert.get("ts"))
            if age is not None and now - age > 3600:
                continue
            detail = "%s expected %s, ran on %s" % (
                _text(alert.get("agent")) or _text(alert.get("agent_type")) or "agent",
                _text(alert.get("expected")) or "?", _text(alert.get("seen")) or "?")
            items.append(_seg("%s model fallback: %s" % (WARN, detail), BOLD_RED, colors))
            break

    # memory_linked (T5): the SessionStart hook linked the memory dir; the index loads next start
    if isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, dict) or _text(alert.get("kind")) != "memory_linked":
                continue
            if _text(alert.get("session_id")) != sid:
                continue
            age = parse_epoch(alert.get("ts"))
            if age is not None and now - age > 3600:
                continue
            items.append(_seg("%s memory linked: restart the session to load the index" % WARN,
                              BOLD_YELLOW, colors))
            break

    if os.path.exists(paths.state_path("disk_low")):
        items.append(_seg("%s ledger: disk low" % WARN, BOLD_RED, colors))

    # seed_growth alert (T10): expert seed over threshold or growing fast
    pkey = _project_key(ctx.get("project"))
    if pkey and isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            if _text(alert.get("kind")) != "seed_growth":
                continue
            if _project_key(alert.get("project")) != pkey:
                continue
            parts = []
            for s in (alert.get("suspects") or []):
                if isinstance(s, dict):
                    sz = s.get("size", 0)
                    parts.append("%s %s" % (s.get("path", "?"), fmt_tokens(sz) if sz else "0"))
            detail = "seed growth: median %dk max %dk" % (
                int((alert.get("median") or 0) / 1000),
                int((alert.get("max") or 0) / 1000))
            if parts:
                detail += " (%s)" % ", ".join(parts[:3])
            items.append(_seg("%s %s" % (WARN, detail), BOLD_RED, colors))
            break

    if not items:
        return ""
    return _join(_fit(items, sl["max_width"], colors), colors)


# --------------------------------------------------------------------------- task block

GLYPH_RUNNING = "▶"      # ▶
GLYPH_DONE = "✓"         # ✓
GLYPH_QUEUED = "·"       # ·
GLYPH_BLOCKED = "✗"      # ✗
GLYPH_NEXT = "▸"         # ▸
GLYPH_SUPERSEDED = "↷"   # ↷ reopened/moved: a normal terminal state (3.10 T25)

_TASK_GLYPHS = {
    "done": GLYPH_DONE, "next": GLYPH_NEXT, "queued": GLYPH_QUEUED,
    "blocked": GLYPH_BLOCKED, "running": GLYPH_RUNNING,
    "superseded": GLYPH_SUPERSEDED,  # was GLYPH_BLOCKED
}
_TASK_COLORS = {
    "done": GREEN, "next": GREEN, "queued": DIM,  # was done: DIM
    "blocked": RED, "running": GREEN, "superseded": DIM,  # was superseded: RED
}


def _short_title(title):
    """Text before the first ``:``, `` — `` or ``;``. The line's own max_width
    cut (not this function) truncates it further when the task line is too long."""
    for delim in (":", " %s " % DASH, ";"):
        pos = title.find(delim)
        if pos >= 0:
            title = title[:pos]
            break
    return title.strip()


def _task_project_name(ctx):
    """Project name from ``pa.json`` ``project``, falling back to the repo folder name."""
    pa_json = ctx.get("pa_json")
    if isinstance(pa_json, dict):
        name = _text(pa_json.get("project"))
        if name:
            return name
    project = ctx.get("project") or ""
    if project:
        return os.path.basename(project.rstrip("/\\")) or None
    return None


def _gen_open_phase(project):
    """First phase id under ``## Phases`` of ``GENERATION_PLAN.md`` whose status is not
    ``closed`` (missing status = open), else None (T13.c1).

    Mirrors ``tools/launch.py gen_phases``: ``- <id> <name> | … | status: <s>`` with
    id ``[0-9]+(\\.[0-9]+)*``. Hand-rolled (no ``re``); never raises on a bad file.
    """
    if not project:
        return None
    try:
        handle = open(os.path.join(project, "GENERATION_PLAN.md"), "r",
                      encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        inside = False
        for raw in handle:
            body = raw.rstrip("\r\n")
            if body.startswith("## "):
                inside = body[3:].strip().lower().startswith("phases")
                continue
            if not inside:
                continue
            line = body.lstrip()
            if not line.startswith("-") or not line[1:2].isspace():
                continue
            head = line[1:].split(None, 1)
            if len(head) < 2:
                continue
            pid = head[0]
            if not all(p and all(c in "0123456789" for c in p)
                                  for p in pid.split(".")):
                continue
            state = "open"
            pos = body.find("status:")
            if pos >= 0:
                token = body[pos + 7:].split(None, 1)
                if token:
                    state = token[0]
            if state != "closed":
                return pid
    except OSError:
        return None
    finally:
        handle.close()
    return None


def _task_block(payload, ctx, cfg, sl, now):
    """The task block (T10.c1): a header line, then the active task first, then
    the plan's not-done tasks in plan order, within the same line budget as
    before (T1.c1's window of 3). Done tasks are dropped unless the active task
    itself is done (it still shows, with its own done mark, and the open ones
    follow).

    The header line (T7.c1) carries project/phase/done-total, present with or
    without a running task; the running line carries only elapsed. Running
    helpers are indented under the active line.
    """
    colors = sl["colors"]
    max_w = sl["max_width"]
    status = ctx["status"] or {}
    # T28.c4: the phase is owned by `router_session`; any other session gets one beside line
    # fix-18: only while a phase is open; a null phase falls through to the no-phase header
    owner = _text(status.get("router_session"))
    if owner and ctx.get("sid") and ctx["sid"] != owner and _text(status.get("phase")):
        beside = SEP.join(p for p in (_task_project_name(ctx), _text(status.get("phase"))) if p)
        return [_paint(beside + " runs in another session", DIM, colors)]
    current_task = _text(status.get("task"))
    plan = scan_phase_plan(ctx["phase_plan"])
    tasks = plan.get("tasks") or []

    # header: <project> · Phase <n> · <done>/<total> (T7.c1, was on the running line)
    header_parts = []
    if sl["show"].get("task_project", True):
        pname = _task_project_name(ctx)
        if pname:
            header_parts.append(pname)
    if tasks:
        header_phase = _text(status.get("phase")) or plan.get("phase") or ""
        if header_phase:
            header_parts.append("Phase %s" % header_phase)
        if plan["total"]:
            header_parts.append("%d/%d" % (plan["done"], plan["total"]))
    else:
        # no task lines (T13.c1): the generation's open phase is being planned, or none is open
        open_phase = _gen_open_phase(ctx.get("project"))
        if open_phase:
            header_parts.extend(["Phase %s" % open_phase, "planning"])
        else:
            header_parts.append("no phase")
    header_text = SEP.join(header_parts)
    if max_w > 0 and len(header_text) > max_w:
        header_text = header_text[:max_w - 1] + "…"
    header = _paint(header_text, DIM, colors)
    if not tasks:
        return [header]
    task_ids = {t["id"] for t in tasks}
    if current_task not in task_ids:
        current_task = ""     # status names no task in the plan: nothing is "running" (T10.c2)

    window_size = min(3, len(tasks))
    active = next((t for t in tasks if t["id"] == current_task), None) if current_task else None
    block_tasks = [active] if active is not None else []
    for t in tasks:
        if active is not None and t["id"] == active["id"]:
            continue
        if t["status"] == "done":
            continue
        block_tasks.append(t)
        if len(block_tasks) >= window_size:
            break

    lines = [header]

    for t in block_tasks:
        tid = t["id"]
        st = t["status"]
        title = _short_title(t["title"])
        if tid == current_task and st != "done":
            st = "running"
        glyph = _TASK_GLYPHS.get(st, GLYPH_QUEUED)
        color = _TASK_COLORS.get(st, DIM)

        if st == "running":
            # current line: Tn ▶ title ◀ elapsed (project/phase/counter moved to the header, T7.c1)
            text = "%s %s %s" % (tid, glyph, title)
            started = parse_epoch(status.get("started"))
            if started is not None:
                text += " %s %s" % (ARROW_BACK, fmt_elapsed(now - started))
        else:
            # non-current: Tn glyph title (title DIM)
            text = "%s %s %s" % (tid, glyph, title)
            color = DIM  # title DIM for non-current tasks

        if max_w > 0 and len(text) > max_w:
            text = text[:max_w - 1] + "…"

        if colors:
            # Tn white (default), glyph in status colour, summary/rest DIM
            glyph_color = _TASK_COLORS.get(st, DIM)
            if st == "running":
                rest = title
                started_val = parse_epoch(status.get("started"))
                if started_val is not None:
                    rest += " %s %s" % (ARROW_BACK, fmt_elapsed(now - started_val))
                painted = "%s %s %s" % (tid, _paint(glyph, glyph_color, True),
                                        _paint(rest, DIM, True))
            else:
                painted = "%s %s %s" % (tid, _paint(glyph, glyph_color, True),
                                        _paint(title, DIM, True))
            lines.append(painted)
        else:
            lines.append(text)

        # children of the running task
        if tid == current_task:
            show = (cfg or {}).get("statusline", {}).get("show", {})
            # agent label, warm count on the current line's children
            run_id = _text(status.get("run_id")) or None
            agent, _live = _running_agent(ctx["running"], ctx["sid"], run_id)
            children = _task_children(ctx, status)
            if agent and t["status"] != "done":
                label = _agent_label(status, agent)
                # (text, colour): label unpainted, handoff mark YELLOW, warm DIM; agent ctx dropped (T27)
                agent_parts = []
                if label:
                    agent_parts.append((label, None))
                if agent.get("handoff_fired"):
                    agent_parts.append((HANDOFF_MARK, YELLOW))
                warm = _warm_pings(ctx.get("project"), now, run_id)
                if warm:
                    agent_parts.append(("warm ×%d" % warm, DIM))
                if agent_parts:
                    agent_parts[0] = ("  " + agent_parts[0][0], agent_parts[0][1])
                if agent_parts:
                    atext = SEP.join(t for t, _c in agent_parts)
                    cut = max_w > 0 and len(atext) > max_w
                    if not colors:
                        lines.append(atext[:max_w - 1] + "…" if cut else atext)
                    else:
                        # per piece (was flat CYAN), truncated at the same width
                        budget = max_w - 1 if cut else len(atext)
                        painted = []
                        for i, (t, c) in enumerate(agent_parts):
                            for text, col in (((SEP, None),) if i else ()) + ((t, c),):
                                piece = text[:max(budget, 0)]
                                budget -= len(piece)
                                painted.append(_paint(piece, col, colors))
                        lines.append("".join(painted) + ("…" if cut else ""))
            # helper lines gated by show.task_helpers (default off, T10.c1)
            if show.get("task_helpers", False):
                for child in children:
                    ctext = "  %s %s %s" % (child["agent_type"], child["status"],
                                             child.get("elapsed") or "")
                    if max_w > 0 and len(ctext) > max_w:
                        ctext = ctext[:max_w - 1] + "…"
                    ccolor = DIM if child.get("stale") else (GREEN if child["status"] == "running" else DIM)
                    lines.append(_paint(ctext, ccolor, colors) if colors and ccolor else ctext)
    return lines


def _task_children(ctx, status):
    """Children of the running task from ``running.json``.

    An entry whose transcript has not changed for more than ``liveness.dead_min``
    and whose last record is a tool result renders with a dim ``?`` after the
    elapsed time until the liveness sweep removes it (a cheap mtime check only;
    no transcript scan on the render path).
    """
    running = ctx["running"]
    sid = ctx["sid"]
    session = _dig(running, "sessions", sid)
    if not isinstance(session, dict):
        return []
    agents = session.get("agents")
    if not isinstance(agents, dict):
        return []
    expert_run = _text(status.get("run_id")) or _text(session.get("expert"))
    dead_s = 20 * 60.0  # liveness.dead_min default; no config on the render path
    children = []
    now = time.time()
    for run_id, entry in agents.items():
        if not isinstance(entry, dict):
            continue
        if run_id == expert_run:
            continue
        agent_type = _text(entry.get("agent_type")) or "agent"
        # determine status from entry
        if entry.get("ended"):
            child_status = "done"
        else:
            child_status = "running"
        started = parse_epoch(entry.get("started"))
        elapsed = fmt_elapsed(now - started) if started else ""
        # stale marker: transcript mtime older than dead_min -> dim "?"
        stale = False
        if child_status == "running":
            tp = _text(entry.get("transcript_path"))
            if tp:
                try:
                    mtime = os.path.getmtime(tp)
                    if (now - mtime) >= dead_s:
                        stale = True
                except OSError:
                    pass
        if stale and elapsed:
            elapsed = elapsed + "?"
        children.append({"agent_type": agent_type, "status": child_status,
                         "elapsed": elapsed, "stale": stale})
    return children


# --------------------------------------------------------------------------- render

def render(payload, cfg=None, user_show=None):
    """The statusline text: task block, model line, Pace, Session, Project, Account, alerts."""
    payload = payload if isinstance(payload, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    sl = sl_config(cfg, user_show)
    now = time.time()
    ctx = _gather(payload, cfg)

    governed = isinstance(ctx["pa_json"], dict)
    lines = []

    # task block comes FIRST (T1.c1)
    if governed and sl["show"].get("task_block", True):
        task_lines = _task_block(payload, ctx, cfg, sl, now)
        lines.extend(task_lines)

    # model line (line 1)
    lines.append(_line1(payload, ctx, cfg, sl, now))
    if sl["lines"] <= 1:
        return "\n".join(l for l in lines if l)

    # Pace line
    if sl["lines"] >= 2:
        line = _line_pace(payload, ctx, cfg, sl, now)
        if line:
            lines.append(line)

    # Session → Project → Account → alerts
    if sl["lines"] >= 3:
        line = _line_session(payload, ctx, cfg, sl, now)
        if line:
            lines.append(line)
    if sl["lines"] >= 4:
        line = _line_project(payload, ctx, cfg, sl, now)
        if line:
            lines.append(line)
    if sl["lines"] >= 5:
        line = _line4(payload, ctx, cfg, sl, now)
        if line:
            lines.append(line)
    if governed and sl["lines"] >= 6:
        line = _line5(payload, ctx, cfg, sl, now)
        if line:
            lines.append(line)
    return "\n".join(l for l in lines if l)


def _fallback(payload):
    """Line 1 from the payload alone -- used when render() raises."""
    try:
        model = _text(_dig(payload, "model", "display_name")) or "claude"
        total = _num(_dig(payload, "context_window", "total_input_tokens"), None)
        size = _num(_dig(payload, "context_window", "context_window_size"), None)
        if total is not None and size:
            return "%s %s %s/%s" % (model, SEP.strip(), fmt_tokens(total), fmt_tokens(size))
        return model
    except Exception:
        return "claude"


# --------------------------------------------------------------------------- sampler

def _scoped_from_cache(usage_cache, cfg, now, account):
    """``model_scoped:*`` windows of the usage-API cache when it is this account's and fresh."""
    if not isinstance(usage_cache, dict):
        return {}
    # an unstamped account cannot prove the cache is its own: never merge on ""
    if not _account_key(account) or _account_key(usage_cache.get("account")) != _account_key(account):
        return {}
    ts = _num(usage_cache.get("ts"), None)
    max_age = 2 * 60 * _cfg_num(cfg, "usage_api.poll_min", 15)
    if ts is None or not (0 <= now - ts <= max_age):
        return {}
    windows = usage_cache.get("windows")
    if not isinstance(windows, dict):
        return {}
    out = {}
    for key, node in windows.items():
        if not str(key).startswith("model_scoped:") or not isinstance(node, dict):
            continue
        pct = _num(node.get("pct"), None)
        if pct is None:
            continue
        out[str(key)] = {"pct": pct, "resets_at": parse_epoch(node.get("resets_at"))}
    return out


def build_sample(payload, cfg=None, now=None, account=None, usage_cache=None):
    """Design §D step 1: the sample ``S`` written to ``spool/<sid>.jsonl``.

    When the payload carries no ``model_scoped`` window, the ``model_scoped:*``
    entries of the usage-API cache (``usage_cache``, already read by the caller)
    are merged in if the cache is this account's and fresh (T18).
    """
    payload = payload if isinstance(payload, dict) else {}
    now = time.time() if now is None else now
    cache = _dig(payload, "prompt_cache") or {}
    windows = windows_of(payload)
    if not any(key.startswith("model_scoped:") for key in windows):
        windows.update(_scoped_from_cache(usage_cache, cfg, now, account))
    return {
        "ts": utc_stamp(now),
        "epoch": round(now, 3),
        "session_id": _text(payload.get("session_id")) or None,
        "account": account,
        "machine": paths.machine_tag(),
        "model": _text(_dig(payload, "model", "id")) or None,
        "agent": _text(_dig(payload, "agent", "name")) or None,
        "effort": _text(_dig(payload, "effort", "level")) or None,
        "session_cost": _num(_dig(payload, "cost", "total_cost_usd"), None),
        "windows": windows,
        "prompt_cache": {
            "misses": _num(cache.get("misses"), None),
            "last_miss_at": cache.get("last_miss_at"),
            "last_miss_cause": cache.get("last_miss_cause"),
            "recache_tokens_if_cold": _num(cache.get("recache_tokens_if_cold"), None),
            "expires_at": cache.get("expires_at"),
            "warm": cache.get("warm"),
        },
        "source": "statusline",
    }


def _windows_changed(prev, cur):
    """True when any window's pct or resets_at moved (design §D step 2)."""
    old = prev.get("windows") if isinstance(prev, dict) else None
    old = old if isinstance(old, dict) else {}
    new = cur.get("windows") or {}
    for key, node in new.items():
        before = old.get(key)
        if not isinstance(before, dict):
            return True
        if _num(before.get("pct"), None) != _num(node.get("pct"), None):
            return True
        if parse_epoch(before.get("resets_at")) != parse_epoch(node.get("resets_at")):
            return True
    return False


def _reasons(prev, cur, state, now, heartbeat):
    if not isinstance(prev, dict):
        return ["first"]
    found = []
    if _windows_changed(prev, cur):
        found.append("change")
    if (cur.get("model") or None) != (prev.get("model") or None):
        found.append("model")
    old_miss = _num(_dig(prev, "prompt_cache", "misses"), None)
    new_miss = _num(_dig(cur, "prompt_cache", "misses"), None)
    if old_miss is not None and new_miss is not None and new_miss > old_miss:
        found.append("miss")
    last_written = _num(state.get("last_written_ts"), None) if isinstance(state, dict) else None
    if last_written is None or (now - last_written) >= heartbeat:
        found.append("heartbeat")
    return found


def _primary(reasons):
    for name in ("change", "model", "miss", "first", "heartbeat"):
        if name in reasons:
            return name
    return reasons[0] if reasons else "heartbeat"


def sample(payload, cfg=None):
    """Design §D steps 1-4: spool every refresh worth keeping, sqlite on change."""
    payload = payload if isinstance(payload, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    sid = _text(payload.get("session_id"))
    if not sid:
        return None
    now = time.time()
    ctx_summary = _read_json(paths.summary_path(), None)
    ctx_running = _read_json(paths.running_path(), None)
    account = (_dig(ctx_summary, "sessions", sid, "account")
               or _dig(ctx_running, "sessions", sid, "account"))
    # When no Stop has fired since a mid-session account switch, refresh the
    # stamp ourselves so utilization rows carry the current account (cheap when
    # the credentials key is unchanged; skipped when no stamp exists yet).
    if account:
        try:
            from . import accounts as _accts
            refreshed, _changed, _prev = _accts.refresh_account(sid, cfg)
            if refreshed:
                account = refreshed
        except Exception:
            pass
    usage_cache = _read_json(os.path.join(paths.ledger_dir(), "usage_api.json"), None)
    cur = build_sample(payload, cfg, now=now, account=account, usage_cache=usage_cache)

    state_path = paths.session_state_path(sid)
    state = _read_json(state_path, None)
    state = state if isinstance(state, dict) else {}
    prev = state.get("sample") if isinstance(state.get("sample"), dict) else None
    heartbeat = _cfg_num(cfg, "sampling.heartbeat_s", HEARTBEAT_S)

    reasons = _reasons(prev, cur, state, now, heartbeat)
    written = []
    if reasons:
        primary = _primary(reasons)
        if "change" in reasons and prev is not None and not state.get("written"):
            bracket = dict(prev)
            bracket["reason"] = "prev"
            written.append(bracket)
        entry = dict(cur)
        entry["reason"] = primary
        if "miss" in reasons and primary != "miss":
            entry["also"] = "miss"
        mismatch = _mismatch(payload, cfg)
        if mismatch and primary in ("first", "model", "change"):
            entry["alert"] = mismatch
        written.append(entry)
        spool = paths.spool_path(sid)
        for line in written:
            try:
                fsutil.append_line(spool, json.dumps(line, ensure_ascii=False, default=str))
            except OSError as exc:
                _log("statusline.spool_failed", sid=sid, err=repr(exc))

    new_state = {
        "schema": 1,
        "sample": cur,
        "written": bool(written),
        "last_written_ts": now if written else state.get("last_written_ts"),
        "spool_offset": state.get("spool_offset") or 0,
        "disk_checked_ts": state.get("disk_checked_ts") or 0,
    }
    if "change" in reasons:
        try:
            _on_change(cfg, sid, prev, cur, new_state)
        except Exception as exc:            # never break the statusline
            _log("statusline.change_failed", sid=sid, err=repr(exc))
    try:
        fsutil.atomic_write_json(state_path, new_state)
    except OSError as exc:
        _log("statusline.state_failed", sid=sid, err=repr(exc))
    # poll usage API when payload has no model_scoped (T1.c4, fail-soft, never raising)
    try:
        rl = payload.get("rate_limits") if isinstance(payload, dict) else None
        if not isinstance(rl, dict) or not rl.get("model_scoped"):
            from . import usage_api
            usage_api.poll(cfg, account=account)
    except Exception:
        pass
    # sweep finished agents that missed their SubagentStop (T1.c6, at most once per 60 s)
    try:
        from . import liveness
        liveness.sweep_if_due(sid, cfg)
    except Exception:
        pass
    return None


def _mismatch(payload, cfg):
    """Design §D step 4: the main thread ran on a model it was not pinned to."""
    agent = _text(_dig(payload, "agent", "name"))
    model = _text(_dig(payload, "model", "id"))
    if not agent or not model:
        return None
    pinned = _dig(cfg, "pinned_models", agent)
    pinned = _text(pinned)
    if not pinned or pinned == model:
        return None
    from . import prices                  # lazy: the hot path keeps module-level imports minimal
    if prices.normalize_model(pinned) == prices.normalize_model(model):
        lost_window = "[1m]" in pinned.lower() and "[1m]" not in model.lower()
        if not lost_window:                # same model; a gained [1m] is not a fallback
            return None
    return {"kind": "model_mismatch", "agent": agent, "expected": pinned, "seen": model}


# --------------------------------------------------------------------------- change path

def _account_key(account):
    """Ledger key for an account that SessionStart has not stamped yet.

    ``window_instances`` has a composite TEXT primary key and SQLite treats two
    NULLs as distinct, so a NULL account would insert a fresh instance row on
    every change.  ``""`` keeps the upsert idempotent and still joins against
    the ``utilization`` rows written from the same samples; ``pa-ledger recalc``
    remaps it once the account is known.
    """
    return _text(account) or ""


def _window_duration(cfg, window):
    key = "five_hour" if window == "five_hour" else "default"
    return _cfg_num(cfg, "fit.window_durations_s.%s" % key,
                    FIVE_HOUR_S if key == "five_hour" else DEFAULT_WINDOW_S)


def _drain_spool(conn, sid, state):
    """Append-only drain of ``spool/<sid>.jsonl`` into ``utilization``.

    The read starts at the byte offset recorded in ``<sid>.last.json`` and stops
    after the last complete line, so a hook draining the same file concurrently
    can neither lose nor duplicate a row (``utilization`` is INSERT OR IGNORE on
    ``UNIQUE(session_id, ts, window)`` anyway).
    """
    from . import db

    path = paths.spool_path(sid)
    size = fsutil.file_size(path)
    offset = int(_num(state.get("spool_offset"), 0) or 0)
    if size < offset:
        offset = 0
    if size <= offset:
        return 0
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            blob = handle.read()
    except OSError:
        return 0
    end = blob.rfind(b"\n")
    if end < 0:
        return 0
    rows = 0
    for raw in blob[:end].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        windows = entry.get("windows")
        if not ts or not isinstance(windows, dict):
            continue
        for window, node in windows.items():
            if not isinstance(node, dict):
                continue
            resets = parse_epoch(node.get("resets_at"))
            db.insert_utilization(conn, {
                "ts": ts, "account": _account_key(entry.get("account")),
                "machine": entry.get("machine"),
                "session_id": sid, "window": window, "pct": _num(node.get("pct"), None),
                "resets_at": int(resets) if resets is not None else None,
                "session_cost": _num(entry.get("session_cost"), None),
                "model": entry.get("model"), "source": entry.get("source") or "statusline",
                "reason": entry.get("reason"),
            })
            rows += 1
        if entry.get("reason") == "miss" or entry.get("also") == "miss":
            cache = entry.get("prompt_cache") or {}
            db.insert_event(conn, "cache_miss", detail={
                "misses": cache.get("misses"), "cause": cache.get("last_miss_cause"),
                "at": cache.get("last_miss_at"),
                "recache_tokens_if_cold": cache.get("recache_tokens_if_cold"),
            }, session_id=sid, account=entry.get("account"), ts=ts)
        alert = entry.get("alert")
        if isinstance(alert, dict):
            db.insert_event(conn, "model_mismatch", detail=alert, session_id=sid,
                            account=entry.get("account"), ts=ts)
    state["spool_offset"] = offset + end + 1
    return rows


def _instances(conn, cfg, sid, prev, cur, account):
    """Design §E.3/§E.4 bookkeeping: one ``window_instances`` row per instance.

    This milestone *stores* instances; the fit itself (``pa.fit``) lands later,
    so ``pct_per_dollar``/``fit_*`` stay NULL.
    """
    from . import db

    drop_pct = _cfg_num(cfg, "fit.drop_reset_pct", DROP_RESET_PCT)
    old = (prev or {}).get("windows") or {}
    stamp = cur.get("ts") or utc_stamp()
    account = _account_key(account)
    made = 0
    for window, node in (cur.get("windows") or {}).items():
        resets = parse_epoch(node.get("resets_at"))
        if resets is None:
            continue
        resets = int(resets)
        before = old.get(window) if isinstance(old, dict) else None
        old_resets = parse_epoch(before.get("resets_at")) if isinstance(before, dict) else None
        reset_source = None
        started_at = int(resets - _window_duration(cfg, window))
        if old_resets is None or int(old_resets) != resets:
            reset_source = "resets_at"
        else:
            old_pct = _num(before.get("pct"), None)
            new_pct = _num(node.get("pct"), None)
            if old_pct is not None and new_pct is not None and (old_pct - new_pct) >= drop_pct:
                reset_source = "drop"
                started_at = int(parse_epoch(cur.get("epoch")) or time.time())
                db.insert_event(conn, "reset", detail={
                    "window": window, "resets_at": resets, "pct_from": old_pct,
                    "pct_to": new_pct, "source": "drop",
                }, session_id=sid, account=account, ts=stamp)
        existing = None
        try:
            existing = conn.execute(
                "SELECT first_seen, reset_source FROM window_instances "
                "WHERE account IS ? AND window=? AND resets_at=?",
                (account, window, resets)).fetchone()
        except Exception:
            existing = None
        row = {"account": account, "window": window, "resets_at": resets,
               "last_seen": stamp}
        if existing is None:
            row["first_seen"] = stamp
            row["started_at"] = started_at
            row["reset_source"] = reset_source or "resets_at"
            made += 1
        elif reset_source == "drop":
            row["started_at"] = started_at
            row["reset_source"] = "drop"
        db.upsert_window_instance(conn, row)
    return made


def _check_disk(cfg, state):
    """Refresh the ``state/disk_low`` marker line 4 reads.

    Change path only, and at most once per heartbeat: ``disk_free_mb`` imports
    ``shutil`` (~17 ms on this box), which has no business on a refresh that is
    only meant to touch small JSON files.
    """
    now = time.time()
    last = _num(state.get("disk_checked_ts"), 0) or 0
    if last and (now - last) < _cfg_num(cfg, "sampling.heartbeat_s", HEARTBEAT_S):
        return
    minimum = _cfg_num(cfg, "ledger.disk_min_free_mb", DISK_MIN_FREE_MB)
    marker = paths.state_path("disk_low")
    free = fsutil.disk_free_mb(paths.ledger_dir())
    state["disk_checked_ts"] = now
    try:
        if free and free < minimum:
            fsutil.atomic_write_text(marker, "%.1f\n" % free)
        elif os.path.exists(marker):
            os.remove(marker)
    except OSError:
        pass


def _on_change(cfg, sid, prev, cur, state):
    """The rare path: sqlite, spool drain, instance detection, summary rebuild."""
    from . import db

    conn = db.connect()
    try:
        if db.schema_version(conn) != db.SCHEMA_VERSION:
            db.migrate(conn)
        rows = _drain_spool(conn, sid, state)
        made = _instances(conn, cfg, sid, prev, cur, cur.get("account"))
        try:
            conn.commit()
        except Exception:
            pass
        # Build union readers once per change (copy mode; a failure → local only)
        readers = None
        try:
            from . import config as _config
            extra = _config.extra_root_entries(cfg)
            if extra:
                readers = db.union_readers(extra, include_local=False)
        except Exception:
            readers = None
        # refit touched instances (lazy import; hot path and module-level imports untouched)
        account = _account_key(cur.get("account"))
        for window, node in (cur.get("windows") or {}).items():
            resets = parse_epoch(node.get("resets_at"))
            if resets is None:
                continue
            try:
                from . import fit as _fit
                f = _fit.refit_instance(conn, cfg, account, window, int(resets),
                                        readers=readers)
                try:
                    conn.commit()
                except Exception:
                    pass
                _log("statusline.refit", window=window,
                     method=f.get("method"), quality=f.get("quality"))
            except Exception:
                pass
        _rebuild_summary(conn, cfg, readers=readers)
        _log("statusline.change", sid=sid, util_rows=rows, instances=made)
    finally:
        # close union reader connections
        for rd in (readers or []):
            try:
                rc = rd.get("conn")
                if rc is not None:
                    db.close(rc)
            except Exception:
                pass
        db.close(conn)
    _check_disk(cfg, state)


def _rebuild_summary(conn, cfg, readers=None):
    """``pa.summary.rebuild(conn, cfg, windows_only=True)`` when it exists yet."""
    try:
        from . import summary as summary_mod
    except Exception as exc:
        _log("statusline.summary_absent", err=repr(exc))
        return False
    rebuild = getattr(summary_mod, "rebuild", None)
    if rebuild is None:
        _log("statusline.summary_absent", err="no rebuild()")
        return False
    try:
        rebuild(conn, cfg, windows_only=True, readers=readers)
        return True
    except TypeError:
        try:
            rebuild(conn, cfg)
            return True
        except Exception as exc:
            _log("statusline.summary_failed", err=repr(exc))
    except Exception as exc:
        _log("statusline.summary_failed", err=repr(exc))
    return False


# --------------------------------------------------------------------------- entry

def read_payload(stream=None):
    """Parse the statusline JSON from stdin; ``{}`` when it is unusable."""
    try:
        handle = stream if stream is not None else getattr(sys.stdin, "buffer", sys.stdin)
        raw = handle.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw) if raw and raw.strip() else {}
        _dump_input(raw, data)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dump_input(raw, data):
    """Probe for T14 (which fields the harness sends): when ``<project>/.run/statusline-dump``
    exists (created by hand), write ``raw`` verbatim to ``.run/statusline-input-<n>.json``,
    n = lowest free of 1..20; past 20 write nothing. Never raises."""
    try:
        run = os.path.join(project_dir_of(data if isinstance(data, dict) else {}), ".run")
        if not os.path.exists(os.path.join(run, "statusline-dump")):
            return
        for n in range(1, 21):
            path = os.path.join(run, "statusline-input-%d.json" % n)
            if not os.path.exists(path):
                with open(path, "x", encoding="utf-8", newline="") as fh:
                    fh.write(raw)
                return
    except Exception:
        pass


def _emit(text):
    try:
        sys.stdout.write((text or "") + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.buffer.write(((text or "") + "\n").encode("utf-8", "replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass


def main(argv=None):
    """Print first, sample second (design §D); always return 0."""
    try:                                  # UTF-8 + LF whatever the console default is
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass
    payload = read_payload()
    try:
        from . import config as config_mod

        cfg = config_mod.load()
        live = config_mod.enabled(cfg)
        user_sl = config_mod.user_layer().get("statusline")  # fix-14: the keys the user set
        user_show = user_sl.get("show") if isinstance(user_sl, dict) else None
        user_show = user_show if isinstance(user_show, dict) else {}
    except Exception as exc:
        _log("statusline.config_failed", err=repr(exc))
        cfg, live, user_show = {}, True, None
    try:
        if live:
            text = render(payload, cfg, user_show)
        else:
            sl = sl_config(cfg)
            text = _line1(payload, None, cfg, sl, time.time())
    except Exception as exc:
        _log("statusline.render_failed", err=repr(exc))
        text = None
    if not text:                          # always print at least a line 1
        text = _fallback(payload)
    _emit(text)
    if live:
        try:
            sample(payload, cfg)
        except Exception as exc:
            _log("statusline.sample_failed", err=repr(exc))
    return 0
