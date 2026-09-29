"""Measured retriever savings (design doc E.1, spec 3.9.2 / 3.9.5).

Public:
    SAVINGS_VERSION
    KEPT_OUT_MODES
    kept_out_tokens(turns, result_tokens_est, own_output_est, answer_tokens, mode)
                                            -> (R, detail)
    retrieval_row(...)                      -> a ``retrievals`` row dict
    price_read(model, at=None)              -> $/MTok cache-read price (0.0 if unknown)
    measured_saved(conn, expert_run_id, until=None)   -> dict
    live_saved(agent_entry, price_read)     -> float          (running.json shape)
    window_saved_measured(conn, account, t0, t1, by_family=False, project=None)  -> float | dict
    headline_ratio(cost_saved_measured, cost_used)    -> float | None

Definitions (spec 3.9.2, vanilla-net-v3, 3.5 T9): the carrier is a 1h main
session.  For each non-void kept-out row of a session, the first session turn
(any run) after the row's return charges R × write_1h; every later turn charges
R × read, except when the gap between consecutive session turns exceeds
``savings.vanilla_ttl_s`` (default 3600 s) the carried mass is rewritten at
write_1h.  The vanilla window is a plain ``savings.window_tokens`` (1M, every
model) that cycles (T30): carried mass accumulates across turns; on a turn
where ctx + carried + first > window the carried rows are dropped for good
and the carry restarts at 0; first writes credit min(first, window − ctx),
carried rows their full mass.  Net = gross − seed carry,
where the seed carry for a subagent run is Σ_turns min(1, seed_ctx/ctx) ×
cost_usd.  Rule C (task-boundary rewrites, 3.2 T11) is retired: the
comparison is now vanilla, not PA2.

Modeled (architecture) savings live in ``pa/replay.py``: a rolling virtual
single context per account across sessions, paying reads, writes and cold
rewrites at each request's model, in the same plain window that cycles to 0
when a turn would overflow it (T30).  Nothing in this module ever adds a
measured figure to a modeled one (D39).
"""

from . import prices

SAVINGS_VERSION = "vanilla-net-v4"  # was "vanilla-net-v3"
KEPT_OUT_MODES = ("results_share", "all_growth", "results_est")


# --------------------------------------------------------------------------- helpers

def _field(row, name, default=None):
    """Read ``name`` from a dict, a ``sqlite3.Row`` or an object."""
    if row is None:
        return default
    val = default
    if isinstance(row, dict):
        val = row.get(name, default)
    else:
        try:
            val = row[name]
        except (TypeError, KeyError, IndexError):
            val = getattr(row, name, default)
    return default if val is None else val


def _int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def price_read(model, at=None):
    """Cache-read price ($/MTok) of ``model``; 0.0 when the model is unknown."""
    row = prices.price_for(model, at)
    return float(row["read"]) if row else 0.0


def price_write(model, at=None):
    """5-minute cache-write price ($/MTok) of ``model``; 0.0 when the model is unknown."""
    row = prices.price_for(model, at)
    return float(row["write_5m"]) if row else 0.0


def price_input(model, at=None):
    """Uncached input price ($/MTok) of ``model``: what a cold read costs; 0.0 when unknown."""
    row = prices.price_for(model, at)
    return float(row["input"]) if row else 0.0


def price_output(model, at=None):
    """Output price ($/MTok) of ``model``; 0.0 when the model is unknown."""
    row = prices.price_for(model, at)
    return float(row["output"]) if row else 0.0


def _prices3(model):
    return price_input(model), price_read(model), price_write(model)


def _ttl_seconds(ttl):
    t = str(ttl or "5m").strip().lower()
    return 3600 if t in ("1h", "60m", "3600") else 300


