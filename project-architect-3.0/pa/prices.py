"""List prices ($/MTok) and per-request cost (spec 1.2 / 3.9(1), design doc E).

Public:
    PRICES                       seed rows (model_key, priority, effective_from, ...)
    PRICES_VERSION               stamp stored in meta.prices_version
    normalize_model(model)       strip "[1m]" and a -YYYYMMDD suffix, lowercase
    model_key(model)             first matching PRICES key by priority, else None
    price_for(model, at=None)    price row dict, or None when the model is unknown
    tokenizer_for(model, at=None)
    cost_of(usage, model, ttl_default="1h", at=None, long_context=False)
                                  -> (cost_usd, parts)
    usage_fields(usage)          normalize an API/cost-state/ledger usage dict
    rows_for_db()                tuples for db.seed_prices()
    LONG_CONTEXT_TOKENS          200000: the long-context tier threshold

Matching rule (kept identical to the delivered analyzers): the normalized model
id is searched for each ``model_key`` in priority order, so the most specific
key wins -- ``fable-5-1`` before ``fable-5``, ``sonnet-5-5`` before ``sonnet-5``
before ``sonnet``, ``opus-4-6`` before ``opus``.  Within a key the latest row
with ``effective_from <= at`` applies (``at=None`` means "current prices").

Verified against both reference sessions: cost-state tokens x these rates
reproduce ``modelUsage[m].costUSD`` exactly for claude-fable-5-1 (1h writes),
claude-opus-5[1m] (1h writes), claude-sonnet-5 (5m writes) and
claude-haiku-4-5-20251001.

Long-context tier (T8): a 1M-window model (``window="1m"``) bills a request
whose context (input + cache write + cache read) exceeds
``LONG_CONTEXT_TOKENS`` at 2x input/cache/read and 1.5x output (the published
rule); ``cost_of`` never auto-detects this from pooled/session-total usage --
a real per-request caller (``pa.transcript.request_cost``, ``db.turn_row_from_
request``) passes ``long_context`` explicitly, computed from that one
request's own context.  A cost-state pool total (``pa.ledger_cli._price_check``)
stays priced flat: apportioning it between under/over-200k shares from the
transcript's own ratio was tried against the WSL session b2fa9a37 and verified
to only widen the delta (its cost-state tokens include an unknown share of
invisible "subagent overhead" the transcript never sees, T8 log); what fixed
that pool's price check (+6.11 % -> +0.12 %) was a separate, real bug the same
investigation turned up: a lone bracket-tagged model key ("[1m]") does not mean
every call used the 1h TTL -- a workflow's 5m-TTL subagent traffic can carry
the exact same bracketed id as its 1h main thread.
"""

import re

PRICES_VERSION = "2026-09"
LONG_CONTEXT_TOKENS = 200000
LONG_CONTEXT_MULT = {"input": 2.0, "write_5m": 2.0, "write_1h": 2.0, "read": 2.0, "output": 1.5}

# (model_key, priority, effective_from, input, write_5m, write_1h, read, output, tokenizer, window)
# window "1m" marks the 1M-context models the long-context tier applies to.
PRICES = [
    ("fable-5-1",  10, "2026-09-01", 10.0, 12.50, 20.0, 0.25, 50.0, "new", "1m"),
    ("fable-5",    20, "2026-09-01", 10.0, 12.50, 20.0, 1.00, 50.0, "new", None),
    ("opus-4-6",   30, "2026-09-01",  5.0,  6.25, 10.0, 0.50, 25.0, "old", "1m"),
    ("opus-4-7",   40, "2026-09-01",  5.0,  6.25, 10.0, 0.50, 25.0, "new", "1m"),
    ("opus-4-8",   41, "2026-09-01",  5.0,  6.25, 10.0, 0.50, 25.0, "new", "1m"),
    ("opus-5-5",   41, "2026-09-01",  4.0,  5.00,  8.0, 0.20, 20.0, "new", "1m"),
    ("opus-5",     42, "2026-09-01",  5.0,  6.25, 10.0, 0.50, 25.0, "new", "1m"),
    ("opus",       50, "2026-09-01",  5.0,  6.25, 10.0, 0.50, 25.0, "new", None),
    ("sonnet-5-5", 59, "2026-09-28",  2.0,  2.50,  4.0, 0.20, 10.0, "new", "1m"),
    ("sonnet-5",   60, "2026-09-01",  2.0,  2.50,  4.0, 0.20, 10.0, "new", "1m"),
    ("sonnet-4-6", 70, "2026-09-01",  3.0,  3.75,  6.0, 0.30, 15.0, "old", None),
    ("sonnet",     80, "2026-09-01",  3.0,  3.75,  6.0, 0.30, 15.0, "old", None),
    ("haiku-4-5",  90, "2026-09-01",  1.0,  1.25,  2.0, 0.10,  5.0, "old", None),
    ("haiku",     100, "2026-09-01",  1.0,  1.25,  2.0, 0.10,  5.0, "old", None),
]

