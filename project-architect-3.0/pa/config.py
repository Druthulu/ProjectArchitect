"""``usage-ledger/config.json`` -- defaults, merge, validation, role mapping.

Public:
    DEFAULTS                        the design doc's section G document (template)
    defaults()                      fresh deep copy with machine/python filled in
    load(path=None, refresh=False)  config.json merged over defaults (cached)
    merge(base, over)               recursive dict merge (returns a new dict)
    validate(cfg)                   list of human-readable problems ([] == ok)
    save(cfg, path=None)            atomic write of slim(cfg) (user-set keys and overrides only)
    slim(cfg, dropped=None)         cfg minus current/past defaults; USER_KEYS always kept
    audit(raw)                      raw config.json -> [(kind, dotted, value, detail)]
                                    kinds past_default | retired_model | unknown_key
    enabled(cfg=None)               False when config.enabled is off or PA_LEDGER_OFF=1
    role_from_agent_type(agent_type, cfg=None)   -> expert|coder|retriever|critic|
                                                    router|planner|review|other
    pinned_model_for(agent_name, cfg=None)       -> model id or None
    ttl_for_role(role, cfg=None)                 -> "5m" | "1h"
    get(cfg, dotted_key, default=None)
    extra_root_entries(cfg)         extra_roots normalized to [{path, account, label}]
    PROJECT_DEFAULTS                pa.json keys read with a default (3.9.5 T2: ``toast``; T4: ``warmer``)
    toast_level(project_cfg)        -> "waiting" | "all" | "off"

The defaults are section G of ``docs/pa3-build/design/A-ledger-statusline-hooks.md``
verbatim; ``machine`` and ``python`` are filled from the running interpreter so
one file works on both the Windows and the WSL install.
"""

import json
import os
import sys

from . import fsutil
from . import paths