def _epoch(ts):
    """ISO ``YYYY-MM-DDTHH:MM:SS[.fff]Z`` -> seconds since the epoch (None when unparsable)."""
    text = str(ts or "").strip()
    if len(text) < 19:
        return None
    try:
        import calendar
        import time as _time

        return calendar.timegm(_time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _same_model(a, b):
    return prices.model_key(a) == prices.model_key(b)


def _model_window(model):
    """Context window size in tokens from the price table's ``window`` column.
    ``'1m'`` → 1 000 000; ``None`` → 200 000; unknown model → ``_cfg_window_tokens()``."""
    p = prices.price_for(model)
    if not p:
        return _cfg_window_tokens()
    return 1000000 if p.get("window") == "1m" else 200000


def _cfg_vanilla_ttl_s():
    """``savings.vanilla_ttl_s`` from config, default 3600."""
    try:
        from . import config as _cfg
        return int(_cfg.get(_cfg.load(), "savings.vanilla_ttl_s", 3600))
    except Exception:
        return 3600


def _cfg_window_tokens():
    """``savings.window_tokens`` from config, default 1 000 000."""
    try:
        from . import config as _cfg
        return int(_cfg.get(_cfg.load(), "savings.window_tokens", 1000000))
    except Exception:
        return 1000000


def _kept_out_with_parent(conn, session_id):
    """Every non-void kept-out row of a session with its ``parent_run_id``,
    oldest first: ``[(run_id, returned_at, kept, parent_run_id)]``."""
    try:
        rows = conn.execute(
            "SELECT r.run_id, r.returned_at, r.kept_out_tokens, r.parent_run_id"
            " FROM retrievals r"
            " LEFT JOIN agent_runs a ON a.run_id = r.run_id"
            " LEFT JOIN agent_runs p ON p.run_id = r.parent_run_id"
            " WHERE COALESCE(a.session_id, p.session_id, r.parent_run_id)=?"
            " AND COALESCE(r.void, 0)=0 AND r.returned_at IS NOT NULL"
            " ORDER BY r.returned_at", (session_id,)).fetchall()
    except Exception:
        return []
    base = [(_field(r, "run_id"), _field(r, "returned_at"),
             _int(_field(r, "kept_out_tokens", 0)), _field(r, "parent_run_id"))
            for r in rows]
    # Merge tool-call carry rows (vanilla-net-v4)
    tc = _tool_calls_carry_rows(conn, session_id)
    if tc:
        base.extend(tc)
        base.sort(key=lambda x: str(x[1] or ""))
    return base


def _tool_calls_carry_rows(conn, session_id):
    """Tool-call carry rows for a session, shaped like ``_kept_out_with_parent``.

    Walks the session's ``tool_calls`` in ts order, computing ``credit.carry_tokens``
    for each.  ``read_paths`` is every path from the session's ``kind='read'`` rows
    (session-wide guard at recalc); ``seen_paths`` grows as rows are visited.
    Rows whose carry is 0 are omitted.  ``credit.carry_tokens`` returns the tokens of
    one row, not to be confused with the ``carry_token_turns`` detail key below
    (:func:`measured_saved`), which sums carried mass across the walk's turns.

    Returns ``[(call_id, ts, carry_tokens, run_id)]``.
    """
    from . import credit as _credit
    try:
        from . import config as _cfg
        cfg = _cfg.load()
    except Exception:
        cfg = {}
    # Gather session-wide read_paths (kind='read')
    read_paths = set()
    try:
        rrows = conn.execute(
            "SELECT path FROM tool_calls WHERE session_id=?"
            " AND kind IN ('read', 'outline-read')"
            " AND path IS NOT NULL AND path != ''",
            (session_id,)).fetchall()
        for r in rrows:
            p = r[0] if not isinstance(r, dict) else r.get("path", "")
            if p:
                read_paths.add(p)
    except Exception:
        pass
    # Walk non-read tool_calls in ts order
    try:
        rows = conn.execute(
            "SELECT id, run_id, ts, kind, path, file_tokens, emission_tokens,"
            " result_tokens, kept_tokens, model FROM tool_calls"
            " WHERE session_id=? AND kind != 'read'"
            " ORDER BY ts, kind = 'outline-read'",
            (session_id,)).fetchall()
    except Exception:
        return []
    seen_paths = set()
    outline_em = {}                                        # (run_id, path) -> the outline row's emission
    out = []
    for r in rows:
        row_dict = {
            "kind": r[3] if not isinstance(r, dict) else r.get("kind", ""),
            "path": r[4] if not isinstance(r, dict) else r.get("path", ""),
            "file_tokens": r[5] if not isinstance(r, dict) else r.get("file_tokens", 0),
            "result_tokens": r[7] if not isinstance(r, dict) else r.get("result_tokens", 0),
            "kept_tokens": r[8] if not isinstance(r, dict) else r.get("kept_tokens", 0),
        }
        _key = (r[1] if not isinstance(r, dict) else r.get("run_id", ""), row_dict["path"])
        if row_dict["kind"] == "outline":
            outline_em[_key] = _int(r[6] if not isinstance(r, dict) else r.get("emission_tokens", 0))
        elif row_dict["kind"] == "outline-read":
            row_dict["outline_emission_tokens"] = outline_em.get(_key, 0)
        carry = _credit.carry_tokens(row_dict, seen_paths, read_paths, cfg)
        if carry > 0:
            call_id = r[0] if not isinstance(r, dict) else r.get("id", "")
            run_id = r[1] if not isinstance(r, dict) else r.get("run_id", "")
            ts = r[2] if not isinstance(r, dict) else r.get("ts", "")
            out.append((call_id, ts, carry, run_id))
    return out


def _session_all_api_turns(conn, session_id, until=None):
    """All api turns of the session across all runs, oldest first."""
    sql = ("SELECT ts, model, ctx, run_id, cost_usd, account"
           " FROM turns WHERE session_id=? AND kind='api' AND ts IS NOT NULL")
    args = [session_id]
    if until:
        sql += " AND ts <= ?"
        args.append(until)
    sql += " ORDER BY ts"
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []


def _seed_carry_run(conn, run_id, session_id):
    """Seed carry for one subagent run: ``Σ_turns min(1, seed_ctx/ctx) × cost_usd``."""
    if run_id == session_id:
        return 0.0
    try:
        r = conn.execute("SELECT seed_ctx FROM agent_runs WHERE run_id=?",
                         (run_id,)).fetchone()
    except Exception:
        return 0.0
    seed = _int(_field(r, "seed_ctx"))
    if seed <= 0:
        return 0.0
    try:
        turns = conn.execute(
            "SELECT ctx, cost_usd FROM turns WHERE run_id=? AND kind='api'",
            (run_id,)).fetchall()
    except Exception:
        return 0.0
    carry = 0.0
    for t in turns:
        ctx = _int(_field(t, "ctx"))
        cost = float(_field(t, "cost_usd") or 0)
        if ctx > 0:
            carry += min(1.0, seed / ctx) * cost
    return carry


def _all_seed_carries(conn, session_id):
    """Seed carry per subagent run: ``{run_id: usd}``."""
    try:
        runs = conn.execute(
            "SELECT run_id, seed_ctx FROM agent_runs"
            " WHERE session_id=? AND run_id!=?",
            (session_id, session_id)).fetchall()
    except Exception:
        return {}
    result = {}
    for r in runs:
        seed = _int(_field(r, "seed_ctx"))
        if seed <= 0:
            continue
        rid = _field(r, "run_id")
        try:
            turns = conn.execute(
                "SELECT ctx, cost_usd FROM turns WHERE run_id=? AND kind='api'",
                (rid,)).fetchall()
        except Exception:
            continue
        carry = 0.0
        for t in turns:
            ctx = _int(_field(t, "ctx"))
            cost = float(_field(t, "cost_usd") or 0)
            if ctx > 0:
                carry += min(1.0, seed / ctx) * cost
        if carry > 0:
            result[rid] = carry
    return result


def _seed_carry_by_account(conn, run_id, session_id):
    """Per-account seed carry: ``{account: usd}`` (T9)."""
    if run_id == session_id:
        return {}
    try:
        r = conn.execute("SELECT seed_ctx FROM agent_runs WHERE run_id=?",
                         (run_id,)).fetchone()
    except Exception:
        return {}
    seed = _int(_field(r, "seed_ctx"))
    if seed <= 0:
        return {}
    try:
        turns = conn.execute(
            "SELECT ctx, cost_usd, account FROM turns WHERE run_id=? AND kind='api'",
            (run_id,)).fetchall()
    except Exception:
        return {}
    by_acct = {}
    for t in turns:
        ctx = _int(_field(t, "ctx"))
        cost = float(_field(t, "cost_usd") or 0)
        acct = _field(t, "account")
        if ctx > 0 and acct is not None:
            by_acct[acct] = by_acct.get(acct, 0.0) + min(1.0, seed / ctx) * cost
    return by_acct


def _account_timeline(conn, run_id):
    """``[(ts, account)]`` for a run's api turns, oldest first (T9)."""
    try:
        rows = conn.execute(
            "SELECT ts, account FROM turns WHERE run_id=? AND kind='api'"
            " AND ts IS NOT NULL ORDER BY ts",
            (run_id,)).fetchall()
        return [(r[0] if not isinstance(r, dict) else r.get("ts"),
                 r[1] if not isinstance(r, dict) else r.get("account"))
                for r in rows]
    except Exception:
        return []


def _vanilla_walk(rows, turns, vtl_s, fallback_model=None, t0=None, t1=None, window=None):
    """Core vanilla-net-v4 session walk: a plain *window* (1M) that cycles.

    *rows* is ``[(krid, ret_at, kept, parent_run_id)]``.
    *turns* is a sequence of rows with ts, model, ctx, account.
    *window* is ``savings.window_tokens`` (default 1 000 000) for every model.

    Carried mass accumulates across turns.  On a turn where
    ``ctx + carried_mass + first_mass > window`` the carried rows are dropped
    (never credited nor re-written again) and the carry restarts at 0; then
    first writes credit ``min(first_mass, max(0, window - ctx))`` at write_1h
    and carried rows credit their full mass at read (write_1h when cold).

    Walks all turns for state; credits only turns in ``[t0, t1)`` when given.
    Returns ``(per_row, gross, by_account)`` where per_row maps krid → dict
    with usd, R, N, n_cold, first_write_ttl, parent_run_id, by_account;
    ``by_account`` is ``{account: usd}`` of the gross split by each turn's
    account (T9).
    """
    window = int(window or 1000000)  # T30: plain 1M cycle; was 1M or 200k per the price table
    written = set()          # row indices that have had their first write
    dropped = set()          # row indices dropped by a window cycle (T30)
    prev_epoch = None
    per_row = {}
    gross = 0.0
    by_account = {}

    for t in turns:
        ts = _field(t, "ts")
        m = _field(t, "model") or fallback_model
        ctx = _int(_field(t, "ctx"))
        account = _field(t, "account")
        e = _epoch(ts)

        cold = (prev_epoch is not None and e is not None
                and (e - prev_epoch) > vtl_s)
        if e is not None:
            prev_epoch = e

        # Should this turn contribute credits?
        in_window = True
        if t0 and str(ts) < str(t0):
            in_window = False
        if t1 and str(ts) >= str(t1):
            in_window = False

        first_items = []
        carried_items = []
        first_mass = 0
        carried_mass = 0

        for idx, (krid, ret_at, kept, parent) in enumerate(rows):
            if not (ret_at and ts and str(ret_at) < str(ts)):
                continue
            if idx in dropped:
                continue
            if idx not in written:
                written.add(idx)
                first_mass += kept
                first_items.append((krid, kept, parent))
            else:
                carried_mass += kept
                carried_items.append((idx, krid, kept, parent))

        # The window cycles: the carry is dropped when this turn would overflow it
        if ctx + carried_mass + first_mass > window:
            for item in carried_items:
                dropped.add(item[0])
            carried_items = []
            carried_mass = 0

        p = prices.price_for(m)
        if not p:
            # State updated above; skip credit
            continue
        w1h = float(p["write_1h"])
        rd = float(p["read"])

        if not in_window:
            continue

        turn_usd = 0.0

        # First writes: only the room left in the window this turn
        cr_first = min(first_mass, max(0, window - ctx))

        # Credit first writes at write_1h
        if first_items and cr_first > 0:
            scale = cr_first / first_mass
            for krid, kept, parent in first_items:
                usd = kept * scale * w1h / 1e6
                entry = per_row.setdefault(krid, {"usd": 0.0, "R": kept, "N": 0,
                    "n_cold": 0, "first_write_ttl": "1h", "parent_run_id": parent,
                    "by_account": {}})
                entry["usd"] += usd
                entry["N"] += 1
                if account is not None:
                    entry["by_account"][account] = entry["by_account"].get(account, 0.0) + usd
                gross += usd
                turn_usd += usd

        # Credit carried mass in full (the cycle above keeps it inside the window)
        if carried_items and carried_mass > 0:
            rate = w1h if cold else rd
            for _idx, krid, kept, parent in carried_items:
                usd = kept * rate / 1e6
                entry = per_row.setdefault(krid, {"usd": 0.0, "R": kept, "N": 0,
                    "n_cold": 0, "first_write_ttl": "1h", "parent_run_id": parent,
                    "by_account": {}})
                entry["usd"] += usd
                entry["N"] += 1
                if cold:
                    entry["n_cold"] += 1
                if account is not None:
                    entry["by_account"][account] = entry["by_account"].get(account, 0.0) + usd
                gross += usd
                turn_usd += usd

        if turn_usd > 0 and account is not None:
            by_account[account] = by_account.get(account, 0.0) + turn_usd

    return per_row, gross, by_account


def kept_out_usd(kept, n_after, p_read, p_write):
    """What ``kept`` tokens kept out of a parent cost it to avoid: the cache write of those tokens
    on the first request after the return (billed instead of an input read), then a cache read on
    every later request (3.1).  Retained for callers and tests; ``measured_saved`` uses per-turn
    TTL-aware pricing (ttl-cold-v2) instead of this flat formula."""
    kept = _int(kept)
    n_after = _int(n_after)
    if kept <= 0 or n_after <= 0:
        return 0.0
    return kept * (float(p_write or 0.0) + (n_after - 1) * float(p_read or 0.0)) / 1e6


# --------------------------------------------------------------------------- kept-out tokens

def kept_out_tokens(turns, result_tokens_est=0, own_output_est=0, answer_tokens=0,
                    mode="results_share"):
    """Tokens a retriever kept out of its parent's context (design doc E.1).

    ``turns`` is the retriever's own requests in order (anything with a ``ctx``
    key: dicts from :func:`pa.transcript.iter_requests`, ``turns`` rows, or a
    two-element ``[{"ctx": seed}, {"ctx": end}]`` stand-in).  ``seed_ctx`` is the
    ctx of the first request (system + tools + CLAUDE.md + brief) and
    ``ctx_at_end`` the ctx of the last -- both API-measured, so ``growth`` is
    tokenizer-exact.

    Modes::

        results_share  R = growth * result_est/(result_est+own_output_est) - answer   (default)
        all_growth     R = growth - answer
        results_est    R = result_tokens_est                     (chars/4; undercounts)

    ``R`` is clamped at 0.  Returns ``(R, detail)``; ``detail`` is JSON-shaped
    and is what ``retrievals``/``savings.measured_detail_json`` store.
    """
    rows = list(turns or [])
    seed = _int(_field(rows[0], "ctx", 0)) if rows else 0
    end = _int(_field(rows[-1], "ctx", 0)) if rows else 0
    growth = max(0, end - seed)
    r_est = max(0, _int(result_tokens_est))
    o_est = max(0, _int(own_output_est))
    answer = max(0, _int(answer_tokens))
    use = mode if mode in KEPT_OUT_MODES else "results_share"

    share = None
    if use == "results_est":
        raw = float(r_est)
    elif use == "all_growth":
        raw = float(growth - answer)
    else:
        denom = r_est + o_est
        share = (r_est / float(denom)) if denom > 0 else 0.0
        raw = growth * share - answer
    kept = int(max(0.0, raw))
    detail = {
        "mode": use, "seed_ctx": seed, "ctx_at_end": end, "growth_tokens": growth,
        "result_tokens_est": r_est, "own_output_est": o_est, "answer_tokens": answer,
        "results_share": share, "raw": raw, "kept_out_tokens": kept, "n_turns": len(rows),
    }
    return kept, detail


def retrieval_row(run_id, parent_run_id=None, agent_type=None, model=None, turns=None,
                  result_tokens_est=0, own_output_est=0, answer_tokens=0,
                  status="completed", mode="results_share",
                  spawned_at=None, returned_at=None):
    """Build a ``retrievals`` row (design doc B.1) for one retriever run.

    ``void`` is set when the run did not complete or returned nothing: R is kept
    for audit but such a retrieval contributes 0 to ``measured_saved``.
    """
    kept, detail = kept_out_tokens(turns, result_tokens_est, own_output_est,
                                   answer_tokens, mode)
    rows = list(turns or [])
    if spawned_at is None and rows:
        spawned_at = _field(rows[0], "ts_iso", None) or _field(rows[0], "ts", None)
    if returned_at is None and rows:
        returned_at = _field(rows[-1], "ts_iso", None) or _field(rows[-1], "ts", None)
    ok = str(status or "completed").lower() in ("completed", "ok", "end_turn", "success", "handoff")
    void = 0 if (ok and max(0, _int(answer_tokens)) > 0) else 1
    return {
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "agent_type": agent_type,
        "model": model,
        "spawned_at": spawned_at,
        "returned_at": returned_at,
        "seed_ctx": detail["seed_ctx"],
        "ctx_at_end": detail["ctx_at_end"],
        "growth_tokens": detail["growth_tokens"],
        "result_tokens_est": detail["result_tokens_est"],
        "own_output_est": detail["own_output_est"],
        "kept_out_tokens": kept,
        "kept_out_mode": detail["mode"],
        "answer_tokens": detail["answer_tokens"],
        "status": status,
        "void": void,
    }


# --------------------------------------------------------------------------- measured savings

def _count_turns_after(conn, run_id, after, until=None):
    """E's api requests with ``after < ts [<= until]`` (ISO strings compare)."""
    if not after:
        return 0
    sql = "SELECT COUNT(*) FROM turns WHERE run_id=? AND kind='api' AND ts IS NOT NULL AND ts > ?"
    args = [run_id, after]
    if until:
        sql += " AND ts <= ?"
        args.append(until)
    row = conn.execute(sql, args).fetchone()
    return int(row[0] or 0) if row else 0


def kept_out_rows(conn, session_id, exclude_run=None):
    """Every kept-out row of a session (the ``retrievals`` table: retrievers, experts, planners,
    critics, coders), oldest first, void rows dropped: ``[(run_id, returned_at, kept)]``."""
    try:
        rows = conn.execute(
            "SELECT r.run_id, r.returned_at, r.kept_out_tokens FROM retrievals r"
            " LEFT JOIN agent_runs a ON a.run_id = r.run_id"
            " LEFT JOIN agent_runs p ON p.run_id = r.parent_run_id"
            " WHERE COALESCE(a.session_id, p.session_id, r.parent_run_id)=?"
            " AND COALESCE(r.void, 0)=0 AND r.returned_at IS NOT NULL"
            " ORDER BY r.returned_at", (session_id,)).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        rid = _field(r, "run_id")
        if exclude_run and rid == exclude_run:
            continue
        out.append((rid, _field(r, "returned_at"), _int(_field(r, "kept_out_tokens", 0))))
    return out


def prior_kept(conn, session_id, before, exclude_run=None):
    """Tokens the session's already-finished agents keep out of a run that starts at ``before``."""
    return sum(k for (_rid, ret_at, k) in kept_out_rows(conn, session_id, exclude_run)
               if ret_at and before and str(ret_at) < str(before))


def session_requests(conn, session_id):
    """The session's api requests in time order, any run: ``[(ts, run_id, model, account, cold)]``.

    ``cold`` says a single context would have had to read everything from scratch here: the
    first request, a gap longer than the request's cache TTL, or the next request after a
    task-level agent (expert, planner, critic, review, coder) returned to the main run (T11).
    A model switch alone is not cold (``_same_model`` stays for other callers)."""
    try:
        rows = conn.execute(
            "SELECT ts, run_id, model, account, ttl_assumed FROM turns"
            " WHERE session_id=? AND kind='api' AND ts IS NOT NULL ORDER BY ts",
            (session_id,)).fetchall()
    except Exception:
        return []
    # T11: task-level boundaries — a non-void return of an expert, planner, critic, review
    # or coder whose parent_run_id is the session id (the main run)
    boundaries = []
    try:
        brows = conn.execute(
            "SELECT r.returned_at FROM retrievals r"
            " JOIN agent_runs a ON a.run_id = r.run_id"
            " WHERE COALESCE(r.void, 0)=0 AND r.returned_at IS NOT NULL"
            " AND r.parent_run_id=?"
            " AND a.kind IN ('expert','planner','critic','review','coder')",
            (session_id,)).fetchall()
        boundaries = sorted(str(b[0]) for b in brows)
    except Exception:
        pass
    # precompute which request indices are cold due to a boundary: for each boundary,
    # the first request with ts > boundary is cold
    cold_boundary = set()
    if boundaries:
        req_ts = [str(_field(r, "ts")) for r in rows]
        bi = 0
        for b in boundaries:
            while bi < len(req_ts) and req_ts[bi] <= b:
                bi += 1
            if bi < len(req_ts):
                cold_boundary.add(bi)
    out, prev_ts = [], None
    for i, r in enumerate(rows):
        ts = _field(r, "ts")
        cold = True
        if prev_ts is not None:
            e1, e0 = _epoch(ts), _epoch(prev_ts)
            gap = (e1 - e0) if (e1 is not None and e0 is not None) else 0
            cold = gap > _ttl_seconds(_field(r, "ttl_assumed")) or i in cold_boundary
        out.append((ts, _field(r, "run_id"), _field(r, "model"), _field(r, "account"), cold))
        prev_ts = ts
    return out


def _credit(kept, cold, first_here, model, cache):
    """What one request avoided for ``kept`` tokens of one finished agent: the cache write when a
    single context would have carried them for the first time here, or again after a cold point
    (the write rate replaces the input rate, never adds to it); a cache read otherwise."""
    if model not in cache:
        cache[model] = _prices3(model)
    _p_in, p_read, p_write = cache[model]
    return kept * (p_write if (cold or first_here) else p_read) / 1e6


def _turn_write_info(turn, model):
    """Write rate ($/MTok) and TTL label for a turn from its observed cache columns.
    Returns ``(rate, ttl_label)``: ttl_label is ``'1h'``, ``'5m'``, or ``'input'``."""
    cw1h = _int(_field(turn, "cache_write_1h"))
    cw5m = _int(_field(turn, "cache_write_5m"))
    cw = _int(_field(turn, "cache_write"))
    p = prices.price_for(model)
    if not p:
        return 0.0, "input"
    if cw1h > 0:
        return float(p["write_1h"]), "1h"
    if cw5m > 0:
        return float(p["write_5m"]), "5m"
    if cw > 0:
        ttl = _field(turn, "ttl_assumed")
        if _ttl_seconds(ttl) >= 3600:
            return float(p["write_1h"]), "1h"
        return float(p["write_5m"]), "5m"
    return float(p["input"]), "input"


def _turn_rate(turn, is_first, model):
    """Per-turn savings rate ($/MTok) for ttl-cold-v2.
    Returns ``(rate, ttl_label)``: the TTL write price on the first turn after the
    return or on a turn with ``rewrite=1``, the read price otherwise, the input
    price when the turn cached nothing."""
    if is_first or _int(_field(turn, "rewrite")):
        return _turn_write_info(turn, model)
    return price_read(model), None


def _run_api_turns(conn, run_id, session_id, until=None):
    """API turns of *run_id* with cache/rewrite columns, oldest first.
    For main-thread parents (run_id == session_id), includes turns with NULL run_id."""
    cols = "ts, model, cache_write_1h, cache_write_5m, cache_write, ttl_assumed, rewrite"
    if run_id == session_id:
        sql = ("SELECT %s FROM turns WHERE kind='api' AND ts IS NOT NULL"
               " AND session_id=? AND (run_id=? OR run_id IS NULL)" % cols)
        args = [session_id, run_id]
    else:
        sql = ("SELECT %s FROM turns WHERE kind='api' AND ts IS NOT NULL"
               " AND run_id=?" % cols)
        args = [run_id]
    if until:
        sql += " AND ts <= ?"
        args.append(until)
    sql += " ORDER BY ts"
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []


def _model_timeline(conn, run_id):
    """``[(ts, model)]`` for a run's api turns, oldest first (NULL-model fallback)."""
    try:
        rows = conn.execute(
            "SELECT ts, model FROM turns WHERE run_id=? AND kind='api'"
            " AND ts IS NOT NULL ORDER BY ts",
            (run_id,)).fetchall()
        return [(r[0] if not isinstance(r, dict) else r.get("ts"),
                 r[1] if not isinstance(r, dict) else r.get("model"))
                for r in rows]
    except Exception:
        return []


def _nearest_model(timeline, ts):
    """Model of the nearest api turn at or before ``ts``."""
    best = None
    for t_ts, t_model in timeline:
        if ts and t_ts and str(t_ts) <= str(ts):
            best = t_model
        elif ts and t_ts and str(t_ts) > str(ts):
            break
    return best


def _emission_for_run(conn, run_id):
    """Emission term for one run's tool_calls.

    Returns ``(calls, carry_tok, emission_tok, emission_usd, by_account)``
    where ``by_account`` is ``{account: usd}`` attributing each call's
    emission to the account of the nearest api turn at or before the call's
    ts (T9).

    ``emission_usd = Σ emission_tokens × price_output(model)``.
    When a row's ``model`` is NULL the model of the run's nearest api turn at
    or before the call's ``ts`` is used so already-written rows are priced.
    """
    try:
        rows = conn.execute(
            "SELECT emission_tokens, model, ts FROM tool_calls WHERE run_id=?"
            " AND COALESCE(kind, '') != 'outline'",       # an outline's print is not model output
            (run_id,)).fetchall()
    except Exception:
        return 0, 0, 0, 0.0, {}
    calls = len(rows)
    total_emission = 0
    total_carry = 0
    total_usd = 0.0
    by_account = {}
    timeline = None                                        # lazy: only built when needed
    acct_tl = None
    for r in rows:
        et = _int(r[0] if not isinstance(r, dict) else r.get("emission_tokens", 0))
        m = r[1] if not isinstance(r, dict) else r.get("model", "")
        ts = r[2] if not isinstance(r, dict) else r.get("ts", "")
        if not m:
            if timeline is None:
                timeline = _model_timeline(conn, run_id)
            m = _nearest_model(timeline, ts)
        total_emission += et
        usd_val = et * price_output(m) / 1e6
        total_usd += usd_val
        # attribute to the account of the nearest api turn (T9)
        if acct_tl is None:
            acct_tl = _account_timeline(conn, run_id)
        acct = _nearest_model(acct_tl, ts)  # reuse: finds nearest value at-or-before ts
        if acct is not None and usd_val > 0:
            by_account[acct] = by_account.get(acct, 0.0) + usd_val
    # carry from the walk is summed in measured_saved; here just totals for the detail
    return calls, total_carry, total_emission, total_usd, by_account


def _emission_for_window(conn, session_id, t0, t1):
    """Emission term for a session's tool_calls in ``[t0, t1)``.

    Returns ``(emission_tokens, emission_usd, by_account)`` where
    ``by_account`` is ``{account: usd}`` attributing each call's emission
    to the account of the nearest api turn (T9).
    When a row's ``model`` is NULL the session's nearest api turn is used.
    """
    sql = ("SELECT emission_tokens, model, ts, run_id FROM tool_calls"
           " WHERE session_id=? AND ts IS NOT NULL"
           " AND COALESCE(kind, '') != 'outline'")
    args = [session_id]
    if t0:
        sql += " AND ts >= ?"
        args.append(t0)
    if t1:
        sql += " AND ts < ?"
        args.append(t1)
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:
        return 0, 0.0, {}
    total_tok = 0
    total_usd = 0.0
    by_account = {}
    tl_cache = {}                                          # run_id -> timeline
    acct_tl_cache = {}
    for r in rows:
        et = _int(r[0] if not isinstance(r, dict) else r.get("emission_tokens", 0))
        m = r[1] if not isinstance(r, dict) else r.get("model", "")
        ts = r[2] if not isinstance(r, dict) else r.get("ts", "")
        rid = r[3] if not isinstance(r, dict) else r.get("run_id", "")
        if not m:
            if rid not in tl_cache:
                tl_cache[rid] = _model_timeline(conn, rid)
            m = _nearest_model(tl_cache[rid], ts)
        total_tok += et
        usd_val = et * price_output(m) / 1e6
        total_usd += usd_val
        # attribute to the account of the nearest api turn (T9)
        if rid not in acct_tl_cache:
            acct_tl_cache[rid] = _account_timeline(conn, rid)
        acct = _nearest_model(acct_tl_cache[rid], ts)
        if acct is not None and usd_val > 0:
            by_account[acct] = by_account.get(acct, 0.0) + usd_val
    return total_tok, total_usd, by_account


def measured_saved(conn, run_id, until=None, model=None):
    """Measured savings of one run (vanilla-net-v4, 3.5 T9 + 3.6 T3).

    Session-wide walk, one write per result per session.  For each non-void
    kept-out row, the first session turn (any run) after the row's return
    charges R × write_1h(model); every later turn charges R × read(model),
    except when the gap between consecutive session turns exceeds
    ``savings.vanilla_ttl_s`` (default 3600 s) the carried mass is rewritten
    at write_1h.  On each turn the credited mass is capped at
    room = window − ctx_turn.

    Attribution: each row's credits go to the run that spawned it (the row's
    ``parent_run_id``).  Net = gross − seed carry.

    ``model`` stands in for turns that carry none; ``until`` caps the turns
    at a timestamp.

    Returns ``{run_id, session_id, phase, model, price_read, price_write,
    price_input, gross_usd, seed_usd, saved_usd (net), n_retrievals, n_void,
    until, savings_version, per_retrieval:[{run_id, R, N, usd, void, n_cold,
    first_write_ttl, savings_version}]}``.
    """
    run = None
    try:
        run = conn.execute(
            "SELECT run_id, session_id, phase, kind, model_seen, model_pinned"
            " FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
    except Exception:
        run = None
    session_id = _field(run, "session_id") or run_id
    use_model = model or _field(run, "model_seen") or _field(run, "model_pinned")
    p_in, p_read, p_write = _prices3(use_model)

    all_rows = _kept_out_with_parent(conn, session_id)
    all_turns = _session_all_api_turns(conn, session_id, until)
    vtl = _cfg_vanilla_ttl_s()

    per_row, _gross, _ba_all = _vanilla_walk(all_rows, all_turns, vtl,
                                              fallback_model=use_model,
                                              window=_cfg_window_tokens())

    # Filter to rows parented to this run
    gross = 0.0
    ba_gross = {}
    per = {}
    for krid, info in per_row.items():
        if info.get("parent_run_id") == run_id:
            gross += info["usd"]
            for acct, val in info.get("by_account", {}).items():
                ba_gross[acct] = ba_gross.get(acct, 0.0) + val
            per[krid] = {"run_id": krid, "R": info["R"], "N": info["N"],
                         "usd": info["usd"], "void": 0, "n_cold": info["n_cold"],
                         "first_write_ttl": "1h",
                         "savings_version": SAVINGS_VERSION}

    # Void rows parented to this run
    n_void = 0
    try:
        for r in conn.execute("SELECT run_id, kept_out_tokens FROM retrievals"
                              " WHERE parent_run_id=? AND COALESCE(void, 0)=1",
                              (run_id,)).fetchall():
            n_void += 1
            per[_field(r, "run_id")] = {
                "run_id": _field(r, "run_id"),
                "R": _int(_field(r, "kept_out_tokens", 0)),
                "N": 0, "usd": 0.0, "void": 1, "n_cold": 0,
                "first_write_ttl": None,
                "savings_version": SAVINGS_VERSION}
    except Exception:
        pass

    seed_usd = _seed_carry_run(conn, run_id, session_id)
    ba_seed = _seed_carry_by_account(conn, run_id, session_id)

    # Emission term: tool-call output tokens priced at the call's model (v4)
    e_calls, _e_carry, e_tokens, e_usd, ba_emission = _emission_for_run(conn, run_id)
    # Carry tokens attributed to this run through the walk
    carry_tok = sum(info["R"] for info in per.values())

    # Per-account net: gross - seed + emission, split by turn account (T9)
    ba_keys = set(list(ba_gross) + list(ba_seed) + list(ba_emission))
    by_account = {}
    for acct in ba_keys:
        val = (ba_gross.get(acct, 0.0)
               - ba_seed.get(acct, 0.0)
               + ba_emission.get(acct, 0.0))
        if abs(val) > 1e-12:
            by_account[acct] = round(val, 10)

    net = gross - seed_usd + e_usd
    per_list = list(per.values())
    return {
        "run_id": run_id,
        "session_id": session_id,
        "phase": _field(run, "phase"),
        "model": use_model,
        "price_read": p_read,
        "price_write": p_write,
        "price_input": p_in,
        "gross_usd": gross,
        "seed_usd": seed_usd,
        "saved_usd": net,
        "n_retrievals": len(per_list),
        "n_void": n_void,
        "until": until,
        "per_retrieval": per_list,
        "savings_version": SAVINGS_VERSION,
        "by_account": by_account,
        "credit": {
            "calls": e_calls,
            "carry_token_turns": carry_tok,
            "emission_tokens": e_tokens,
            "emission_usd": e_usd,
        },
    }


def live_saved(agent_entry, read_price, write_price=0.0, input_price=None):
    """Provisional savings of a running agent from ``running.json`` (B.2).

    Vanilla-net-v3: starts at minus the run's seed carry so far, then adds
    credits at the read price for each own retrieval (never a write — the
    locked figure uses the session-wide walk which charges write_1h on first
    writes, so locked ≥ provisional holds, D43).  When ``seed_carry_usd`` is
    absent from the entry, the seed is treated as 0.
    """
    entry = agent_entry if isinstance(agent_entry, dict) else {}
    seed_carry = float(entry.get("seed_carry_usd") or 0)
    total = -seed_carry
    rets = entry.get("retrievals")
    if isinstance(rets, dict):
        for item in rets.values():
            if not isinstance(item, dict) or item.get("void"):
                continue
            r = _int(item.get("R"))
            n = _int(item.get("n_after"))
            if r > 0 and n > 0:
                total += r * n * float(read_price or 0.0) / 1e6
    total += float(entry.get("emission_live_usd") or 0)
    return total


def _norm_project(path):
    """Normalize a session's ``project``/``cwd`` path the same way as
    ``pa.summary._project_key`` (T12); a separate three-line copy so this module
    never imports ``pa.summary`` at module scope."""
    text = str(path or "").strip()
    if not text:
        return None
    import os
    return os.path.normcase(os.path.normpath(text))


def window_saved_measured(conn, account, t0, t1, by_family=False, project=None,
                          detail=False, session=None, window=None):
    """Measured savings accrued in ``[t0, t1)`` (vanilla-net-v4, 3.5 T9 + 3.6 T3).

    Session-wide walk restricted to the window: one write per result per
    session, idle-gap rewrites only, window bound, seed carry.  Net is the
    default return (a float); ``detail=True`` returns ``{gross, seeds, net}``.
    ``session``: when given, only that session's turns and rows are walked.
    ``by_family=True`` returns ``{family: net_usd}``.
    ``project``: only sessions whose project normalizes to this key.
    ``window``: carry window in tokens; default ``savings.window_tokens`` (fix-10).
    """
    try:
        sql = ("SELECT DISTINCT t.session_id, s.project, s.cwd FROM turns t "
               "LEFT JOIN sessions s ON s.session_id = t.session_id "
               "WHERE t.kind='api' AND t.session_id IS NOT NULL"
               + (" AND t.account=?" if account else ""))
        srows = conn.execute(sql, ([account] if account else [])).fetchall()
    except Exception:
        if detail:
            return {"gross": 0.0, "seeds": 0.0, "net": 0.0}
        return {} if by_family else 0.0

    if session:
        sids = [session]
    elif project:
        sids = [r[0] for r in srows if _norm_project(r[1] or r[2]) == project]
    else:
        sids = [r[0] for r in srows]

    vtl = _cfg_vanilla_ttl_s()
    win = window or _cfg_window_tokens()
    total_gross = 0.0
    total_seeds = 0.0
    total_emission = 0.0
    by_fam = {} if by_family else None

    for sid in sids:
        rows = _kept_out_with_parent(conn, sid)
        if not rows:
            # Even with no rows, the session may have seed-carrying runs
            pass
        try:
            full = conn.execute(
                "SELECT ts, model, ctx, run_id, cost_usd, account FROM turns"
                " WHERE session_id=? AND kind='api' AND ts IS NOT NULL"
                " ORDER BY ts", (sid,)).fetchall()
        except Exception:
            continue

        per_row, gross, ba_gross = _vanilla_walk(rows, full, vtl, t0=t0, t1=t1, window=win)
        # When account is specified, take only that account's share (T9)
        if account:
            total_gross += ba_gross.get(account, 0.0)
        else:
            total_gross += gross

        # Emission term for tool_calls in [t0, t1) (v4)
        _e_tok, e_usd, ba_emission = _emission_for_window(conn, sid, t0, t1)
        if account:
            total_emission += ba_emission.get(account, 0.0)
        else:
            total_emission += e_usd

        if by_family:
            # Re-walk with family tracking: accumulate per-family gross from
            # per_row by looking at which turns credited which model.
            # Simplified: attribute each row's USD to the family of the row's
            # parent run's model (or fallback to the session's).
            for krid, info in per_row.items():
                from . import fit as _fit
                # Use the first turn's model as representative; exact per-turn
                # family split is expensive and not needed here.
                fam = "unknown"
                parent = info.get("parent_run_id")
                try:
                    pr = conn.execute("SELECT model_seen FROM agent_runs WHERE run_id=?",
                                      (parent,)).fetchone()
                    if pr:
                        fam = _fit.family_of(_field(pr, "model_seen"))
                except Exception:
                    pass
                # Per-account: use the account's share of the row (T9)
                row_usd = info.get("by_account", {}).get(account, info["usd"]) if account else info["usd"]
                by_fam[fam] = by_fam.get(fam, 0.0) + row_usd

        # Seed carry of turns in [t0, t1)
        try:
            runs = conn.execute(
                "SELECT run_id, seed_ctx FROM agent_runs"
                " WHERE session_id=? AND run_id!=?",
                (sid, sid)).fetchall()
        except Exception:
            runs = []
        for r in runs:
            seed = _int(_field(r, "seed_ctx"))
            if seed <= 0:
                continue
            rid = _field(r, "run_id")
            try:
                rturns = conn.execute(
                    "SELECT ts, ctx, cost_usd, account FROM turns"
                    " WHERE run_id=? AND kind='api' AND ts IS NOT NULL",
                    (rid,)).fetchall()
            except Exception:
                continue
            for rt in rturns:
                ts = _field(rt, "ts")
                if (t0 and str(ts) < str(t0)) or (t1 and str(ts) >= str(t1)):
                    continue
                ctx = _int(_field(rt, "ctx"))
                cost = float(_field(rt, "cost_usd") or 0)
                if ctx > 0:
                    seed_val = min(1.0, seed / ctx) * cost
                    # When account is specified, only count this account's turns (T9)
                    acct = _field(rt, "account")
                    if account and acct != account:
                        continue
                    total_seeds += seed_val

    if by_family:
        # Subtract proportional seeds from families (simplified: spread evenly)
        net_base = total_gross + total_emission
        if total_seeds > 0 and net_base > 0:
            for fam in by_fam:
                by_fam[fam] -= total_seeds * by_fam[fam] / net_base
        # Add emission proportionally to families
        if total_emission > 0 and total_gross > 0:
            for fam in list(by_fam):
                by_fam[fam] += total_emission * by_fam.get(fam, 0.0) / total_gross
        return by_fam
    net = total_gross + total_emission - total_seeds
    if detail:
        return {"gross": total_gross, "seeds": total_seeds,
                "emission": total_emission, "net": net}
    return net


def detail_by_account(detail_json, session_account, measured_usd):
    """Extract ``{account: usd}`` from a savings row's per-turn attribution.

    Reads ``by_account`` from the parsed detail when present (T9); falls back
    to attributing the whole ``measured_usd`` to ``session_account``.
    Used by both ``pa.summary.accounts_block`` and ``pa.ledger_cli.cmd_report``
    (one shared helper, no second formula).
    """
    if detail_json:
        try:
            import json as _json
            detail = _json.loads(detail_json) if isinstance(detail_json, str) else detail_json
            if isinstance(detail.get("by_account"), dict):
                return dict(detail["by_account"])
        except (ValueError, TypeError):
            pass
    return {session_account or "(unknown)": float(measured_usd or 0.0)}


def headline_ratio(cost_saved_measured, cost_used):
    """``saved / used`` (spec 3.9.5); ``None`` when nothing was spent."""
    try:
        used = float(cost_used or 0.0)
        saved = float(cost_saved_measured or 0.0)
    except (TypeError, ValueError):
        return None
    if used <= 0.0:
        return None
    return saved / used
