"""ledger.sqlite: pragmas, DDL, idempotent upserts, cross-root union readers.

Public:
    SCHEMA_VERSION, PRAGMAS, SCHEMA_SQL, TABLES
    connect(path=None, readonly=False, create=True)
    init_schema(conn, machine=None), migrate(conn), schema_version(conn)
    set_meta(conn, key, value), get_meta(conn, key, default=None)
    upsert(conn, table, row, mode="merge")
    upsert_account, upsert_session, upsert_agent_run, upsert_turn, upsert_turns,
    upsert_retrieval, upsert_savings, upsert_window_instance, upsert_price,
    upsert_fit_pool, upsert_summary, insert_utilization, insert_event
    seed_prices(conn, force=False)
    turn_row_from_request(req, run_id=None, session_id=None, account=None,
                          ttl_default="5m", kind="api")
    union_readers(extra_roots, include_local=False) -> [{conn, path, account, label}], union_copy(root)
    wal_checkpoint(conn, mode="TRUNCATE"), disk_free_mb(path), close(conn)

This is the only module that imports ``sqlite3``; hooks import it on cold paths
only (the statusline never does on an ordinary refresh).

Idempotency (design doc B.1): ``turns.msg_id`` INSERT OR REPLACE (keep-last
semantics), ``utilization UNIQUE(session_id, ts, window)`` INSERT OR IGNORE, and
merge-upserts everywhere else so a later hook (SubagentStop, SessionEnd) can
fill columns an earlier one (SubagentStart) left empty without erasing it.
``foreign_keys`` stays OFF: unioned rows from another machine may reference
sessions this database does not have.

Cross-root reads never open SQLite over ``\\\\wsl.localhost`` or ``/mnt/c``
(WAL over 9P/drvfs is unsafe): :func:`union_copy` copies ``ledger.sqlite``
(+ ``-wal``) into a temp directory when the source mtime/size changed,
checkpoints the copy, and opens it ``mode=ro``.
"""

import json
import os
import sqlite3
import time

from . import fsutil
from . import paths
from . import prices
from .fsutil import disk_free_mb  # re-exported: the WSL disk guard lives here

SCHEMA_VERSION = 7  # was 6

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=250",
    "PRAGMA journal_size_limit=4194304",
    "PRAGMA wal_autocheckpoint=1000",
    "PRAGMA temp_store=MEMORY",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts(
  email TEXT PRIMARY KEY, label TEXT, org_id TEXT, org_name TEXT, subscription TEXT,
  first_seen TEXT, last_seen TEXT, tier TEXT, tier_source TEXT);

CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, account TEXT, account_source TEXT,
  machine TEXT, project TEXT, cwd TEXT, transcript_path TEXT,
  kind TEXT,
  agent_name TEXT, model_launch TEXT, effort_launch TEXT, version TEXT,
  started TEXT, ended TEXT, end_reason TEXT,
  cost_usd REAL, cost_source TEXT, cost_updated TEXT,
  phase TEXT, harness_cost_usd REAL);

CREATE TABLE IF NOT EXISTS agent_runs(
  run_id TEXT PRIMARY KEY,
  session_id TEXT, parent_run_id TEXT, parent_source TEXT,
  kind TEXT,
  agent_type TEXT, task_id TEXT, phase TEXT, description TEXT, tool_use_id TEXT, spawn_depth INTEGER,
  model_pinned TEXT, model_seen TEXT, effort TEXT,
  started TEXT, ended TEXT, status TEXT,
  turns INTEGER, seed_ctx INTEGER, ctx_at_end INTEGER,
  input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER, cache_read INTEGER, output INTEGER, thinking INTEGER,
  cost_usd REAL, handoff_fired INTEGER DEFAULT 0, locked INTEGER DEFAULT 0, lock_source TEXT,
  transcript_path TEXT, transcript_bytes INTEGER);

CREATE TABLE IF NOT EXISTS turns(
  msg_id TEXT PRIMARY KEY,
  request_id TEXT, run_id TEXT, session_id TEXT, account TEXT, ts TEXT, model TEXT, effort TEXT,
  input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER, cache_write INTEGER, cache_read INTEGER,
  output INTEGER, thinking INTEGER,
  ctx INTEGER,
  new_tokens INTEGER,
  gap_s REAL, cold INTEGER, rewrite INTEGER, stop_reason TEXT,
  cost_usd REAL, ttl_assumed TEXT, kind TEXT DEFAULT 'api');

