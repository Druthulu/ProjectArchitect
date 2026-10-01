"""``pa-ledger`` -- the usage-ledger command line (design doc A.2 / F).

Public:
    main(argv=None) -> int          entry point used by ``pa_ledger.py``
    build_parser()
    cmd_recalc cmd_report cmd_reconcile cmd_prices cmd_accounts cmd_summary
    cmd_sql cmd_tail cmd_watch cmd_vacuum cmd_doctor cmd_export cmd_import
    import_slices(conn, repo_dir, cfg)
    iter_transcripts(root, session=None, project=None, since=None)
    recalc_session_file(conn, path, slug, sid, cfg, opts, stats)
    lock_agent_transcript(conn, agent_path, ...)        (the --pending routine)
    reconcile_session(conn, path, sid, cfg)

Everything streams: a 256 MB subagent transcript is read line by line, never
loaded.  All SQLite access goes through :mod:`pa.db`; ``PA_LEDGER_DIR`` moves
the whole ledger (tests point it at a temp directory).

``pa.summary.rebuild`` is imported lazily and its absence is tolerated -- until
that module lands, :func:`rebuild_summary` writes a minimal ``summary.json``
with the same top-level shape (design doc B.3).
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

from . import __version__
from . import accounts
from . import config
from . import db
from . import fsutil
from . import hooks
from . import paths
from . import prices
from . import savings
from . import transcript

PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

# the 11 hook events the installer writes (design doc A.3) -> pa.hooks module
HOOK_EVENTS = (
    ("SessionStart", "session_start"),
    ("UserPromptSubmit", "user_prompt_submit"),
    ("PreToolUse", "pre_tool_use"),
    ("PostToolUse", "post_tool_use"),
    ("SubagentStart", "subagent_start"),
    ("SubagentStop", "subagent_stop"),
    ("Stop", "stop"),
    ("StopFailure", "stop_failure"),
    ("Notification", "notification"),
    ("PostModelSwitch", "model_switch"),
    ("SessionEnd", "session_end"),
)

WINDOW_SECONDS = {"five_hour": 18000, "seven_day": 604800}
COVERAGE_TARGET = 97.0          # per cent (design doc F metric 2)
PRICE_TOLERANCE = 0.1           # per cent (design doc F metric 1)
PRICE_MIN_COST = 0.01           # models below this are listed, not graded
LIVE_GRACE_S = 600              # a transcript quiet for longer is closed for recalc (no end_reason)

_TOOLU_RE = re.compile(rb"toolu_[A-Za-z0-9_\-]+")
_PROBE_RE = re.compile(r"^===\s+(\w+)\s+(\S+)\s+===$")
_MONEY_RE = re.compile(r"\$\s*([0-9]+(?:[.,][0-9]+)?)")
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_SQL_BANNED_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA)\b",
    re.I)


# =========================================================================== output

def out(text=""):
    """print() that never dies on a legacy console encoding."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(str(text).encode(enc, "replace").decode(enc, "replace"))


def err(text):
    sys.stderr.write(str(text).rstrip("\n") + "\n")


def table(headers, rows, aligns=None):
    """Render a fixed-width table as a list of lines."""
    cols = len(headers)
    body = [[("" if c is None else str(c)) for c in row] + [""] * (cols - len(row)) for row in rows]
    widths = [len(str(h)) for h in headers]
    for row in body:
        for i in range(cols):
            widths[i] = max(widths[i], len(row[i]))
    aligns = aligns or ["<"] * cols
    fmt = "  ".join("{:%s%d}" % (aligns[i], widths[i]) for i in range(cols))
    lines = [fmt.format(*[str(h) for h in headers]).rstrip(),
             "  ".join("-" * widths[i] for i in range(cols))]
    for row in body:
        lines.append(fmt.format(*row).rstrip())
    return lines


def usd(value, prec=2):
    try:
        val = float(value or 0.0)
    except (TypeError, ValueError):
        return "-"
    if prec == 2 and 0 < abs(val) < 0.01:
        prec = 4
    return ("$%%.%df" % prec) % val


def num(value):
    try:
        return "{:,}".format(int(value or 0))
    except (TypeError, ValueError):
        return "-"


def pct(value, prec=2):
    try:
        return ("%%.%df%%%%" % prec) % float(value)
    except (TypeError, ValueError):
        return "-"


# =========================================================================== time

def now_iso():
    return transcript.iso()


def parse_date(text):
    """``2026-09-01`` / full ISO -> a comparable ``...Z`` string (or None)."""
    if not text:
        return None
    raw = str(text).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw + "T00:00:00Z"
    when = transcript.parse_ts(raw)
    return transcript.iso(when) if when else raw


def iso_ago(seconds):
    return transcript.iso(time.time() - float(seconds))


def _pre_install_cutoff(cfg):
    """The ledger's root ``installed_at`` as a comparable ISO string, or None when unset (T10)."""
    ts = (cfg or {}).get("installed_at")
    return ts if isinstance(ts, str) and ts else None


def _project_cutoff(cwd):
    """The ``.claude/pa.json`` above ``cwd``'s own ``installed_at``, or None (T10.1)."""
    if not cwd or not os.path.isabs(cwd):
        return None                     # another OS's path (Z:/... on Linux) never resolves here
    root = hooks.find_project_root(cwd)
    if not root:
        return None
    ts = hooks.project_config(root).get("installed_at")
    return ts if isinstance(ts, str) and ts else None


def _session_cutoff(cfg, cwd):
    """A session's cutoff: its project's own install date, else the root's (T10.1)."""
    return _project_cutoff(cwd) or _pre_install_cutoff(cfg)


def _session_pre_install(cfg, started, cwd=None):
    """True when ``started`` (an ISO ts) predates the session's cutoff (T10.1)."""
    cutoff = _session_cutoff(cfg, cwd)
    return bool(cutoff and started and str(started) < cutoff)


def _session_governed(gov_path):
    """True when ``gov_path`` holds ``.claude/pa.json`` now, or is unreachable (T11).

    A non-existent directory (another machine's cwd) is treated as governed to
    avoid voiding sessions that cannot be checked on this host."""
    if not gov_path:
        return True
    try:
        if not os.path.isdir(str(gov_path)):
            return True                                 # unreachable: may be another machine
        return os.path.isfile(os.path.join(str(gov_path), ".claude", "pa.json"))
    except (OSError, TypeError):
        return True


def period_start(cfg, account=None):
    """Start of the current billing period from the account's renewal day; None when the account has
    none (3.11 T9: the day is asked, never guessed)."""
    day = config.renewal_day_for(cfg, account)
    if day is None:
        return None
    today = dt.datetime.now(dt.timezone.utc)
    year, month = today.year, today.month
    if today.day < day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    while True:
        try:
            start = dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
            break
        except ValueError:                       # e.g. renewal_day 31 in February
            day -= 1
    return transcript.iso(start)


def window_cutoff(window, cfg, summary_doc=None, account=None):
    """``(since_iso, label)`` for a report window (summary.json wins when present)."""
    if not window or window == "all":
        return None, "all"
    if window == "period":
        acct = account or cfg.get("recalc_default_account")
        start = period_start(cfg, acct)
        if start is None:                        # 3.11 T9: never an all-time report called "period"
            err("period: the renewal day of %s is unknown; set it with `pa_ledger.py accounts renewal "
                "<email> <day>` (the Claude app shows the date under Settings > Billing)"
                % (acct or "this account"))
            raise SystemExit(2)
        return start, "period"
    started = None
    acct = ((summary_doc or {}).get("accounts") or {}).get(account or "") or {}
    info = (acct.get("windows") or {}).get(window) or {}
    if isinstance(info.get("started_at"), (int, float)) and info["started_at"] > 0:
        started = transcript.iso(info["started_at"])
    if started:
        return started, window
    return iso_ago(WINDOW_SECONDS.get(window, 604800)), window


# =========================================================================== db

def open_db(create=True):
    """Open (and, first time, create) the local ledger with its schema."""
    path = paths.db_path()
    if create:
        paths.ensure_ledger_tree()
    conn = db.connect(path, create=create)
    if db.schema_version(conn) != db.SCHEMA_VERSION:
        db.migrate(conn)
    else:
        db.init_schema(conn)
    return conn


class _tx(object):
    """One explicit transaction (``db.connect`` runs in autocommit)."""

    def __init__(self, conn, active=True):
        self.conn = conn
        self.active = active

    def __enter__(self):
        if self.active:
            try:
                self.conn.execute("BEGIN")
            except sqlite3.DatabaseError:
                self.active = False
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.active:
            return False
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        except sqlite3.DatabaseError:
            pass
        return False


def scalar(conn, sql, args=(), default=0):
    try:
        row = conn.execute(sql, args).fetchone()
    except sqlite3.DatabaseError:
        return default
    if not row or row[0] is None:
        return default
    return row[0]


# =========================================================================== summary

def minimal_summary(conn, cfg):
    """Fallback ``summary.json`` (design doc B.3) while ``pa.summary`` is absent."""
    doc = {"schema": 1, "updated": now_iso(), "prices_version": prices.PRICES_VERSION,
           "source": "ledger_cli.minimal", "accounts": {}, "projects": {}, "sessions": {},
           "alerts": []}
    start = period_start(cfg)
    labels = cfg.get("accounts") or {}
    rows = []
    try:
        rows = conn.execute(
            "SELECT COALESCE(account,'(unknown)') AS account,"
            " COALESCE(SUM(cost_usd),0) AS cost FROM turns GROUP BY 1").fetchall()
    except sqlite3.DatabaseError:
        rows = []
    for row in rows:
        acct = row["account"]
        start = period_start(cfg, acct)
        if start is None:                        # 3.11 T9: no renewal day, no period
            doc["accounts"][acct] = {
                "label": (labels.get(acct) or {}).get("label") if isinstance(labels.get(acct), dict) else None,
                "windows": {}, "period": {"start": None, "renewal_day": None, "unknown": True},
                "cost_used_total": float(row["cost"] or 0.0),
            }
            continue
        used_period = scalar(conn, "SELECT COALESCE(SUM(cost_usd),0) FROM turns"
                                   " WHERE COALESCE(account,'(unknown)')=? AND ts >= ?",
                             (acct, start), 0.0)
        saved = scalar(conn, "SELECT COALESCE(SUM(measured_saved_usd),0) FROM savings s"
                             " JOIN sessions ss ON ss.session_id = s.session_id"
                             " WHERE COALESCE(ss.account,'(unknown)')=?", (acct,), 0.0)
        doc["accounts"][acct] = {
            "label": (labels.get(acct) or {}).get("label") if isinstance(labels.get(acct), dict) else None,
            "windows": {},
            "period": {"start": start[:10], "renewal_day": config.renewal_day_for(cfg, acct),
                       "cost_used": float(used_period), "cost_saved_measured": float(saved),
                       "cost_saved_modeled": None},
            "cost_used_total": float(row["cost"] or 0.0),
        }
        db.upsert_summary(conn, {"account": acct, "window": "period", "period_start": start,
                                 "cost_used": float(used_period),
                                 "cost_saved_measured": float(saved),
                                 "cost_saved_modeled": None, "updated": doc["updated"]})
    try:
        sess = conn.execute(
            "SELECT session_id, account, kind, project, phase, started, cost_usd"
            " FROM sessions ORDER BY COALESCE(ended, started) DESC LIMIT 50").fetchall()
    except sqlite3.DatabaseError:
        sess = []
    for row in sess:
        sid = row["session_id"]
        saved = scalar(conn, "SELECT COALESCE(SUM(measured_saved_usd),0) FROM savings"
                             " WHERE session_id=?", (sid,), 0.0)
        cost = float(row["cost_usd"] or 0.0)
        doc["sessions"][sid] = {
            "account": row["account"], "kind": row["kind"], "project": row["project"],
            "phase": row["phase"], "started": row["started"], "cost_usd": cost,
            "saved_measured": float(saved), "saved_modeled": None,
            "ratio": savings.headline_ratio(saved, cost),
        }
    fsutil.atomic_write_json(paths.summary_path(), doc, indent=1)
    return doc


def rebuild_summary(conn, cfg, readers=None):
    """``pa.summary.rebuild`` when it exists, else :func:`minimal_summary`."""
    try:
        from . import summary as summary_mod        # noqa: F401  (sibling milestone)
    except Exception:
        return minimal_summary(conn, cfg)
    fn = getattr(summary_mod, "rebuild", None)
    if not callable(fn):
        return minimal_summary(conn, cfg)
    try:
        return fn(conn, cfg, readers=readers)
    except TypeError:
        try:
            return fn(conn)
        except Exception:
            return minimal_summary(conn, cfg)
    except Exception:
        return minimal_summary(conn, cfg)


# =========================================================================== transcripts

def projects_roots(root_args, include_default=True):
    """Roots to scan.  ``--root`` REPLACES the default (see the build log)."""
    roots = []
    for root in (root_args or []):
        if root:
            roots.append(os.path.abspath(root))
    if not roots and include_default:
        roots.append(paths.projects_dir())
    return roots


def _base_dir(root):
    """A root may be a ``.claude`` dir, a ``projects`` dir or a single slug dir."""
    if os.path.isdir(os.path.join(root, "projects")):
        return os.path.join(root, "projects")
    return root


def iter_transcripts(root, session=None, project=None, since=None):
    """Yield ``{slug, sid, path, size, mtime}`` for every main transcript found.

    Handles the three shapes a ``--root`` can have: ``<root>/projects/<slug>/<sid>.jsonl``,
    ``<root>/<slug>/<sid>.jsonl`` and ``<root>/<sid>.jsonl`` (a slug dir itself).
    Session directories (``<sid>/subagents``) are never mistaken for slug dirs.
    """
    base = _base_dir(os.path.abspath(root))
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return
    since_ts = None
    if since:
        when = transcript.parse_ts(parse_date(since))
        since_ts = when.timestamp() if when else None

    def emit(slug, path):
        sid = os.path.basename(path)[:-6]
        if session and not (sid == session or sid.startswith(session)):
            return None
        if project and project.lower() not in str(slug).lower():
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        if since_ts is not None and st.st_mtime < since_ts:
            return None
        return {"slug": slug, "sid": sid, "path": path, "size": st.st_size, "mtime": st.st_mtime}

    top = [n for n in names if n.endswith(".jsonl") and os.path.isfile(os.path.join(base, n))]
    if top:                                        # base is a slug directory
        slug = os.path.basename(os.path.normpath(base))
        for name in top:
            item = emit(slug, os.path.join(base, name))
            if item:
                yield item
        return
    for name in names:
        slug_dir = os.path.join(base, name)
        if not os.path.isdir(slug_dir):
            continue
        try:
            files = sorted(os.listdir(slug_dir))
        except OSError:
            continue
        for fname in files:
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(slug_dir, fname)
            if not os.path.isfile(path):
                continue
            item = emit(name, path)
            if item:
                yield item


def find_transcript(sid, roots):
    """First main transcript whose file name matches ``sid``."""
    for root in roots:
        for item in iter_transcripts(root, session=sid):
            return item
    return None


def first_record(path, max_lines=40):
    """First record carrying ``cwd``/``version`` (session metadata)."""
    seen = 0
    for rec in transcript.iter_records(path):
        seen += 1
        if rec.get("cwd") or rec.get("version"):
            return rec
        if seen >= max_lines:
            break
    return {}


def scan_tool_use_owners(path, pending, owner, run_id):
    """Map still-unresolved ``toolUseId``s to the run whose transcript issued them.

    One streaming pass; only lines that hold a wanted id are JSON-parsed, and
    the scan stops as soon as ``pending`` is empty.
    """
    if not pending:
        return
    try:
        fh = open(path, "rb")
    except OSError:
        return
    try:
        for raw in fh:
            if b"toolu_" not in raw or b'"tool_use"' not in raw:
                continue
            ids = set(m.group(0).decode("ascii", "replace") for m in _TOOLU_RE.finditer(raw))
            hits = ids & pending
            if not hits:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            for block in transcript.blocks(msg):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                bid = block.get("id")
                if bid in hits and bid != run_id and bid not in owner:
                    owner[bid] = {"run_id": run_id, "tool": block.get("name"),
                                  "ts": rec.get("timestamp")}
                    pending.discard(bid)
            if not pending:
                return
    finally:
        fh.close()


def scan_session_extras(path):
    """``away_summary`` timestamps and ``ai-title`` count (design doc V2)."""
    away = []
    titles = 0
    try:
        fh = open(path, "rb")
    except OSError:
        return {"away_summary": [], "ai_title": 0}
    try:
        for raw in fh:
            if b"away_summary" not in raw and b'"ai-title"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("type") == "ai-title":
                titles += 1
            elif rec.get("type") == "system" and rec.get("subtype") == "away_summary":
                away.append(rec.get("timestamp"))
    finally:
        fh.close()
    return {"away_summary": away, "ai_title": titles}


# =========================================================================== recalc

def _new_agg():
    return {"n": 0, "cost": 0.0, "first_ts": None, "last_ts": None, "seed_ctx": 0,
            "ctx_at_end": 0, "input": 0, "cache_write_5m": 0, "cache_write_1h": 0,
            "cache_read": 0, "output": 0, "thinking": 0, "model": None,
            "last_output": 0, "last_thinking": 0, "last_stop": None, "cold": 0, "rewrite": 0}


def ingest_turns(conn, path, run_id, session_id, account, ttl_default, dry_run=False,
                 account_fn=None):
    """Stream one transcript into ``turns`` and return its aggregate."""
    agg = _new_agg()
    for req in transcript.iter_requests(path):
        acct = account_fn(req.get("ts_iso")) if account_fn else None
        row = db.turn_row_from_request(req, run_id=run_id, session_id=session_id,
                                       account=acct if acct is not None else account,
                                       ttl_default=ttl_default)
        agg["n"] += 1
        agg["cost"] += float(row["cost_usd"] or 0.0)
        if agg["first_ts"] is None:
            agg["first_ts"] = row["ts"]
            agg["seed_ctx"] = int(row["ctx"] or 0)
        if row["ts"]:
            agg["last_ts"] = row["ts"]
        agg["ctx_at_end"] = int(row["ctx"] or 0)
        for key in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
            agg[key] += int(row[key] or 0)
        agg["thinking"] += int(row["thinking"] or 0)
        agg["model"] = row["model"] or agg["model"]
        agg["last_output"] = int(row["output"] or 0)
        agg["last_thinking"] = int(row["thinking"] or 0)
        agg["last_stop"] = row["stop_reason"]
        agg["cold"] += 1 if row["cold"] else 0
        agg["rewrite"] += 1 if row["rewrite"] else 0
        if not dry_run:
            db.upsert_turn(conn, row)
    return agg


def _run_row(run_id, session_id, agg, path, **extra):
    row = {
        "run_id": run_id, "session_id": session_id,
        "started": agg["first_ts"], "ended": agg["last_ts"],
        "turns": agg["n"], "seed_ctx": agg["seed_ctx"], "ctx_at_end": agg["ctx_at_end"],
        "input": agg["input"], "cache_write_5m": agg["cache_write_5m"],
        "cache_write_1h": agg["cache_write_1h"], "cache_read": agg["cache_read"],
        "output": agg["output"], "thinking": agg["thinking"], "cost_usd": agg["cost"],
        "model_seen": agg["model"], "status": "completed", "locked": 1,
        "lock_source": "recalc", "transcript_path": path,
        "transcript_bytes": fsutil.file_size(path),
    }
    row.update(extra)
    return row


def _answer_tokens(agg):
    """Visible answer of a run = last request's output minus its thinking."""
    return max(0, int(agg["last_output"] or 0) - int(agg["last_thinking"] or 0))


def _status_from_stop(agg):
    stop = agg.get("last_stop")
    if not agg["n"]:
        return "interrupted"                                 # empty transcript -> killed before responding
    if stop == "tool_use":
        return "interrupted"                                 # asked for a tool and never continued
    if stop in (None, "end_turn", "stop_sequence"):
        return "completed"
    return str(stop)


def lock_agent_transcript(conn, agent_path, run_id=None, session_id=None, cfg=None,
                          account=None, parent_run_id=None, parent_source=None,
                          dry_run=False, stats=None):
    """Parse one ``agent-*.jsonl`` into ``turns`` + ``agent_runs`` (+ ``retrievals``).

    This is the routine ``recalc --pending`` drains ``state/pending_locks.jsonl``
    through, and the one ``recalc --from transcripts`` calls per subagent file.
    Returns the ``agent_runs`` row that was written (or None).
    """
    cfg = cfg if cfg is not None else config.load()
    rid = run_id or transcript.agent_id_from_path(agent_path)
    if not rid or not os.path.exists(agent_path):
        return None
    meta = transcript.read_meta(agent_path)
    agent_type = meta.get("agentType")
    role = config.role_from_agent_type(agent_type, cfg)
    ttl = config.ttl_for_role(role, cfg)
    sid = session_id or ""
    acct_fn = accounts.account_timeline(conn, sid) if sid else None
    agg = ingest_turns(conn, agent_path, rid, sid, account, ttl, dry_run=dry_run,
                       account_fn=acct_fn)
    started = agg["first_ts"]
    cwd = None
    if sid:
        try:
            srow = conn.execute("SELECT started, cwd FROM sessions WHERE session_id=?",
                                (sid,)).fetchone()
        except sqlite3.DatabaseError:
            srow = None
        if srow and srow["started"]:
            started = srow["started"]
        if srow:
            cwd = srow["cwd"]
    pre_install = _session_pre_install(cfg, started, cwd)
    wf_id = transcript.workflow_id_of(agent_path)
    desc = meta.get("description") or ""
    if wf_id:
        desc = ("wf:%s %s" % (wf_id, desc)).rstrip()
    row = _run_row(rid, sid, agg, agent_path,
                   kind=role, agent_type=agent_type,
                   description=desc or None,
                   tool_use_id=meta.get("toolUseId"),
                   spawn_depth=meta.get("spawnDepth"),
                   model_pinned=config.pinned_model_for(agent_type, cfg),
                   status=_status_from_stop(agg))
    if wf_id and not parent_run_id and not meta.get("parentAgentId"):
        row["parent_run_id"] = sid
        row["parent_source"] = "workflow"
    if parent_run_id:
        row["parent_run_id"] = parent_run_id
        row["parent_source"] = parent_source or "agent_tool"
    if not dry_run:
        db.upsert_agent_run(conn, row)
    if stats is not None:
        stats["runs"] = stats.get("runs", 0) + 1
        stats["turns"] = stats.get("turns", 0) + agg["n"]

    if agg["n"]:
        limit = int(config.get(cfg, "ledger.lock_inline_max_bytes", 20000000) or 20000000)
        mode = config.get(cfg, "savings.kept_out_mode", "results_share")
        est = {"result_tokens": 0, "own_output": 0}
        if wf_id:
            mode = "workflow"                         # T1.1: workflow agents are cost, never savings
        elif role != "retriever":
            mode = "all_growth"                       # an expert keeps its whole growth out of everyone
        elif row["transcript_bytes"] <= limit:
            est = transcript.tool_result_tokens(agent_path)
        else:
            mode = "all_growth"                       # no second pass over a huge file
        ret = savings.retrieval_row(
            run_id=rid, parent_run_id=row.get("parent_run_id"), agent_type=agent_type,
            model=agg["model"],
            turns=[{"ctx": agg["seed_ctx"]}, {"ctx": agg["ctx_at_end"]}],
            result_tokens_est=est.get("result_tokens", 0),
            own_output_est=est.get("own_output", 0),
            answer_tokens=_answer_tokens(agg), status=row["status"], mode=mode,
            spawned_at=agg["first_ts"], returned_at=agg["last_ts"])
        if pre_install:
            ret["void"] = 1
            ret["kept_out_tokens"] = 0
            ret["kept_out_mode"] = "pre-install"            # T10: session predates the install
        elif wf_id:
            ret["void"] = 1
            ret["kept_out_tokens"] = 0
            ret["kept_out_mode"] = "workflow"
        if not dry_run:
            db.upsert_retrieval(conn, ret)
        if stats is not None:
            stats["retrievals"] = stats.get("retrievals", 0) + 1
    return row


def write_savings(conn, session_id, cfg, dry_run=False, stats=None):
    """Measured savings for every run of a session, the main run included (the rule of 3.1;
    modeled stays NULL).  A session whose ``started`` predates its cutoff -- the project's own
    ``installed_at`` above its recorded cwd, else the root's (T10.1) -- gets no savings row:
    any it had are deleted and the function returns early."""
    if dry_run:
        return 0
    try:
        srow = conn.execute("SELECT started, cwd FROM sessions WHERE session_id=?",
                            (session_id,)).fetchone()
    except sqlite3.DatabaseError:
        srow = None
    cutoff = _session_cutoff(cfg, srow["cwd"] if srow else None)
    if cutoff and srow and srow["started"] and str(srow["started"]) < cutoff:
        try:
            conn.execute("DELETE FROM savings WHERE session_id=?", (session_id,))
        except sqlite3.DatabaseError:
            pass
        return 0
    try:
        runs = conn.execute("SELECT run_id, session_id, phase FROM agent_runs"
                            " WHERE session_id=? AND COALESCE(kind, '') != 'probe'",
                            (session_id,)).fetchall()
    except sqlite3.DatabaseError:
        return 0
    written = 0
    for run in runs:
        detail = savings.measured_saved(conn, run["run_id"])
        if float(detail.get("saved_usd") or 0.0) <= 0:
            # A12: still lock if this run itself has kept_out_tokens > 0
            try:
                own = conn.execute(
                    "SELECT kept_out_tokens FROM retrievals"
                    " WHERE run_id=? AND COALESCE(void, 0)=0",
                    (run["run_id"],)).fetchone()
            except Exception:
                own = None
            if not own or not own[0] or int(own[0]) <= 0:
                continue
        main = run["run_id"] == run["session_id"]
        db.upsert_savings(conn, {
            "run_id": run["run_id"], "session_id": run["session_id"], "phase": run["phase"],
            "measured_saved_usd": detail["saved_usd"],
            "measured_detail_json": json.dumps(detail, ensure_ascii=False, default=str),
            "locked": 0 if main else 1, "updated": now_iso()})
        written += 1
    if stats is not None:
        stats["savings"] = stats.get("savings", 0) + written
    return written