DEFAULTS = {
    "schema": 1,
    "enabled": True,
    "machine": None,                      # filled by defaults(): win|wsl|linux|mac
    "python": None,                       # filled by defaults(): sys.executable
    "installed_at": None,                 # T10: ISO UTC, set once by the root installer
    "renewal_day": None,                  # was 21; 3.11 T9: ignored, each account's own day is asked, never guessed
    "extra_roots": [],
    "extra_roots_mode": "copy",
    "accounts": {},
    "recalc_default_account": None,
    "roles": {
        "prefix_map": {
            "expert-": "expert",
            "coder-": "coder",
            "retriever-": "retriever",
            "critic": "critic",
            "router": "router",
            "pa-session": "router",
            "planner-": "planner",
            "review": "review",
        }
    },
    "pinned_models": {
        "router": "claude-sonnet-5-5",  # was claude-sonnet-5
        "pa-session": "claude-sonnet-5-5",  # was claude-sonnet-5
        "planner-gen": "claude-opus-5-5",  # was claude-fable-5-1
        "planner-phase": "claude-opus-5-5",  # was claude-fable-5-1
        "review": "claude-fable-5-1",
        "expert-fable": "claude-fable-5-1",
        "expert-opus55": "claude-opus-5-5",
        "expert-fable-5m": "claude-fable-5-1",
        "expert-opus55-5m": "claude-opus-5-5",
        "coder-opus46": "claude-opus-4-6",
        "coder-opus55": "claude-opus-5-5",
        "coder-sonnet": "claude-sonnet-5",
        "retriever-code": "claude-sonnet-5-5",  # was claude-haiku-4-5
        "retriever-digest": "claude-sonnet-5-5",  # was claude-sonnet-5
        "retriever-web": "claude-sonnet-5-5",  # was claude-sonnet-5
        "critic": "claude-fable-5-1",
    },
    "ttl_default": {
        "main": "1h", "expert": "1h", "coder": "5m",  # expert was "5m" (was "1h" before); coder was "1h"
        "critic": "5m", "retriever": "5m", "other": "5m",
    },
    "handoff": {"threshold_tokens": 350000, "roles": ["expert", "coder"], "text": None},  # was ["expert"]
    "fit": {
        "n_min": 8, "min_span_pct": 3, "min_crossings": 3, "good_crossings": 5,
        "good_span_pct": 5, "pool_instances": 3, "bracket_eps_usd": 0.05,
        "drop_reset_pct": 2, "family_min_usd": 5,
        "instance_min_age_s": 86400, "instance_min_samples": 50,  # was 36; T30.c2: young -> previous fit
        "instance_min_span_pct": 10, "ratio_min_pct": 5,  # T32.1: maturity span; meter/cost ratio past 5%
        # T32.1: ISO UTC; samples before it are another usage-limit regime (accounts.<email> wins)
        "regime_since": None, "pool_min_instance_samples": 8,
        "borrow_rates": True,  # fix-20: 7d rate borrowed from 5h for families the 7d fit cannot separate
        "window_durations_s": {"five_hour": 18000, "default": 604800},
    },
    "sampling": {"heartbeat_s": 600, "spool_max_lines": 5000},
    "summary": {"session_rebuild_cooldown_s": 60},  # 3.9.6 T11.1: statusline render-time sessions rebuild, once per sid per cooldown
    "modeled": {"ttl_old_s": 3600, "subtract_agent_seeds": True},  # T30: seed/reset keys retired (plain 1M cycle)
    "savings": {"kept_out_mode": "results_share", "tokenizer_uplift": 1.0,
                "track_retriever_live": False,
                "vanilla_ttl_s": 3600, "window_tokens": 1000000},
    "notify": {
        "toast": True, "toast_channel": "auto", "toast_min_interval_s": 20,
        "toast_on_stop_with_background": False, "discord_webhook": None,
        "discord_events": ["question", "waiting", "model_mismatch", "stop_failure", "new_account"],
    },
    "statusline": {
        "lifetime_since": None,  # ISO date override for era boundary; null = meta.created
        "lines": 7, "colors": True, "ctx_yellow": 200000, "ctx_red": 300000,  # was 6, was 5
        "win_yellow": 50, "win_red": 80, "show_modeled": False, "show_pace": True,  # was: show_modeled rendered "vs monolithic" on the session line; retired T10.c1
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
        "fewer_numbers": False,  # fix-14: preset of fewer figures; explicit show keys win
    },
    "ledger": {"lock_inline_max_bytes": 20000000, "disk_min_free_mb": 50,
               "wal_size_limit": 4194304, "retain_samples_days": 120},
    "seed": {"max_tokens": 40000, "growth_pct": 20},
    "usage_api": {"enabled": True, "poll_min": 15},  # was 5
    "liveness": {"dead_min": 20, "scan_bytes": 4194304},
    "warmer": {"fire_5m_s": 285, "fire_1h_s": 3300, "relay_lead_s": 25,
               "max_pings_5m": 12,  # the ceiling (3.15 T3); was 3 (max_pings), developer 2026-09-24
               "max_pings_5m_fallback": 3,  # 3.15 T3: cap when its inputs are missing
               "max_pings_1h": 3, "poll_s": 5},  # 3.9.5 T4
    "ttl_choice": {"min_runs": 5, "idle_min": 15},  # 3.15 T5: tools/expert_ttl.py, expert 1h vs -5m twin
    "resume": {"liveness_min": 5, "unstop": True},
    "discussion_allow_scripts": ["tools/analysis/*.py", "tools/plan_show.py", "tools/discussion.py",
                                 "tools/card.py slice|check", "tools/plan_edit.py show|grammar"],  # 3.9.7 T9
    "credit": {"spill_chars": 20000, "note_max_age_s": 3600},  # measured 2026-09-22: inline max 92998, spilled min 20114
    "audit": {"file_chars_flag": 500000, "growth_flag": 0.2,
              "noise_patterns": ["warning: in the working copy of", "CRLF will be replaced",
                                 "usage: ", "No such file or directory"],
              "router_ctx_flag": 300000},
    "install": {"source": "https://github.com/Druthulu/ProjectArchitect.git", "clone_dir": "pa3-src",
                "update_check_hours": 24, "fetch_timeout_s": 8},  # 3.7: the package clone under the config dir
    "card": {"max_chars": 7000},  # 3.7: HOW_WE_WORK.md cap, enforced by the archive (developer 2026-09-23)
    "guard": {"spilled_read": True, "whole_plan_roles": ["review", "critic"], "tool_source_roles": ["expert"],
              "whole_read_chars": 20000, "reread_deny": True},
    "doctor": {"hook_warn_ms": 2000},  # 3.9.6 T5: doctor WARNs when a hook's run() on the fixture exceeds this
    "bench": {"window_max_pct": 80},  # 3.9.6 T6: bench.py run refuses / clean-stops above this five-hour %
    "setup_offer": "on",  # 3.11 T7.1: the setup line in an ungoverned git repository at every start; "off" silences it
}