CREATE TABLE IF NOT EXISTS retrievals(
  run_id TEXT PRIMARY KEY, parent_run_id TEXT, agent_type TEXT, model TEXT,
  spawned_at TEXT, returned_at TEXT, seed_ctx INTEGER, ctx_at_end INTEGER, growth_tokens INTEGER,
  result_tokens_est INTEGER, own_output_est INTEGER, kept_out_tokens INTEGER, kept_out_mode TEXT,
  answer_tokens INTEGER, status TEXT, void INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS savings(
  run_id TEXT PRIMARY KEY, session_id TEXT, phase TEXT,
  measured_saved_usd REAL, measured_detail_json TEXT,
  modeled_saved_usd REAL, assumptions_json TEXT,
  locked INTEGER DEFAULT 0, updated TEXT);

CREATE TABLE IF NOT EXISTS utilization(
  id INTEGER PRIMARY KEY, ts TEXT, account TEXT, machine TEXT, session_id TEXT,
  window TEXT,
  pct REAL, resets_at INTEGER, session_cost REAL, model TEXT,
  source TEXT DEFAULT 'statusline', reason TEXT,
  UNIQUE(session_id, ts, window));

CREATE TABLE IF NOT EXISTS window_instances(
  account TEXT, window TEXT, resets_at INTEGER, started_at INTEGER, first_seen TEXT, last_seen TEXT,
  quantized INTEGER, pct_per_dollar REAL, fit_method TEXT, fit_n INTEGER, fit_crossings INTEGER,
  fit_span REAL, fit_se REAL, fit_quality TEXT, fit_detail TEXT, reset_source TEXT,
  PRIMARY KEY(account, window, resets_at));

CREATE TABLE IF NOT EXISTS fit_pool(
  tier TEXT, window TEXT, family TEXT, rate REAL, se REAL, n INTEGER, n_instances INTEGER,
  regime_since TEXT, built_at TEXT,
  PRIMARY KEY(tier, window, family));

CREATE TABLE IF NOT EXISTS prices(
  model_key TEXT, priority INTEGER, effective_from TEXT,
  input REAL, write_5m REAL, write_1h REAL, read REAL, output REAL, tokenizer TEXT,
  PRIMARY KEY(model_key, effective_from));

CREATE TABLE IF NOT EXISTS summary(
  account TEXT, window TEXT, period_start TEXT, cost_used REAL,
  cost_saved_measured REAL, cost_saved_modeled REAL, pct_per_dollar REAL, fit_n INTEGER, fit_quality TEXT,
  updated TEXT, PRIMARY KEY(account, window));

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, run_id TEXT, account TEXT,
  kind TEXT,
  detail_json TEXT);

CREATE INDEX IF NOT EXISTS ix_turns_run ON turns(run_id, ts);
CREATE INDEX IF NOT EXISTS ix_turns_session ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_turns_account_ts ON turns(account, ts);
CREATE INDEX IF NOT EXISTS ix_runs_session ON agent_runs(session_id);
CREATE INDEX IF NOT EXISTS ix_runs_parent ON agent_runs(parent_run_id);
CREATE INDEX IF NOT EXISTS ix_util_win ON utilization(account, window, resets_at, ts);
CREATE INDEX IF NOT EXISTS ix_retr_parent ON retrievals(parent_run_id);
CREATE INDEX IF NOT EXISTS ix_sessions_acct ON sessions(account, started);
CREATE TABLE IF NOT EXISTS tool_calls(
  id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, phase TEXT,
  ts TEXT, tool TEXT, kind TEXT, script TEXT, sub TEXT, path TEXT,
  file_tokens INTEGER, emission_tokens INTEGER, result_tokens INTEGER,
  kept_tokens INTEGER, model TEXT);