def resolve_account(conn, sid, opt_account, cfg):
    """Account for a pre-ledger session: existing stamp, --account, config, else None."""
    try:
        row = conn.execute("SELECT account, account_source FROM sessions WHERE session_id=?",
                           (sid,)).fetchone()
    except sqlite3.DatabaseError:
        row = None
    if row and row["account"]:
        return row["account"], (row["account_source"] or "assumed")
    acct = opt_account or cfg.get("recalc_default_account")
    return acct, ("assumed" if acct else "unknown")


def recalc_session_file(conn, path, slug, sid, cfg, opts, stats):
    """Ingest one session: main turns, subagent runs, retrievals, savings, residual."""
    dry = bool(opts.dry_run)
    account, account_source = resolve_account(conn, sid, opts.account, cfg)
    acct_fn = accounts.account_timeline(conn, sid)
    head = first_record(path)
    cwd = head.get("cwd")
    version = head.get("version")
    try:
        existing = conn.execute("SELECT kind, phase, end_reason FROM sessions WHERE session_id=?",
                                (sid,)).fetchone()
    except sqlite3.DatabaseError:
        existing = None
    kind = (existing["kind"] if existing else None) or "other"
    phase = existing["phase"] if existing else None

    subs = transcript.subagent_files(path)
    metas = {}
    pending = set()
    for sub in subs:
        agent_id = transcript.agent_id_from_path(sub)
        if not agent_id:
            continue
        meta = transcript.read_meta(sub)
        metas[agent_id] = (sub, meta)
        tid = meta.get("toolUseId")
        if tid:
            pending.add(tid)

    owner = {}
    with _tx(conn, not dry):
        main_agg = ingest_turns(conn, path, sid, sid, account, config.ttl_for_role("main", cfg),
                                dry_run=dry, account_fn=acct_fn)
    if pending:
        scan_tool_use_owners(path, pending, owner, sid)

    sub_aggs = {}
    for agent_id, (sub_path, meta) in metas.items():
        role = config.role_from_agent_type(meta.get("agentType"), cfg)
        with _tx(conn, not dry):
            agg = ingest_turns(conn, sub_path, agent_id, sid, account,
                               config.ttl_for_role(role, cfg), dry_run=dry,
                               account_fn=acct_fn)
        sub_aggs[agent_id] = (sub_path, meta, role, agg)
        if pending:
            scan_tool_use_owners(sub_path, pending, owner, agent_id)

    total_turns = main_agg["n"] + sum(a[3]["n"] for a in sub_aggs.values())
    total_cost = main_agg["cost"] + sum(a[3]["cost"] for a in sub_aggs.values())
    pre_install = _session_pre_install(cfg, main_agg["first_ts"], cwd)    # T10.1
    ungoverned = not _session_governed(cwd)                              # T11

    with _tx(conn, not dry):
        main_row = _run_row(sid, sid, main_agg, path, kind=kind, parent_source="main",
                            spawn_depth=0, phase=phase,
                            model_pinned=config.pinned_model_for(kind, cfg))
        if not dry:
            db.upsert_agent_run(conn, main_row)
        stats["runs"] += 1

        for agent_id, (sub_path, meta, role, agg) in sub_aggs.items():
            tid = meta.get("toolUseId")
            hit = owner.get(tid) if tid else None
            wf_id = transcript.workflow_id_of(sub_path)
            if meta.get("parentAgentId"):                    # the harness's own link (2.1.278)
                parent, source = str(meta["parentAgentId"]), "meta"
            elif wf_id and not hit:                          # workflow agent without a parent link
                parent, source = sid, "workflow"
            elif hit:
                parent, source = hit["run_id"], "agent_tool"
            else:
                parent, source = sid, "main"
            desc = meta.get("description") or ""
            if wf_id:
                desc = ("wf:%s %s" % (wf_id, desc)).rstrip()
            row = _run_row(agent_id, sid, agg, sub_path, kind=role,
                           agent_type=meta.get("agentType"),
                           description=desc or None, tool_use_id=tid,
                           spawn_depth=meta.get("spawnDepth"), parent_run_id=parent,
                           parent_source=source, phase=phase,
                           model_pinned=config.pinned_model_for(meta.get("agentType"), cfg),
                           status=_status_from_stop(agg))
            if not dry:
                db.upsert_agent_run(conn, row)
            stats["runs"] += 1

            if agg["n"] and role != "probe":
                limit = int(config.get(cfg, "ledger.lock_inline_max_bytes", 20000000) or 20000000)
                mode = config.get(cfg, "savings.kept_out_mode", "results_share")
                est = {"result_tokens": 0, "own_output": 0}
                if wf_id:
                    mode = "workflow"                 # T1.1: workflow agents are cost, never savings
                elif role != "retriever":
                    mode = "all_growth"               # an expert keeps its whole growth out of everyone
                elif row["transcript_bytes"] <= limit:
                    est = transcript.tool_result_tokens(sub_path)
                else:
                    mode = "all_growth"
                ret = savings.retrieval_row(
                    run_id=agent_id, parent_run_id=parent, agent_type=meta.get("agentType"),
                    model=agg["model"],
                    turns=[{"ctx": agg["seed_ctx"]}, {"ctx": agg["ctx_at_end"]}],
                    result_tokens_est=est.get("result_tokens", 0),
                    own_output_est=est.get("own_output", 0),
                    answer_tokens=_answer_tokens(agg), status=row["status"], mode=mode,
                    spawned_at=agg["first_ts"], returned_at=agg["last_ts"])
                if pre_install:
                    ret["void"] = 1
                    ret["kept_out_tokens"] = 0
                    ret["kept_out_mode"] = "pre-install"    # T10: session predates the install
                elif ungoverned:
                    ret["void"] = 1
                    ret["kept_out_tokens"] = 0
                    ret["kept_out_mode"] = "ungoverned"     # T11: no .claude/pa.json at the cwd
                elif wf_id:
                    ret["void"] = 1
                    ret["kept_out_tokens"] = 0
                    ret["kept_out_mode"] = "workflow"
                if not dry:
                    db.upsert_retrieval(conn, ret)
                stats["retrievals"] += 1

        stats["turns"] += total_turns

        cost_state_rec, cs_fresh, _cs_offset = transcript.cost_state_fresh(path)
        cost_usd = None
        residual = None
        harness_cost = (float(cost_state_rec.get("totalCostUSD") or 0.0)
                        if cost_state_rec else None)
        if cost_state_rec and cs_fresh:
            cost_usd = harness_cost
            if dry:
                attributed = total_cost
            else:
                attributed = float(scalar(conn, "SELECT COALESCE(SUM(cost_usd),0) FROM turns"
                                                " WHERE session_id=? AND kind='api'", (sid,), 0.0))
            residual = cost_usd - attributed
            res_row = {
                "msg_id": "residual:%s" % sid, "request_id": None, "run_id": sid,
                "session_id": sid, "account": account,
                "ts": main_agg["last_ts"] or transcript.iso(
                    cost_state_rec.get("startTime", 0) / 1000.0
                    if cost_state_rec.get("startTime") else None),
                "model": None, "effort": None, "input": 0, "cache_write_5m": 0,
                "cache_write_1h": 0, "cache_write": 0, "cache_read": 0, "output": 0,
                "thinking": 0, "ctx": 0, "new_tokens": 0, "gap_s": None, "cold": 0,
                "rewrite": 0, "stop_reason": None, "cost_usd": residual, "ttl_assumed": None,
                "kind": "residual"}
            if not dry:
                db.upsert_turn(conn, res_row)
            stats["residuals"] += 1
        elif cost_state_rec and not cs_fresh:
            # stale: delete any existing residual row, use turns as cost source
            if not dry:
                try:
                    conn.execute("DELETE FROM turns WHERE msg_id=? AND session_id=?",
                                 ("residual:%s" % sid, sid))
                except sqlite3.DatabaseError:
                    pass
            cost_usd = None        # fall through to turns-based cost

        session_row = {
            "session_id": sid, "account": account, "account_source": account_source,
            "machine": paths.machine_tag(), "project": cwd or slug, "cwd": cwd,
            "transcript_path": path, "kind": kind, "version": version,
            "started": main_agg["first_ts"],
        }
        # a session is closed when its SessionEnd hook stamped an end_reason or its transcript
        # has been quiet for LIVE_GRACE_S; a live one keeps `ended` NULL so `report --until`
        # and `recalc --check` leave it out (a recalc re-reads a live transcript and books
        # requests the hooks have not written yet)
        closed = bool(existing and existing["end_reason"])
        if not closed:
            try:
                closed = (time.time() - os.path.getmtime(path)) > LIVE_GRACE_S
            except OSError:
                closed = True
        session_row["ended"] = main_agg["last_ts"] if closed else None
        # T30: the turns sum is the session cost; the cost-state total is the harness cross-check
        session_row.update({"cost_usd": total_cost, "cost_source": "turns",
                            "harness_cost_usd": harness_cost, "cost_updated": now_iso()})
        if not dry:
            db.upsert_session(conn, session_row)
            if not closed:                      # a merge upsert keeps an old stamp; clear it
                conn.execute("UPDATE sessions SET ended=NULL WHERE session_id=?", (sid,))
        stats["sessions"] += 1

    if ungoverned and not dry:                                            # T11
        try:
            conn.execute("DELETE FROM savings WHERE session_id=?", (sid,))
        except sqlite3.DatabaseError:
            pass
    else:
        write_savings(conn, sid, cfg, dry_run=dry, stats=stats)
    stats["cost"] += total_cost
    key = (phase or "-") if opts.by == "phase" else sid
    stats["groups"][key] = stats["groups"].get(key, 0.0) + total_cost
    return {"sid": sid, "slug": slug, "turns": total_turns, "runs": 1 + len(sub_aggs),
            "cost": total_cost, "cost_state": cost_usd, "residual": residual}


def recalc_from_turns(conn, cfg, opts, stats):
    """Rebuild ``agent_runs`` aggregates, ``retrievals`` links and ``savings`` from ``turns``."""
    where = ["kind='api'"]
    args = []
    if opts.session:
        where.append("session_id=?")
        args.append(opts.session)
    if opts.since:
        where.append("ts >= ?")
        args.append(parse_date(opts.since))
    sql = ("SELECT run_id, session_id, COUNT(*) AS n, MIN(ts) AS t0, MAX(ts) AS t1,"
           " SUM(input) AS input, SUM(cache_write_5m) AS w5, SUM(cache_write_1h) AS w1,"
           " SUM(cache_read) AS cr, SUM(output) AS out, SUM(thinking) AS think,"
           " SUM(cost_usd) AS cost FROM turns WHERE %s GROUP BY run_id, session_id"
           % " AND ".join(where))
    rows = conn.execute(sql, args).fetchall()
    sessions = set()
    with _tx(conn, not opts.dry_run):
        for row in rows:
            rid = row["run_id"]
            if not rid:
                continue
            sessions.add(row["session_id"])
            first = conn.execute("SELECT ctx, model FROM turns WHERE run_id=? AND kind='api'"
                                 " ORDER BY ts LIMIT 1", (rid,)).fetchone()
            last = conn.execute("SELECT ctx, model FROM turns WHERE run_id=? AND kind='api'"
                                " ORDER BY ts DESC LIMIT 1", (rid,)).fetchone()
            run = {"run_id": rid, "session_id": row["session_id"], "turns": row["n"],
                   "started": row["t0"], "ended": row["t1"], "input": row["input"],
                   "cache_write_5m": row["w5"], "cache_write_1h": row["w1"],
                   "cache_read": row["cr"], "output": row["out"], "thinking": row["think"],
                   "cost_usd": row["cost"],
                   "seed_ctx": first["ctx"] if first else None,
                   "ctx_at_end": last["ctx"] if last else None,
                   "model_seen": (last["model"] if last else None)}
            if not opts.dry_run:
                db.upsert_agent_run(conn, run)
            stats["runs"] += 1
            stats["turns"] += int(row["n"] or 0)
            stats["cost"] += float(row["cost"] or 0.0)
    for sid in sorted(x for x in sessions if x):
        stats["sessions"] += 1
        write_savings(conn, sid, cfg, dry_run=opts.dry_run, stats=stats)
    return stats


def drain_pending(conn, cfg, opts, stats):
    """``--pending``: finish deferred SubagentStop locks (state/pending_locks.jsonl)."""
    path = paths.pending_locks_path()
    if not os.path.exists(path):
        out("pending: nothing queued (%s)" % path)
        return 0
    done = 0
    kept = []
    try:
        lock = fsutil.file_lock(path, timeout_ms=4000)
        lock.acquire()
    except (TimeoutError, OSError):
        err("pending: could not lock %s" % path)
        return 1
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            entries = [ln.strip() for ln in fh if ln.strip()]
        for line in entries:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            agent_path = item.get("agent_transcript_path") or item.get("transcript_path")
            run_id = item.get("run_id") or item.get("agent_id")
            sid = item.get("session_id")
            if not agent_path or not os.path.exists(agent_path):
                kept.append(line)
                continue
            account, _src = resolve_account(conn, sid or "", opts.account, cfg)
            with _tx(conn, not opts.dry_run):
                row = lock_agent_transcript(conn, agent_path, run_id=run_id, session_id=sid,
                                            cfg=cfg, account=account, dry_run=opts.dry_run,
                                            stats=stats)
            if row is None:
                kept.append(line)
                continue
            done += 1
            if sid:
                write_savings(conn, sid, cfg, dry_run=opts.dry_run, stats=stats)
        if not opts.dry_run:
            fsutil.atomic_write_text(path, "".join(ln + "\n" for ln in kept))
    finally:
        lock.release()
    out("pending: locked %d, deferred %d" % (done, len(kept)))
    return 0


# =========================================================================== export / import

# tables exported per session (deterministic order, then primary-key sort)
_EXPORT_TABLES = ("sessions", "agent_runs", "turns", "retrievals", "savings",
                  "utilization", "events")


def _session_ids_for_project(conn, project_dir):
    """Session ids whose ``cwd`` or ``project`` is under *project_dir*."""
    if project_dir[1:3] in (":/", ":\\") and not os.path.isabs(project_dir):
        norm = project_dir                  # a drive path is absolute on any host (Windows slices on Linux)
    else:
        norm = os.path.normpath(os.path.abspath(project_dir))
    norm = norm.replace("\\", "/").rstrip("/")
    rows = conn.execute(
        "SELECT session_id, cwd, project FROM sessions").fetchall()
    sids = []
    for r in rows:
        for col in ("cwd", "project"):
            val = r[col]
            if val:
                v = val.replace("\\", "/").rstrip("/")
                if v == norm or v.startswith(norm + "/"):
                    sids.append(r["session_id"])
                    break
    return sorted(set(sids))


def _session_ids_for_phase(conn, sids_project, phase, project_root=None):
    """Narrow *sids_project* to those matching *phase* or overlapping its span.

    When no session is tagged, falls back to ``phases.phase_span`` for the time
    window (git-derived from the plan's hash) so pre-stamping sessions are found.
    """
    # 1. sessions explicitly tagged with the phase
    tagged = set()
    for sid in sids_project:
        row = conn.execute("SELECT phase FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if row and row["phase"] == phase:
            tagged.add(sid)
    if tagged:
        # 2. time span of those sessions' turns
        placeholders = ",".join("?" * len(tagged))
        span = conn.execute(
            "SELECT MIN(ts) AS t0, MAX(ts) AS t1 FROM turns WHERE session_id IN (%s)"
            % placeholders, list(tagged)).fetchone()
        if not span or span["t0"] is None:
            return sorted(tagged)
        t0, t1 = span["t0"], span["t1"]
    elif project_root:
        # span fallback: git-derived from phases.phase_span; an open phase's
        # upper bound is now
        from . import phases
        t0, t1 = phases.phase_span(project_root, phase)
        t1 = phases.span_end_or_now(t1)
    else:
        return []
    # 3. other project sessions whose turns fall inside that span (never a beside session, T28)
    from . import phases as _ph
    result = set(tagged)
    for sid in sids_project:
        if sid in result or _ph.beside_session(conn, sid):
            continue
        hit = conn.execute(
            "SELECT 1 FROM turns WHERE session_id=? AND ts>=? AND ts<=? LIMIT 1",
            (sid, t0, t1)).fetchone()
        if hit:
            result.add(sid)
    return sorted(result)


def _export_rows(conn, sids):
    """Yield ``(table, dict)`` for every row belonging to *sids*."""
    sid_set = set(sids)
    placeholders = ",".join("?" * len(sids))
    for tbl in _EXPORT_TABLES:
        if tbl == "retrievals":
            # retrievals link through agent_runs.run_id
            if not sids:
                continue
            run_ids = [r[0] for r in conn.execute(
                "SELECT run_id FROM agent_runs WHERE session_id IN (%s)" % placeholders,
                sids).fetchall()]
            if not run_ids:
                continue
            rpl = ",".join("?" * len(run_ids))
            rows = conn.execute(
                "SELECT * FROM retrievals WHERE run_id IN (%s) ORDER BY run_id"
                % rpl, run_ids).fetchall()
        elif tbl == "events":
            if not sids:
                continue
            rows = conn.execute(
                "SELECT * FROM events WHERE session_id IN (%s) ORDER BY id"
                % placeholders, sids).fetchall()
        else:
            pk_cols = db.TABLES[tbl][0]
            order = ", ".join(pk_cols) if pk_cols else "rowid"
            if not sids:
                continue
            rows = conn.execute(
                "SELECT * FROM %s WHERE session_id IN (%s) ORDER BY %s"
                % (tbl, placeholders, order), sids).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            d["table"] = tbl
            yield tbl, d


def _prev_phase_from_genplan(root, phase):
    """The previous phase in ``GENERATION_PLAN.md``'s phase order, or None."""
    gp = os.path.join(root, "GENERATION_PLAN.md")
    if not os.path.isfile(gp):
        return None
    import re as _re
    phase_re = _re.compile(r"^-\s+(\S+)\s")
    phases_seen = []
    try:
        with open(gp, "r", encoding="utf-8") as fh:
            for line in fh:
                m = phase_re.match(line)
                if m:
                    phases_seen.append(m.group(1))
    except OSError:
        return None
    try:
        idx = phases_seen.index(phase)
    except ValueError:
        return None
    return phases_seen[idx - 1] if idx > 0 else None


def cmd_audit(args):
    """Run the carry audit for a phase and print results."""
    from . import audit, config, phases

    project_dir = os.path.abspath(args.project)
    phase = args.phase
    prev = args.prev
    if prev is None:
        prev = _prev_phase_from_genplan(project_dir, phase)

    conn = open_db(create=False)
    try:
        cfg = config.load()
    except Exception:
        cfg = {}

    try:
        sids = phases.phase_sessions(conn, project_dir, phase)
        prev_sids = phases.phase_sessions(conn, project_dir, prev) if prev else None
        span = phases.phase_span(project_dir, phase)
        prev_span = phases.phase_span(project_dir, prev) if prev else None
        result = audit.audit_phase(conn, cfg, project_dir, phase, prev_phase=prev,
                                   sids=sids, prev_sids=prev_sids,
                                   span=span, prev_span=prev_span)
        if args.json:
            if isinstance(result, dict):
                result = dict({"version": __version__}, **result)
            out(json.dumps(result, indent=2, default=str))
        else:
            out(audit.render_md(result))
    finally:
        db.close(conn)
    return 0


def cmd_export(args):
    """Export sessions for a project (and optionally a phase) to gzipped JSONL."""
    import gzip

    project_dir = args.project
    conn = open_db(create=False)
    try:
        sids = _session_ids_for_project(conn, project_dir)
        if args.phase:
            sids = _session_ids_for_phase(conn, sids, args.phase,
                                          project_root=os.path.abspath(project_dir))
        if not sids:
            err("export: no sessions found")
            return 1

        # account labels
        labels = {}
        for r in conn.execute("SELECT email, label FROM accounts").fetchall():
            if r["label"]:
                labels[r["email"]] = r["label"]

        # count rows per table
        counts = {}
        rows_buf = []
        for tbl, d in _export_rows(conn, sids):
            counts[tbl] = counts.get(tbl, 0) + 1
            rows_buf.append(d)

        machine = db.get_meta(conn, "machine") or paths.machine_tag()
        header = {
            "type": "header",
            "schema": db.SCHEMA_VERSION,
            "prices_version": db.get_meta(conn, "prices_version") or "",
            "machine": machine,
            "account_labels": labels,
            "exported_at": now_iso(),
            "counts": counts,
            "created": db.get_meta(conn, "created"),   # fix-10.c3: the era travels with the data
        }
        beside = []
        if args.phase:
            # T28: beside sessions listed on their own row; their rows are not exported
            from . import phases as _ph
            beside = [{"session_id": b["session_id"], "cost_usd": b["cost_usd"],
                       "n_turns": b["n_turns"]}
                      for b in _ph.beside_costs(conn, os.path.abspath(project_dir), args.phase)]
            header["beside"] = beside

        # determine output path
        if args.out:
            out_path = args.out
        else:
            ledger_out = os.path.join(os.path.abspath(project_dir),
                                      ".claude-state", "ledger")
            os.makedirs(ledger_out, exist_ok=True)
            tag = "phase%s" % args.phase if args.phase else "all"
            out_path = os.path.join(ledger_out, "%s-%s.jsonl.gz" % (machine, tag))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(header, ensure_ascii=False, default=str) + "\n")
            for d in rows_buf:
                fh.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")

        size = os.path.getsize(out_path)
        total = sum(counts.values())
        out("exported: %s" % out_path)
        out("rows: %s  size: %s bytes" % (num(total), num(size)))
        for tbl in _EXPORT_TABLES:
            if tbl in counts:
                out("  %s: %s" % (tbl, num(counts[tbl])))
        if beside:
            out("beside: %d sessions $%.2f (own rows, not in the phase)"
                % (len(beside), sum(b["cost_usd"] or 0.0 for b in beside)))
    finally:
        db.close(conn)
    return 0


def _import_file(conn, path):
    """Import one JSONL (gzip or plain) with local-wins. Returns per-table counts."""
    import gzip

    inserted = {}
    skipped = {}
    opener = gzip.open if path.endswith(".gz") else open
    # read existing session ids for local-wins on events
    local_sids = set()

    with opener(path, "rt", encoding="utf-8") as fh:
        header = None
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if lineno == 1 and obj.get("type") == "header":
                header = obj
                # fix-10.c3: an imported ledger inherits the earliest era of its sources
                created = header.get("created")
                local_created = db.get_meta(conn, "created")
                if created and (not local_created or str(created) < str(local_created)):
                    db.set_meta(conn, "created", created)
                continue
            tbl = obj.pop("table", None)
            if not tbl or tbl not in db.TABLES:
                continue
            pk_cols = db.TABLES[tbl][0]
            if pk_cols:
                # check if the row exists locally (local wins)
                where = " AND ".join("%s=?" % c for c in pk_cols)
                vals = [obj.get(c) for c in pk_cols]
                existing = conn.execute(
                    "SELECT 1 FROM %s WHERE %s LIMIT 1" % (tbl, where), vals).fetchone()
                if existing:
                    skipped[tbl] = skipped.get(tbl, 0) + 1
                    continue
                db.upsert(conn, tbl, obj, mode="replace")
                inserted[tbl] = inserted.get(tbl, 0) + 1
            else:
                # events: no PK; insert if session not already local
                sid = obj.get("session_id")
                if sid and sid not in local_sids:
                    # check once per session
                    exists = conn.execute(
                        "SELECT 1 FROM events WHERE session_id=? LIMIT 1",
                        (sid,)).fetchone()
                    if exists:
                        local_sids.add(sid)
                if sid and sid in local_sids:
                    skipped[tbl] = skipped.get(tbl, 0) + 1
                    continue
                db.upsert(conn, tbl, obj, mode="insert")
                inserted[tbl] = inserted.get(tbl, 0) + 1
    try:
        conn.commit()
    except sqlite3.Error:
        pass
    return inserted, skipped


def cmd_import(args):
    """Import JSONL slices into the local ledger (local-wins)."""
    files = args.import_files
    if not files:
        err("import: no files given")
        return 1
    conn = open_db()
    try:
        total_ins = {}
        total_skip = {}
        for path in files:
            if not os.path.isfile(path):
                err("import: file not found: %s" % path)
                continue
            ins, skip = _import_file(conn, path)
            for t, n in ins.items():
                total_ins[t] = total_ins.get(t, 0) + n
            for t, n in skip.items():
                total_skip[t] = total_skip.get(t, 0) + n
        # rebuild summary
        from . import summary
        cfg = config.load()
        summary.rebuild(conn, cfg)
        out("import done")
        for tbl in _EXPORT_TABLES:
            i = total_ins.get(tbl, 0)
            s = total_skip.get(tbl, 0)
            if i or s:
                out("  %s: %d inserted, %d skipped" % (tbl, i, s))
    finally:
        db.close(conn)
    return 0