_FIELDS = ("model_key", "priority", "effective_from",
           "input", "write_5m", "write_1h", "read", "output", "tokenizer", "window")

_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_NORM_CACHE = {}
_KEY_CACHE = {}


def _row(tup):
    return dict(zip(_FIELDS, tup))


def normalize_model(model):
    """Lowercase the model id and strip ``[1m]``-style tags and date suffixes.

    ``claude-haiku-4-5-20251001`` -> ``claude-haiku-4-5``;
    ``claude-opus-5[1m]`` -> ``claude-opus-5``.
    """
    raw = str(model or "").strip()
    hit = _NORM_CACHE.get(raw)
    if hit is not None:
        return hit
    m = _BRACKET_RE.sub("", raw.lower()).strip()
    m = m.replace(" ", "")
    m = _DATE_SUFFIX_RE.sub("", m)
    if len(_NORM_CACHE) < 512:
        _NORM_CACHE[raw] = m
    return m


def model_key(model):
    """The first ``PRICES`` key contained in the normalized id, or None."""
    m = normalize_model(model)
    if not m:
        return None
    hit = _KEY_CACHE.get(m)
    if hit is not None:
        return hit or None
    found = ""
    for tup in sorted(PRICES, key=lambda r: r[1]):
        if tup[0] in m:
            found = tup[0]
            break
    if len(_KEY_CACHE) < 512:
        _KEY_CACHE[m] = found
    return found or None


def _at_key(at):
    """Normalize ``at`` (datetime | ISO string | None) to a comparable date str."""
    if at is None:
        return None
    if hasattr(at, "strftime"):
        return at.strftime("%Y-%m-%d")
    s = str(at)
    return s[:10] if len(s) >= 10 else s


def price_for(model, at=None):
    """Price row for ``model`` effective at ``at`` (latest row when at=None)."""
    key = model_key(model)
    if not key:
        return None
    rows = [_row(t) for t in PRICES if t[0] == key]
    rows.sort(key=lambda r: r["effective_from"])
    when = _at_key(at)
    chosen = None
    for r in rows:
        if when is None or r["effective_from"] <= when:
            chosen = r
    if chosen is None:                      # ``at`` predates every row: use the oldest
        chosen = rows[0]
    return dict(chosen)


def tokenizer_for(model, at=None):
    """``"old"`` (pre-4.7) or ``"new"`` (4.7+) tokenizer generation, or None."""
    p = price_for(model, at)
    return p["tokenizer"] if p else None


def _num(*vals):
    for v in vals:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return v
    return 0