CREATE INDEX IF NOT EXISTS ix_calls_session ON tool_calls(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, ts);
"""

# v3 (T3): one row per (bench run, arm key, task); arm = "role|agent|model|effort|body_hash".
# v4 (3.9.7 T5): one row per attempt: PK gains ``attempt``; ``failure_kind``, ``consequence_cost``
# (the consequence's cost_usd; ``cost_usd`` stays the task's own); ``rubric`` = score/max.
BENCH_SQL = """
CREATE TABLE IF NOT EXISTS bench(
  run_id TEXT, arm TEXT, task_id TEXT, attempt INTEGER NOT NULL DEFAULT 1, role TEXT, agent TEXT,
  model TEXT, effort TEXT, body_hash TEXT,
  fixture_hash TEXT, harness_version TEXT, price_version TEXT, suite TEXT, tier INTEGER, source TEXT,
  started REAL, secs REAL, passed INTEGER, stopped INTEGER DEFAULT 0, cost_usd REAL, turns INTEGER,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER,
  w5h_before REAL, w5h_after REAL, rubric REAL, failure_kind TEXT, consequence_cost REAL, detail TEXT,
  PRIMARY KEY(run_id, arm, task_id, attempt));
"""
SCHEMA_SQL += BENCH_SQL

# table -> (primary key columns, all columns, default upsert mode)
TABLES = {
    "meta": (("key",), ("key", "value"), "replace"),
    "accounts": (("email",), ("email", "label", "org_id", "org_name", "subscription",
                              "first_seen", "last_seen", "tier", "tier_source"), "merge"),
    "sessions": (("session_id",), ("session_id", "account", "account_source", "machine", "project",
                                   "cwd", "transcript_path", "kind", "agent_name", "model_launch",
                                   "effort_launch", "version", "started", "ended", "end_reason",
                                   "cost_usd", "cost_source", "cost_updated", "phase",
                                   "harness_cost_usd"), "merge"),
    "agent_runs": (("run_id",), ("run_id", "session_id", "parent_run_id", "parent_source", "kind",
                                 "agent_type", "task_id", "phase", "description", "tool_use_id",
                                 "spawn_depth", "model_pinned", "model_seen", "effort", "started",
                                 "ended", "status", "turns", "seed_ctx", "ctx_at_end", "input",
                                 "cache_write_5m", "cache_write_1h", "cache_read", "output",
                                 "thinking", "cost_usd", "handoff_fired", "locked", "lock_source",
                                 "transcript_path", "transcript_bytes"), "merge"),
    "turns": (("msg_id",), ("msg_id", "request_id", "run_id", "session_id", "account", "ts",
                            "model", "effort", "input", "cache_write_5m", "cache_write_1h",
                            "cache_write", "cache_read", "output", "thinking", "ctx", "new_tokens",
                            "gap_s", "cold", "rewrite", "stop_reason", "cost_usd", "ttl_assumed",
                            "kind"), "replace"),
    "retrievals": (("run_id",), ("run_id", "parent_run_id", "agent_type", "model", "spawned_at",
                                 "returned_at", "seed_ctx", "ctx_at_end", "growth_tokens",
                                 "result_tokens_est", "own_output_est", "kept_out_tokens",
                                 "kept_out_mode", "answer_tokens", "status", "void"), "merge"),
    "savings": (("run_id",), ("run_id", "session_id", "phase", "measured_saved_usd",
                              "measured_detail_json", "modeled_saved_usd", "assumptions_json",
                              "locked", "updated"), "merge"),
    "utilization": (("session_id", "ts", "window"),
                    ("id", "ts", "account", "machine", "session_id", "window", "pct", "resets_at",
                     "session_cost", "model", "source", "reason"), "ignore"),
    "window_instances": (("account", "window", "resets_at"),
                         ("account", "window", "resets_at", "started_at", "first_seen", "last_seen",
                          "quantized", "pct_per_dollar", "fit_method", "fit_n", "fit_crossings",
                          "fit_span", "fit_se", "fit_quality", "fit_detail", "reset_source"), "merge"),
    "fit_pool": (("tier", "window", "family"),
                 ("tier", "window", "family", "rate", "se", "n", "n_instances", "regime_since",
                  "built_at"), "replace"),
    "prices": (("model_key", "effective_from"),
               ("model_key", "priority", "effective_from", "input", "write_5m", "write_1h",
                "read", "output", "tokenizer"), "replace"),
    "summary": (("account", "window"), ("account", "window", "period_start", "cost_used",
                                        "cost_saved_measured", "cost_saved_modeled",
                                        "pct_per_dollar", "fit_n", "fit_quality", "updated"),
                "replace"),
    "events": ((), ("id", "ts", "session_id", "run_id", "account", "kind", "detail_json"), "insert"),
    "tool_calls": (("id",), ("id", "session_id", "run_id", "phase", "ts", "tool", "kind",
                              "script", "sub", "path", "file_tokens", "emission_tokens",
                              "result_tokens", "kept_tokens", "model"), "merge"),
    "bench": (("run_id", "arm", "task_id", "attempt"),
              ("run_id", "arm", "task_id", "attempt", "role", "agent", "model", "effort", "body_hash",
               "fixture_hash", "harness_version", "price_version", "suite", "tier", "source",
               "started", "secs", "passed", "stopped", "cost_usd", "turns", "input", "output",
               "cache_read", "cache_write", "w5h_before", "w5h_after", "rubric", "failure_kind",
               "consequence_cost", "detail"),
              "replace"),
}

INDEX_NAMES = ("ix_turns_run", "ix_turns_session", "ix_turns_account_ts", "ix_runs_session",
               "ix_runs_parent", "ix_util_win", "ix_retr_parent", "ix_sessions_acct",
               "ix_calls_session", "ix_events_kind")


# --------------------------------------------------------------------------- connect

def connect(path=None, readonly=False, create=True, timeout=5.0):
    """Open ``ledger.sqlite`` with the design doc's pragmas.

    ``readonly=True`` opens ``file:…?mode=ro`` (used for the union readers).
    Rows come back as ``sqlite3.Row``.
    """
    p = os.path.abspath(path or paths.db_path())
    if readonly:
        conn = sqlite3.connect(_ro_uri(p), uri=True, timeout=timeout)
    else:
        if create:
            fsutil.ensure_dir(p)
        conn = sqlite3.connect(p, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        if readonly and ("journal_mode" in pragma or "synchronous" in pragma
                         or "wal_autocheckpoint" in pragma or "journal_size_limit" in pragma):
            continue
        try:
            conn.execute(pragma)
        except sqlite3.DatabaseError:
            pass
    return conn


def close(conn):
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _ro_uri(path):
    from urllib.parse import quote

    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + quote(p, safe="/:") + "?mode=ro"


def init_schema(conn, machine=None):
    """Create every table and index, stamp ``meta`` and seed ``prices``."""
    conn.executescript(SCHEMA_SQL)
    _bench_v4(conn)   # a v3 bench left by IF NOT EXISTS (hooks and the installer call init_schema)
    _sessions_v5(conn)   # likewise a v4 sessions table (T30)
    _accounts_v7(conn)   # likewise a v6 accounts table (3.10.6 T1)
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
    if get_meta(conn, "created") is None:
        set_meta(conn, "created", now)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    set_meta(conn, "machine", machine or paths.machine_tag())
    set_meta(conn, "prices_version", prices.PRICES_VERSION)
    seed_prices(conn)
    try:
        conn.commit()
    except sqlite3.Error:
        pass
    return conn


def schema_version(conn):
    try:
        return int(get_meta(conn, "schema_version") or 0)
    except (TypeError, ValueError):
        return 0


def migrate(conn):
    """Bring an existing database up to ``SCHEMA_VERSION``.

    v1 is the first schema: ``init_schema`` is idempotent (``IF NOT EXISTS``
    everywhere), so migrating is running it again.  Later versions add their
    ``ALTER TABLE`` steps here, keyed on the stored version.  v2 -> v3 adds the
    ``bench`` table (``BENCH_SQL``).  v3 -> v4 recreates ``bench`` with the
    ``attempt`` key (:func:`_bench_v4`: old rows become attempt 1, one
    transaction; SQLite cannot alter a primary key).  v4 -> v5 adds
    ``sessions.harness_cost_usd`` (:func:`_sessions_v5`, T30).  v5 -> v6 adds the
    ``fit_pool`` table (T32.1; created by ``SCHEMA_SQL``).  v6 -> v7 adds
    ``accounts.tier`` and ``accounts.tier_source`` (:func:`_accounts_v7`).  A database stamped newer than
    ``SCHEMA_VERSION`` is returned untouched: a version is never stamped down.
    """
    have = schema_version(conn)
    if have >= SCHEMA_VERSION:
        return have
    # v1 -> v2: add fit_detail TEXT to window_instances (T2.1)
    if have < 2:
        try:
            conn.execute("ALTER TABLE window_instances ADD COLUMN fit_detail TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists (idempotent)
    # v2 -> v3: the bench table (T3); init_schema re-runs SCHEMA_SQL, which includes it too
    if have < 3:
        conn.executescript(BENCH_SQL)
    # v3 -> v4: bench keyed per attempt (3.9.7 T5)
    if have < 4:
        _bench_v4(conn)
    # v4 -> v5: sessions.harness_cost_usd, the harness cost-state as a cross-check (T30)
    if have < 5:
        _sessions_v5(conn)
    # v5 -> v6: fit_pool, the per-(tier, window) pooled family fit (T32.1); CREATE IF NOT EXISTS
    if have < 6:
        conn.executescript(SCHEMA_SQL)
    # v6 -> v7: accounts.tier + tier_source, the account's plan tier and where it came from
    if have < 7:
        _accounts_v7(conn)
    init_schema(conn)
    return SCHEMA_VERSION


def _sessions_v5(conn):
    """Add ``sessions.harness_cost_usd`` to a pre-v5 table (T30); no-op when present."""
    have = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if not have or "harness_cost_usd" in have:
        return False
    conn.execute("ALTER TABLE sessions ADD COLUMN harness_cost_usd REAL")
    return True


def _accounts_v7(conn):
    """Add ``accounts.tier`` and ``accounts.tier_source`` to a pre-v7 table; no-op when present."""
    have = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if not have:
        return False
    added = False
    for col in ("tier", "tier_source"):
        if col not in have:
            conn.execute("ALTER TABLE accounts ADD COLUMN %s TEXT" % col)
            added = True
    return added


def _bench_v4(conn):
    """Recreate a pre-v4 ``bench`` (no ``attempt`` column) in the v4 shape; no-op otherwise."""
    have = [r[1] for r in conn.execute("PRAGMA table_info(bench)").fetchall()]
    if not have or "attempt" in have:
        return False
    keep = ", ".join(c for c in TABLES["bench"][1] if c in have)
    try:
        conn.executescript("BEGIN;\n"
                           + BENCH_SQL.replace("EXISTS bench(", "EXISTS bench_v4(")
                           + "INSERT INTO bench_v4 (%s, attempt) SELECT %s, 1 FROM bench;\n" % (keep, keep)
                           + "DROP TABLE bench;\nALTER TABLE bench_v4 RENAME TO bench;\nCOMMIT;\n")
    except sqlite3.Error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return True


# --------------------------------------------------------------------------- meta

def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
    return value


def get_meta(conn, key, default=None):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.DatabaseError:
        return default
    return row[0] if row else default


# --------------------------------------------------------------------------- upserts

def _clean(table, row):
    pk, cols, _ = TABLES[table]
    known = [c for c in cols if c in row]
    return pk, known, [row[c] for c in known]


def _sql(table, mode, cols, pk):
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    if mode == "replace":
        return "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (table, names, marks)
    if mode == "ignore":
        return "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (table, names, marks)
    if mode == "insert" or not pk:
        return "INSERT INTO %s (%s) VALUES (%s)" % (table, names, marks)
    updates = [c for c in cols if c not in pk]
    if not updates:
        return "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (table, names, marks)
    setters = ", ".join("%s=excluded.%s" % (c, c) for c in updates)
    return "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s" % (
        table, names, marks, ", ".join(pk), setters)


def upsert(conn, table, row, mode=None):
    """Insert/merge one dict into ``table`` using that table's idempotency key.

    ``mode``: ``merge`` (default per table; only the columns present in ``row``
    are written, others keep their value), ``replace`` (INSERT OR REPLACE),
    ``ignore`` (INSERT OR IGNORE) or ``insert``.  Unknown keys are dropped, so
    a hook can pass a superset dict.
    """
    if table not in TABLES:
        raise KeyError("unknown table: %s" % table)
    pk, cols, values = _clean(table, row)
    if not cols:
        return 0
    use = mode or TABLES[table][2]
    cur = conn.execute(_sql(table, use, cols, pk), values)
    return cur.rowcount


def upsert_account(conn, row):
    """accounts: merge (keeps ``first_seen``, refreshes ``last_seen``)."""
    return upsert(conn, "accounts", row)


def upsert_session(conn, row):
    """sessions: merge on ``session_id``."""
    return upsert(conn, "sessions", row)


def upsert_agent_run(conn, row):
    """agent_runs: merge on ``run_id`` (SubagentStart then SubagentStop)."""
    return upsert(conn, "agent_runs", row)


def upsert_turn(conn, row):
    """turns: INSERT OR REPLACE on ``msg_id`` -- keep-LAST semantics."""
    return upsert(conn, "turns", row, mode="replace")


def upsert_turns(conn, rows):
    """Bulk keep-last insert of turn rows; returns the number written."""
    n = 0
    for row in rows:
        n += upsert_turn(conn, row) or 0
    return n


def upsert_retrieval(conn, row):
    return upsert(conn, "retrievals", row)


def upsert_savings(conn, row):
    return upsert(conn, "savings", row)


def upsert_window_instance(conn, row):
    return upsert(conn, "window_instances", row)


def upsert_fit_pool(conn, row):
    return upsert(conn, "fit_pool", row)


def upsert_price(conn, row):
    return upsert(conn, "prices", row, mode="replace")


def upsert_summary(conn, row):
    return upsert(conn, "summary", row, mode="replace")


def upsert_tool_call(conn, row):
    return upsert(conn, "tool_calls", row)


def insert_utilization(conn, row):
    """utilization: INSERT OR IGNORE on UNIQUE(session_id, ts, window)."""
    return upsert(conn, "utilization", row, mode="ignore")


def insert_event(conn, kind, detail=None, session_id=None, run_id=None, account=None, ts=None):
    """Append an ``events`` row; ``detail`` is JSON-encoded."""
    row = {
        "ts": ts or (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"),
        "session_id": session_id, "run_id": run_id, "account": account, "kind": kind,
        "detail_json": json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None,
    }
    return upsert(conn, "events", row, mode="insert")


def seed_prices(conn, force=False):
    """Write the ``prices.PRICES`` seed table (skipped when rows exist)."""
    if not force:
        row = conn.execute("SELECT COUNT(*) FROM prices").fetchone()
        if row and row[0]:
            return 0
    n = 0
    for tup in prices.rows_for_db():
        conn.execute(
            "INSERT OR REPLACE INTO prices (model_key, priority, effective_from, input,"
            " write_5m, write_1h, read, output, tokenizer) VALUES (?,?,?,?,?,?,?,?,?)", tup)
        n += 1
    return n


# --------------------------------------------------------------------------- rows

def turn_row_from_request(req, run_id=None, session_id=None, account=None,
                          ttl_default="5m", kind="api", effort=None):
    """Build a ``turns`` row from a :func:`pa.transcript.iter_requests` dict.

    The 5m/1h columns are resolved here: the record's own
    ``usage.cache_creation`` split when present, otherwise the whole cache-write
    is attributed to ``ttl_default`` and ``ttl_assumed`` records which rule was
    used (``split`` | ``1h`` | ``5m``).

    T8: does not price a >200k-context request at the long-context tier --
    see ``pa.transcript.request_cost``, which this mirrors for the same reason
    (``turns.cost_usd`` and the reconcile transcript-truth total must agree).
    """
    cost, parts = prices.cost_of(req, req.get("model"), ttl_default=ttl_default,
                                 at=req.get("ts_iso"))
    write = int(req.get("cache_write") or 0)
    if req.get("split"):
        w5 = int(req.get("cache_write_5m") or 0)
        w1 = int(req.get("cache_write_1h") or 0)
    elif parts["ttl"] == "1h":
        w5, w1 = 0, write
    else:
        w5, w1 = write, 0
    return {
        "msg_id": req.get("msg_id"),
        "request_id": req.get("request_id"),
        "run_id": run_id if run_id is not None else req.get("run_id"),
        "session_id": session_id if session_id is not None else req.get("session_id"),
        "account": account,
        "ts": req.get("ts_iso"),
        "model": req.get("model"),
        "effort": effort,
        "input": int(req.get("input") or 0),
        "cache_write_5m": w5,
        "cache_write_1h": w1,
        "cache_write": write,
        "cache_read": int(req.get("cache_read") or 0),
        "output": int(req.get("output") or 0),
        "thinking": req.get("thinking"),
        "ctx": int(req.get("ctx") or 0),
        "new_tokens": int(req.get("new_tokens") or 0),
        "gap_s": req.get("gap_s"),
        "cold": 1 if req.get("cold") else 0,
        "rewrite": 1 if req.get("rewrite") else 0,
        "stop_reason": req.get("stop_reason"),
        "cost_usd": cost,
        "ttl_assumed": parts["ttl"],
        "kind": kind,
    }


# --------------------------------------------------------------------------- maintenance

def wal_checkpoint(conn, mode="TRUNCATE"):
    """``PRAGMA wal_checkpoint(<mode>)``; returns the pragma row or None."""
    try:
        return conn.execute("PRAGMA wal_checkpoint(%s)" % mode).fetchone()
    except sqlite3.DatabaseError:
        return None


# --------------------------------------------------------------------------- union readers

def union_dir():
    """Temp directory holding the read-only copies of foreign ledgers."""
    import tempfile

    return os.path.join(tempfile.gettempdir(), "pa3-union")


def _root_db(root):
    """``<root>`` may be the usage-ledger directory or the .sqlite file itself."""
    r = os.path.abspath(str(root))
    return r if r.lower().endswith(".sqlite") else os.path.join(r, "ledger.sqlite")


def union_copy(root):
    """Copy a foreign ``ledger.sqlite`` (+ ``-wal``) to a temp dir if it changed.

    Returns the path of the local copy, or None when the source is unreadable.
    The copy is checkpointed and switched to a rollback journal so it can be
    opened ``mode=ro`` (a WAL copy would need write access to recover).
    """
    import hashlib
    import shutil

    src = _root_db(root)
    try:
        st = os.stat(src)
    except OSError:
        return None
    wal = src + "-wal"
    wal_sig = (fsutil.mtime(wal), fsutil.file_size(wal))
    sig = {"src": src, "mtime": st.st_mtime, "size": st.st_size,
           "wal_mtime": wal_sig[0], "wal_size": wal_sig[1]}

    tag = hashlib.sha1(src.encode("utf-8", "replace")).hexdigest()[:12]
    dest_dir = os.path.join(union_dir(), tag)
    dest = os.path.join(dest_dir, "ledger.sqlite")
    marker = os.path.join(dest_dir, "source.json")
    if os.path.exists(dest) and fsutil.read_json(marker, None) == sig:
        return dest
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            stale = dest + suffix
            if os.path.exists(stale):
                os.remove(stale)
        shutil.copyfile(src, dest)
        if os.path.exists(wal):
            shutil.copyfile(wal, dest + "-wal")
        tmp = sqlite3.connect(dest, timeout=5.0)
        try:
            tmp.execute("PRAGMA journal_mode=DELETE")     # replays + drops the WAL
            tmp.commit()
        finally:
            tmp.close()
    except (OSError, sqlite3.DatabaseError):
        return None
    fsutil.atomic_write_json(marker, sig)
    return dest


def union_readers(extra_roots, include_local=False):
    """Read-only connections to other machines' ledgers (config.extra_roots).

    Each root is copied locally first (:func:`union_copy`) -- SQLite is never
    opened directly over UNC/9P paths.  Unreachable roots are skipped silently.
    ``extra_roots`` items may be a bare path string or ``{path, account, label}``
    (``config.extra_root_entries`` shape); either form works here directly.

    Returns a list of ``{"conn": sqlite3.Connection, "path": str|None,
    "account": str|None, "label": str|None, "machine": str|None}`` -- one dict
    per reader, the local connection first when ``include_local`` (its
    ``path``/``account``/``label`` are None).  ``machine`` is that reader's own
    ``meta.machine`` (stamped by :func:`init_schema` on the box that built it,
    e.g. ``"wsl"``) -- the fallback a caller labels rows with when a row's own
    ``machine`` column is unset.  A dict, not a bare connection, because
    ``sqlite3.Connection`` takes no custom attributes; the caller reads
    ``item["conn"]`` and uses ``account``/``label``/``machine`` to attribute
    NULL-account, NULL-machine rows from that root.
    """
    conns = []
    if include_local:
        try:
            conn = connect()
            conns.append({"conn": conn, "path": None, "account": None, "label": None,
                          "machine": get_meta(conn, "machine")})
        except sqlite3.DatabaseError:
            pass
    for item in (extra_roots or []):
        if isinstance(item, dict):
            path, account, label = item.get("path"), item.get("account"), item.get("label")
        else:
            path, account, label = item, None, None
        if not path:
            continue
        copy_path = union_copy(path)
        if not copy_path:
            continue
        try:
            conn = connect(copy_path, readonly=True)
            conns.append({"conn": conn, "path": path, "account": account, "label": label,
                          "machine": get_meta(conn, "machine")})
        except sqlite3.DatabaseError:
            continue
    return conns