def import_slices(conn, repo_dir, cfg):
    """Import every ``<repo>/.claude-state/ledger/*.jsonl.gz`` whose sessions
    the local ledger lacks.  Returns ``{file: {inserted: dict, skipped: dict}}``
    for the installer to call later.
    """
    import glob

    ledger_src = os.path.join(os.path.abspath(repo_dir), ".claude-state", "ledger")
    if not os.path.isdir(ledger_src):
        return {}
    result = {}
    for path in sorted(glob.glob(os.path.join(ledger_src, "*.jsonl.gz"))):
        # peek: does the local ledger already have these sessions?
        import gzip
        has_all = True
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    obj = json.loads(line.strip())
                    if obj.get("type") == "header":
                        continue
                    if obj.get("table") == "sessions":
                        sid = obj.get("session_id")
                        if sid:
                            existing = conn.execute(
                                "SELECT 1 FROM sessions WHERE session_id=? LIMIT 1",
                                (sid,)).fetchone()
                            if not existing:
                                has_all = False
                                break
        except (OSError, json.JSONDecodeError):
            continue
        if has_all:
            result[path] = {"inserted": {}, "skipped": {}}
            continue
        ins, skip = _import_file(conn, path)
        result[path] = {"inserted": ins, "skipped": skip}
    if result:
        from . import summary
        summary.rebuild(conn, cfg)
    return result


_REPORT_SNAPSHOT_VOLATILE_KEYS = ("updated", "generated")


def _report_snapshot(until=None):
    """In-process ``report --window all --json``, parsed, volatile keys stripped.

    ``until`` bounds both snapshots to the same point in time, so rows a live
    session adds between them don't show as a diff (T5.c2)."""
    ns = argparse.Namespace(window="all", account=None, since=None, until=until, session=None,
                            phase=None, by="session", json=True, seeds=False,
                            thinking=False, limit=25, modeled=False, basis="both",
                            all_kinds=False)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_report(ns)
    doc = json.loads(buf.getvalue())
    for key in _REPORT_SNAPSHOT_VOLATILE_KEYS:
        doc.pop(key, None)
    return doc


def cmd_recalc(args):
    if getattr(args, "check", False):
        return _cmd_recalc_check(args)
    return _cmd_recalc_run(args)