def usage_fields(usage):
    """Normalize an API ``usage`` / ``cost-state`` / ledger row into one shape.

    Returns ``{input, cache_write, cache_write_5m, cache_write_1h, cache_read,
    output, thinking, split}`` where ``split`` says whether the 5m/1h breakdown
    came from the record (``usage.cache_creation``) rather than a TTL default.
    """
    u = usage if isinstance(usage, dict) else {}
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    details = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}

    inp = _num(u.get("input_tokens"), u.get("input"), u.get("inputTokens"))
    write = _num(u.get("cache_creation_input_tokens"), u.get("cache_write"),
                 u.get("cacheCreationInputTokens"))
    read = _num(u.get("cache_read_input_tokens"), u.get("cache_read"),
                u.get("cacheReadInputTokens"))
    out = _num(u.get("output_tokens"), u.get("output"), u.get("outputTokens"))
    think = _num(details.get("thinking_tokens"), u.get("thinking"), u.get("thinkingTokens"))

    w5 = cc.get("ephemeral_5m_input_tokens") if cc else None
    w1 = cc.get("ephemeral_1h_input_tokens") if cc else None
    if w5 is None and w1 is None:
        w5 = u.get("cache_write_5m")
        w1 = u.get("cache_write_1h")
    split = w5 is not None or w1 is not None
    if split:
        w5 = _num(w5)
        w1 = _num(w1)
        if not write:
            write = w5 + w1
    else:
        w5 = None
        w1 = None
    return {"input": inp, "cache_write": write, "cache_write_5m": w5, "cache_write_1h": w1,
            "cache_read": read, "output": out, "thinking": think, "split": split}


def cost_of(usage, model, ttl_default="1h", at=None, long_context=False):
    """Cost of one request in USD (spec 3.9(1)).

    ``cost = input*P_in + cw5*P_w5 + cw1*P_w1 + cr*P_r + output*P_out`` at the
    request's model.  When ``usage`` carries no ``cache_creation`` split the
    whole cache-write is priced by ``ttl_default`` ("1h" for main/expert/coder,
    "5m" for retriever/critic/other -- see ``config.ttl_for_role``).

    ``long_context`` (T8): when true *and* the model's price row is a 1M-window
    model (``window="1m"``), every bucket is priced at the long-context tier
    (``LONG_CONTEXT_MULT``) instead of the base rate.  ``cost_of`` never infers
    this itself from ``usage`` -- ``usage`` may be one real request or a pooled
    multi-request total (a cost-state ``modelUsage`` entry), and only the caller
    knows which; a real per-request caller passes ``long_context = ctx >
    LONG_CONTEXT_TOKENS`` for *that* request's own context (``pa.transcript.
    request_cost`` does this).  Default ``False`` keeps every existing caller
    (including a pooled cost-state total, whose summed context is meaningless)
    priced exactly as before.

    Returns ``(cost_usd, parts)``; ``parts`` carries the per-bucket dollars plus
    ``ttl`` (the TTL actually used), ``model_key``, ``long_context`` (whether the
    tier was actually applied) and the token counts.  ``cost_usd`` is 0.0 and
    ``parts["priced"]`` is False for an unknown model.
    """
    f = usage_fields(usage)
    p = price_for(model, at)
    ttl = "1h" if str(ttl_default).lower() in ("1h", "3600", "hour") else "5m"
    if f["split"]:
        w5, w1, ttl_used = f["cache_write_5m"] or 0, f["cache_write_1h"] or 0, "split"
    elif ttl == "1h":
        w5, w1, ttl_used = 0, f["cache_write"], "1h"
    else:
        w5, w1, ttl_used = f["cache_write"], 0, "5m"

    applied = bool(long_context) and bool(p) and p.get("window") == "1m"
    parts = {"input": 0.0, "write_5m": 0.0, "write_1h": 0.0, "read": 0.0, "output": 0.0,
             "ttl": ttl_used, "model_key": p["model_key"] if p else None,
             "priced": bool(p), "long_context": applied, "tokens": dict(f)}
    if not p:
        return 0.0, parts
    mult = LONG_CONTEXT_MULT if applied else {}
    parts["input"] = f["input"] * p["input"] * mult.get("input", 1.0) / 1e6
    parts["write_5m"] = w5 * p["write_5m"] * mult.get("write_5m", 1.0) / 1e6
    parts["write_1h"] = w1 * p["write_1h"] * mult.get("write_1h", 1.0) / 1e6
    parts["read"] = f["cache_read"] * p["read"] * mult.get("read", 1.0) / 1e6
    parts["output"] = f["output"] * p["output"] * mult.get("output", 1.0) / 1e6
    cost = parts["input"] + parts["write_5m"] + parts["write_1h"] + parts["read"] + parts["output"]
    parts["total"] = cost
    return cost, parts


def rows_for_db():
    """Seed rows for the ledger's ``prices`` table (db.seed_prices)."""
    return [(t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8]) for t in PRICES]