# Every default value a key had before its current one (from the ``# was`` comments
# in DEFAULTS above), retired keys included.  This is the single place future ``# was``
# values are recorded; when a default changes or a key retires, append the old value
# here rather than only adding a comment.  save() drops these from config.json.
SUPERSEDED = {
    "statusline.lines": [4, 5, 6],
    "statusline.seven_day": ["pace,used"],
    "statusline.pace_yellow": [1.0],
    "statusline.pace_red": [1.2],
    "ttl_default.expert": ["5m"],  # was ["1h"]
    "ttl_default.coder": ["1h"],
    "usage_api.poll_min": [5],
    "pinned_models.planner-gen": ["claude-fable-5-1"],
    "pinned_models.planner-phase": ["claude-fable-5-1"],
    "handoff.roles": [["expert"]],
    "discussion_allow_scripts": [["tools/analysis/*.py", "tools/plan_show.py"]],
    "statusline.show_modeled": [True],
    "statusline.max_width": [110],
    "warmer.max_pings": [3],                 # retired key: renamed max_pings_5m (66ae86f)
    "archive_transcripts": [False],          # retired key: dropped (7d39518)
    "modeled.seed_old": [150000],            # retired key: plain 1M cycle (T30)
    "modeled.old_reset": [900000],           # retired key: plain 1M cycle (T30)
    "modeled.vanilla_seed": [40000],         # retired key: plain 1M cycle (T30)
}

# Top-level keys only the user (or the installer, for them) sets: never pruned by save().
USER_KEYS = ("schema", "machine", "python", "installed_at", "renewal_day", "extra_roots",
             "accounts", "recalc_default_account")

# Maps whose children are user-named: audit() never calls a child an unknown key.
OPEN_MAPS = ("accounts", "pinned_models", "roles.prefix_map", "fit.window_durations_s")

# pa.json keys a hook reads with a default when the project file omits them
PROJECT_DEFAULTS = {
    "toast": "waiting",
    "warmer": "on",
}
TOAST_LEVELS = ("waiting", "all", "off")

_VALID_ROLES = ("router", "planner", "review", "expert", "coder", "retriever", "critic", "other")
_CACHE = {}


def _clone(obj):
    """Deep copy of JSON-shaped data (cheaper than importing ``copy``)."""
    try:
        return json.loads(json.dumps(obj))
    except (TypeError, ValueError):
        return obj


def defaults():
    """A fresh copy of DEFAULTS with ``machine`` and ``python`` resolved."""
    cfg = _clone(DEFAULTS)
    cfg["machine"] = paths.machine_tag()
    cfg["python"] = sys.executable or "python"
    return cfg


def toast_level(project_cfg):
    """pa.json ``toast`` -> ``waiting|all|off``: ``true`` all, ``false`` off, missing waiting."""
    default = PROJECT_DEFAULTS["toast"]
    val = project_cfg.get("toast", default) if isinstance(project_cfg, dict) else default
    if val is True:
        return "all"
    if val is False:
        return "off"
    val = str(val or "").strip().lower()
    return val if val in TOAST_LEVELS else default