def _round_floats(obj):
    """Recursively round every float in a parsed JSON structure to 6 decimals,
    so summation-order noise below 1e-6 doesn't show up as a diff."""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def _cmd_recalc_check(args):
    """``recalc --check``: snapshot the report, recalc, snapshot again, compare.

    Both snapshots use ``until=t0`` (fixed before either runs), so rows a live
    session adds while this check runs don't count as a diff."""
    import contextlib
    import difflib
    import io

    t0 = now_iso()
    before = _report_snapshot(until=t0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _cmd_recalc_run(args)
    if rc:
        out(buf.getvalue())
        return rc
    after = _report_snapshot(until=t0)
    a = json.dumps(_round_floats(before), ensure_ascii=False, indent=1, sort_keys=True, default=str).splitlines()
    b = json.dumps(_round_floats(after), ensure_ascii=False, indent=1, sort_keys=True, default=str).splitlines()
    if a == b:
        out("idempotent OK")
        return 0
    diff = list(difflib.unified_diff(a, b, fromfile="before", tofile="after", lineterm=""))
    for line in diff[:20]:
        out(line)
    return 1


def _cmd_recalc_run(args):
    cfg = config.load()
    conn = open_db()
    stats = {"sessions": 0, "runs": 0, "turns": 0, "retrievals": 0, "savings": 0,
             "residuals": 0, "cost": 0.0, "files": 0, "groups": {}}
    try:
        if args.pending:
            rc = drain_pending(conn, cfg, args, stats)
            if rc:
                return rc
        if args.source == "turns":
            recalc_from_turns(conn, cfg, args, stats)
        elif not args.pending or args.root or args.session or args.project:
            roots = projects_roots(args.root)
            for root in roots:
                if not os.path.isdir(_base_dir(os.path.abspath(root))):
                    err("recalc: root not readable: %s" % root)
                    continue
                for item in iter_transcripts(root, session=args.session,
                                             project=args.project, since=args.since):
                    stats["files"] += 1
                    try:
                        res = recalc_session_file(conn, item["path"], item["slug"], item["sid"],
                                                  cfg, args, stats)
                    except (OSError, sqlite3.DatabaseError) as exc:
                        err("recalc: %s: %s" % (item["sid"], exc))
                        continue
                    if args.verbose:
                        out("  %-40s %5d turns  %8s  %s" % (
                            res["sid"], res["turns"], usd(res["cost"]), item["slug"]))
        if not args.dry_run:
            # Build readers for the refit + summary so the fit sees all roots
            readers = None
            try:
                extra = config.extra_root_entries(cfg)
                if extra:
                    readers = db.union_readers(extra, include_local=False)
            except Exception:
                pass
            try:
                from . import fit as _fit
                _fit.refit_all(conn, cfg, readers=readers)
            except Exception:
                pass
            rebuild_summary(conn, cfg, readers=readers)
            # close reader connections
            for rd in (readers or []):
                try:
                    rc = rd.get("conn")
                    if rc is not None:
                        db.close(rc)
                except Exception:
                    pass
    finally:
        db.close(conn)

    head = "recalc (dry run)" if args.dry_run else "recalc"
    out("%s from %s" % (head, args.source))
    lines = table(["metric", "count"],
                  [["transcripts", stats["files"]], ["sessions", stats["sessions"]],
                   ["agent_runs", stats["runs"]], ["turns", stats["turns"]],
                   ["retrievals", stats["retrievals"]], ["savings rows", stats["savings"]],
                   ["residual rows", stats["residuals"]],
                   ["attributed cost", usd(stats["cost"])]], ["<", ">"])
    for line in lines:
        out(line)
    if args.by and stats["groups"]:
        out("")
        out("by %s:" % args.by)
        rows = sorted(stats["groups"].items(), key=lambda kv: -kv[1])[:20]
        for line in table([args.by, "cost"], [[k, usd(v)] for k, v in rows], ["<", ">"]):
            out(line)
    return 0


# =========================================================================== report

def _report_conns(cfg):
    """Local + extra-root readers: ``[{conn, path, account, label}]`` (``db.union_readers``)."""
    open_db()                                   # make sure the local schema exists
    readers = db.union_readers(cfg.get("extra_roots") or [], include_local=True)
    return readers or [{"conn": db.connect(), "path": None, "account": None, "label": None}]


_GROUP_SQL = {"session": "t.session_id", "run": "t.run_id", "model": "t.model",
              "day": "substr(t.ts,1,10)", "project": "COALESCE(s.project, s.cwd, '(none)')"}


def _report_rows(conn, args, since, default_account=None, until=None):
    """Per group-key totals; a NULL ``turns.account`` takes ``default_account`` (the reader's
    configured account, None for the local root), then ``(unknown)``."""
    acct_expr = "COALESCE(t.account, ?, '(unknown)')"
    where = [] if getattr(args, "all_kinds", False) else ["t.kind='api'"]
    where_params = []
    if since:
        where.append("t.ts >= ?")
        where_params.append(since)
    if until:
        # only sessions closed before the cutoff, and only their turns before it: a live
        # session's transcript is re-read by a recalc and books requests older than the
        # cutoff that the hooks had not written yet, so `--check` must leave it out
        where.append("t.ts < ?")
        where_params.append(until)
        where.append("t.session_id IN (SELECT session_id FROM sessions"
                     " WHERE ended IS NOT NULL AND ended < ?)")
        where_params.append(until)
    if args.account:
        where.append("%s = ?" % acct_expr)
        where_params.append(default_account)
        where_params.append(args.account)
    if args.session:
        where.append("t.session_id = ?")
        where_params.append(args.session)
    if args.phase:
        where.append("COALESCE(a.phase, s.phase) = ?")
        where_params.append(args.phase)
        if not args.phase.startswith("beside "):
            # T31: a beside session's turns never count under the owner phase (unhealed runs)
            where.append("COALESCE(s.phase, '') NOT LIKE 'beside %'")
    grp = _GROUP_SQL.get(args.by or "session", "t.session_id")
    sql = ("SELECT %s AS account, %s AS grp,"
           " SUM(t.cost_usd) AS cost, COUNT(*) AS requests, AVG(t.ctx) AS avg_ctx,"
           " SUM(t.output) AS output, SUM(t.thinking) AS thinking, MAX(s.machine) AS machine"
           " FROM turns t LEFT JOIN agent_runs a ON a.run_id = t.run_id"
           " LEFT JOIN sessions s ON s.session_id = t.session_id"
           " WHERE %s GROUP BY 1, 2" % (acct_expr, grp, " AND ".join(where) if where else "1=1"))
    params = [default_account] + where_params
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError:
        return []


def _saved_rows(conn, args, since, default_account=None, until=None):
    """Measured savings per account and per group key (session/run only).

    Uses per-turn attribution from ``by_account`` in the detail (T9); falls
    back to ``sessions.account`` for pre-T9 rows.
    """
    where = ["1=1"]
    where_params = []
    if args.session:
        where.append("sv.session_id = ?")
        where_params.append(args.session)
    if args.phase:
        where.append("COALESCE(sv.phase, ss.phase) = ?")
        where_params.append(args.phase)
    if since:
        where.append("COALESCE(sv.updated, ss.started) >= ?")
        where_params.append(since)
    if until:
        # savings of sessions closed before the cutoff only: a live session's savings rows are
        # recomputed with every new turn (and a recalc restamps sv.updated), so neither the
        # row's stamp nor the session's start can hold a `--check` snapshot steady
        where.append("ss.ended IS NOT NULL AND ss.ended < ?")
        where_params.append(until)
    sql = ("SELECT COALESCE(ss.account, ?, '(unknown)') AS session_account,"
           " sv.session_id AS session_id, sv.run_id AS run_id,"
           " sv.measured_saved_usd AS measured, sv.modeled_saved_usd AS modeled,"
           " sv.measured_detail_json AS detail_json"
           " FROM savings sv LEFT JOIN sessions ss ON ss.session_id = sv.session_id"
           " WHERE %s" % " AND ".join(where))
    params = [default_account] + where_params
    try:
        raw = conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError:
        return []
    # Expand rows via per-turn attribution (T9 shared helper)
    out = []
    for r in raw:
        session_account = r["session_account"] if hasattr(r, "keys") else r[0]
        session_id = r["session_id"] if hasattr(r, "keys") else r[1]
        run_id = r["run_id"] if hasattr(r, "keys") else r[2]
        measured = float((r["measured"] if hasattr(r, "keys") else r[3]) or 0.0)
        modeled = r["modeled"] if hasattr(r, "keys") else r[4]
        detail_json = r["detail_json"] if hasattr(r, "keys") else r[5]
        ba = savings.detail_by_account(detail_json, session_account, measured)
        for acct, val in ba.items():
            if args.account and acct != args.account:
                continue
            # modeled stays on session account only (no per-turn split)
            mod = modeled if acct == session_account else None
            out.append({"account": acct, "session_id": session_id,
                        "run_id": run_id, "measured": val, "modeled": mod})
    return out


def _counted_rows(conn, since, until, default_account=None):
    """Counted tier per account (fix-10): non-void helper kept-out tokens by return (else spawn)
    time, plus credited tool-call ``kept_tokens``; ``{acct: {tokens, runs, calls}}``."""
    acct_expr = "COALESCE(ss.account, ?, '(unknown)')"
    out_ = {}
    bounds = [(op, val) for op, val in ((">=", since), ("<", until)) if val]
    r_ts = "COALESCE(r.returned_at, r.spawned_at)"
    r_where = ["COALESCE(r.void, 0)=0"] + ["%s %s ?" % (r_ts, op) for op, _ in bounds]
    c_where = ["COALESCE(c.kept_tokens, 0) > 0"] + ["c.ts %s ?" % op for op, _ in bounds]
    vals = [v for _, v in bounds]
    queries = (
        ("runs", "SELECT %s, COUNT(*), COALESCE(SUM(r.kept_out_tokens), 0) FROM retrievals r"
                 " LEFT JOIN agent_runs a ON a.run_id = r.run_id"
                 " LEFT JOIN agent_runs p ON p.run_id = r.parent_run_id"
                 " LEFT JOIN sessions ss ON ss.session_id ="
                 " COALESCE(a.session_id, p.session_id, r.parent_run_id)"
                 " WHERE %s GROUP BY 1" % (acct_expr, " AND ".join(r_where))),
        ("calls", "SELECT %s, COUNT(*), COALESCE(SUM(c.kept_tokens), 0) FROM tool_calls c"
                  " LEFT JOIN sessions ss ON ss.session_id = c.session_id"
                  " WHERE %s GROUP BY 1" % (acct_expr, " AND ".join(c_where))))
    for kind, sql in queries:
        try:
            rows = conn.execute(sql, [default_account] + vals).fetchall()
        except sqlite3.DatabaseError:
            rows = []                              # an older ledger lacks the table
        for row in rows:
            slot = out_.setdefault(row[0], {"tokens": 0, "runs": 0, "calls": 0})
            slot[kind] += int(row[1] or 0)
            slot["tokens"] += int(row[2] or 0)
    return out_


def _seed_rows(conn, args, since):
    where = ["1=1"]
    params = []
    if args.session:
        where.append("a.session_id = ?")
        params.append(args.session)
    if since:
        where.append("COALESCE(a.started,'') >= ?")
        params.append(since)
    if args.phase:
        where.append("a.phase = ?")
        params.append(args.phase)
        if not args.phase.startswith("beside "):
            # T31: a beside session's runs never count under the owner phase (unhealed runs)
            where.append("a.session_id NOT IN (SELECT session_id FROM sessions"
                         " WHERE phase LIKE 'beside %')")
    sql = ("SELECT a.run_id, a.session_id, a.kind, a.agent_type, a.seed_ctx, a.ctx_at_end,"
           " a.turns FROM agent_runs a WHERE %s ORDER BY a.started" % " AND ".join(where))
    try:
        runs = conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError:
        return []
    rows = []
    for run in runs:
        first = conn.execute("SELECT ctx, cache_read, input, cache_write FROM turns"
                             " WHERE run_id=? AND kind='api' ORDER BY ts LIMIT 1",
                             (run["run_id"],)).fetchone()
        ctx = int((first["ctx"] if first else run["seed_ctx"]) or 0)
        read = int((first["cache_read"] if first else 0) or 0)
        rows.append({"run_id": run["run_id"], "session_id": run["session_id"],
                     "kind": run["kind"], "agent_type": run["agent_type"],
                     "seed_ctx": ctx, "cache_read": read,
                     "read_share": (read / float(ctx)) if ctx else None,
                     "ctx_at_end": run["ctx_at_end"], "turns": run["turns"]})
    return rows


def _thinking_rows(conn, args, since):
    """Thinking/output share per model, read from each session's ``cost-state``."""
    where = ["transcript_path IS NOT NULL"]
    params = []
    if args.session:
        where.append("session_id = ?")
        params.append(args.session)
    if args.account:
        where.append("account = ?")
        params.append(args.account)
    if since:
        where.append("COALESCE(ended, started, '') >= ?")
        params.append(since)
    try:
        rows = conn.execute("SELECT session_id, transcript_path FROM sessions WHERE %s"
                            " ORDER BY started DESC LIMIT 200" % " AND ".join(where),
                            params).fetchall()
    except sqlite3.DatabaseError:
        return {}
    per_model = {}
    for row in rows:
        path = row["transcript_path"]
        if not path or not os.path.exists(path):
            continue
        state = transcript.cost_state_last(path, full_scan=False)
        if not state:
            continue
        for model, usage in (state.get("modelUsage") or {}).items():
            slot = per_model.setdefault(model, {"output": 0, "thinking": 0, "cost": 0.0,
                                                "sessions": 0})
            slot["output"] += int(usage.get("outputTokens") or 0)
            slot["thinking"] += int(usage.get("thinkingTokens") or 0)
            slot["cost"] += float(usage.get("costUSD") or 0.0)
            slot["sessions"] += 1
    return per_model


def cmd_report(args):
    cfg = config.load()
    summary_doc = fsutil.read_json(paths.summary_path(), {}) or {}
    since, label = window_cutoff(args.window, cfg, summary_doc, args.account)
    if args.since:
        since = parse_date(args.since)
        label = "since %s" % since[:10]
    until = parse_date(getattr(args, "until", None))

    readers = _report_conns(cfg)
    accounts = {}
    try:
        for reader in readers:
            conn = reader["conn"]
            default_account = reader.get("account")
            machine_fallback = reader.get("machine") or reader.get("label") or "local"
            for row in _report_rows(conn, args, since, default_account, until):
                acct = accounts.setdefault(row["account"], {
                    "account": row["account"], "cost_used": 0.0, "requests": 0,
                    "ctx_sum": 0.0, "output": 0, "thinking": 0,
                    "saved_measured": 0.0, "saved_modeled": None, "rows": {}})
                cost = float(row["cost"] or 0.0)
                acct["cost_used"] += cost
                acct["requests"] += int(row["requests"] or 0)
                acct["ctx_sum"] += float(row["avg_ctx"] or 0.0) * int(row["requests"] or 0)
                acct["output"] += int(row["output"] or 0)
                acct["thinking"] += int(row["thinking"] or 0)
                key = row["grp"] if row["grp"] is not None else "(none)"
                machine = row["machine"] or machine_fallback
                slot = acct["rows"].setdefault(key, {"key": key, "cost": 0.0, "requests": 0,
                                                     "ctx_sum": 0.0, "saved_measured": 0.0,
                                                     "saved_modeled": None,
                                                     "account": row["account"], "machine": machine})
                slot["cost"] += cost
                slot["requests"] += int(row["requests"] or 0)
                slot["ctx_sum"] += float(row["avg_ctx"] or 0.0) * int(row["requests"] or 0)
                if args.by == "session" and "harness" not in slot:
                    # T30: the harness cost-state total, a cross-check column
                    try:
                        hc = conn.execute("SELECT harness_cost_usd FROM sessions WHERE session_id=?",
                                          (key,)).fetchone()
                    except Exception:
                        hc = None            # a pre-v5 ledger has no column
                    slot["harness"] = hc[0] if hc and hc[0] is not None else None
            for row in _saved_rows(conn, args, since, default_account, until):
                acct = accounts.get(row["account"])
                if acct is None:
                    continue
                acct["saved_measured"] += float(row["measured"] or 0.0)
                if row["modeled"] is not None:
                    acct["saved_modeled"] = (acct["saved_modeled"] or 0.0) + float(row["modeled"])
                key = row["session_id"] if args.by == "session" else row["run_id"]
                if key in acct["rows"]:
                    acct["rows"][key]["saved_measured"] += float(row["measured"] or 0.0)
                    if row["modeled"] is not None:
                        acct["rows"][key]["saved_modeled"] = (
                            acct["rows"][key].get("saved_modeled") or 0.0
                        ) + float(row["modeled"])

        # compute modeled savings in-memory when DB has none
        from . import replay
        for acct_key, acct_data in list(accounts.items()):
            if acct_data["saved_modeled"] is not None:
                continue
            db_account = None if acct_key == "(unknown)" else acct_key
            acct_total = 0.0
            for reader in readers:
                conn = reader["conn"]
                turns_r, seeds_r = replay.account_turns(conn, db_account)
                if not turns_r:
                    continue
                rp = replay.params_from_cfg(cfg)
                results_r = replay.rolling(turns_r, seeds_r, rp)
                sums_r = replay.session_sums(results_r)
                for sid, saved_mod in sums_r.items():
                    if sid in acct_data["rows"]:
                        acct_data["rows"][sid]["saved_modeled"] = round(saved_mod, 6)
                        acct_total += saved_mod
                if "_assumptions" not in acct_data:
                    acct_data["_assumptions"] = replay.assumptions(rp, results_r)
            acct_data["saved_modeled"] = round(acct_total, 6)

        # fix-10: counted and modeled tiers per account for the report's window
        cfg_win = savings._cfg_window_tokens()
        # the pair adds ~23 s on the whole ledger (live, 2026-09-27): bounded windows only
        sens_wins = (450000, 200000) if since else ()
        from . import summary as _summary_era
        for reader in readers:
            conn = reader["conn"]
            # fix-10.c2: the all-time block starts at the ledger era, like the summary's lifetime
            t0 = since or _summary_era.era_start(conn, cfg)
            counted = _counted_rows(conn, t0, until, reader.get("account"))
            for acct_key, acct_data in accounts.items():
                db_account = None if acct_key == "(unknown)" else acct_key
                tiers = acct_data.setdefault("tiers", {
                    "counted": {"tokens": 0, "runs": 0, "calls": 0},
                    "carry": {"window": cfg_win, "usd": None if db_account is None else 0.0,
                              "sensitivity": ({str(w): 0.0 for w in sens_wins}
                                              if sens_wins and db_account else None)},
                    "replay_usd": None, "since": t0})
                for k, v in (counted.get(acct_key) or {}).items():
                    tiers["counted"][k] += v
                if db_account is None:
                    continue                       # None walks every account: not this one's
                tiers["carry"]["usd"] += savings.window_saved_measured(
                    conn, db_account, t0, until)
                for w in sens_wins:
                    tiers["carry"]["sensitivity"][str(w)] += savings.window_saved_measured(
                        conn, db_account, t0, until, window=w)
        for acct_data in accounts.values():
            tiers = acct_data.get("tiers")
            if tiers:
                if tiers["carry"]["usd"] is not None:
                    tiers["carry"]["usd"] = round(tiers["carry"]["usd"], 6)
                for w in tiers["carry"]["sensitivity"] or {}:
                    tiers["carry"]["sensitivity"][w] = round(tiers["carry"]["sensitivity"][w], 6)
                tiers["replay_usd"] = acct_data["saved_modeled"]
                tiers["replay_in_memory"] = "_assumptions" in acct_data

        seeds = []
        thinking = {}
        if args.seeds:
            for reader in readers:
                seeds.extend(_seed_rows(reader["conn"], args, since))
        if args.thinking:
            for reader in readers:
                for model, slot in _thinking_rows(reader["conn"], args, since).items():
                    tgt = thinking.setdefault(model, {"output": 0, "thinking": 0, "cost": 0.0,
                                                      "sessions": 0})
                    for key in ("output", "thinking", "sessions"):
                        tgt[key] += slot[key]
                    tgt["cost"] += slot["cost"]

        # latest pct used per account for 5h, 7d and every model_scoped:* window (T18)
        util_latest = {}                          # {account: {window: (ts, pct)}}
        for reader in readers:
            try:
                u_rows = reader["conn"].execute(
                    "SELECT account, window, pct, MAX(ts) AS ts FROM utilization"
                    " WHERE pct IS NOT NULL AND (window IN ('five_hour', 'seven_day')"
                    " OR window LIKE 'model_scoped:%') GROUP BY account, window").fetchall()
            except Exception:
                u_rows = []
            for u in u_rows:
                u_acct = u["account"] or reader.get("account") or "(unknown)"
                seen = util_latest.setdefault(u_acct, {})
                prior = seen.get(u["window"])
                if prior is None or (u["ts"] or "") > (prior[0] or ""):
                    seen[u["window"]] = (u["ts"], u["pct"])

        # T32.1.c3: the pooled per-family fit per (tier, window), first reader that has one
        from . import fit as _fit_mod
        pools = {}                                # {(tier, window): {"rates", "meta"}}
        for reader in readers:
            try:
                tw = reader["conn"].execute(
                    "SELECT DISTINCT tier, window FROM fit_pool ORDER BY tier, window").fetchall()
            except Exception:
                tw = []
            for t_row in tw:
                key = (t_row[0], t_row[1])
                if key in pools:
                    continue
                rates = _fit_mod.pool_rates(reader["conn"], key[0], key[1], cfg)
                if rates:
                    pools[key] = {"rates": rates,
                                  "meta": _fit_mod.pool_meta(reader["conn"], key[0], key[1])}
        # harness delta: residual rows (harness minus turns) per account; never in fit or ratio
        harness_delta = {}
        for reader in readers:
            hd_where, hd_params = ["t.kind='residual'"], [reader.get("account")]
            if since:
                hd_where.append("t.ts >= ?")
                hd_params.append(since)
            if until:
                hd_where.append("t.ts < ?")
                hd_params.append(until)
            try:
                hd_rows = reader["conn"].execute(
                    "SELECT COALESCE(t.account, ?, '(unknown)') AS acct,"
                    " COALESCE(SUM(t.cost_usd), 0) FROM turns t WHERE %s GROUP BY 1"
                    % " AND ".join(hd_where), hd_params).fetchall()
            except Exception:
                hd_rows = []
            for hd in hd_rows:
                harness_delta[hd[0]] = harness_delta.get(hd[0], 0.0) + float(hd[1] or 0.0)

        # pre-PA3 history (T11): cost and requests from pre-era sessions
        from . import summary as _summary_mod
        pre_era_info = {}                         # {account: {cost, requests, first_ts, last_ts, era_date}}
        for reader in readers:
            rconn = reader["conn"]
            era_iso = _summary_mod.era_start(rconn, cfg)
            if not era_iso:
                continue
            pre_sids = _summary_mod._pre_era_sids(rconn, era_iso)
            if not pre_sids:
                continue
            default_account = reader.get("account")
            placeholders = ",".join("?" * len(pre_sids))
            try:
                pe_rows = rconn.execute(
                    "SELECT COALESCE(t.account, ?, '(unknown)') AS acct,"
                    " COUNT(*) AS n, COALESCE(SUM(t.cost_usd), 0) AS cost,"
                    " MIN(t.ts) AS first_ts, MAX(t.ts) AS last_ts"
                    " FROM turns t WHERE t.kind='api' AND t.session_id IN (%s)"
                    " GROUP BY 1" % placeholders,
                    [default_account] + list(pre_sids)).fetchall()
            except Exception:
                pe_rows = []
            for pe_row in pe_rows:
                pe_acct = pe_row["acct"] if hasattr(pe_row, "keys") else pe_row[0]
                slot = pre_era_info.setdefault(pe_acct, {"cost": 0.0, "requests": 0,
                                                          "first_ts": None, "last_ts": None,
                                                          "era_date": era_iso[:10]})
                slot["cost"] += float(pe_row["cost"] if hasattr(pe_row, "keys") else pe_row[2])
                slot["requests"] += int(pe_row["n"] if hasattr(pe_row, "keys") else pe_row[1])
                ft = pe_row["first_ts"] if hasattr(pe_row, "keys") else pe_row[3]
                if ft and (slot["first_ts"] is None or ft < slot["first_ts"]):
                    slot["first_ts"] = ft
                lt = pe_row["last_ts"] if hasattr(pe_row, "keys") else pe_row[4]
                if lt and (slot["last_ts"] is None or lt > slot["last_ts"]):
                    slot["last_ts"] = lt
    finally:
        for reader in readers:
            db.close(reader["conn"])

    all_kinds = getattr(args, "all_kinds", False)
    cost_basis = "all turn kinds" if all_kinds else "priced api turns"
    doc = {"version": __version__, "window": label, "since": since, "by": args.by,
           "cost_basis": cost_basis, "accounts": []}
    for acct in sorted(accounts.values(), key=lambda a: -a["cost_used"]):
        win = ((summary_doc.get("accounts") or {}).get(acct["account"]) or {})
        winfo = (win.get("windows") or {}).get(args.window or "", {}) or {}
        entry = {
            "account": acct["account"],
            "cost_used": acct["cost_used"],
            "saved_measured": acct["saved_measured"],
            "saved_modeled": acct["saved_modeled"],
            "ratio": savings.headline_ratio(acct["saved_measured"], acct["cost_used"]),
            "pct_per_dollar": winfo.get("pct_per_dollar"),
            "fit_quality": winfo.get("fit_quality"),
            "fit_method": winfo.get("fit_method"),
            "fit_reason": winfo.get("fit_reason"),
            "pct_saved_pooled": winfo.get("pct_saved_pooled"),
            "pool_rates": winfo.get("pool_rates"),
            "harness_delta": round(harness_delta.get(acct["account"], 0.0), 6),
            "requests": acct["requests"],
            "avg_ctx": (acct["ctx_sum"] / acct["requests"]) if acct["requests"] else 0.0,
            "output": acct["output"], "thinking": acct["thinking"],
            "pct_used": {w: v[1] for w, v in sorted(
                (util_latest.get(acct["account"]) or {}).items())},
            "tiers": acct.get("tiers"),
            "rows": [],
        }
        for slot in sorted(acct["rows"].values(), key=lambda r: -r["cost"]):
            row = {
                "key": slot["key"], "cost": slot["cost"], "requests": slot["requests"],
                "avg_ctx": (slot["ctx_sum"] / slot["requests"]) if slot["requests"] else 0.0,
                "saved_measured": slot["saved_measured"],
                "saved_modeled": slot.get("saved_modeled"),
                "account": slot.get("account"), "machine": slot.get("machine")}
            if args.by == "session":
                row["harness_cost_usd"] = slot.get("harness")
            if args.by == "project":
                # T12: the project's share of the current windows and its lifetime savings,
                # from summary.json's `projects` block (already keyed the same way).
                # Usage share comes from this row's own account's slice in `by_account`
                # (T9), the way statusline._line_project reads it; the money columns
                # (lifetime, period, windows) stay the top-level all-accounts totals.
                from . import summary as summary_mod
                proj = (summary_doc.get("projects") or {}).get(
                    summary_mod._project_key(slot["key"])) or {}
                by_account = proj.get("by_account")
                acct_slice = by_account.get(slot["account"]) if isinstance(by_account, dict) else None
                if isinstance(acct_slice, dict):
                    pct_used = acct_slice.get("pct_used") or {}
                else:
                    # backward compat: no by_account in this summary (old data)
                    pct_used = proj.get("pct_used") or {}
                row["pct_used_five_hour"] = pct_used.get("five_hour")
                row["pct_used_seven_day"] = pct_used.get("seven_day")
                row["lifetime_saved"] = (proj.get("lifetime") or {}).get("cost_saved_measured")
            entry["rows"].append(row)
        pe = pre_era_info.get(acct["account"])
        if pe and pe["requests"]:
            fm = pe["first_ts"][:7] if pe["first_ts"] else "?"
            lm = pe["last_ts"][:7] if pe["last_ts"] else "?"
            entry["pre_era"] = {"date": pe["era_date"], "cost": round(pe["cost"], 2),
                                "requests": pe["requests"],
                                "first_month": fm, "last_month": lm}
        doc["accounts"].append(entry)
    if args.seeds:
        doc["seeds"] = seeds
    if args.thinking:
        doc["thinking"] = [dict(model=m, **v) for m, v in sorted(thinking.items())]

    # modeled assumptions for --json and footer
    from . import replay as _replay
    _rp = _replay.params_from_cfg(cfg)
    # prefer the in-memory replay's assumptions when available
    _assume = None
    for _a in accounts.values():
        if isinstance(_a, dict) and "_assumptions" in _a:
            _assume = _a["_assumptions"]
            break
    if _assume is None:
        _assume = _replay.assumptions(_rp, [])
    doc["assumptions"] = _assume

    # --modeled --json: add replay bases and measured detail to the doc before output
    if args.modeled and args.json:
        from . import replay as _replay_mod
        from . import savings as _savings_mod
        basis_choice = getattr(args, "basis", "both") or "both"
        readers_j = _report_conns(cfg)
        try:
            basis_map = {}
            for reader in readers_j:
                bm = _replay_mod.bases(cfg, reader["conn"])
                if not basis_map:
                    basis_map = bm

            show_bases = []
            if basis_choice in ("vanilla", "both"):
                show_bases.append(("vanilla", basis_map.get("vanilla", {})))
            # T30: the pa2 basis is retired and omitted (plain 1M cycle)

            doc["modeled_bases"] = {}
            for basis_name, basis_params in show_bases:
                bp = dict(basis_params)
                src = bp.pop("_source", {})
                per_acct = {}
                for reader in readers_j:
                    rc_j = reader["conn"]
                    for acct_key in sorted(accounts):
                        db_account = None if acct_key == "(unknown)" else acct_key
                        turns_r, seeds_r = _replay_mod.account_turns(rc_j, db_account)
                        if not turns_r:
                            continue
                        rp = dict(bp)
                        results_r = _replay_mod.rolling(turns_r, seeds_r, rp)
                        cr = sum(r["cost_replay"] for r in results_r)
                        ca = sum(float(t.get("cost_usd") or 0) for t in turns_r)
                        slot = per_acct.setdefault(acct_key, {"replay_cost": 0.0, "actual_cost": 0.0})
                        slot["replay_cost"] += cr
                        slot["actual_cost"] += ca
                for ak in per_acct:
                    s = per_acct[ak]
                    s["replay_cost"] = round(s["replay_cost"], 6)
                    s["actual_cost"] = round(s["actual_cost"], 6)
                    s["saving"] = round(s["replay_cost"] - s["actual_cost"], 6)
                    s["multiplier"] = round(s["replay_cost"] / s["actual_cost"], 1) if s["actual_cost"] > 0 else 0
                doc["modeled_bases"][basis_name] = {
                    "params": bp,
                    "source": src,
                    "accounts": per_acct,
                }

            # measured block -- aggregate across readers
            measured_accts = {}
            for reader in readers_j:
                rc_j = reader["conn"]
                for acct_key in sorted(accounts):
                    db_account = None if acct_key == "(unknown)" else acct_key
                    detail = _savings_mod.window_saved_measured(
                        rc_j, db_account, since, until,
                        detail=True, session=getattr(args, "session", None))
                    slot = measured_accts.setdefault(acct_key, {"gross": 0.0, "seeds": 0.0, "net": 0.0})
                    slot["gross"] += detail.get("gross", 0.0)
                    slot["seeds"] += detail.get("seeds", 0.0)
                    slot["net"] += detail.get("net", 0.0)
            for ak in measured_accts:
                for fld in ("gross", "seeds", "net"):
                    measured_accts[ak][fld] = round(measured_accts[ak][fld], 6)
            doc["measured"] = measured_accts
        finally:
            for reader in readers_j:
                db.close(reader["conn"])

    if args.json:
        out(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
        return 0

    out("Project Architect 3.0, version %s · report  window=%s  by=%s%s"
        % (__version__, label, args.by, "  since %s" % since if since else ""))
    if all_kinds:
        out("cost = all turn kinds (including residual corrections)")
    else:
        out("cost = priced api turns; residual corrections in reconcile")
    out("harness cost-state: cross-check only (a [1m] launch model is billed at the long-context "
        "premium every turn, ~+1/3 on router sessions; sessions.cost_usd is the ledger's "
        "per-turn pricing)")
    out("savings = %s (helpers' kept-out + tool-call credit: counted tokens × a carry model at a"
        " 1M window and cache-read prices; the replay is a second model; never summed, D39;"
        " the all-time block starts at the ledger era)"
        % savings.SAVINGS_VERSION)
    if not pools:
        out("pool: none (set fit.regime_since or accounts.<email>.regime_since)")
    for (p_tier, p_win), pool in sorted(pools.items()):
        fams = " · ".join(
            ("%s %.5f/$ (borrowed from 5h)" % (fam, info["weight"]) if info.get("borrowed_from")
             else "%s %s/$ (se %s, n %s)" % (
                fam, "-" if info.get("weight") is None else "%.5f" % info["weight"],
                "-" if info.get("se") is None else "%.5f" % info["se"],
                "-" if info.get("n") is None else info["n"]))
            for fam, info in pool["rates"].items())
        no_rate = [f for f in _fit_mod.FAMILIES if f not in pool["rates"]]   # fix-20: never a silent 0
        if no_rate:
            fams += " · no rate: " + ", ".join(no_rate)
        meta = pool.get("meta") or {}
        out("pool %s %s: %s  | instances %s, regime since %s"
            % (p_tier, p_win, fams, meta.get("n_instances", "-"),
               meta.get("regime_since") or "-"))
    if not doc["accounts"]:
        out("(no turns match)")
    for entry in doc["accounts"]:
        out("")
        ratio = entry["ratio"]
        headers = ["account", "cost used", "saved meas.", "ratio", "pct/$",
                   "quality", "method", "requests", "avg ctx"]
        values = [entry["account"], usd(entry["cost_used"]), usd(entry["saved_measured"]),
                  "-" if ratio is None else "%.2fx" % ratio,
                  "-" if entry["pct_per_dollar"] is None else "%.5f" % entry["pct_per_dollar"],
                  entry["fit_quality"] or "-", entry["fit_method"] or "-",
                  num(entry["requests"]), num(round(entry["avg_ctx"]))]
        aligns = ["<", ">", ">", ">", ">", "<", "<", ">", ">"]
        if args.modeled:
            headers.insert(3, "saved mod.")
            values.insert(3, "-" if entry["saved_modeled"] is None else usd(entry["saved_modeled"]))
            aligns.insert(3, ">")
        pu = entry.get("pct_used") or {}
        if pu:
            # T18: account pct used, 5h/7d then each model_scoped:* weekly window
            for win, lbl in ([("five_hour", "5h"), ("seven_day", "7d")]
                             + [(w, "%s 7d" % w.split(":", 1)[1]) for w in pu
                                if w.startswith("model_scoped:")]):
                headers.append(lbl)
                values.append("-" if pu.get(win) is None else pct(pu[win], 0))
                aligns.append(">")
        head = table(headers, [values], aligns)
        for line in head:
            out(line)
        out("harness delta (residual rows, harness minus turns; excluded from fit and ratio): %s"
            % usd(entry["harness_delta"]))
        tiers = entry.get("tiers")
        if tiers:
            # fix-10: what is counted and what is modeled, with the carry window sensitivity
            cn, cy = tiers["counted"], tiers["carry"]
            sens = cy["sensitivity"]
            out("  savings, %s, %s" % (
                "since %s" % tiers["since"][:10] if not since and tiers.get("since") else label,
                entry["account"]))
            out("    counted   kept out of the parent context: %s tokens over %s helper runs"
                " and %s credited tool calls" % (num(cn["tokens"]), num(cn["runs"]), num(cn["calls"])))
            out("    modeled   carry avoided at a %sk window: %s   (%s)"
                % (num(cy["window"] // 1000), "-" if cy["usd"] is None else usd(cy["usd"]),
                   "450k: %s · 200k: %s" % (usd(sens["450000"]), usd(sens["200000"])) if sens
                   else "no account to walk" if cy["usd"] is None
                   else "sensitivity: use --window seven_day"))
            out("    modeled   whole account replayed as one session: %s%s   (the two modeled"
                " figures are never summed)"
                % ("-" if tiers["replay_usd"] is None else usd(tiers["replay_usd"]),
                   " (in-memory replay)" if tiers.get("replay_in_memory") else ""))
        if entry["rows"]:
            out("")
            show_machine = args.by == "session"
            headers = [args.by, "cost", "requests", "avg ctx", "saved meas."]
            aligns = ["<", ">", ">", ">", ">"]
            if show_machine:
                headers.insert(1, "machine")
                aligns.insert(1, "<")
                headers.insert(3, "harness")
                aligns.insert(3, ">")
            if args.modeled:
                headers.append("saved mod.")
                aligns.append(">")
            if args.by == "project":
                headers += ["5h", "7d", "lifetime"]
                aligns += [">", ">", ">"]
            rows = []
            for r in entry["rows"][:args.limit]:
                row = [r["key"]]
                if show_machine:
                    row.append(r.get("machine") or "-")
                row.append(usd(r["cost"]))
                if show_machine:
                    hc = r.get("harness_cost_usd")
                    row.append("-" if hc is None else usd(hc))
                row += [num(r["requests"]), num(round(r["avg_ctx"])), usd(r["saved_measured"])]
                if args.modeled:
                    row.append("-" if r.get("saved_modeled") is None else usd(r["saved_modeled"]))
                if args.by == "project":
                    row += ["-" if r.get("pct_used_five_hour") is None else pct(r["pct_used_five_hour"], 0),
                           "-" if r.get("pct_used_seven_day") is None else pct(r["pct_used_seven_day"], 0),
                           "-" if r.get("lifetime_saved") is None else usd(r["lifetime_saved"])]
                rows.append(row)
            for line in table(headers, rows, aligns):
                out("  " + line)
        if entry.get("pre_era"):
            pe = entry["pre_era"]
            out("  pre-PA3 (before %s): %s · %s requests · %s-%s · not on the lines"
                % (pe["date"], usd(pe["cost"]), num(pe["requests"]),
                   pe["first_month"], pe["last_month"]))
    if args.seeds:
        out("")
        out("seeds (first request per run)")
        rows = [[s["run_id"], s["kind"] or "-", s["agent_type"] or "-", num(s["seed_ctx"]),
                 num(s["cache_read"]),
                 "-" if s["read_share"] is None else pct(100.0 * s["read_share"], 1),
                 num(s["ctx_at_end"]), s["turns"]] for s in seeds[:args.limit]]
        for line in table(["run", "kind", "agent_type", "seed ctx", "cache_read", "read share",
                           "ctx at end", "turns"], rows,
                          ["<", "<", "<", ">", ">", ">", ">", ">"]):
            out("  " + line)
    if args.thinking:
        out("")
        out("thinking share (from cost-state)")
        rows = []
        for item in doc["thinking"]:
            share = (100.0 * item["thinking"] / item["output"]) if item["output"] else None
            rows.append([item["model"], num(item["output"]), num(item["thinking"]),
                         "-" if share is None else pct(share, 1), usd(item["cost"]),
                         item["sessions"]])
        for line in table(["model", "output", "thinking", "share", "cost", "sessions"], rows,
                          ["<", ">", ">", ">", ">", ">"]):
            out("  " + line)
    # --modeled: two replay bases and the measured block
    if args.modeled:
        from . import replay as _replay_mod
        from . import savings as _savings_mod
        basis_choice = getattr(args, "basis", "both") or "both"
        readers = _report_conns(cfg)
        try:
            basis_map = {}
            for reader in readers:
                rc = reader["conn"]
                bm = _replay_mod.bases(cfg, rc)
                if not basis_map:
                    basis_map = bm
        finally:
            for reader in readers:
                db.close(reader["conn"])

        show_bases = []
        if basis_choice in ("vanilla", "both"):
            show_bases.append(("vanilla", basis_map.get("vanilla", {})))
        if basis_choice in ("pa2", "both"):
            # T30: the pa2 basis is not replayed (plain 1M cycle)
            out("")
            out("replay basis: pa2 -- retired at T30 (the counterfactual is a plain 1M window)")

        # replay each basis over the same turns
        readers2 = _report_conns(cfg)
        try:
            for basis_name, basis_params in show_bases:
                out("")
                out("replay basis: %s" % basis_name)
                # aggregate across readers per account
                agg = {}
                last_assume = None
                for reader in readers2:
                    rc2 = reader["conn"]
                    for acct_key in sorted(accounts):
                        db_account = None if acct_key == "(unknown)" else acct_key
                        turns_r, seeds_r = _replay_mod.account_turns(rc2, db_account)
                        if not turns_r:
                            continue
                        rp = dict(basis_params)
                        rp.pop("_source", None)
                        results_r = _replay_mod.rolling(turns_r, seeds_r, rp)
                        cr = sum(r["cost_replay"] for r in results_r)
                        ca = sum(float(t.get("cost_usd") or 0) for t in turns_r)
                        slot = agg.setdefault(acct_key, {"replay": 0.0, "actual": 0.0})
                        slot["replay"] += cr
                        slot["actual"] += ca
                        last_assume = _replay_mod.assumptions(rp, results_r)
                for acct_key in sorted(agg):
                    slot = agg[acct_key]
                    saving = slot["replay"] - slot["actual"]
                    mult = (slot["replay"] / slot["actual"]) if slot["actual"] > 0 else 0
                    out("  account: %s" % acct_key)
                    out("  replay cost: %s  actual: %s  saving: %s  multiplier: %.1fx"
                        % (usd(slot["replay"]), usd(slot["actual"]), usd(saving), mult))
                src = basis_params.get("_source", {})
                rp_show = dict(basis_params)
                rp_show.pop("_source", None)
                pv = last_assume.get("prices_version", "?") if last_assume else "?"
                out("  assumptions: window %dk · ttl %ds · agent seeds %s · prices %s"
                    % (int(rp_show.get("window", 1000000)) // 1000,
                       int(rp_show.get("ttl_old_s", 3600)),
                       "subtracted" if rp_show.get("subtract_agent_seeds", True) else "not subtracted",
                       pv))

            # measured block -- aggregate across readers
            out("")
            out("measured (%s)" % savings.SAVINGS_VERSION)
            measured_agg = {}
            for reader in readers2:
                rc2 = reader["conn"]
                for acct_key in sorted(accounts):
                    db_account = None if acct_key == "(unknown)" else acct_key
                    detail = _savings_mod.window_saved_measured(
                        rc2, db_account, since, until,
                        detail=True,
                        session=getattr(args, "session", None))
                    slot = measured_agg.setdefault(acct_key, {"gross": 0.0, "seeds": 0.0, "net": 0.0})
                    slot["gross"] += detail.get("gross", 0.0)
                    slot["seeds"] += detail.get("seeds", 0.0)
                    slot["net"] += detail.get("net", 0.0)
            for acct_key in sorted(measured_agg):
                slot = measured_agg[acct_key]
                cost_acct = accounts.get(acct_key, {}).get("cost_used", 0.0)
                vanilla_equiv = cost_acct + slot["net"]
                pct_fewer = (100.0 * slot["net"] / vanilla_equiv) if vanilla_equiv > 0 else 0
                mult_longer = (vanilla_equiv / cost_acct) if cost_acct > 0 else 0
                out("  account: %s" % acct_key)
                out("  gross: %s  seeds: %s  net: %s" % (usd(slot["gross"]), usd(slot["seeds"]), usd(slot["net"])))
                if vanilla_equiv > 0 and cost_acct > 0:
                    out("  %.0f%% fewer than vanilla · lasts %.1fx longer" % (pct_fewer, mult_longer))
        finally:
            for reader in readers2:
                db.close(reader["conn"])

    return 0


# =========================================================================== reconcile


def _transcript_truth(path, sid, cfg):
    """(a) Aggregate every request of main + subagent transcripts by normalized model.

    Returns ``{norm_key: {n, input, cache_write_5m, cache_write_1h, cache_read,
    output, cost, over_n}, ...}``, per_run ``{run_id: {agent_type, n, cost}, ...}``,
    and ``transcript_total`` (float).  ``over_n`` (T8) counts the requests whose
    own context exceeded ``LONG_CONTEXT_TOKENS`` -- ``cost`` already prices them
    at the long-context tier (``transcript.request_cost``); ``_price_check``
    only reads ``over_n`` as a per-pool "long-context" flag (a cost-state pool
    total has no per-request ctx to apportion, see ``prices.cost_of``).
    """
    pools = {}          # norm_key -> aggregate
    per_run = {}        # run_id -> {agent_type, n, cost}

    def _add(req, ttl):
        nk = prices.normalize_model(req.get("model"))
        cost_usd, _parts = transcript.request_cost(req, ttl_default=ttl)
        if nk not in pools:
            pools[nk] = {"n": 0, "input": 0, "cache_write_5m": 0,
                         "cache_write_1h": 0, "cache_read": 0, "output": 0, "cost": 0.0,
                         "over_n": 0}
        p = pools[nk]
        p["n"] += 1
        p["input"] += int(req.get("input") or 0)
        cw = int(req.get("cache_write") or 0)
        if req.get("cache_write_5m") is not None or req.get("cache_write_1h") is not None:
            w5 = int(req.get("cache_write_5m") or 0)
            w1 = int(req.get("cache_write_1h") or 0)
        elif ttl in ("5m", "5min"):
            w5, w1 = cw, 0
        else:
            w5, w1 = 0, cw
        p["cache_write_5m"] += w5
        p["cache_write_1h"] += w1
        p["cache_read"] += int(req.get("cache_read") or 0)
        p["output"] += int(req.get("output") or 0)
        p["cost"] += cost_usd
        if int(req.get("ctx") or 0) > prices.LONG_CONTEXT_TOKENS:
            p["over_n"] += 1
        return cost_usd

    # main transcript
    run_cost = 0.0
    run_n = 0
    for req in transcript.iter_requests(path):
        _add(req, "1h")
        run_cost += pools[prices.normalize_model(req.get("model"))]["cost"] - (run_cost if run_n else 0.0)
        run_n += 1
    per_run[sid] = {"agent_type": "main", "n": run_n, "cost": sum(p["cost"] for p in pools.values())}

    # subagent transcripts
    for sub_path in transcript.subagent_files(path):
        agent_id = transcript.agent_id_from_path(sub_path)
        if not agent_id:
            continue
        meta = transcript.read_meta(sub_path)
        agent_type = meta.get("agentType") or "other"
        role = config.role_from_agent_type(agent_type, cfg)
        ttl = config.ttl_for_role(role, cfg)
        sub_n = 0
        sub_cost = 0.0
        for req in transcript.iter_requests(sub_path):
            c = _add(req, ttl)
            sub_cost += c
            sub_n += 1
        per_run[agent_id] = {"agent_type": agent_type, "n": sub_n, "cost": sub_cost}

    total = sum(p["cost"] for p in pools.values())
    return pools, per_run, total


def _split_candidates(nk, cs, tp, creation):
    """T8.c2: grade a pool's ``cacheCreationInputTokens`` 5m/1h split against four
    candidates and return the one that best explains the pool's own cost-state
    cost -- the T8.c1 bracket-key rule (bare keys → 5m) underprices a bare-key
    pool whose bare traffic is itself mixed (e.g. a coder subagent's 1h writes
    pooled with a retriever subagent's 5m writes under the same bare key,
    a6ab5364: -1.93 %); the transcript's own pooled ratio catches that case.

    Returns ``(candidates, best_label)``: ``candidates`` is
    ``{label: {"w5", "w1", "cost", "delta_pct"}}`` (a pool with no transcript
    requests has no "transcript …" candidate); ``best_label`` is the smallest
    |delta_pct| among priced candidates (falls back to "key rule" when the
    pool's cost-state cost is 0, so no delta can be graded).
    """
    actual = cs["cost"]

    def _price(w5, w1):
        shaped = {"inputTokens": cs["inputTokens"], "outputTokens": cs["outputTokens"],
                  "cacheReadInputTokens": cs["cacheReadInputTokens"],
                  "cache_write_5m": w5, "cache_write_1h": w1}
        cost, parts = prices.cost_of(shaped, nk, ttl_default="1h")
        delta = ((cost - actual) / actual * 100.0) if actual else None
        return {"w5": w5, "w1": w1, "cost": cost, "delta_pct": delta, "priced": parts["priced"]}

    cands = {}
    total_tw = (tp["cache_write_5m"] + tp["cache_write_1h"]) if tp else 0
    if total_tw > 0:
        w5 = int(round(creation * tp["cache_write_5m"] / float(total_tw)))
        w1 = creation - w5
        pct1h = int(round(tp["cache_write_1h"] / float(total_tw) * 100.0))
        cands["transcript %d%% 1h" % pct1h] = _price(w5, w1)
    # key rule (T8.c1): bracket-tagged keys' writes are 1h, bare keys' are 5m
    cands["key rule"] = _price(cs["cw_bare"], cs["cw_bracket"])
    cands["all 1h"] = _price(0, creation)
    cands["all 5m"] = _price(creation, 0)

    graded = {label: c for label, c in cands.items() if c["delta_pct"] is not None}
    best = min(graded, key=lambda label: abs(graded[label]["delta_pct"])) if graded else "key rule"
    return cands, best


def _price_check(state, transcript_pools):
    """(b) Price check per pool: cost-state modelUsage pooled by normalize_model.

    Returns ``(models_list, worst_delta, ok_price)``.
    """
    cs_pools = {}       # norm_key -> {cost, input, output, cache_read, cache_write, thinking}
    for model, usage in (state.get("modelUsage") or {}).items():
        nk = prices.normalize_model(model)
        if nk not in cs_pools:
            cs_pools[nk] = {"cost": 0.0, "inputTokens": 0, "outputTokens": 0,
                            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                            "thinkingTokens": 0, "raw_keys": [],
                            "cw_bracket": 0, "cw_bare": 0}
        p = cs_pools[nk]
        p["cost"] += float(usage.get("costUSD") or 0.0)
        p["inputTokens"] += int(usage.get("inputTokens") or 0)
        p["outputTokens"] += int(usage.get("outputTokens") or 0)
        p["cacheReadInputTokens"] += int(usage.get("cacheReadInputTokens") or 0)
        cw = int(usage.get("cacheCreationInputTokens") or 0)
        p["cacheCreationInputTokens"] += cw
        p["thinkingTokens"] += int(usage.get("thinkingTokens") or 0)
        p["raw_keys"].append(model)
        if "[" in model:
            p["cw_bracket"] += cw       # bracket-tagged key -> 1h cache
        else:
            p["cw_bare"] += cw

    worst = 0.0
    models = []
    for nk, cs in sorted(cs_pools.items()):
        actual = cs["cost"]
        creation = cs["cacheCreationInputTokens"]
        tp = transcript_pools.get(nk)
        # T8.c2: grade the 5m/1h split against four candidates (transcript ratio,
        # T8.c1 key rule, all-1h, all-5m) and keep the one that best explains the
        # pool's own cost-state cost -- see ``_split_candidates``.
        cands, best = _split_candidates(nk, cs, tp, creation)
        chosen = cands[best]
        w5, w1, cost, delta = chosen["w5"], chosen["w1"], chosen["cost"], chosen["delta_pct"]
        # T8: the long-context tier is per-request (prices.cost_of, applied via
        # pa.transcript.request_cost for real per-request costs); a cost-state
        # pool is a whole-session total whose own token counts include an
        # unknown share of invisible "subagent overhead" traffic (design doc F),
        # so apportioning it between under/over-200k shares from the transcript's
        # own ratio was verified (b2fa9a37) to overshoot in every combination
        # tried -- the pool stays priced flat; "long_context" only flags that its
        # transcript carries over-200k requests, for the reconcile table's note.
        long_ctx = bool(tp and tp.get("over_n"))
        graded = actual >= PRICE_MIN_COST and chosen["priced"]
        if graded and delta is not None:
            worst = max(worst, abs(delta))
        models.append({
            "model": nk, "raw_keys": cs["raw_keys"],
            "cost_state_usd": actual, "computed_usd": cost,
            "delta_pct": delta, "graded": graded, "priced": chosen["priced"],
            "long_context": long_ctx,
            "write_split": best,
            "split_candidates": {label: c["delta_pct"] for label, c in cands.items()},
            "w5": w5, "w1": w1,
            "input": cs["inputTokens"], "output": cs["outputTokens"],
            "cache_read": cs["cacheReadInputTokens"],
            "cache_write": creation, "thinking": cs["thinkingTokens"]})
    ok_price = worst <= PRICE_TOLERANCE
    return models, worst, ok_price


def _coverage_check(conn, sid, transcript_total, per_run):
    """(c) Coverage = Σ turns.cost_usd (kind='api') / transcript total.

    Returns ``(attributed, coverage_pct, ok_coverage, n_main, n_sub, unbooked)``.
    """
    attributed = float(scalar(conn, "SELECT COALESCE(SUM(cost_usd),0) FROM turns"
                                    " WHERE session_id=? AND kind='api'", (sid,), 0.0))
    n_main = int(scalar(conn, "SELECT COUNT(*) FROM turns WHERE session_id=? AND kind='api'"
                              " AND run_id=?", (sid, sid), 0))
    n_sub = int(scalar(conn, "SELECT COUNT(*) FROM turns WHERE session_id=? AND kind='api'"
                             " AND run_id<>?", (sid, sid), 0))
    coverage = (attributed / transcript_total * 100.0) if transcript_total else 0.0
    ok_coverage = coverage >= COVERAGE_TARGET

    # detect unbooked runs: booked count < transcript count
    unbooked = []
    try:
        booked_rows = conn.execute(
            "SELECT run_id, COUNT(*) AS n FROM turns"
            " WHERE session_id=? AND kind='api' GROUP BY run_id", (sid,)).fetchall()
    except sqlite3.DatabaseError:
        booked_rows = []
    booked_map = {r["run_id"]: int(r["n"]) for r in booked_rows}
    for run_id, info in sorted(per_run.items()):
        booked_n = booked_map.get(run_id, 0)
        transcript_n = info["n"]
        if booked_n < transcript_n:
            unbooked.append({"run_id": run_id, "agent_type": info["agent_type"],
                             "booked": booked_n, "in_transcript": transcript_n,
                             "missing_cost": info["cost"] * (transcript_n - booked_n) / transcript_n
                             if transcript_n else 0.0})
    unbooked.sort(key=lambda u: u["missing_cost"], reverse=True)
    return attributed, coverage, ok_coverage, n_main, n_sub, unbooked[:10]


def _cost_state_crosscheck(state, fresh, stale_offset, path, transcript_pools, transcript_total,
                           per_run):
    """(d) Cost-state cross-check (never part of the exit code).

    Fresh: Δ = totalCostUSD − transcript total, split into classes.
    Stale: one line with message position.
    """
    total_cs = float(state.get("totalCostUSD") or 0.0)
    if not fresh:
        # count assistant records (requests) before the cost-state to report position
        n_before = 0
        n_total = 0
        for offset, rec in transcript.iter_records(path, start_offset=0, with_offset=True):
            msg, usage = transcript._usage_of(rec)
            if msg is not None:
                n_total += 1
                if offset <= stale_offset:
                    n_before += 1
        return {"cost_state_fresh": False,
                "cost_state_stale_at": "message %d of %d" % (n_before, n_total)}

    excess_total = total_cs - transcript_total
    extras = scan_session_extras(path)

    # per-pool breakdown
    cs_pools = {}
    for model, usage in (state.get("modelUsage") or {}).items():
        nk = prices.normalize_model(model)
        if nk not in cs_pools:
            cs_pools[nk] = 0.0
        cs_pools[nk] += float(usage.get("costUSD") or 0.0)

    cli_internal = 0.0      # pools with cost-state cost but no transcript request
    cli_internal_keys = []
    subagent_overhead = 0.0
    for nk, cs_cost in cs_pools.items():
        tp = transcript_pools.get(nk)
        if tp is None or tp["n"] == 0:
            cli_internal += cs_cost
            cli_internal_keys.append(nk)
        else:
            pool_excess = cs_cost - tp["cost"]
            if pool_excess > 0:
                subagent_overhead += pool_excess

    n_sub_requests = sum(info["n"] for rid, info in per_run.items()
                         if info["agent_type"] != "main")

    result = {"cost_state_fresh": True,
              "excess": excess_total,
              "cli_internal": cli_internal,
              "cli_internal_keys": cli_internal_keys,
              "subagent_overhead": subagent_overhead,
              "n_sub_requests": n_sub_requests,
              "away_summary": extras["away_summary"],
              "ai_title": extras["ai_title"]}
    if abs(excess_total) < 0.01:
        result["excess_class"] = "exact"
    return result


def reconcile_session(conn, path, sid, cfg):
    """Design doc F: transcript-truth price check + coverage + cost-state cross-check.

    T8: a killed or headless session (every Windows workflow session included)
    may carry no cost-state record at all -- ``ok_price`` is then None (not
    graded: "n/a", not FAIL), the cross-check is skipped ("cost-state absent"),
    and coverage alone (against the transcript) decides the exit code.
    """
    result = {"session_id": sid, "transcript": path, "models": [], "ok_price": False,
              "ok_coverage": False, "total_cost_state": 0.0, "computed": 0.0}

    # (a) transcript truth
    transcript_pools, per_run, transcript_total = _transcript_truth(path, sid, cfg)
    result["transcript_total"] = transcript_total

    # cost-state record + freshness
    cs_rec, fresh, cs_offset = transcript.cost_state_fresh(path)
    result["cost_state_fresh"] = fresh
    if not cs_rec:
        result["cost_state_absent"] = True
        result["ok_price"] = None
        result["worst_delta_pct"] = None
        attributed, coverage, ok_coverage, n_main, n_sub, unbooked = _coverage_check(
            conn, sid, transcript_total, per_run)
        extras_for_compat = scan_session_extras(path)
        result.update({
            "attributed": attributed, "coverage_pct": coverage, "ok_coverage": ok_coverage,
            "residual": None, "n_main": n_main, "n_sub": n_sub,
            "away_summary": extras_for_compat["away_summary"],
            "ai_title": extras_for_compat["ai_title"],
            "unbooked": unbooked})
        return result
    total_cs = float(cs_rec.get("totalCostUSD") or 0.0)
    result["total_cost_state"] = total_cs

    # (b) price check per pool
    models, worst, ok_price = _price_check(cs_rec, transcript_pools)
    computed = sum(m["computed_usd"] for m in models)
    result["models"] = models
    result["computed"] = computed
    result["worst_delta_pct"] = worst
    result["ok_price"] = ok_price

    # (c) coverage (against transcript total)
    attributed, coverage, ok_coverage, n_main, n_sub, unbooked = _coverage_check(
        conn, sid, transcript_total, per_run)
    extras_for_compat = scan_session_extras(path)
    result.update({
        "attributed": attributed, "coverage_pct": coverage, "ok_coverage": ok_coverage,
        "residual": total_cs - attributed, "n_main": n_main, "n_sub": n_sub,
        "away_summary": extras_for_compat["away_summary"],
        "ai_title": extras_for_compat["ai_title"],
        "unbooked": unbooked})

    # (d) cost-state cross-check
    xcheck = _cost_state_crosscheck(cs_rec, fresh, cs_offset, path,
                                    transcript_pools, transcript_total, per_run)
    result.update(xcheck)
    return result


def cmd_reconcile(args):
    cfg = config.load()
    roots = projects_roots(args.root)
    item = find_transcript(args.sid, roots)
    if not item:
        err("reconcile: no transcript for %s under %s" % (args.sid, ", ".join(roots)))
        return 1
    conn = open_db()
    try:
        res = reconcile_session(conn, item["path"], item["sid"], cfg)
    finally:
        db.close(conn)
    if res.get("error"):
        err("reconcile: %s" % res["error"])
        return 1
    if args.json:
        out(json.dumps(dict({"version": __version__}, **res), ensure_ascii=False, indent=1,
                       default=str))
        ok = res["ok_coverage"] if res.get("cost_state_absent") else bool(
            res["ok_price"] and res["ok_coverage"])
        return 0 if ok else 1

    out("reconcile %s  (%s)" % (res["session_id"], item["slug"]))
    out("")
    out("1. price check per pool  (cost-state tokens x pa.prices, target |delta| <= %.1f%%)" % PRICE_TOLERANCE)
    if res.get("cost_state_absent"):
        out("  n/a (no cost-state record)")
    else:
        rows = []
        for m in res["models"]:
            keys_str = ", ".join(m.get("raw_keys", [m["model"]]))
            note_bits = []
            if not m["graded"]:
                note_bits.append("unpriced" if not m["priced"] else "below $%.2f" % PRICE_MIN_COST)
            if m.get("long_context"):
                note_bits.append("long-context")
            rows.append([m["model"], keys_str, usd(m["cost_state_usd"], 6), usd(m["computed_usd"], 6),
                         "-" if m["delta_pct"] is None else "%+.4f%%" % m["delta_pct"],
                         m.get("write_split", "-"), "; ".join(note_bits)])
        for line in table(["pool", "keys", "cost-state", "computed", "delta", "write split", "note"], rows,
                          ["<", "<", ">", ">", ">", "<", "<"]):
            out("  " + line)
    out("")
    out("2. coverage  (sum turns.cost_usd / transcript total, target >= %.0f%%)" % COVERAGE_TARGET)
    away = res.get("away_summary", [])
    for line in table(["metric", "value"], [
            ["api turns", "%s main + %s subagent" % (num(res["n_main"]), num(res["n_sub"]))],
            ["sum turns.cost_usd", usd(res["attributed"], 4)],
            ["transcript total", usd(res.get("transcript_total", 0.0), 4)],
            ["coverage", pct(res["coverage_pct"], 3)],
            ["away_summary recaps", "%d%s" % (len(away), ("  " + ", ".join(str(a) for a in away[:4])) if away else "")],
            ["ai-title records", str(res["ai_title"])]], ["<", "<"]):
        out("  " + line)
    if res.get("unbooked"):
        out("")
        out("  unbooked runs (booked < transcript):")
        ub_rows = []
        for u in res["unbooked"]:
            ub_rows.append([u["run_id"][:16], u["agent_type"],
                            str(u["booked"]), str(u["in_transcript"]),
                            usd(u["missing_cost"], 4)])
        for line in table(["run", "type", "booked", "in transcript", "missing cost"], ub_rows,
                          ["<", "<", ">", ">", ">"]):
            out("    " + line)
    out("")
    out("3. cost-state cross-check")
    if res.get("cost_state_absent"):
        out("  cost-state absent")
    elif res.get("cost_state_fresh"):
        excess = res.get("excess", 0.0)
        cls = res.get("excess_class", "")
        if cls == "exact":
            out("  excess: %s (exact)" % usd(excess, 4))
        else:
            out("  excess: %s" % usd(excess, 4))
            cli = res.get("cli_internal", 0.0)
            if cli:
                out("    cli-internal: %s  (%s)" % (usd(cli, 4),
                    ", ".join(res.get("cli_internal_keys", []))))
            so = res.get("subagent_overhead", 0.0)
            if so:
                out("    subagent overhead: %s  (%d subagent requests)" % (
                    usd(so, 4), res.get("n_sub_requests", 0)))
    else:
        out("  cost-state stale at %s" % res.get("cost_state_stale_at", "?"))

    out("")
    if res.get("cost_state_absent"):
        ok = bool(res["ok_coverage"])
        out("RESULT  price n/a (no cost-state record)   coverage %s (%.3f%%)   cost-state absent  -> exit %d" % (
            "OK" if res["ok_coverage"] else "FAIL", res["coverage_pct"], 0 if ok else 1))
    else:
        ok = bool(res["ok_price"] and res["ok_coverage"])
        fresh_label = "fresh" if res.get("cost_state_fresh") else "stale"
        stale_extra = ""
        if not res.get("cost_state_fresh") and res.get("cost_state_stale_at"):
            stale_extra = " (%s)" % res["cost_state_stale_at"]
        out("RESULT  price %s (worst |delta| %.4f%%)   coverage %s (%.3f%%)   cost-state %s%s  -> exit %d" % (
            "OK" if res["ok_price"] else "FAIL", res.get("worst_delta_pct") or 0.0,
            "OK" if res["ok_coverage"] else "FAIL", res["coverage_pct"],
            fresh_label, stale_extra, 0 if ok else 1))
    if not res["n_main"]:
        out("note: no turns for this session -- run 'pa-ledger recalc --session %s' first"
            % res["session_id"])
    return 0 if ok else 1


# =========================================================================== prices

def _strip_html(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"&nbsp;?", " ", text)


def _money(cell):
    m = _MONEY_RE.search(cell or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _label_model_key(cell):
    """``Claude Fable 5.1`` / ``claude-fable-5-1`` -> the ``pa.prices`` key."""
    raw = str(cell or "")
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return prices.model_key(slug) or prices.model_key(raw)


def parse_price_table(text):
    """Best-effort parse of a markdown/HTML pricing table into price rows."""
    if "<" in text and ">" in text and "|" not in text:
        text = _strip_html(text)
    elif "<td" in text.lower() or "<tr" in text.lower():
        text = _strip_html(text)
    rows = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        key = _label_model_key(cells[0])
        if not key:
            continue
        vals = [v for v in (_money(c) for c in cells[1:]) if v is not None]
        if len(vals) >= 5:
            row = dict(zip(("input", "write_5m", "write_1h", "read", "output"), vals[:5]))
        elif len(vals) == 4:
            row = {"input": vals[0], "write_5m": vals[1], "read": vals[2], "output": vals[3]}
            row["write_1h"] = round(vals[0] * 2.0, 4)
        else:
            continue
        row["model_key"] = key
        rows.setdefault(key, row)
    return list(rows.values())


def fetch_price_page(url=PRICING_URL, timeout=20):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "pa-ledger/3.0 (+stdlib)"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read(4000000).decode("utf-8", "replace")


def _current_prices(conn):
    cur = {}
    try:
        rows = conn.execute("SELECT model_key, priority, effective_from, input, write_5m,"
                            " write_1h, read, output, tokenizer FROM prices"
                            " ORDER BY priority, effective_from").fetchall()
    except sqlite3.DatabaseError:
        rows = []
    for row in rows:
        cur[row["model_key"]] = dict(row)
    return cur


def cmd_prices(args):
    conn = open_db()
    try:
        current = _current_prices(conn)
        if not current:
            current = {t[0]: dict(zip(("model_key", "priority", "effective_from", "input",
                                       "write_5m", "write_1h", "read", "output", "tokenizer"), t))
                       for t in prices.rows_for_db()}
        if args.show or not (args.refresh or args.paste or args.apply):
            out("prices  version=%s  rows=%d  (%s)" % (
                prices.PRICES_VERSION, len(current),
                "ledger.sqlite" if _current_prices(conn) else "pa.prices seed"))
            rows = [[r["model_key"], r["effective_from"], "%.2f" % r["input"],
                     "%.2f" % r["write_5m"], "%.2f" % r["write_1h"], "%.2f" % r["read"],
                     "%.2f" % r["output"], r.get("tokenizer") or "-"]
                    for r in sorted(current.values(), key=lambda r: r["priority"])]
            for line in table(["model_key", "effective_from", "input", "write 5m", "write 1h",
                               "read", "output", "tok"], rows,
                              ["<", "<", ">", ">", ">", ">", ">", "<"]):
                out(line)
            if not (args.refresh or args.paste or args.apply):
                return 0

        parsed = []
        if args.refresh:
            try:
                text = fetch_price_page(args.url)
            except Exception as exc:                     # network / TLS / proxy
                err("prices --refresh: fetch failed: %s" % exc)
                return 1
            parsed = parse_price_table(text)
            out("")
            out("fetched %s: %d price rows parsed" % (args.url, len(parsed)))
        elif args.paste:
            text = sys.stdin.read()
            parsed = parse_price_table(text)
            out("pasted table: %d price rows parsed" % len(parsed))
        if args.apply and not parsed:
            err("prices --apply: nothing parsed (use --refresh or --paste)")
            return 1
        if not parsed:
            return 0

        eff = parse_date(args.effective_from)[:10] if args.effective_from else \
            time.strftime("%Y-%m-%d")
        out("")
        rows = []
        for row in sorted(parsed, key=lambda r: r["model_key"]):
            old = current.get(row["model_key"])
            changed = []
            for field in ("input", "write_5m", "write_1h", "read", "output"):
                if not old or abs(float(old[field]) - float(row[field])) > 1e-9:
                    changed.append("%s %s->%.2f" % (field, "-" if not old else "%.2f" % old[field],
                                                    row[field]))
            rows.append([row["model_key"], "%.2f" % row["input"], "%.2f" % row["write_5m"],
                         "%.2f" % row["write_1h"], "%.2f" % row["read"], "%.2f" % row["output"],
                         ", ".join(changed) if changed else "(same)"])
        for line in table(["model_key", "input", "write 5m", "write 1h", "read", "output", "diff"],
                          rows, ["<", ">", ">", ">", ">", ">", "<"]):
            out(line)
        if not args.apply:
            out("")
            out("(diff only -- re-run with --apply [--effective-from DATE] to write rows)")
            return 0
        written = 0
        with _tx(conn):
            for row in parsed:
                old = current.get(row["model_key"]) or {}
                db.upsert_price(conn, {
                    "model_key": row["model_key"],
                    "priority": old.get("priority", 500),
                    "effective_from": eff,
                    "input": row["input"], "write_5m": row["write_5m"],
                    "write_1h": row["write_1h"], "read": row["read"], "output": row["output"],
                    "tokenizer": old.get("tokenizer")})
                written += 1
        out("")
        out("applied %d rows with effective_from=%s" % (written, eff))
        return 0
    finally:
        db.close(conn)


# =========================================================================== accounts / summary

def cmd_accounts(args):
    cfg = config.load()
    conn = open_db()
    try:
        action = args.action or "list"
        if action == "list":
            try:
                rows = conn.execute(
                    "SELECT a.email, a.label, a.subscription, a.first_seen, a.last_seen, a.tier,"
                    " (SELECT COUNT(*) FROM sessions s WHERE s.account = a.email) AS sessions,"
                    " (SELECT COALESCE(SUM(t.cost_usd),0) FROM turns t WHERE t.account = a.email) AS cost"
                    " FROM accounts a ORDER BY a.email").fetchall()
            except sqlite3.DatabaseError:
                rows = []
            seen = set(r["email"] for r in rows)
            extra = []
            try:
                for row in conn.execute("SELECT COALESCE(account,'(unknown)') AS email,"
                                        " COUNT(*) AS sessions,"
                                        " (SELECT COALESCE(SUM(cost_usd),0) FROM turns t"
                                        "  WHERE COALESCE(t.account,'(unknown)') = COALESCE(s.account,'(unknown)')) AS cost"
                                        " FROM sessions s GROUP BY 1").fetchall():
                    if row["email"] not in seen:
                        extra.append(row)
            except sqlite3.DatabaseError:
                pass
            labels = cfg.get("accounts") or {}
            body = []
            for row in list(rows) + extra:
                label = row["label"] if "label" in row.keys() else None
                if not label:
                    entry = labels.get(row["email"])
                    label = entry.get("label") if isinstance(entry, dict) else None
                tier = row["tier"] if "tier" in row.keys() else None
                renews = config.renewal_day_for(cfg, row["email"])
                body.append([row["email"], label or "-", tier or "-", renews or "-",
                             "*" if row["email"] == cfg.get("recalc_default_account") else "",
                             row["sessions"], usd(row["cost"])])
            if not body:
                out("no accounts recorded yet")
                return 0
            for line in table(["email", "label", "tier", "renews", "default", "sessions", "cost"], body,
                              ["<", "<", "<", ">", "^", ">", ">"]):
                out(line)
            return 0
        if action == "label":
            if not args.email or not args.label:
                err("accounts label EMAIL LABEL")
                return 2
            cfg.setdefault("accounts", {})
            entry = cfg["accounts"].get(args.email)
            cfg["accounts"][args.email] = {"label": args.label} if not isinstance(entry, dict) \
                else dict(entry, label=args.label)
            config.save(cfg)
            with _tx(conn):
                db.upsert_account(conn, {"email": args.email, "label": args.label,
                                         "last_seen": now_iso()})
            out("labelled %s -> %s" % (args.email, args.label))
            return 0
        if action == "default":
            if not args.email:
                err("accounts default EMAIL")
                return 2
            cfg["recalc_default_account"] = args.email
            config.save(cfg)
            out("recalc_default_account = %s  (%s)" % (args.email, paths.config_path()))
            return 0
        if action == "tier":
            # the developer's override: wins over the credentials tier and survives a restamp
            value = args.label
            if not args.email or value not in accounts.TIERS + ("clear",):
                err("accounts tier EMAIL max20|max5|pro|clear")
                return 2
            with _tx(conn):
                accounts.stamp_tier(conn, args.email, None if value == "clear" else value,
                                    source="override")
            out("tier %s -> %s" % (args.email, "cleared" if value == "clear" else value))
            return 0
        if action == "renewal":
            # 3.11 T9: the user's word, read from the Claude app's Settings > Billing page; never a guess
            value = args.label
            day = None
            if value != "clear":
                try:
                    day = int(value)
                except (TypeError, ValueError):
                    day = None
            if not args.email or (value != "clear" and not (day and 1 <= day <= 31)):
                err("accounts renewal EMAIL DAY|clear  (DAY: the day of the month the plan renews, 1-31; "
                    "the Claude app shows it under Settings > Billing)")
                return 2
            cfg.setdefault("accounts", {})
            entry = dict(cfg["accounts"].get(args.email) or {})
            if day is None:
                entry.pop("renewal_day", None)
            else:
                entry["renewal_day"] = day
            cfg["accounts"][args.email] = entry
            config.save(cfg)
            try:
                from . import summary as _summary

                _summary.request_rebuild(cfg)       # the month figures return at the next rebuild
            except Exception:
                pass
            out("renewal day %s -> %s" % (args.email, "cleared" if day is None else day))
            return 0
        err("accounts: unknown action %r" % action)
        return 2
    finally:
        db.close(conn)


def cmd_fit(args):
    """Show or refit window instances."""
    from . import fit as _fit, summary as _summary

    cfg = config.load()
    conn = open_db()
    # Build readers so the fit sees all roots
    readers = None
    try:
        extra = config.extra_root_entries(cfg)
        if extra:
            readers = db.union_readers(extra, include_local=False)
    except Exception:
        pass
    try:
        if args.refit:
            _fit.refit_all(conn, cfg, account=args.account, readers=readers)
            rebuild_summary(conn, cfg, readers=readers)

        # Gather instances
        where = []
        params = []
        if args.account:
            where.append("account IS ?")
            params.append(args.account)
        if args.window:
            where.append("window=?")
            params.append(args.window)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            "SELECT account, window, resets_at, started_at, quantized, "
            "pct_per_dollar, fit_method, fit_n, fit_crossings, fit_span, fit_se, "
            "fit_quality, fit_detail "
            "FROM window_instances" + clause + " ORDER BY account, window, resets_at",
            params).fetchall()

        if not rows:
            out("(no instances)")
            if args.check:
                err("fit --check: no instances found")
                return 1
            return 0

        # For each instance, compute fit in memory (without writing) when not --refit
        import time as _time
        tbl_rows = []
        all_fits = {}  # (account, window) -> [fit_dict, ...]
        for r in rows:
            acct = r["account"]
            win = r["window"]
            ra = r["resets_at"]
            started = r["started_at"]

            # the real utilization row count for this instance -- distinct from the
            # fit's own point count (fit.n, which quantization/filtering can shrink)
            samples_2d = _fit.instance_samples(conn, acct, win, ra,
                                                  readers=readers)
            sample_count = len(samples_2d)

            # If --refit was done, use stored values; else compute in memory
            if args.refit:
                fit_detail_raw = r["fit_detail"]
                fam_dict = None
                if fit_detail_raw:
                    try:
                        fam_dict = json.loads(fit_detail_raw)
                    except (ValueError, TypeError):
                        pass
                fit = {"method": r["fit_method"], "slope": r["pct_per_dollar"],
                       "se": r["fit_se"], "n": r["fit_n"], "crossings": r["fit_crossings"],
                       "span_pct": r["fit_span"], "quality": r["fit_quality"] or "none",
                       "dollars_per_window": (1.0 / r["pct_per_dollar"]
                                              if r["pct_per_dollar"] and r["pct_per_dollar"] > 0
                                              else None),
                       "families": fam_dict}
            else:
                # Compute in memory
                if not samples_2d:
                    fit = {"method": None, "slope": None, "quality": "none",
                           "n": 0, "crossings": 0, "span_pct": 0, "se": None,
                           "dollars_per_window": None, "families": None}
                else:
                    if started is None:
                        dur = _summary.window_duration(win, cfg)
                        started = int(ra) - dur
                    mf = _fit._model_filter_key(win)
                    if mf:
                        epochs = [s[0] for s in samples_2d]
                        costs = _fit.cost_axis(conn, acct, started, epochs,
                                              model_filter=mf, readers=readers)
                        samples_3d = [(samples_2d[i][0], samples_2d[i][1], costs[i])
                                      for i in range(len(samples_2d))]
                    else:
                        epochs = [s[0] for s in samples_2d]
                        fam_costs = _fit.cost_axes(conn, acct, started, epochs,
                                                   readers=readers)
                        samples_3d = [(samples_2d[i][0], samples_2d[i][1], fam_costs[i])
                                      for i in range(len(samples_2d))]
                    fit = _fit.fit_instance(samples_3d, cfg)

            key = (acct, win)
            all_fits.setdefault(key, []).append(fit)

            if (sample_count == 0 or not acct) and not args.all:
                continue                              # empty / unowned: hidden by default

            resets_utc = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(ra))
            started_utc = (_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(started))
                           if started else "-")
            dpw = fit.get("dollars_per_window")
            # Build weights column: "fable 0.0050 . opus 0.0005 . sonnet 0.0006"
            weights_str = "-"
            fams = fit.get("families")
            if fams and isinstance(fams, dict):
                parts = []
                for fam in _fit.FAMILIES:
                    info = fams.get(fam)
                    if info and isinstance(info, dict) and info.get("weight"):
                        parts.append("%s %.4f" % (fam, info["weight"]))
                if parts:
                    weights_str = " . ".join(parts)
                # Show the Fable $/window when available, else dominant
                if "fable" in fams and isinstance(fams["fable"], dict):
                    fw = fams["fable"].get("weight")
                    if fw and fw > 0:
                        dpw = 1.0 / fw
            tbl_rows.append([
                acct if acct else "(unknown)",
                win,
                resets_utc,
                started_utc,
                str(sample_count),
                str(fit.get("crossings") or 0),
                str(fit.get("n") or 0),
                "%.0f" % fit.get("span_pct") if fit.get("span_pct") else "-",
                fit.get("method") or "-",
                "%.6f" % fit["slope"] if fit.get("slope") else "-",
                "$%.0f" % dpw if dpw else "-",
                fit.get("quality") or "none",
                weights_str,
            ])

        fit_headers = ["account", "window", "resets_at", "started", "samples", "crossings",
                       "fit n", "span", "method", "slope", "$/window", "quality", "weights"]
        if args.json:
            import json as _json
            out(_json.dumps([dict(zip(
                ["account", "window", "resets_at", "started", "samples", "crossings",
                 "fit_n", "span", "method", "slope", "$/window", "quality", "weights"], row))
                for row in tbl_rows], indent=1, default=str))
        else:
            lines = table(
                fit_headers, tbl_rows,
                aligns=["<", "<", "<", "<", ">", ">", ">", ">", "<", ">", ">", "<", "<"])
            for line in lines:
                out(line)

        # --check
        if args.check:
            # Find the latest five_hour or seven_day instance for the account
            check_acct = args.account
            if not check_acct:
                # Find the account with the newest sample
                newest = conn.execute(
                    "SELECT account FROM utilization ORDER BY ts DESC LIMIT 1").fetchone()
                check_acct = newest[0] if newest else None
            if not check_acct:
                err("fit --check: no account found")
                return 1
            best_q = "none"
            for win in ("five_hour", "seven_day"):
                latest = conn.execute(
                    "SELECT resets_at FROM window_instances "
                    "WHERE account IS ? AND window=? ORDER BY resets_at DESC LIMIT 1",
                    (check_acct, win)).fetchone()
                if not latest:
                    continue
                key = (check_acct, win)
                fits_for = all_fits.get(key, [])
                if fits_for:
                    q = fits_for[-1].get("quality", "none")
                    if q in ("ok", "band"):
                        best_q = q
            if best_q in ("ok", "band"):
                out("fit --check: quality %s" % best_q)
                return 0
            else:
                err("fit --check: quality %s (need ok or band)" % best_q)
                return 1
        return 0
    finally:
        for rd in (readers or []):
            try:
                rc = rd.get("conn")
                if rc is not None:
                    db.close(rc)
            except Exception:
                pass
        db.close(conn)


def _summary_line3(conn, cfg, doc, args):
    """Plain-text Pace, Session, Project and Account lines for the newest sampled session
    (``--line3``, T1.c1: four separate lines in the new schema)."""
    from . import statusline

    sessions = doc.get("sessions") if isinstance(doc, dict) else None
    sessions = sessions if isinstance(sessions, dict) else {}
    sid = getattr(args, "session", None)
    if not sid and sessions:
        sid = max(sessions, key=lambda s: sessions[s].get("started") or "")
    if not sid:
        return None
    row = conn.execute(
        "SELECT session_cost, model FROM utilization WHERE session_id=? "
        "AND session_cost IS NOT NULL ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
    if row and row[0] is not None:
        cost, model = row[0], row[1]
    else:
        cost = (sessions.get(sid) or {}).get("cost_usd")
        model = None
    # add rate_limits so the Pace line can compute
    rl_row = conn.execute(
        "SELECT pct, resets_at FROM utilization WHERE session_id=? AND window='seven_day' "
        "ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
    rate_limits = {}
    if rl_row and rl_row[0] is not None:
        rate_limits["seven_day"] = {"used_percentage": rl_row[0], "resets_at": rl_row[1]}
    payload = {"session_id": sid, "cost": {"total_cost_usd": cost}, "model": {"id": model},
               "rate_limits": rate_limits}
    line_cfg = dict(cfg)
    node = dict(cfg.get("statusline") or {})
    node["colors"] = False
    line_cfg["statusline"] = node
    ctx = statusline._gather(payload, line_cfg)
    sl = statusline.sl_config(line_cfg)
    now = time.time()
    return statusline._line3(payload, ctx, line_cfg, sl, now)


def cmd_summary(args):
    cfg = config.load()
    conn = open_db()
    readers = None                              # the union of roots, as recalc and the sampler use it
    try:
        try:
            extra = config.extra_root_entries(cfg)
            if extra:
                readers = db.union_readers(extra, include_local=False)
        except Exception:
            readers = None
        try:
            from . import fit as _fit
            _fit.refit_all(conn, cfg, readers=readers)
        except Exception:
            pass
        doc = rebuild_summary(conn, cfg, readers=readers)
        line3 = _summary_line3(conn, cfg, doc, args) if getattr(args, "line3", False) else None
    finally:
        for rd in (readers or []):
            try:
                rc = rd.get("conn")
                if rc is not None:
                    db.close(rc)
            except Exception:
                pass
        db.close(conn)
    out("summary.json written: %s" % paths.summary_path())
    if args.print_:
        out(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
    if line3 is not None:
        out(line3)
    return 0


# =========================================================================== sql / tail / watch

def is_read_only_sql(text):
    """True only for a single SELECT/WITH/EXPLAIN statement."""
    body = _SQL_COMMENT_RE.sub(" ", str(text or "")).strip()
    if not body:
        return False
    body = body.rstrip(";").strip()
    if ";" in body:
        return False
    head = body.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH", "EXPLAIN"):
        return False
    if _SQL_BANNED_RE.search(body):
        return False
    return True


def cmd_sql(args):
    query = args.query
    if not is_read_only_sql(query):
        err("sql: refused -- only a single read-only SELECT / WITH / EXPLAIN statement is allowed")
        return 2
    db.close(open_db())                                  # make sure the schema exists
    try:
        conn = db.connect(paths.db_path(), readonly=True)
    except sqlite3.DatabaseError as exc:
        err("sql: %s" % exc)
        return 1
    try:
        cur = conn.execute(query)
        rows = cur.fetchmany(args.limit) if args.limit else cur.fetchall()
        headers = [d[0] for d in (cur.description or [])]
    except sqlite3.DatabaseError as exc:
        err("sql: %s" % exc)
        return 1
    finally:
        db.close(conn)
    if args.json:
        out(json.dumps([dict(zip(headers, list(r))) for r in rows], ensure_ascii=False,
                       indent=1, default=str))
        return 0
    if not rows:
        out("(0 rows)")
        return 0
    body = [[("" if v is None else v) for v in list(r)] for r in rows]
    for line in table(headers, body):
        out(line)
    out("(%d rows)" % len(rows))
    return 0


def cmd_tail(args):
    db.close(open_db())
    try:
        conn = db.connect(paths.db_path(), readonly=True)
    except sqlite3.DatabaseError as exc:
        err("tail: %s" % exc)
        return 1
    try:
        rows = conn.execute(
            "SELECT ts, session_id, run_id, model, ctx, output, cost_usd, kind FROM turns"
            " ORDER BY ts DESC, rowid DESC LIMIT ?", (args.n,)).fetchall()
    except sqlite3.DatabaseError as exc:
        err("tail: %s" % exc)
        return 1
    finally:
        db.close(conn)
    if not rows:
        out("(no turns)")
        return 0
    body = [[r["ts"], (r["session_id"] or "")[:8], (r["run_id"] or "")[:17], r["model"] or "-",
             num(r["ctx"]), num(r["output"]), usd(r["cost_usd"], 4), r["kind"]]
            for r in reversed(rows)]
    for line in table(["ts", "session", "run", "model", "ctx", "output", "cost", "kind"], body,
                      ["<", "<", "<", "<", ">", ">", ">", "<"]):
        out(line)
    return 0


def _watch_line(name, path):
    doc = fsutil.read_json(path, None)
    if not isinstance(doc, dict):
        return "(unreadable)"
    if name == "running":
        sessions = doc.get("sessions") or {}
        agents = sum(len((s or {}).get("agents") or {}) for s in sessions.values())
        return "sessions %d  agents %d  updated %s" % (len(sessions), agents,
                                                       doc.get("updated") or "-")
    if name == "summary":
        accounts = doc.get("accounts") or {}
        parts = []
        for acct, info in sorted(accounts.items()):
            period = info.get("period") or {}
            parts.append("%s $%.2f" % (acct, float(period.get("cost_used") or 0.0)))
        return "  ".join(parts) or "(no accounts)"
    task = doc.get("task") or doc.get("task_id") or "-"
    return "phase %s task %s status %s" % (doc.get("phase") or "-", task,
                                           doc.get("status") or doc.get("note") or "-")


def cmd_watch(args):
    cwd = os.path.abspath(args.cwd or os.getcwd())
    targets = [("running", paths.running_path()), ("summary", paths.summary_path()),
               ("status", os.path.join(cwd, ".run", "status.json"))]
    out("watch  every %.1fs  (Ctrl-C to stop)" % args.interval)
    for name, path in targets:
        out("  %-8s %s" % (name, path))
    last = {}
    rounds = 0
    try:
        while True:
            for name, path in targets:
                stamp = fsutil.mtime(path)
                if stamp and last.get(name) != stamp:
                    last[name] = stamp
                    out("%s  %-8s %s" % (time.strftime("%H:%M:%S"), name,
                                         _watch_line(name, path)))
            rounds += 1
            if args.rounds and rounds >= args.rounds:
                break
            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        out("")
        out("watch stopped")
    return 0


# =========================================================================== follow

def cmd_follow(args):
    """3.1 T12.1.1.1: read-only live view of one subagent's transcript, in its own focus tab.

    Prints the agent's text and tool calls as they land; exits when SubagentStop drops
    ``state/follow/<run_id>.stop`` (the tab closes and focus returns), on Ctrl-C, or after
    ``--max-minutes``.
    """
    run_id = args.run_id
    path = args.transcript or ""
    stop = paths.follow_path(run_id, "stop")
    started = time.time()
    marker = fsutil.read_json(paths.follow_path(run_id), {}) or {}
    color = bool(getattr(sys.stdout, "isatty", lambda: False)())
    out(_paint(marker.get("title") or marker.get("agent") or run_id, _BOLD, color))
    out(_paint("run %s  (closes when the agent finishes; Ctrl-C to leave early)" % run_id, _DIM, color))
    offset, rounds, tools = 0, 0, {}
    try:
        while True:
            if path and os.path.exists(path):
                offset = _follow_render(path, offset, tools)
            rounds += 1
            if os.path.exists(stop):
                out("")
                out("agent finished")
                break
            if args.rounds and rounds >= args.rounds:
                break
            if time.time() - started > args.max_minutes * 60:
                out("follow timed out after %d min" % args.max_minutes)
                break
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        out("")
    finally:
        for p in (stop, paths.follow_path(run_id)):
            try:
                os.remove(p)
            except OSError:
                pass
    return 0


_DIM, _BOLD, _RED, _CYAN, _RESET = "\033[2m", "\033[1m", "\033[31m", "\033[36m", "\033[0m"


def _paint(text, code, color):
    return "%s%s%s" % (code, text, _RESET) if color else text


def _one_line(text, limit):
    one = " ".join(str(text or "").split())
    return one[:limit] + ("..." if len(one) > limit else "")


_GENERIC_NAMES = frozenset(["index.md", "readme.md", "skill.md", "__init__.py", "settings.json",
                            "pa.json", "phase_plan.md", "recap.md"])


def _base(path):
    """The file name, with its directory when the name alone says nothing (``cookbook/INDEX.md``)."""
    text = str(path or "").replace(chr(92), "/").rstrip("/")
    parts = [x for x in text.split("/") if x]
    if not parts:
        return text
    if parts[-1].lower() in _GENERIC_NAMES and len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


def _notice(text):
    """What a user-side text block is: a task notification, a subagent hand-back, or a message."""
    txt = str(text or "")
    if "<task-notification>" in txt:
        m = re.search(r"<summary>(.*?)</summary>", txt, re.S)
        return ("agent", _one_line(m.group(1) if m else "agent finished", 120))
    if "[Subagent hand-back]" in txt or "<agent-message" in txt:
        body = txt.split("The report follows:", 1)[-1]
        return ("agent", "hand-back: " + _one_line(body, 140))
    return ("message", _one_line(txt, 200))


def _result_text(body):
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return " ".join(str(b.get("text") or "") for b in body if isinstance(b, dict))
    return json.dumps(body) if body is not None else ""


def _follow_call(name, inp):
    """``(kind, line)`` for one tool call, summarised the way the Claude Code view does."""
    inp = inp if isinstance(inp, dict) else {}
    n = str(name or "tool")
    low = n.lower()
    if low == "agent":
        return ("agent", "%s: %s" % (inp.get("subagent_type") or "agent",
                                     _one_line(inp.get("description") or inp.get("prompt"), 90)))
    if low == "read":
        span = ""
        if inp.get("offset") or inp.get("limit"):
            span = " :%s+%s" % (inp.get("offset") or 0, inp.get("limit") or "")
        return ("tool", "read %s%s" % (_base(inp.get("file_path")), span))
    if low in ("grep", "glob"):
        where = _base(inp.get("path")) if inp.get("path") else ""
        return ("tool", "%s %s%s" % (low, _one_line(inp.get("pattern"), 70),
                                     (" in " + where) if where else ""))
    if low in ("bash", "powershell", "shell"):
        return ("tool", "%s %s" % (low, _one_line(inp.get("command") or inp.get("script"), 100)))
    if low in ("edit", "multiedit", "notebookedit"):
        return ("tool", "edit %s" % _base(inp.get("file_path") or inp.get("notebook_path")))
    if low == "write":
        return ("tool", "write %s" % _base(inp.get("file_path")))
    for key in ("description", "prompt", "query", "url", "command", "file_path", "pattern", "path"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return ("tool", "%s %s" % (n, _one_line(val, 90)))
    return ("tool", n)


def _follow_render(path, offset, tools, color=None):
    """Print what the transcript gained since ``offset``: the agent's text, its thinking (one dim
    line each), one short line per tool call, spawned agents and their returns, failed results.
    Returns the new offset; a partial last line waits for the next round."""
    if color is None:
        color = bool(getattr(sys.stdout, "isatty", lambda: False)())
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return offset
    lines = chunk.split(b"\n")
    tail = lines.pop()                                   # b"" after a complete line, else a partial one
    consumed = len(chunk) - len(tail)
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        kind = rec.get("type")
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if kind == "assistant" and btype == "text" and str(block.get("text") or "").strip():
                out(str(block["text"]).rstrip())
            elif kind == "assistant" and btype == "thinking" and str(block.get("thinking") or "").strip():
                out(_paint("  ~ " + _one_line(block["thinking"], 240), _DIM, color))
            elif kind == "assistant" and btype == "tool_use":
                what, line = _follow_call(block.get("name"), block.get("input"))
                tools[block.get("id")] = (block.get("name") or "tool", what, line)
                if what == "agent":
                    out(_paint("  \u25cf " + line, _BOLD + _CYAN, color))
                else:
                    out(_paint("  " + line, _DIM, color))
            elif kind == "user" and btype == "tool_result":
                name, what, line = tools.get(block.get("tool_use_id"), ("tool", "tool", "tool"))
                body = block.get("content")
                if block.get("is_error"):
                    out(_paint("  \u2717 %s failed: %s"
                               % (name, _one_line(_result_text(body), 140)), _RED, color))
                elif what == "agent":
                    text = _result_text(body)
                    if "Async agent launched" in text or "agentId:" in text:
                        out(_paint("  \u25cf %s running in the background"
                                   % line.split(":")[0], _CYAN, color))
                    else:
                        out(_paint("  \u25cf %s returned (%d chars)"
                                   % (line.split(":")[0], len(text)), _CYAN, color))
            elif kind == "user" and btype == "text" and str(block.get("text") or "").strip():
                what, line = _notice(block.get("text"))
                if what == "agent":
                    out(_paint("  \u25cf " + line, _BOLD + _CYAN, color))
                else:
                    out(_paint("  \u00bb " + line, _DIM, color))
    return offset + consumed


# =========================================================================== vacuum

def _days(text, default=120):
    m = re.match(r"^\s*(\d+)\s*d?\s*$", str(text or ""))
    return int(m.group(1)) if m else default


def cmd_vacuum(args):
    cfg = config.load()
    conn = open_db()
    try:
        days = _days(args.samples_older_than, int(config.get(cfg, "ledger.retain_samples_days", 120) or 120))
        cutoff = iso_ago(days * 86400)
        rows = []
        try:
            rows = conn.execute(
                "SELECT account, window, resets_at, MIN(ts) AS first_seen, MAX(ts) AS last_seen,"
                " COUNT(*) AS n FROM utilization WHERE ts < ? GROUP BY account, window, resets_at",
                (cutoff,)).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        folded = 0
        with _tx(conn):
            for row in rows:
                db.upsert_window_instance(conn, {
                    "account": row["account"], "window": row["window"],
                    "resets_at": row["resets_at"], "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"], "fit_n": row["n"]})
                folded += 1
            deleted = conn.execute("DELETE FROM utilization WHERE ts < ?", (cutoff,)).rowcount
        before = fsutil.file_size(paths.db_path() + "-wal")
        try:
            conn.execute("VACUUM")
        except sqlite3.DatabaseError as exc:
            err("vacuum: %s" % exc)
        db.wal_checkpoint(conn, "TRUNCATE")           # VACUUM itself grows the WAL; truncate after
        after = fsutil.file_size(paths.db_path() + "-wal")
        out("vacuum  samples older than %dd (%s)" % (days, cutoff[:10]))
        for line in table(["metric", "value"], [
                ["window_instances folded", folded],
                ["utilization rows deleted", deleted],
                ["wal before", "%.1f KB" % (before / 1024.0)],
                ["wal after", "%.1f KB" % (after / 1024.0)],
                ["db size", "%.1f MB" % (fsutil.file_size(paths.db_path()) / 1048576.0)]],
                ["<", ">"]):
            out(line)
        return 0
    finally:
        db.close(conn)


# =========================================================================== doctor

def _unquote(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def command_paths(command):
    """(interpreter, [script paths]) of a hook / statusline command string."""
    try:
        import shlex

        tokens = [_unquote(t) for t in shlex.split(str(command or ""), posix=False)]
    except ValueError:
        tokens = [_unquote(t) for t in str(command or "").split()]
    if not tokens:
        return None, []
    scripts = [t for t in tokens[1:] if t.lower().endswith((".py", ".sh", ".ps1"))]
    return tokens[0], scripts


def probe_events(path):
    """Parse a probe ``hooks.log`` fixture into ``[(event_name, payload), ...]``."""
    events = []
    name = None
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return events
    try:
        for line in fh:
            stripped = line.strip()
            m = _PROBE_RE.match(stripped)
            if m:
                name = m.group(1)
                continue
            if name and stripped.startswith("{"):
                try:
                    events.append((name, json.loads(stripped)))
                except ValueError:
                    pass
                name = None
    finally:
        fh.close()
    return events


class _Doctor(object):
    def __init__(self):
        self.lines = []
        self.fails = 0
        self.warns = 0

    def add(self, level, text):
        if level == "FAIL":
            self.fails += 1
        elif level == "WARN":
            self.warns += 1
        self.lines.append("%-4s %s" % (level, text))

    def ok(self, text):
        self.add("OK", text)

    def warn(self, text):
        self.add("WARN", text)

    def fail(self, text):
        self.add("FAIL", text)


def _doctor_update_rules(doc, user_settings_path, project_root):
    """T20: the allow rules ``tools/pa3_update.py`` needs (user) and ``tools/*`` (project)."""
    import re as _re
    py = sys.executable.replace("\\", "/")

    def _allow(path):
        data = fsutil.read_json(path, None)
        perms = data.get("permissions") if isinstance(data, dict) else None
        allow = perms.get("allow") if isinstance(perms, dict) else None
        return [a for a in allow if isinstance(a, str)] if isinstance(allow, list) else []

    if any(_re.match(r"^Bash\(.+ tools/pa3_update\.py\)$", a) for a in _allow(user_settings_path)):
        doc.ok("user: allow rule for tools/pa3_update.py")
    else:
        doc.warn("user: no allow rule for tools/pa3_update.py in %s; run the root installer once, "
                 "or run it yourself: ! %s tools/pa3_update.py" % (user_settings_path, py))
    if not project_root:
        return
    proj = os.path.join(project_root, ".claude", "settings.json")
    if not os.path.isfile(proj):
        return
    rules = _allow(proj)
    if any(_re.match(r"^Bash\(.+ tools/\*\)$", a) for a in rules):
        doc.ok("project: allow rule for tools/*")
    elif any(_re.match(r"^Bash\(.+ tools/\*:\*\)$", a) for a in rules):
        # 3.11 T15: Claude Code (2.1.284) reads a * before :* as a literal, so this form matches nothing
        doc.warn("project: allow rule `Bash(… tools/*:*)` in %s matches nothing in current Claude Code; "
                 "replace it with `Bash(%s tools/*)` (and `Bash(bash tools/*:*)` with `Bash(bash tools/*)`)"
                 % (proj, py))
    else:
        doc.warn("project: no allow rule for tools/* in %s; run %s tools/pa3_update.py yourself "
                 "once, then pa_install.py --project re-merges it" % (proj, py))


def _doctor_settings(doc, label, settings_path):
    data = fsutil.read_json(settings_path, None)
    if data is None:
        doc.warn("%s: no settings.json at %s" % (label, settings_path))
        return
    status = data.get("statusLine") or {}
    cmd = status.get("command") if isinstance(status, dict) else None
    if not cmd:
        doc.warn("%s: statusLine not configured" % label)
    else:
        interp, scripts = command_paths(cmd)
        missing = [s for s in scripts if not os.path.exists(s)]
        relative = [s for s in scripts if not os.path.isabs(s)]
        if missing:
            doc.fail("%s: statusLine script missing: %s" % (label, ", ".join(missing)))
        elif relative:
            doc.warn("%s: statusLine path is not absolute: %s" % (label, ", ".join(relative)))
        else:
            doc.ok("%s: statusLine -> %s" % (label, scripts[0] if scripts else interp))
    hooks = data.get("hooks") or {}
    for event, module in HOOK_EVENTS:
        entries = hooks.get(event) or []
        commands = []
        for group in entries:
            for hook in (group or {}).get("hooks", []) or []:
                if hook.get("type") == "command" and hook.get("command"):
                    commands.append(hook["command"])
        mine = [c for c in commands if "pa_hook.py" in c]
        if not mine:
            doc.fail("%s: hook %s has no pa_hook.py entry" % (label, event))
            continue
        bad = []
        for cmd in mine:
            _interp, scripts = command_paths(cmd)
            bad.extend([s for s in scripts if not os.path.exists(s)])
        if bad:
            doc.fail("%s: hook %s -> missing file %s" % (label, event, bad[0]))
        elif module not in " ".join(mine):
            doc.warn("%s: hook %s does not pass event %r" % (label, event, module))
        else:
            doc.ok("%s: hook %s" % (label, event))


def _hook_args(fn, payload, cfg):
    """``run(inp)`` and ``run(inp, cfg)`` are both accepted (design doc A.1)."""
    try:
        import inspect

        kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        required = [p for p in inspect.signature(fn).parameters.values()
                    if p.kind in kinds and p.default is inspect.Parameter.empty]
    except (TypeError, ValueError):
        return (payload,)
    return (payload, cfg) if len(required) >= 2 else (payload,)


def _doctor_hooks_latency(doc, fixture, cfg=None, skip=False):
    if skip:
        doc.warn("hook latency: skipped (--skip-hooks)")
        return
    if not os.path.exists(fixture):
        doc.warn("hook latency: fixture not found (%s)" % fixture)
        return
    try:
        import importlib

        importlib.import_module("pa.hooks")
    except Exception as exc:
        doc.warn("hook latency: pa.hooks not importable yet (%s)" % exc.__class__.__name__)
        return
    events = probe_events(fixture)
    try:
        limit = int(config.get(cfg or {}, "doctor.hook_warn_ms", 2000))
    except (TypeError, ValueError):
        limit = 2000
    by_event = {}
    for name, payload in events:
        by_event.setdefault(name, payload)
    for event, module in HOOK_EVENTS:
        payload = by_event.get(event)
        if payload is None:
            continue
        try:
            import importlib

            mod = importlib.import_module("pa.hooks.%s" % module)
            fn = getattr(mod, "run", None)
            if not callable(fn):
                doc.fail("hook %s: pa.hooks.%s has no run()" % (event, module))
                continue
            # a probe never mutates the project: hooks see pa_probe on a copy, the fixture stays as read
            probe = dict(payload, pa_probe=True) if isinstance(payload, dict) else payload
            args = _hook_args(fn, probe, cfg)
            start = time.perf_counter()
            fn(*args)
            ms = (time.perf_counter() - start) * 1000.0
            if ms > limit:
                doc.warn("hook %s: run() %.0f ms > %d ms" % (event, ms, limit))
            else:
                doc.ok("hook %s: run() %.1f ms" % (event, ms))
        except Exception as exc:
            doc.fail("hook %s: run() raised %s: %s" % (event, exc.__class__.__name__, exc))
    absent = [event for event, _m in HOOK_EVENTS if event not in by_event]
    if absent:
        doc.ok("hook latency: no fixture payload for %s" % ", ".join(absent))


_CARD_PLACEHOLDER_RE = re.compile(r"\{\{\w+\}\}")


def _doctor_package_clone(doc):
    """``doctor`` row: the ``pa3-src`` clone's HEAD and whether VERSION names it (T9).

    VERSION's ``source:`` naming an existing dir is the clone checked (a second config
    dir shares it); otherwise ``<config-dir>/pa3-src``.
    """
    clone = paths.package_clone_dir()
    ver_text = fsutil.read_text(os.path.join(paths.install_dir(), "VERSION"), "")
    for line in ver_text.splitlines():
        line = line.strip()
        if line.startswith("source:"):
            src = line.split(":", 1)[1].strip()
            if src and os.path.isdir(src):
                clone = os.path.normpath(src)
            break
    if not os.path.isdir(clone):
        doc.warn("package clone: not present (%s)" % clone)
        return
    head = None
    try:
        proc = subprocess.run(["git", "-C", clone, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=2)
        if proc.returncode == 0:
            head = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    named = False
    clone_norm = os.path.normpath(clone).replace("\\", "/").lower()
    for line in ver_text.splitlines():
        line = line.strip()
        if line.startswith("source:"):
            src = line.split(":", 1)[1].strip().replace("\\", "/").lower()
            named = clone_norm in src or src in clone_norm
            break
    if not head:
        doc.warn("package clone: %s (HEAD unavailable)" % clone)
    elif not named:
        doc.warn("package clone: %s @ %s (VERSION source: does not name it)" % (clone, head))
    else:
        from . import parse_version
        doc.ok("package clone: %s (version %s, git %s)"
               % (clone, parse_version(ver_text) or __version__, head))


def _doctor_update_check(doc, cfg):
    """``doctor`` row: age/state of ``<pa3_dir>/update.json`` (T2 writes it; T9)."""
    path = os.path.join(paths.install_dir(), "update.json")
    data = fsutil.read_json(path, None)
    if not isinstance(data, dict):
        doc.ok("update check: never")
        return
    checked_at = data.get("checked_at")
    try:
        behind = int(data.get("behind") or 0)
    except (TypeError, ValueError):
        behind = 0
    status = str(data.get("status") or "").lower()

    age_s = None
    age_str = "?"
    if checked_at:
        try:
            if isinstance(checked_at, (int, float)):     # an epoch, like usage_api.json's ts
                age_s = time.time() - float(checked_at)
            else:
                ts = dt.datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
                age_s = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()
            if age_s < 60:
                age_str = "%ds" % int(age_s)
            elif age_s < 3600:
                age_str = "%dm" % int(age_s / 60)
            elif age_s < 86400:
                age_str = "%.1fh" % (age_s / 3600)
            else:
                age_str = "%.1fd" % (age_s / 86400)
        except Exception:
            age_s = None

    if status == "offline":
        state = "offline"
    elif behind > 0:
        state = "behind %d" % behind
    elif not checked_at or status == "never":
        state = "never"
    else:
        state = "current"

    hours = float(config.get(cfg, "install.update_check_hours", 24) or 24)
    stale = age_s is not None and age_s > hours * 3600 * 2
    text = (("update check: %s (%s ago)" % (state, age_str)) if age_s is not None
            else "update check: %s" % state)
    if behind > 0 or stale:
        doc.warn(text)
    else:
        doc.ok(text)


def _doctor_card(doc, cfg, project):
    """``doctor`` row (``--project`` only): HOW_WE_WORK.md vs ``card.max_chars`` (T9).

    Doctor only warns -- the archive is what refuses an over-cap or placeholder card.
    """
    if not project:
        return
    path = os.path.join(project, "HOW_WE_WORK.md")
    text = fsutil.read_text(path, None)
    if text is None:
        doc.warn("card: HOW_WE_WORK.md not found at %s" % path)
        return
    chars = len(text)
    cap = int(config.get(cfg, "card.max_chars", 7000) or 7000)
    placeholders = len(_CARD_PLACEHOLDER_RE.findall(text))
    line = "card: %d chars (cap %d), %d placeholders" % (chars, cap, placeholders)
    if chars > cap or placeholders:
        doc.warn(line)
    else:
        doc.ok(line)


def _doctor_mcp_servers(doc, project):
    """``doctor`` row: MCP servers configured user-wide and (with ``--project``) per-project (T9)."""
    note = "each adds its instruction block to every session; disable the unused ones"
    names = []
    cfg_dir = paths.config_dir()
    user_path = os.path.join(os.path.dirname(cfg_dir), ".claude.json")
    data = fsutil.read_json(user_path, None)
    if isinstance(data, dict):
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            names.extend(sorted(servers.keys()))
    if project:
        proj_data = fsutil.read_json(os.path.join(project, ".mcp.json"), None)
        if isinstance(proj_data, dict):
            pservers = proj_data.get("mcpServers")
            if isinstance(pservers, dict):
                names.extend(sorted(pservers.keys()))
    if not names:
        doc.ok("mcp servers: 0 configured")
    else:
        doc.warn("mcp servers: %d configured (%s) -- %s" % (len(names), ", ".join(names), note))


def _doctor_sizes(args):
    """``doctor --sizes``: governed-file size checks only (T10)."""
    project = args.project or os.getcwd()
    if not os.path.isdir(project):
        out("sizes")
        out("  FAIL project dir not found: %s" % project)
        return 1
    cfg = config.load()
    doc = _Doctor()

    # CLAUDE.md <= 1200 chars
    claude_md = os.path.join(project, "CLAUDE.md")
    if os.path.isfile(claude_md):
        sz = os.path.getsize(claude_md)
        if sz > 1200:
            doc.warn("CLAUDE.md: %d chars (limit 1200)" % sz)
        else:
            doc.ok("CLAUDE.md: %d chars" % sz)
    else:
        doc.ok("CLAUDE.md: not found")

    # SKILL.md files <= 14000
    skills_dir = os.path.join(project, ".claude", "skills")
    if os.path.isdir(skills_dir):
        try:
            for name in sorted(os.listdir(skills_dir)):
                skill_md = os.path.join(skills_dir, name, "SKILL.md")
                if os.path.isfile(skill_md):
                    sz = os.path.getsize(skill_md)
                    label = ".claude/skills/%s/SKILL.md" % name
                    if sz > 14000:
                        doc.warn("%s: %d chars (limit 14000)" % (label, sz))
                    else:
                        doc.ok("%s: %d chars" % (label, sz))
        except OSError:
            pass

    # HOW_WE_WORK.md <= 14000
    hww = os.path.join(project, "HOW_WE_WORK.md")
    if os.path.isfile(hww):
        sz = os.path.getsize(hww)
        if sz > 14000:
            doc.warn("HOW_WE_WORK.md: %d chars (limit 14000)" % sz)
        else:
            doc.ok("HOW_WE_WORK.md: %d chars" % sz)
    else:
        doc.ok("HOW_WE_WORK.md: not found")

    # memory index <= 200 lines and <= 25 KB (over harness cap -> FAIL)
    mem_path = os.path.join(project, ".claude-state", "memory", "MEMORY.md")
    if not os.path.isfile(mem_path):
        slug = paths.project_slug(project)
        mem_path = os.path.join(paths.projects_dir(), slug, "memory", "MEMORY.md")
    if os.path.isfile(mem_path):
        sz = os.path.getsize(mem_path)
        try:
            with open(mem_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            lines = 0
        problems = []
        if lines > 200:
            problems.append("%d lines (limit 200)" % lines)
        if sz > 25600:                       # 25 KB
            problems.append("%d bytes (limit 25 KB)" % sz)
        if problems:
            doc.fail("memory index: %s" % ", ".join(problems))
        else:
            doc.ok("memory index: %d lines, %d bytes" % (lines, sz))
    else:
        doc.ok("memory index: not found")

    # cookbook/INDEX.md and rules/INDEX.md <= 400 lines
    for idx_name in ("cookbook/INDEX.md", "rules/INDEX.md", "docs/ops/INDEX.md"):
        idx_path = os.path.join(project, idx_name)
        if os.path.isfile(idx_path):
            try:
                with open(idx_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = sum(1 for _ in fh)
            except OSError:
                lines = 0
            if lines > 400:
                doc.warn("%s: %d lines (limit 400)" % (idx_name, lines))
            else:
                doc.ok("%s: %d lines" % (idx_name, lines))
        else:
            doc.ok("%s: not found" % idx_name)

    # expert seed median <= 40k when the ledger has rows
    max_tokens = int(config.get(cfg, "seed.max_tokens", 40000) or 40000)
    try:
        conn = open_db()
        try:
            pkey = os.path.normcase(os.path.normpath(project))
            rows = conn.execute(
                "SELECT ar.phase, ar.seed_ctx "
                "FROM agent_runs ar "
                "JOIN sessions s ON s.session_id = ar.session_id "
                "WHERE ar.kind = 'expert' AND ar.seed_ctx IS NOT NULL AND ar.seed_ctx > 0 "
                "AND (LOWER(REPLACE(COALESCE(s.project, s.cwd, ''), '\\', '/')) = "
                "     LOWER(REPLACE(?, '\\', '/')))",
                (project.replace("\\", "/"),)).fetchall()
        finally:
            db.close(conn)
        if rows:
            by_phase = {}
            for r in rows:
                ph = str(r["phase"] or "")
                if ph:
                    by_phase.setdefault(ph, []).append(int(r["seed_ctx"]))
            if by_phase:
                cur = sorted(by_phase.keys())[-1]
                vals = sorted(by_phase[cur])
                n = len(vals)
                mid = n // 2
                med = (vals[mid - 1] + vals[mid]) // 2 if n % 2 == 0 else vals[mid]
                if med > max_tokens:
                    doc.warn("expert seed median: %dk (limit %dk, phase %s, n=%d)"
                             % (med // 1000, max_tokens // 1000, cur, n))
                else:
                    doc.ok("expert seed median: %dk (phase %s, n=%d)"
                           % (med // 1000, cur, n))
            else:
                doc.ok("expert seed: no phased runs")
        else:
            doc.ok("expert seed: no runs for this project")
    except Exception:
        doc.ok("expert seed: ledger unavailable")

    out("sizes")
    for line in doc.lines:
        out("  " + line)
    out("")
    out("%d OK, %d WARN, %d FAIL" % (len(doc.lines) - doc.warns - doc.fails, doc.warns, doc.fails))
    return 1 if doc.fails else 0


SAVINGS_DISPLAY_MIN_USD = 1.0
SAVINGS_DISPLAY_HOURS = 24


def _savings_display_fails(conn, doc=None):
    """``[(sid, cause)]`` for sessions with ledger cost >= $1 in the last 24 h whose
    ``summary.json`` entry has no ``windows`` block, or has helper runs and a null
    ``saved_measured`` (T11.1: the statusline shows ``-`` for them)."""
    if doc is None:
        doc = fsutil.read_json(paths.summary_path(), None) or {}
    sess = (doc.get("sessions") if isinstance(doc, dict) else None) or {}
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                           time.gmtime(time.time() - SAVINGS_DISPLAY_HOURS * 3600.0))
    try:
        rows = conn.execute(
            "SELECT session_id, COALESCE(cost_usd, 0) AS cost FROM sessions"
            " WHERE COALESCE(ended, started, '') >= ? AND COALESCE(cost_usd, 0) >= ?"
            " ORDER BY session_id", (cutoff, SAVINGS_DISPLAY_MIN_USD)).fetchall()
    except sqlite3.DatabaseError:
        rows = []
    fails = []
    for row in rows:
        sid = row["session_id"]
        entry = sess.get(sid)
        if not isinstance(entry, dict) or "windows" not in entry:
            fails.append((sid, "no windows block"))
            continue
        if entry.get("saved_measured") is None:
            helpers = int(scalar(conn, "SELECT COUNT(*) FROM agent_runs"
                                       " WHERE session_id=? AND run_id<>?", (sid, sid), 0))
            if helpers:
                fails.append((sid, "saved_measured null with %d helper runs" % helpers))
    return fails, len(rows)


def _savings_display_row(conn):
    """``(ok, row)``: the doctor's ``savings display`` row text (level first)."""
    fails, n = _savings_display_fails(conn)
    if fails:
        return False, "FAIL savings display: %s (pa_ledger.py doctor --repair savings)" % "; ".join(
            "%s %s" % (sid, cause) for sid, cause in fails)
    return True, "OK   savings display: %d sessions >= $%.0f in %d h all shown" % (
        n, SAVINGS_DISPLAY_MIN_USD, SAVINGS_DISPLAY_HOURS)


def repair_savings(sid=None, project=None):
    """``doctor --repair savings``: recalc then full ``summary.rebuild`` for the FAIL
    sessions (plus ``sid``), re-check; on OK drop ``savings_missing`` from
    ``<project>/.run/health.json``.  Idempotent; prints nothing.  ``(ok, row)``."""
    import contextlib
    import io

    conn = open_db()
    try:
        fails, _n = _savings_display_fails(conn)
    finally:
        db.close(conn)
    targets = sorted(set([f[0] for f in fails] + ([sid] if sid else [])))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        for target in targets:
            try:
                _cmd_recalc_run(build_parser().parse_args(["recalc", "--session", target]))
            except Exception as exc:        # a recalc failure leaves the row FAIL
                err("repair savings: %s: %s" % (target, exc))
        if not targets:                     # nothing flagged: still refresh summary.json once
            conn = open_db()
            try:
                rebuild_summary(conn, config.load())
            finally:
                db.close(conn)
    conn = open_db()
    try:
        ok, row = _savings_display_row(conn)
    finally:
        db.close(conn)
    if ok:
        hpath = os.path.join(project or os.getcwd(), ".run", "health.json")
        health = fsutil.read_json(hpath, None)
        if isinstance(health, dict) and "savings_missing" in health:
            health.pop("savings_missing", None)
            fsutil.atomic_write_json(hpath, health, indent=1)
    return ok, row


def cmd_doctor(args):
    if getattr(args, "sizes", False):
        return _doctor_sizes(args)
    if getattr(args, "repair", None) == "savings":
        ok, row = repair_savings(args.session, args.project)
        out(row)
        return 0 if ok else 1
    if getattr(args, "repair", None) == "phase":
        from . import phases
        conn = open_db()
        try:
            n = phases.heal_run_phases(conn, args.session)
            conn.commit()
        finally:
            conn.close()
        out("repair phase: %d rows restamped" % n)
        return 0
    cfg = config.load()
    doc = _Doctor()

    ver = sys.version_info
    if ver >= (3, 12):
        doc.ok("interpreter: %d.%d.%d (%s)" % (ver[0], ver[1], ver[2], sys.executable))
    elif ver >= (3, 9):
        doc.warn("interpreter: %d.%d.%d -- PA3 targets 3.12+" % (ver[0], ver[1], ver[2]))
    else:
        doc.fail("interpreter: %d.%d.%d is too old" % (ver[0], ver[1], ver[2]))

    _doctor_settings(doc, "user", os.path.join(paths.claude_dir(), "settings.json"))
    for cdir in (args.config_dir or []):
        _doctor_settings(doc, os.path.basename(os.path.normpath(cdir)) or cdir,
                         os.path.join(cdir, "settings.json"))
    _doctor_update_rules(doc, os.path.join(paths.claude_dir(), "settings.json"), args.project)

    ledger = paths.ledger_dir()
    if os.path.isdir(ledger):
        doc.ok("ledger dir: %s" % ledger)
    else:
        doc.warn("ledger dir missing: %s" % ledger)
    conn = open_db()
    try:
        have = set()
        try:
            have = set(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        except sqlite3.DatabaseError as exc:
            doc.fail("sqlite: %s" % exc)
        missing = [t for t in sorted(db.TABLES) if t not in have]
        if missing:
            doc.fail("sqlite: missing tables %s" % ", ".join(missing))
        else:
            doc.ok("sqlite: %d tables, schema v%s, prices v%s" % (
                len(have), db.schema_version(conn), db.get_meta(conn, "prices_version")))
        turns = int(scalar(conn, "SELECT COUNT(*) FROM turns", (), 0))
        sessions = int(scalar(conn, "SELECT COUNT(*) FROM sessions", (), 0))
        doc.ok("ledger rows: %s turns in %s sessions" % (num(turns), num(sessions)))
    finally:
        db.close(conn)

    wal = fsutil.file_size(paths.db_path() + "-wal")
    limit = int(config.get(cfg, "ledger.wal_size_limit", 4194304) or 4194304)
    if wal > limit:
        doc.warn("WAL %.1f MB exceeds journal_size_limit %.1f MB (run vacuum)" % (
            wal / 1048576.0, limit / 1048576.0))
    else:
        doc.ok("WAL size %.1f KB (limit %.1f MB)" % (wal / 1024.0, limit / 1048576.0))

    free = fsutil.disk_free_mb(ledger)
    floor = float(config.get(cfg, "ledger.disk_min_free_mb", 50) or 50)
    if free < floor:
        doc.fail("disk free %.0f MB below ledger.disk_min_free_mb %.0f MB" % (free, floor))
    else:
        doc.ok("disk free %.0f MB" % free)

    root_entries = config.extra_root_entries(cfg)
    if not root_entries:
        doc.ok("extra roots: none configured")
    for entry in root_entries:
        root = entry["path"]
        tag = " (%s)" % entry["account"] if entry.get("account") else ""
        if os.path.isdir(root) or os.path.exists(root):
            doc.ok("extra root reachable: %s%s" % (root, tag))
        else:
            doc.warn("extra root unreachable: %s%s" % (root, tag))

    projects = paths.projects_dir()
    if os.path.isdir(projects):
        doc.ok("transcripts root: %s" % projects)
    else:
        doc.warn("transcripts root missing: %s" % projects)

    problems = config.validate(cfg)
    if problems:
        for item in problems:
            doc.warn("config: %s" % item)
    else:
        doc.ok("config: valid (%s)" % paths.config_path())

    # T12: audit the raw file (config.json holds only user-set keys and overrides)
    findings = config.audit(fsutil.read_json(paths.config_path(), {}) or {})
    for kind, dotted, val, detail in findings:
        fix = "edit config.json" if kind == "retired_model" else "pa_install.py --root drops it"
        doc.warn("config: %s %s = %s (%s); %s"
                 % (kind, dotted, json.dumps(val), detail, fix))
    if not findings:
        doc.ok("config: 0 past defaults, retired models or unknown keys")

    _doctor_package_clone(doc)
    _doctor_update_check(doc, cfg)
    _doctor_card(doc, cfg, args.project)
    _doctor_mcp_servers(doc, args.project)

    # the warmer (3.9.5 T5): daemon alive when any .run/warmer/*.pid names a live process
    try:
        import glob
        from . import warmer

        w_root = args.project or os.getcwd()
        w_alive = False
        for w_pid in glob.glob(warmer.path(w_root, "*", "pid")):
            try:
                with open(w_pid, "r", encoding="utf-8") as fh:
                    w_alive = warmer.pid_alive(int(fh.read().strip() or 0)) or w_alive
            except (OSError, ValueError):
                pass
        w_runs = (fsutil.read_json(warmer.state_path(w_root), None) or {}).get("runs") or {}
        w_n = sum(1 for r in w_runs.values()
                  if isinstance(r, dict) and not r.get("finished") and r.get("ended") is None)
    except Exception:
        w_alive, w_n = False, 0
    doc.ok("warmer: daemon %s, %d runs watched" % ("alive" if w_alive else "idle", w_n))

    # usage api status (T4 / T1.c4 writes usage_api.json)
    try:
        uapi_path = os.path.join(paths.ledger_dir(), "usage_api.json")
        uapi = fsutil.read_json(uapi_path, None)
        if not isinstance(uapi, dict):
            doc.ok("usage api: not polled yet")
        elif uapi.get("last_error"):
            doc.ok("usage api: %s" % uapi["last_error"])
        elif uapi.get("ts"):
            try:
                raw_ts = uapi["ts"]
                if isinstance(raw_ts, (int, float)):            # usage_api.py writes an epoch
                    age_s = time.time() - float(raw_ts)
                else:
                    uapi_ts = dt.datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    age_s = (dt.datetime.now(dt.timezone.utc) - uapi_ts).total_seconds()
                if age_s < 60:
                    age_str = "%ds" % int(age_s)
                elif age_s < 3600:
                    age_str = "%dm" % int(age_s / 60)
                else:
                    age_str = "%.1fh" % (age_s / 3600)
            except Exception:
                age_str = "?"
            windows = uapi.get("windows") or {}
            parts = []
            for wname, wpct in sorted(windows.items()):
                if isinstance(wpct, dict):                     # usage_api.py: {pct, resets_at, severity, is_active}
                    wpct = wpct.get("pct")
                if wpct is not None:
                    parts.append("%s=%.0f%%" % (wname, float(wpct)))
            if parts:
                doc.ok("usage api: %s ago, %s" % (age_str, ", ".join(parts)))
            else:
                doc.ok("usage api: %s ago" % age_str)
        else:
            doc.ok("usage api: not polled yet")
    except Exception:
        doc.ok("usage api: not polled yet")

    conn = open_db()
    try:
        sd_ok, sd_row = _savings_display_row(conn)
        # 3.15 T12: fallback-shaped account switches the repair step does not rewrite (read-only)
        try:
            from . import repairs
            fb = repairs.account_fallback_findings(conn, cfg)
        except Exception as exc:
            fb = None
            doc.warn("account fallback: check failed (%s)" % type(exc).__name__)
    finally:
        db.close(conn)
    doc.add("OK" if sd_ok else "FAIL", sd_row[5:])
    for item in fb or []:
        doc.warn("account fallback: session %s switched %s -> %s at %s with no switch back; "
                 "if it stayed on %s, run: %s" % (item["session_id"], item["from"], item["to"],
                                                  item["ts"], item["account"], item["command"]))
    if fb == []:
        doc.ok("account fallback: no unrepaired fallback switches")

    _doctor_hooks_latency(doc, args.fixture or default_fixture(), cfg, args.skip_hooks)

    out("pa-ledger doctor")
    for line in doc.lines:
        out("  " + line)
    out("")
    out("%d OK, %d WARN, %d FAIL" % (len(doc.lines) - doc.warns - doc.fails, doc.warns, doc.fails))
    return 1 if doc.fails else 0


def default_fixture():
    """The doctor's latency probe fixture: the installed copy first (``pa_install.py
    --root`` ships ``tests/fixtures/hooks/probe1.log`` next to the package), else the
    package's own copy next to ``pa/`` (a dev checkout that hasn't been promoted yet)."""
    installed = os.path.join(paths.install_dir(), "tests", "fixtures", "hooks", "probe1.log")
    if os.path.exists(installed):
        return installed
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "tests", "fixtures", "hooks", "probe1.log")


# =========================================================================== account-switch

def cmd_account_switch(args):
    """``pa_ledger.py account-switch --session SID --to EMAIL [--since] [--from] [--dry-run]``

    Rebooks a session's turns, utilization rows, stamp and running entry to a
    different account -- the repair for a mid-session account switch that the
    hooks missed.
    """
    from . import accounts
    from . import running

    sid = args.session
    to_acct = args.to
    dry = bool(args.dry_run)

    conn = open_db()
    try:
        srow = conn.execute("SELECT account FROM sessions WHERE session_id=?",
                            (sid,)).fetchone()
        if not srow:
            err("account-switch: unknown session %s" % sid)
            return 1

        from_acct = args.from_account or srow["account"]
        if to_acct == from_acct:
            err("account-switch: --to equals --from (%s)" % to_acct)
            return 1

        if args.since:
            since = parse_date(args.since)
        else:
            try:
                st = os.stat(accounts.credentials_path())
                since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
            except OSError:
                err("account-switch: cannot stat credentials file and --since not given")
                return 1

        cfg = config.load()

        # --- turns ---
        if dry:
            n_turns = scalar(conn,
                "SELECT COUNT(*) FROM turns"
                " WHERE session_id=? AND ts>=? AND account=?",
                (sid, since, from_acct), 0)
        else:
            cur = conn.execute(
                "UPDATE turns SET account=?"
                " WHERE session_id=? AND ts>=? AND account=?",
                (to_acct, sid, since, from_acct))
            n_turns = cur.rowcount
        out("turns: %d -> %s since %s" % (n_turns, to_acct, since))

        # --- utilization (machine-wide, not per session) ---
        if dry:
            n_util = scalar(conn,
                "SELECT COUNT(*) FROM utilization"
                " WHERE ts>=? AND account=?",
                (since, from_acct), 0)
        else:
            cur = conn.execute(
                "UPDATE utilization SET account=?"
                " WHERE ts>=? AND account=?",
                (to_acct, since, from_acct))
            n_util = cur.rowcount
        out("utilization: %d (machine-wide)" % n_util)

        # --- session row ---
        if dry:
            n_sess = 1 if srow["account"] == from_acct else 0
        else:
            cur = conn.execute(
                "UPDATE sessions SET account=?"
                " WHERE session_id=? AND account=?",
                (to_acct, sid, from_acct))
            n_sess = cur.rowcount
        out("session row: %d" % n_sess)

        # --- stamp ---
        if not dry:
            accounts.stamp_session(sid, to_acct, source="account-switch",
                                   extra={"cred_key": accounts.credentials_key(),
                                          "previous": from_acct,
                                          "switched_at": since})
        out("stamp: %s" % ("updated" if not dry else "dry-run"))

        # --- running.json ---
        if not dry:
            try:
                running.patch_session(sid, {"account": to_acct})
            except Exception:
                pass
        out("running: %s" % ("updated" if not dry else "dry-run"))

        # --- event (idempotent) ---
        existing = conn.execute(
            "SELECT 1 FROM events WHERE session_id=? AND kind='account_change'"
            " AND ts=? AND detail_json LIKE ?",
            (sid, since, '%"source": "account-switch"%')).fetchone()
        if existing:
            out("event: present")
        elif not dry:
            db.insert_event(conn, "account_change",
                            {"from": from_acct, "to": to_acct,
                             "source": "account-switch", "since": since,
                             "account_source": "manual"},
                            session_id=sid, account=to_acct, ts=since)
            out("event: added")
        else:
            out("event: dry-run")

        # --- summary ---
        if not dry:
            rebuild_summary(conn, cfg)
        out("summary: %s" % ("rebuilt" if not dry else "dry-run"))

        # --- account split ---
        rows = conn.execute(
            "SELECT account, COUNT(*) AS n, MIN(ts) AS t0, MAX(ts) AS t1"
            " FROM turns WHERE session_id=? AND kind='api'"
            " GROUP BY account ORDER BY t0",
            (sid,)).fetchall()
        out("")
        for r in rows:
            out("  %s  %d turns  %s .. %s" % (r["account"], r["n"],
                                               r["t0"] or "?", r["t1"] or "?"))
    finally:
        db.close(conn)
    return 0


# =========================================================================== bench

# docs/pa3-build/design/bench.md: results JSON format 1|2 <-> one ``bench`` row per (run, arm key, task, attempt)
_BENCH_ARM_KEY = ("role", "agent", "model", "effort", "body_hash")
_BENCH_TOP = ("run_id", "suite", "harness_version", "price_version", "fixture_hash")
_BENCH_TASK_COLS = ("tier", "cost_usd", "secs", "turns", "input", "output", "cache_read",
                    "cache_write", "rubric")
_BENCH_TASK_CORE = ("tier", "cost_usd", "secs", "turns")   # always exported, null included
_BENCH_ARM_COLS = ("w5h_before", "w5h_after")


def _bench_epoch(date):
    """``date`` (ISO date or datetime; naive = UTC) -> epoch seconds, or None."""
    if not date:
        return None
    text = str(date).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


def _bench_date(epoch, full=True):
    """epoch -> ``YYYY-MM-DD`` (midnight UTC or ``full=False``) or an ISO datetime ending in Z."""
    if epoch is None:
        return None if full else "?"
    when = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    if not full or (when.hour, when.minute, when.second, when.microsecond) == (0, 0, 0, 0):
        return when.strftime("%Y-%m-%d")
    return when.replace(tzinfo=None).isoformat() + "Z"


def bench_rows(doc, source="local"):
    """Results JSON (format 1 or 2) -> ``bench`` rows; a record without ``attempt`` is attempt 1."""
    if doc.get("format") not in (1, 2):
        raise ValueError("unsupported results format %r" % (doc.get("format"),))
    started = _bench_epoch(doc.get("date"))
    rows = []
    for arm in doc.get("arms") or []:
        key = "|".join("" if arm.get(k) is None else str(arm.get(k)) for k in _BENCH_ARM_KEY)
        for task in arm.get("tasks") or []:
            row = {k: doc.get(k) for k in _BENCH_TOP}
            row.update({k: arm.get(k) for k in _BENCH_ARM_KEY})
            row.update({k: arm.get(k) for k in _BENCH_ARM_COLS})
            row.update({k: task.get(k) for k in _BENCH_TASK_COLS})
            rub = row.get("rubric")   # 3.9.7 T4: the results carry {score, max, model}; the column holds score/max
            if isinstance(rub, dict):
                row["rubric"] = rub["score"] / rub["max"] if rub.get("score") is not None and rub.get("max") else None
            cons = task.get("consequence")
            row["consequence_cost"] = cons.get("cost_usd") if isinstance(cons, dict) else None
            passed = task.get("passed")
            row.update(arm=key, task_id=str(task.get("id")), attempt=int(task.get("attempt") or 1),
                       failure_kind=task.get("failure_kind"), source=source, started=started,
                       stopped=1 if arm.get("stopped") else 0,
                       passed=None if passed is None else (1 if passed else 0))
            detail = {"version": arm.get("version"), "claude_code": doc.get("claude_code"),
                      "preset": doc.get("preset"), "session_id":task.get("session_id"), "turns_source": task.get("turns_source")}
            detail = {k: v for k, v in detail.items() if v is not None}
            row["detail"] = json.dumps(detail, sort_keys=True, separators=(",", ":")) if detail else None
            rows.append(row)
    return rows


def bench_import(conn, doc, source="local"):
    """INSERT OR REPLACE every row of one results JSON in one transaction; returns the row count."""
    rows = bench_rows(doc, source)
    with _tx(conn):
        for row in rows:
            db.upsert(conn, "bench", row, "replace")
    return len(rows)


def bench_export(conn, run_id):
    """Rebuild the format-2 results JSON of ``run_id`` from its rows (None when absent)."""
    rows = conn.execute("SELECT * FROM bench WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
    if not rows:
        return None
    first = rows[0]
    d0 = json.loads(first["detail"] or "{}")
    doc = {"format": 2, "run_id": run_id, "date": _bench_date(first["started"]),
           "repeat": max(r["attempt"] or 1 for r in rows)}
    doc.update({k: first[k] for k in _BENCH_TOP if k != "run_id"})
    if d0.get("claude_code") is not None:
        doc["claude_code"] = d0["claude_code"]
    if d0.get("preset") is not None:
        doc["preset"] = d0["preset"]
    arms, by_key = [], {}
    for r in rows:
        det = json.loads(r["detail"] or "{}")
        arm = by_key.get(r["arm"])
        if arm is None:
            arm = {k: r[k] for k in _BENCH_ARM_KEY}
            if det.get("version") is not None:
                arm["version"] = det["version"]
            arm["stopped"] = bool(r["stopped"])
            arm.update({k: r[k] for k in _BENCH_ARM_COLS if r[k] is not None})
            arm["tasks"] = []
            by_key[r["arm"]] = arm
            arms.append(arm)
        task = {"id": r["task_id"], "attempt": r["attempt"] or 1,
                "passed": None if r["passed"] is None else bool(r["passed"])}
        task.update({k: r[k] for k in _BENCH_TASK_COLS if r[k] is not None or k in _BENCH_TASK_CORE})
        if r["failure_kind"] is not None:
            task["failure_kind"] = r["failure_kind"]
        if r["consequence_cost"] is not None:
            task["consequence"] = {"cost_usd": r["consequence_cost"]}
        task.update({k: det[k] for k in ("session_id", "turns_source") if det.get(k) is not None})
        arm["tasks"].append(task)
    doc["arms"] = arms
    return doc


def _bench_spend(r):
    return (r["cost_usd"] or 0.0) + (r["consequence_cost"] or 0.0)


def _bench_ids(rows):
    """task id -> its ran attempt rows (unrun attempts dropped; ids with none dropped)."""
    ids = {}
    for r in rows:
        if r["passed"] is not None:
            ids.setdefault(r["task_id"], []).append(r)
    return ids


def _bench_noise(rows):
    """(f, n): n ids with >= 2 ran attempts, f of them whose attempts disagree."""
    multi = [rs for rs in _bench_ids(rows).values() if len(rs) >= 2]
    return sum(1 for rs in multi if len({bool(r["passed"]) for r in rs}) > 1), len(multi)


def _bench_repeat(run):
    return max(r["attempt"] or 1 for r in run["rows"])


def _bench_mean(vals):
    return sum(vals) / len(vals) if vals else None


def _bench_delta(x, fmt):
    """``+0.12`` / ``\u22120.05`` (U+2212); a rounded zero is ``+``."""
    text = fmt % abs(x)
    return ("\u2212" if x < 0 and float(text) != 0 else "+") + text


def _bench_run_line(run, prev, older=(), body=None):
    """One report line for ``run`` ({stopped, rows}) against ``prev`` (or None); ``older`` = the
    key's earlier runs, newest first (the noise fallback); ``body`` = prev's body hash when prev is
    another key's run (cross-body pairing: ``vs previous body <hash8>``)."""
    rows, first = run["rows"], run["rows"][0]
    head = "%s %s %s/%s" % (_bench_date(first["started"], full=False), first["agent"],
                            first["model"], first["effort"])
    cost = sum(_bench_spend(r) for r in rows)
    ran = [r for r in rows if r["passed"] is not None]
    if run["stopped"]:
        return "%s clean-stop %d/%d $%.2f" % (head, len(ran), len(rows), cost)
    ids = _bench_ids(rows)
    now = {t: all(r["passed"] for r in rs) for t, rs in ids.items()}   # passes when every attempt passes
    tiers = []
    for tier in sorted({rs[0]["tier"] for rs in ids.values()}, key=lambda t: (t is None, t or 0)):
        tr = [t for t, rs in ids.items() if rs[0]["tier"] == tier]
        tiers.append("t%s %d/%d" % ("?" if tier is None else tier, sum(1 for t in tr if now[t]), len(tr)))
    if _bench_repeat(run) >= 2:
        noise = "noise %d/%d" % _bench_noise(rows)
    else:
        rep_run = next((o for o in older if not o["stopped"] and _bench_repeat(o) >= 2
                        and (o["started"] or 0.0) < (run["started"] or 0.0)), None)
        noise = "noise unknown" if rep_run is None else "noise \u00b1%d/%d (%s)" % (
            _bench_noise(rep_run["rows"]) + (_bench_date(rep_run["rows"][0]["started"], full=False),))
    rub = [r["rubric"] for r in rows if r["rubric"] is not None]
    turns = [r["turns"] for r in rows if r["turns"] is not None]
    line = "%s pass %d/%d%s \u00b7 %s \u00b7 rubric %s \u00b7 $%.2f" % (
        head, sum(1 for t in now if now[t]), len(now),
        (" (%s)" % " \u00b7 ".join(tiers)) if tiers else "", noise,
        "%.2f" % _bench_mean(rub) if rub else "\u2014", cost)
    tbar = "%.1f" % _bench_mean(turns) if turns else "\u2014"
    if prev is None:
        return line + " (\u0394 \u2014) \u00b7 turns %s (\u0394 \u2014) vs previous: \u2014" % tbar
    pids = _bench_ids(prev["rows"])
    before = {t: all(r["passed"] for r in rs) for t, rs in pids.items()}
    shared = sorted(set(before) & set(now))
    dcost = sum(_bench_mean([_bench_spend(r) for r in ids[t]]) - _bench_mean([_bench_spend(r) for r in pids[t]])
                for t in shared)
    dts = []
    for t in shared:
        a = _bench_mean([r["turns"] for r in ids[t] if r["turns"] is not None])
        b = _bench_mean([r["turns"] for r in pids[t] if r["turns"] is not None])
        if a is not None and b is not None:
            dts.append(a - b)
    dturn = "\u0394" + _bench_delta(_bench_mean(dts), "%.1f") if dts else "\u0394 \u2014"
    gained = sum(1 for t in shared if now[t] and not before[t])
    lost = sum(1 for t in shared if before[t] and not now[t])
    return line + " (\u0394%s) \u00b7 turns %s (%s) vs previous%s: +%d \u2212%d flips (paired %d)" % (
        _bench_delta(dcost, "%.2f"), tbar, dturn, " body " + body[:8] if body else "", gained, lost,
        len(shared))


def bench_report(conn, agent=None, role=None, last=5):
    """Report lines: one header per arm key, then its newest ``last`` runs."""
    where, params = [], []
    if agent:
        where.append("agent=?")
        params.append(agent)
    if role:
        where.append("role=?")
        params.append(role)
    sql = "SELECT * FROM bench" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY rowid"
    series = {}
    for r in conn.execute(sql, params).fetchall():
        runs = series.setdefault(r["arm"], {})
        run = runs.setdefault(r["run_id"], {"run_id": r["run_id"], "started": r["started"],
                                            "stopped": 0, "rows": []})
        run["stopped"] = max(run["stopped"], r["stopped"] or 0)
        run["rows"].append(r)
    if not series:
        return ["no bench runs"]
    ordered = []
    for key, runs in series.items():
        runs = sorted(runs.values(), key=lambda x: (x["started"] or 0.0, x["run_id"]), reverse=True)
        ordered.append((runs[0]["started"] or 0.0, key, runs))
    ordered.sort(key=lambda x: (-x[0], x[1]))
    lines = []
    for _, key, runs in ordered:
        role_, agent_, model, effort, body = key.split("|", 4)
        lines.append("%s %s %s/%s %s" % (role_, agent_, model, effort, body[:8]))
        for i, run in enumerate(runs[:max(last, 0)]):
            prev = next((r for r in runs[i + 1:] if not r["stopped"]), None)
            pbody = None
            if prev is None:  # cross-body: newest earlier non-stopped run of another body, same role and agent
                t0 = run["started"] or 0.0
                cands = sorted(((r["started"] or 0.0, r["run_id"], k.split("|", 4)[4], r)
                                for k, rs in series.items() if k != key
                                and k.split("|", 4)[:2] == [role_, agent_] and k.split("|", 4)[4] != body
                                for r in rs.values() if not r["stopped"] and (r["started"] or 0.0) < t0),
                               key=lambda c: (c[0], c[1]), reverse=True)
                if cands:
                    pbody, prev = cands[0][2], cands[0][3]
            lines.append("  " + _bench_run_line(run, prev, runs[i + 1:], pbody))
    return lines


def cmd_bench(args):
    """``pa_ledger.py bench report|import|export`` (docs/pa3-build/design/bench.md)."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    if enc != "utf8" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # the report prints U+00B7 and U+2212
        except (ValueError, OSError):
            pass
    conn = open_db()
    try:
        if args.bench_cmd == "import":
            try:
                with open(args.json_path, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                n = bench_import(conn, doc, args.source)
            except (OSError, ValueError) as exc:
                err("bench import: %s" % exc)
                return 1
            out("bench import: %d rows from %s" % (n, args.json_path))
            return 0
        if args.bench_cmd == "export":
            doc = bench_export(conn, args.run)
            if doc is None:
                err("bench export: no run %s" % args.run)
                return 1
            text = json.dumps(doc, indent=2) + "\n"
            if args.out:
                with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            else:
                sys.stdout.write(text)
            return 0
        for line in bench_report(conn, args.arm, args.role, args.last):
            out(line)
        return 0
    finally:
        db.close(conn)


# =========================================================================== parser

def build_parser():
    p = argparse.ArgumentParser(prog="pa-ledger", description="Project Architect 3.0 usage ledger")
    p.add_argument("--version", action="version", version="pa-ledger 3.0 (schema %d)" % db.SCHEMA_VERSION)
    sub = p.add_subparsers(dest="command")

    rc = sub.add_parser("recalc", help="rebuild the ledger from transcripts or turns")
    rc.add_argument("--from", dest="source", choices=("transcripts", "turns"),
                    default="transcripts")
    rc.add_argument("--session")
    rc.add_argument("--project")
    rc.add_argument("--since")
    rc.add_argument("--root", action="append",
                    help="transcript root (repeatable); replaces the default ~/.claude/projects")
    rc.add_argument("--account")
    rc.add_argument("--pending", action="store_true", help="drain state/pending_locks.jsonl")
    rc.add_argument("--by", choices=("phase", "session"), default="session")
    rc.add_argument("--dry-run", action="store_true")
    rc.add_argument("--check", action="store_true",
                    help="snapshot report --window all --json, recalc, compare, "
                         "'idempotent OK' or the first 20 diff lines (exit 1)")
    rc.add_argument("-v", "--verbose", action="store_true")
    rc.set_defaults(func=cmd_recalc)

    rp = sub.add_parser("report", help="per-account cost / savings tables")
    rp.add_argument("--window", choices=("five_hour", "seven_day", "period", "all"), default="all")
    rp.add_argument("--account")
    rp.add_argument("--since")
    rp.add_argument("--until", help="ISO date/time or epoch; only sessions closed before it, and"
                                    " only their turns before it (a live session is left out)")
    rp.add_argument("--session")
    rp.add_argument("--phase")
    rp.add_argument("--by", choices=("session", "run", "model", "day", "project"), default="session")
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--seeds", action="store_true", help="first-request ctx per run")
    rp.add_argument("--thinking", action="store_true", help="thinking share per model")
    rp.add_argument("--limit", type=int, default=25)
    rp.add_argument("--modeled", action="store_true",
                    help="show the two replay bases (vanilla, pa2) and the measured block")
    rp.add_argument("--basis", choices=("vanilla", "pa2", "both"), default="both",
                    help="which replay basis to show with --modeled (default: both)")
    rp.add_argument("--all-kinds", action="store_true",
                    help="include every turn kind in cost sums (default: api only)")
    rp.set_defaults(func=cmd_report)

    rn = sub.add_parser("reconcile", help="price-table check + coverage for one session")
    rn.add_argument("sid")
    rn.add_argument("--root", action="append")
    rn.add_argument("--json", action="store_true")
    rn.set_defaults(func=cmd_reconcile)

    pr = sub.add_parser("prices", help="show / refresh the price table")
    pr.add_argument("--show", action="store_true")
    pr.add_argument("--refresh", action="store_true")
    pr.add_argument("--paste", action="store_true", help="read a markdown table from stdin")
    pr.add_argument("--apply", action="store_true")
    pr.add_argument("--effective-from", dest="effective_from")
    pr.add_argument("--url", default=PRICING_URL)
    pr.set_defaults(func=cmd_prices)

    ac = sub.add_parser("accounts", help="list / label accounts, set the default, override the tier, "
                                         "set the renewal day (accounts renewal EMAIL DAY|clear)")
    ac.add_argument("action", nargs="?", choices=("list", "label", "default", "tier", "renewal"), default="list")
    ac.add_argument("email", nargs="?")
    ac.add_argument("label", nargs="?")
    ac.set_defaults(func=cmd_accounts)

    ft = sub.add_parser("fit", help="show / refit window-instance fits")
    ft.add_argument("--account")
    ft.add_argument("--window")
    ft.add_argument("--refit", action="store_true", help="refit all instances before display")
    ft.add_argument("--check", action="store_true",
                    help="exit 0 when the latest five_hour or seven_day has quality ok or band")
    ft.add_argument("--all", action="store_true",
                    help="show every instance, including empty or unowned ones")
    ft.add_argument("--json", action="store_true")
    ft.set_defaults(func=cmd_fit)

    sm = sub.add_parser("summary", help="rebuild summary.json")
    sm.add_argument("--print", dest="print_", action="store_true")
    sm.add_argument("--line3", action="store_true",
                     help="print the plain-text statusline line 3 for the newest session")
    sm.add_argument("--session", help="the session id for --line3 (default: the newest)")
    sm.set_defaults(func=cmd_summary)

    sq = sub.add_parser("sql", help="read-only query against ledger.sqlite")
    sq.add_argument("query")
    sq.add_argument("--json", action="store_true")
    sq.add_argument("--limit", type=int, default=200)
    sq.set_defaults(func=cmd_sql)

    tl = sub.add_parser("tail", help="last turns rows")
    tl.add_argument("-n", type=int, default=20)
    tl.set_defaults(func=cmd_tail)

    wt = sub.add_parser("watch", help="follow running.json / summary.json / .run/status.json")
    wt.add_argument("--interval", type=float, default=2.0)
    wt.add_argument("--cwd")
    wt.add_argument("--rounds", type=int, default=0, help="stop after N polls (0 = forever)")
    wt.set_defaults(func=cmd_watch)

    fl = sub.add_parser("follow", help="live read-only view of one subagent's transcript (focus tab)")
    fl.add_argument("run_id")
    fl.add_argument("--transcript", default="")
    fl.add_argument("--session", default="")
    fl.add_argument("--interval", type=float, default=0.5)
    fl.add_argument("--rounds", type=int, default=0, help="stop after N polls (0 = until the agent stops)")
    fl.add_argument("--max-minutes", dest="max_minutes", type=int, default=180)
    fl.set_defaults(func=cmd_follow)

    vc = sub.add_parser("vacuum", help="fold old utilization samples and compact the db")
    vc.add_argument("--samples-older-than", dest="samples_older_than", default="120d")
    vc.set_defaults(func=cmd_vacuum)

    au = sub.add_parser("audit", help="carry audit for a phase")
    au.add_argument("--phase", required=True, help="phase id (e.g. 3.5)")
    au.add_argument("--project", default=".", help="project root directory")
    au.add_argument("--prev", default=None, help="previous phase id for growth comparison")
    au_fmt = au.add_mutually_exclusive_group()
    au_fmt.add_argument("--md", action="store_true", help="markdown output (default)")
    au_fmt.add_argument("--json", action="store_true", help="JSON output")
    au.set_defaults(func=cmd_audit)

    ex = sub.add_parser("export", help="export project sessions to gzipped JSONL")
    ex.add_argument("--project", required=True, help="project directory")
    ex.add_argument("--phase", help="restrict to sessions in this phase")
    ex.add_argument("--out", help="output path (default: <project>/.claude-state/ledger/<machine>-<tag>.jsonl.gz)")
    ex.set_defaults(func=cmd_export)

    im = sub.add_parser("import", help="import JSONL slices (local-wins)")
    im.add_argument("import_files", nargs="+", metavar="FILE", help="JSONL files to import")
    im.set_defaults(func=cmd_import)

    sw = sub.add_parser("account-switch", help="rebook a session's turns/usage to a different account")
    sw.add_argument("--session", required=True, help="session id")
    sw.add_argument("--to", required=True, help="target account email")
    sw.add_argument("--since", help="ISO UTC timestamp (default: credentials file mtime)")
    sw.add_argument("--from", dest="from_account", help="source account (default: session's current account)")
    sw.add_argument("--dry-run", action="store_true", help="print counts without changing anything")
    sw.set_defaults(func=cmd_account_switch)

    dc = sub.add_parser("doctor", help="settings, interpreter, ledger, hook latency")
    dc.add_argument("--config-dir", action="append",
                    help="extra CLAUDE_CONFIG_DIR to check (repeatable)")
    dc.add_argument("--fixture", help="probe hooks.log used for the latency probe")
    dc.add_argument("--skip-hooks", action="store_true")
    dc.add_argument("--sizes", action="store_true",
                    help="only the governed-file size checks (T10)")
    dc.add_argument("--project", help="project directory for --sizes, card checks and"
                    " --repair (its .run/health.json)")
    dc.add_argument("--repair", choices=["savings", "phase"],
                    help="savings: recalc + rebuild summary.json for the FAIL sessions,"
                         " re-check, clear the health flag (T11.1); phase: restamp each"
                         " session's agent_runs/savings/tool_calls to its sessions.phase (T31)")
    dc.add_argument("--session", help="with --repair: also repair this session id")
    dc.set_defaults(func=cmd_doctor)

    bn = sub.add_parser("bench", help="bench results: report / import / export")
    bsub = bn.add_subparsers(dest="bench_cmd", required=True)
    bnr = bsub.add_parser("report", help="per arm key, newest runs first, flips vs the previous run")
    bnr.add_argument("--arm", help="agent name")
    bnr.add_argument("--role")
    bnr.add_argument("--last", type=int, default=5, help="runs per arm key (default 5)")
    bni = bsub.add_parser("import", help="import one results JSON (idempotent)")
    bni.add_argument("json_path", metavar="JSON")
    bni.add_argument("--source", choices=("local", "public"), default="local")
    bne = bsub.add_parser("export", help="rebuild one run's results JSON")
    bne.add_argument("--run", required=True)
    bne.add_argument("--out", help="output file (default: stdout)")
    bn.set_defaults(func=cmd_bench)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        err("interrupted")
        return 130
    except BrokenPipeError:
        return 0
