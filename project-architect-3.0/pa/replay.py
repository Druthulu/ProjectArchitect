"""Rolling vs-monolithic replay: one virtual context per account across sessions.

Public:
    account_turns(conn, account) -> (turns, seeds)
    rolling(turns, seeds, params) -> list of per-turn dicts
    session_sums(results) -> {session_id: modeled_saved}
    sum_since(results, iso_ts) -> float
    assumptions(params, results) -> dict
    params_from_cfg(cfg) -> dict
    bases(cfg, conn) -> {"vanilla": params}   (plain 1M window that cycles, T30)

The replay runs only in ``summary.rebuild`` and in the report, never in the
statusline hot path.  Measured and modeled are never summed (D39).

See also: ``pa/savings.py`` (measured savings, the other half of the ledger).
"""

import calendar
import time

from . import prices


def _parse_epoch(ts):
    """Parse an ISO 8601 timestamp to epoch seconds."""
    if not ts:
        return 0.0
    try:
        t = time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")
        return float(calendar.timegm(t))
    except (ValueError, IndexError):
        return 0.0


def account_turns(conn, account):
    """All api turns for *account* sorted by (ts, run_id, msg_id), plus run seeds.

    Returns ``(turns, seeds)`` where *turns* is a list of dicts and *seeds* is
    ``{run_id: seed_ctx}``.
    """
    rows = conn.execute(
        "SELECT msg_id, session_id, run_id, ts, model, new_tokens, output, cost_usd "
        "FROM turns WHERE kind='api' AND account IS ? AND ts IS NOT NULL "
        "ORDER BY ts, run_id, msg_id", (account,)).fetchall()
    turns = []
    for r in rows:
        ts = r["ts"]
        turns.append({
            "msg_id": r["msg_id"], "session_id": r["session_id"],
            "run_id": r["run_id"], "ts": ts, "epoch": _parse_epoch(ts),
            "model": r["model"], "new_tokens": int(r["new_tokens"] or 0),
            "output": int(r["output"] or 0), "cost_usd": float(r["cost_usd"] or 0.0),
        })
    seed_rows = conn.execute(
        "SELECT run_id, seed_ctx FROM agent_runs WHERE session_id IN "
        "(SELECT DISTINCT session_id FROM turns WHERE kind='api' AND account IS ?)",
        (account,)).fetchall()
    seeds = {r["run_id"]: int(r["seed_ctx"] or 0) for r in seed_rows}
    return turns, seeds


def rolling(turns, seeds, params):
    """Walk every turn with one virtual context, returning per-turn modeled cost.

    *params* keys: ``window``, ``ttl_old_s``, ``subtract_agent_seeds``.
    The context is a plain window that cycles: it starts at 0, and a turn
    whose new tokens would overflow ``window`` resets it to 0 first (no seed
    write, no compact).  Deterministic: no clocks, no dict-order dependence.
    Sets ``params["_n_unpriced"]`` as a side effect for :func:`assumptions`.
    """
    # T30: seed_old/old_reset retired; the counterfactual is a plain 1M cycle.
    window = int(params.get("window", 1000000))
    ttl_old_s = int(params.get("ttl_old_s", 3600))
    subtract_seeds = bool(params.get("subtract_agent_seeds", True))

    ctx = 0
    prev_epoch = None
    seen_runs = set()
    n_unpriced = 0
    results = []

    for turn in turns:
        p = prices.price_for(turn["model"])
        new = int(turn.get("new_tokens") or 0)
        output = int(turn.get("output") or 0)
        run_id = turn.get("run_id")
        epoch = turn.get("epoch", 0.0)

        # subtract seed_ctx on first turn of a run when subtract_agent_seeds
        first_of_run = run_id is not None and run_id not in seen_runs
        if first_of_run:
            seen_runs.add(run_id)
            if subtract_seeds:
                sc = seeds.get(run_id, 0)
                if sc:
                    new = max(0, new - sc)

        cold = False
        reset = False

        if ctx + new > window:
            reset = True
            ctx = 0

        if p is None:
            # unpriced model: cost_replay = cost_usd, modeled_saved = 0
            n_unpriced += 1
            cost_replay = float(turn.get("cost_usd") or 0.0)
            modeled_saved = 0.0
            ctx += new
        else:
            read_price = float(p.get("read", 0.0))
            write_1h = float(p.get("write_1h", 0.0))
            out_price = float(p.get("output", 0.0))

            cold = prev_epoch is not None and (epoch - prev_epoch) > ttl_old_s

            if cold:
                prefix_cost = ctx * write_1h / 1e6
            else:
                prefix_cost = ctx * read_price / 1e6

            new_cost = new * write_1h / 1e6
            out_cost = output * out_price / 1e6
            cost_replay = prefix_cost + new_cost + out_cost

            ctx += new

            modeled_saved = cost_replay - float(turn.get("cost_usd") or 0.0)

        prev_epoch = epoch

        results.append({
            "msg_id": turn.get("msg_id"),
            "session_id": turn.get("session_id"),
            "ts": turn.get("ts"),
            "cost_replay": round(cost_replay, 6),
            "modeled_saved": round(modeled_saved, 6),
            "ctx_after": ctx,
            "cold": cold,
            "reset": reset,
        })

    params["_n_unpriced"] = n_unpriced
    return results


def session_sums(results):
    """``{session_id: Σ modeled_saved}``."""
    out = {}
    for r in results:
        sid = r["session_id"]
        out[sid] = out.get(sid, 0.0) + r["modeled_saved"]
    return out


def sum_since(results, iso_ts):
    """Σ modeled_saved over turns with ``ts >= iso_ts``."""
    total = 0.0
    for r in results:
        if r["ts"] and r["ts"] >= str(iso_ts):
            total += r["modeled_saved"]
    return total


def assumptions(params, results):
    """Build the assumptions dict from *params* and *results*."""
    first_ts = None
    last_ts = None
    for r in results:
        ts = r.get("ts")
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
    return {
        "window": int(params.get("window", 1000000)),
        "ttl_old_s": int(params.get("ttl_old_s", 3600)),
        "subtract_agent_seeds": bool(params.get("subtract_agent_seeds", True)),
        "prices_version": prices.PRICES_VERSION,
        "n_turns": len(results),
        "n_unpriced": int(params.get("_n_unpriced", 0)),
        "span": [first_ts, last_ts],
    }


def params_from_cfg(cfg):
    """Read the ``modeled`` params from config (``window`` = ``savings.window_tokens``)."""
    from . import config as _config

    return {
        "window": int(_config.get(cfg, "savings.window_tokens", 1000000) or 1000000),
        "ttl_old_s": _config.get(cfg, "modeled.ttl_old_s", 3600),
        "subtract_agent_seeds": _config.get(cfg, "modeled.subtract_agent_seeds", True),
    }


# --------------------------------------------------------------------------- bases
# T30: the seed-derived vanilla and the pa2 set retired (plain 1M cycle).

def bases(cfg, conn):
    """One replay parameter set: vanilla, the plain 1M window that cycles.

    Returns ``{"vanilla": params}``.  *params* carries ``window``,
    ``ttl_old_s``, ``subtract_agent_seeds`` and ``_source`` (a dict describing
    where each value came from).  *conn* is unused (kept for the call shape).
    """
    vanilla = params_from_cfg(cfg)
    vanilla["ttl_old_s"] = 3600
    vanilla["_source"] = {"window": "savings.window_tokens", "ttl_old_s": "fixed 1h"}
    return {"vanilla": vanilla}