def merge(base, over):
    """Recursive dict merge; ``over`` wins, lists/scalars replace."""
    out = _clone(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = _clone(v)
    return out


def load(path=None, refresh=False):
    """``config.json`` merged over :func:`defaults` (missing file == defaults)."""
    p = os.path.abspath(path or paths.config_path())
    if not refresh:
        hit = _CACHE.get(p)
        if hit is not None:
            return hit
    cfg = merge(defaults(), fsutil.read_json(p, {}) or {})
    _CACHE[p] = cfg
    return cfg


def user_layer(path=None):
    """``config.json`` as stored: only the keys the user set, no defaults (``{}`` if absent)."""
    raw = fsutil.read_json(os.path.abspath(path or paths.config_path()), {})
    return raw if isinstance(raw, dict) else {}


def save(cfg, path=None):
    """Write ``slim(cfg)`` atomically (installer / ``pa-ledger``); cache the full merged cfg.

    config.json holds only user-set keys and overrides; defaults live in the package.
    """
    p = os.path.abspath(path or paths.config_path())
    fsutil.atomic_write_json(p, slim(cfg), indent=2)
    _CACHE[p] = merge(defaults(), cfg)
    return p


_MISSING = object()


def _eq(a, b):
    """JSON-value equality; numbers compare as numbers, bools only with bools."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def _is_default(dotted, val):
    """True when *val* is the current default at *dotted* or one of its past defaults."""
    cur = get(DEFAULTS, dotted, _MISSING)
    if cur is not _MISSING and _eq(val, cur):
        return True
    return any(_eq(val, old) for old in SUPERSEDED.get(dotted, ()))


def slim(cfg, dropped=None):
    """*cfg* without keys equal to a current or past default (USER_KEYS always kept).

    Empty dicts are dropped.  When *dropped* is a list, the dotted path of every
    dropped leaf is appended to it.
    """
    def walk(node, prefix):
        out = {}
        for k, v in node.items():
            dotted = prefix + k
            if not prefix and k in USER_KEYS:
                out[k] = _clone(v)
            elif isinstance(v, dict) and v:
                sub = walk(v, dotted + ".")
                if sub:
                    out[k] = sub
            elif isinstance(v, dict) or _is_default(dotted, v):
                if dropped is not None:
                    dropped.append(dotted)
            else:
                out[k] = _clone(v)
        return out

    return walk(cfg, "") if isinstance(cfg, dict) else {}


def _model_norm(model):
    """``claude-opus-5-5[1m]`` -> ``claude-opus-5-5`` (tags stripped, lowercase)."""
    text = str(model or "").strip().lower()
    cut = text.find("[")
    return (text[:cut] if cut >= 0 else text).strip()


def audit(raw):
    """Findings over the raw config.json: ``[(kind, dotted, value, detail)]``.

    ``past_default``: a leaf (outside USER_KEYS) equal to a SUPERSEDED value, retired
    keys included.  ``unknown_key``: a path absent from DEFAULTS (children of
    OPEN_MAPS excepted) and not already a past default.  ``retired_model``: a
    ``pinned_models.<agent>`` whose model no current default pins.
    """
    out = []
    if not isinstance(raw, dict):
        return out
    current_models = set(_model_norm(v) for v in DEFAULTS["pinned_models"].values())

    def walk(node, prefix):
        for k in sorted(node):
            v = node[k]
            dotted = prefix + k
            if not prefix and k in USER_KEYS:
                continue
            olds = SUPERSEDED.get(dotted, ())
            if any(_eq(v, old) for old in olds):
                cur = get(DEFAULTS, dotted, _MISSING)
                detail = ("retired key" if cur is _MISSING
                          else "past default; now %s" % json.dumps(cur))
                out.append(("past_default", dotted, v, detail))
                continue
            parent = prefix[:-1]
            if parent not in OPEN_MAPS and get(DEFAULTS, dotted, _MISSING) is _MISSING:
                out.append(("unknown_key", dotted, v, "not read by the package"))
                continue
            if isinstance(v, dict) and isinstance(get(DEFAULTS, dotted), dict):
                walk(v, dotted + ".")

    walk(raw, "")
    pins = raw.get("pinned_models")
    if isinstance(pins, dict):
        for agent in sorted(pins):
            model = pins[agent]
            if isinstance(model, str) and _model_norm(model) not in current_models:
                out.append(("retired_model", "pinned_models." + agent, model,
                            "no current default pins %s" % _model_norm(model)))
    return out


def superseded(cfg):
    """Keys whose configured value is a superseded default (not the current one).

    Returns ``[(dotted_key, configured_value, current_default)]`` for every key in
    :data:`SUPERSEDED` whose value in *cfg* equals one of its old defaults and
    differs from the current default.  Numbers are compared as numbers, strings as
    strings.
    """
    defs = defaults()
    out = []
    for dotted, old_values in sorted(SUPERSEDED.items()):
        val = get(cfg, dotted)
        cur = get(defs, dotted)
        if val is None or cur is None:
            continue
        # normalise for comparison: compare numbers as float, strings as str
        def _eq(a, b):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return float(a) == float(b)
            return a == b
        if _eq(val, cur):
            continue
        if any(_eq(val, old) for old in old_values):
            out.append((dotted, val, cur))
    return out


def get(cfg, dotted_key, default=None):
    """``get(cfg, "handoff.threshold_tokens")`` without KeyErrors."""
    node = cfg
    for part in str(dotted_key).split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def extra_root_entries(cfg):
    """``extra_roots`` normalized to ``[{path, account, label}]``.

    Each entry is either a bare path string (``account``/``label`` come back
    None) or an object ``{"path": ..., "account": ..., "label": ...}``; a
    NULL-account row read from that root takes ``account`` (developer decides
    item 2).  Entries without a usable ``path`` are dropped.
    """
    out = []
    for item in (cfg.get("extra_roots") or []):
        if isinstance(item, dict):
            path = item.get("path")
            if not path:
                continue
            out.append({"path": path, "account": item.get("account"),
                        "label": item.get("label")})
        elif item:
            out.append({"path": item, "account": None, "label": None})
    return out


def enabled(cfg=None):
    """Global kill switch: ``config.enabled`` false or ``PA_LEDGER_OFF=1``."""
    if os.environ.get("PA_LEDGER_OFF", "").strip() not in ("", "0", "false", "False"):
        return False
    cfg = cfg if cfg is not None else load()
    return bool(cfg.get("enabled", True))


def validate(cfg):
    """Return a list of problems; empty list means the config is usable."""
    problems = []
    if not isinstance(cfg, dict):
        return ["config is not an object"]
    if cfg.get("schema") != 1:
        problems.append("schema: expected 1, got %r" % (cfg.get("schema"),))
    if not isinstance(cfg.get("enabled", True), bool):
        problems.append("enabled: expected a boolean")
    day = cfg.get("renewal_day")
    if day is not None and (not isinstance(day, int) or not 1 <= day <= 31):
        problems.append("renewal_day: expected 1..31 or null, got %r" % (day,))
    installed_at = cfg.get("installed_at")
    if installed_at is not None and not isinstance(installed_at, str):
        problems.append("installed_at: expected an ISO string or null, got %r" % (installed_at,))
    accounts = cfg.get("accounts")
    if isinstance(accounts, dict):
        for email, entry in accounts.items():
            if not isinstance(entry, dict):
                continue
            tier = entry.get("tier")
            if tier is not None and (not isinstance(tier, str) or not tier):
                problems.append("accounts.%s.tier: expected a string, got %r" % (email, tier))
            rs = entry.get("regime_since")
            if rs is not None and not isinstance(rs, str):
                problems.append("accounts.%s.regime_since: expected an ISO string or null, got %r"
                                % (email, rs))
            if entry.get("renewal_day") is None:
                continue
            aday = entry.get("renewal_day")
            if not isinstance(aday, int) or not 1 <= aday <= 31:
                problems.append("accounts.%s.renewal_day: expected 1..31, got %r" % (email, aday))
    roots = cfg.get("extra_roots", [])
    if not isinstance(roots, list):
        problems.append("extra_roots: expected a list of directories")
    else:
        for item in roots:
            if isinstance(item, dict):
                if not item.get("path"):
                    problems.append("extra_roots: object entry missing 'path'")
            elif not isinstance(item, str):
                problems.append(
                    "extra_roots: entries must be a path string or {path, account, label}")
    if cfg.get("extra_roots_mode") not in ("copy", "direct"):
        problems.append("extra_roots_mode: expected 'copy' or 'direct'")
    prefix_map = get(cfg, "roles.prefix_map", None)
    if not isinstance(prefix_map, dict) or not prefix_map:
        problems.append("roles.prefix_map: expected a non-empty object")
    else:
        for key, role in prefix_map.items():
            if role not in _VALID_ROLES:
                problems.append("roles.prefix_map[%s]: unknown role %r" % (key, role))
    for role, ttl in (get(cfg, "ttl_default", {}) or {}).items():
        if ttl not in ("5m", "1h"):
            problems.append("ttl_default[%s]: expected '5m' or '1h', got %r" % (role, ttl))
    thr = get(cfg, "handoff.threshold_tokens", None)
    if not isinstance(thr, int) or thr <= 0:
        problems.append("handoff.threshold_tokens: expected a positive integer")
    if not isinstance(get(cfg, "handoff.roles", None), list):
        problems.append("handoff.roles: expected a list of roles")
    for key in ("fit.n_min", "fit.min_crossings", "sampling.heartbeat_s",
                "ledger.lock_inline_max_bytes", "ledger.disk_min_free_mb"):
        val = get(cfg, key, None)
        if not isinstance(val, (int, float)) or val <= 0:
            problems.append("%s: expected a positive number, got %r" % (key, val))
    frs = get(cfg, "fit.regime_since", None)
    if frs is not None and not isinstance(frs, str):
        problems.append("fit.regime_since: expected an ISO string or null, got %r" % (frs,))
    pms = get(cfg, "fit.pool_min_instance_samples", None)
    if pms is not None and (isinstance(pms, bool) or not isinstance(pms, int) or pms <= 0):
        problems.append("fit.pool_min_instance_samples: expected a positive integer, got %r" % (pms,))
    if not isinstance(get(cfg, "pinned_models", None), dict):
        problems.append("pinned_models: expected an object")
    show = get(cfg, "statusline.show", None)
    if isinstance(show, dict):
        valid_show = set(DEFAULTS["statusline"]["show"])
        for key in show:
            if key not in valid_show:
                problems.append("statusline.show.%s: unknown segment key" % key)
    ls = get(cfg, "statusline.lifetime_since")
    if ls is not None and not isinstance(ls, str):
        problems.append("statusline.lifetime_since: expected an ISO string or null, got %r" % (ls,))
    ua = cfg.get("usage_api")
    if isinstance(ua, dict):
        if not isinstance(ua.get("enabled", True), bool):
            problems.append("usage_api.enabled: expected a boolean")
        pm = ua.get("poll_min")
        if pm is not None and (not isinstance(pm, (int, float)) or pm <= 0):
            problems.append("usage_api.poll_min: expected a positive number, got %r" % (pm,))
    liv = cfg.get("liveness")
    if isinstance(liv, dict):
        dm = liv.get("dead_min")
        if dm is not None and (not isinstance(dm, (int, float)) or dm <= 0):
            problems.append("liveness.dead_min: expected a positive number, got %r" % (dm,))
        sb = liv.get("scan_bytes")
        if sb is not None and (not isinstance(sb, (int, float)) or sb <= 0):
            problems.append("liveness.scan_bytes: expected a positive number, got %r" % (sb,))
    res = cfg.get("resume")
    if isinstance(res, dict):
        lm = res.get("liveness_min")
        if lm is not None and (not isinstance(lm, (int, float)) or lm <= 0):
            problems.append("resume.liveness_min: expected a positive number, got %r" % (lm,))
        us = res.get("unstop")
        if us is not None and not isinstance(us, bool):
            problems.append("resume.unstop: expected a boolean, got %r" % (us,))
    cred = cfg.get("credit")
    if isinstance(cred, dict):
        sc = cred.get("spill_chars")
        if sc is not None and (not isinstance(sc, (int, float)) or sc <= 0):
            problems.append("credit.spill_chars: expected a positive number, got %r" % (sc,))
        nma = cred.get("note_max_age_s")
        if nma is not None and (not isinstance(nma, (int, float)) or nma <= 0):
            problems.append("credit.note_max_age_s: expected a positive number, got %r" % (nma,))
    aud = cfg.get("audit")
    if isinstance(aud, dict):
        fcf = aud.get("file_chars_flag")
        if fcf is not None and (not isinstance(fcf, (int, float)) or fcf <= 0):
            problems.append("audit.file_chars_flag: expected a positive number, got %r" % (fcf,))
        gf = aud.get("growth_flag")
        if gf is not None and (not isinstance(gf, (int, float)) or gf <= 0):
            problems.append("audit.growth_flag: expected a positive number, got %r" % (gf,))
        np = aud.get("noise_patterns")
        if np is not None and not isinstance(np, list):
            problems.append("audit.noise_patterns: expected a list, got %r" % (type(np).__name__,))
        rcf = aud.get("router_ctx_flag")
        if rcf is not None:
            if isinstance(rcf, bool) or not isinstance(rcf, int) or rcf <= 0:
                problems.append("audit.router_ctx_flag: expected a positive integer, got %r" % (rcf,))
    grd = cfg.get("guard")
    if isinstance(grd, dict):
        wrc = grd.get("whole_read_chars")
        if wrc is not None:
            if isinstance(wrc, bool) or not isinstance(wrc, int) or wrc <= 0:
                problems.append("guard.whole_read_chars: expected a positive integer, got %r" % (wrc,))
    # T30: the modeled seed key's check retired with the key (plain 1M cycle)
    sav = cfg.get("savings")
    if isinstance(sav, dict):
        vt = sav.get("vanilla_ttl_s")
        if vt is not None and (not isinstance(vt, (int, float)) or vt <= 0):
            problems.append("savings.vanilla_ttl_s: expected a positive number, got %r" % (vt,))
        wt = sav.get("window_tokens")
        if wt is not None and (not isinstance(wt, (int, float)) or wt <= 0):
            problems.append("savings.window_tokens: expected a positive number, got %r" % (wt,))
    return problems


def renewal_day_for(cfg, account=None):
    """The billing renewal day of ``account``: ``accounts.<email>.renewal_day``, set by the user from the
    Claude app's Settings > Billing page; None when unknown, and the month figures then show a dash.

    3.11 T9 (developer 2026-09-28): never guessed. The global ``renewal_day`` fallback and the default 21 are
    retired; a set day is clamped to 1..28 so every month has it."""
    cfg = cfg if isinstance(cfg, dict) else {}
    if not account:
        return None
    entry = (cfg.get("accounts") or {}).get(account)
    day = entry.get("renewal_day") if isinstance(entry, dict) else None
    try:
        day = int(day)
    except (TypeError, ValueError):
        return None
    return min(28, max(1, day))


def _account_entry(cfg, account):
    entry = ((cfg if isinstance(cfg, dict) else {}).get("accounts") or {}).get(account) if account else None
    return entry if isinstance(entry, dict) else {}


def tier_for(cfg, account=None):
    """The plan tier for ``account`` (``accounts.<email>.tier``), default ``"max"`` (T32.1: the
    pooled fit pools only same-tier accounts)."""
    tier = _account_entry(cfg, account).get("tier")
    return tier if isinstance(tier, str) and tier else "max"


def regime_since_for(cfg, account=None):
    """The usage-limit regime boundary for ``account``: ``accounts.<email>.regime_since`` when set,
    else ``fit.regime_since``, else None (no boundary: the account is never pooled)."""
    rs = _account_entry(cfg, account).get("regime_since")
    if rs is None:
        rs = get(cfg if isinstance(cfg, dict) else {}, "fit.regime_since", None)
    return rs if isinstance(rs, str) and rs else None


def role_from_agent_type(agent_type, cfg=None):
    """Map an ``agent_type`` to a PA3 role via ``roles.prefix_map``.

    Longest key first, so ``expert-opus55`` -> expert and ``retriever-code`` ->
    retriever; keys without a trailing dash match exactly (``router``,
    ``critic``, ``review``).  Anything unknown (including None) -> ``other``.
    """
    name = str(agent_type or "").strip().lower()
    if not name:
        return "other"
    cfg = cfg if cfg is not None else load()
    prefix_map = get(cfg, "roles.prefix_map", None) or DEFAULTS["roles"]["prefix_map"]
    for key in sorted(prefix_map, key=len, reverse=True):
        k = str(key).lower()
        if not k:
            continue
        if k.endswith("-"):
            if name.startswith(k):
                return prefix_map[key]
        elif name == k or name.startswith(k + "-"):
            return prefix_map[key]
    return "other"


_LADDER_TIER = None     # (tier, pa.json ladder override) for this process (3.10.6 T3); tests set it


def _set_ladder_tier(tier, override=None):
    """Force the process's ladder tier (tests); ``tier=None`` resets to resolve on next use."""
    global _LADDER_TIER
    _LADDER_TIER = (tier, override) if tier else None


def _ladder_tier():
    """``(tier, override)``: the cached auth email's db row via ``accounts.tier_for``, the
    project's raw pa.json (tier fallback, ``ladder`` override); computed once per process."""
    global _LADDER_TIER
    if _LADDER_TIER is not None:
        return _LADDER_TIER
    from . import accounts

    pa = {}
    try:
        from .hooks import find_project_root
        root = find_project_root(os.getcwd())
        if root:
            pa = fsutil.read_json(os.path.join(root, ".claude", "pa.json"), {}) or {}
    except Exception:
        pa = {}
    pa = pa if isinstance(pa, dict) else {}
    conn = None
    try:
        email = accounts._cache_read().get("email")
        if email and os.path.exists(paths.db_path()):
            from . import db
            conn = db.connect(paths.db_path(), readonly=True)
        tier = accounts.tier_for(conn, email, pa)
    except Exception:
        tier = accounts.tier_for(None, None, pa)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    override = pa.get("ladder") if isinstance(pa.get("ladder"), dict) else None
    _LADDER_TIER = (tier, override)
    return _LADDER_TIER


def pinned_model_for(agent_name, cfg=None):
    """Model id pinned to an agent/session kind, or None when unpinned.

    3.10.6 T3: a ladder agent without a user-set pin (an entry differing from the default)
    gets its ladder row's model for this process's tier, ``[1m]`` stripped."""
    name = str(agent_name or "").strip()
    if not name:
        return None
    cfg = cfg if cfg is not None else load()
    pinned = get(cfg, "pinned_models", None) or {}
    low = name.lower()
    found, model = False, None
    if name in pinned:
        found, model = True, pinned[name]
    else:
        for key, val in pinned.items():
            if str(key).lower() == low:
                found, model = True, val
                break
    from .install import ladder
    if low in ladder.MANAGED:
        default = next((v for k, v in DEFAULTS["pinned_models"].items() if k.lower() == low), None)
        if not found or model == default:
            tier, override = _ladder_tier()
            row = ladder.row(low, tier, override)
            if row and row.get("model"):
                m = str(row["model"])
                return m[:-4] if m.lower().endswith("[1m]") else m
    return model if found else None


def ttl_for_role(role, cfg=None):
    """Cache-write TTL assumed for a role when ``usage`` carries no split."""
    cfg = cfg if cfg is not None else load()
    table = get(cfg, "ttl_default", {}) or {}
    return table.get(role) or table.get("other") or "5m"
