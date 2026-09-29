"""Streaming reader for Claude Code transcripts (design doc A.1/F, recon V1).

Public:
    CPT, MISS_FRACTION, MISS_MIN_CTX, RESET_FRACTION, USAGE_KEYS
    parse_ts(s), iso(dt), fmt_gap(seconds), est(obj), blocks(message)
    iter_records(path, start_offset=0, with_offset=False)
    iter_requests(path, start_offset=0, cold_gap_s=3600)
    read_new_requests(path, start_offset=0, cold_gap_s=3600) -> (requests, offset)
    rewrite_rule(index, ctx, cache_write)
    request_cost(req, ttl_default="5m", at=None) -> (cost_usd, parts)
    tail_last_usage(path, tail_bytes=131072)
    cost_state_last(path, tail_bytes=262144, full_scan=True)
    cost_state_fresh(path) -> (record, fresh, offset)
    subagent_files(sid_dir), find_agent_transcript(sid_dir, agent_id),
    workflow_id_of(path), agent_id_from_path(path), read_meta(agent_jsonl)
    tool_result_tokens(path, start_offset=0)

Extracted from PA3's own analysis scripts ``session_decompose.py`` and
``context_reconcile.py`` (the dev repo's ``tools/analysis``) with one correction that both analyzers now carry:
usage is deduped by ``message.id`` keeping the **LAST** jsonl line of a message.
Subagent transcripts store a placeholder ``output_tokens`` on the first line of a
multi-block message and the real value on the last (recon V1: keep-first
undercounts subagent output 3x).

Everything here streams line by line and is bounded by the longest line in the
file: a 256 MB transcript must never be loaded into memory.  ``tail_last_usage``
and ``cost_state_last`` read only the tail and are O(1) in file size.
"""

import datetime as dt
import json
import os
import re

from . import prices

CPT = 4                    # chars per token, rough estimate for unpriced content
RESET_FRACTION = 0.5       # context below this fraction of the prior max => reset
MISS_FRACTION = 0.5        # cache_creation above this fraction of ctx => prefix rewritten
MISS_MIN_CTX = 20_000      # ignore tiny contexts when flagging misses/resets

USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens", "output_tokens")

_AGENT_RE = re.compile(r"agent-([0-9a-fA-F]+)\.jsonl$")


# --------------------------------------------------------------------------- tiny helpers

def parse_ts(s):
    """ISO-8601 (with ``Z``) -> aware datetime, or None."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def iso(when=None):
    """UTC ISO-8601 with a ``Z`` suffix (``None`` -> now)."""
    if when is None:
        when = dt.datetime.now(dt.timezone.utc)
    if isinstance(when, (int, float)):
        when = dt.datetime.fromtimestamp(when, dt.timezone.utc)
    if getattr(when, "tzinfo", None) is not None:
        when = when.astimezone(dt.timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def fmt_gap(seconds):
    """``4200`` -> ``1h10m``; ``None`` -> ``?`` (analyzer formatting)."""
    if seconds is None:
        return "?"
    m = int(seconds // 60)
    return "%dh%02dm" % (m // 60, m % 60) if m >= 60 else "%dm" % m


def est(x):
    """chars/4 token estimate for a string or any JSON-shaped object."""
    if x is None:
        return 0
    if isinstance(x, str):
        return len(x) // CPT
    try:
        return len(json.dumps(x, ensure_ascii=False)) // CPT
    except (TypeError, ValueError):
        return len(str(x)) // CPT


def blocks(msg):
    """Content blocks of a message (a bare string counts as one text block)."""
    c = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def rewrite_rule(index, ctx, cache_write):
    """True when this request rewrote most of its prefix (cold cache).

    The analyzers' rule: not the first request, ``ctx > MISS_MIN_CTX`` and
    ``cache_write > MISS_FRACTION * ctx``.
    """
    return bool(index > 0 and ctx > MISS_MIN_CTX and cache_write > MISS_FRACTION * ctx)


# --------------------------------------------------------------------------- record stream

def iter_records(path, start_offset=0, with_offset=False):
    """Yield JSON objects from a ``.jsonl`` transcript, streaming.

    ``start_offset`` resumes at a byte offset (the Stop hook stores one per
    session).  Undecodable or truncated lines are skipped.  With
    ``with_offset=True`` each item is ``(offset_after_line, record)`` so the
    caller can persist an offset that always lands on a record boundary.
    """
    try:
        fh = open(path, "rb")
    except OSError:
        return
    try:
        pos = 0
        if start_offset:
            try:
                size = os.fstat(fh.fileno()).st_size
            except OSError:
                size = 0
            pos = max(0, min(int(start_offset), size))
            fh.seek(pos)
        for raw in fh:
            pos += len(raw)
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            yield (pos, rec) if with_offset else rec
    finally:
        fh.close()


# --------------------------------------------------------------------------- requests

def _usage_of(rec):
    """(message, usage) when the record is a real, billable assistant request."""
    msg = rec.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return None, None
    u = msg.get("usage")
    if not isinstance(u, dict) or not u:
        return None, None
    if (msg.get("model") or "") == "<synthetic>":
        return None, None
    for k in USAGE_KEYS:
        v = u.get(k)
        if isinstance(v, (int, float)) and v:
            return msg, u
    return None, None


def _apply_usage(req, usage):
    """Overwrite a request's token fields with a later line's usage (keep-LAST)."""
    cc = usage.get("cache_creation")
    cc = cc if isinstance(cc, dict) else None
    details = usage.get("output_tokens_details")
    details = details if isinstance(details, dict) else None

    req["input"] = int(usage.get("input_tokens") or 0)
    req["cache_write"] = int(usage.get("cache_creation_input_tokens") or 0)
    req["cache_read"] = int(usage.get("cache_read_input_tokens") or 0)
    req["output"] = int(usage.get("output_tokens") or 0)
    if cc is not None:
        req["cache_write_5m"] = int(cc.get("ephemeral_5m_input_tokens") or 0)
        req["cache_write_1h"] = int(cc.get("ephemeral_1h_input_tokens") or 0)
        req["split"] = True
    else:
        req["cache_write_5m"] = None
        req["cache_write_1h"] = None
        req["split"] = False
    req["thinking"] = int(details.get("thinking_tokens")) if details and details.get("thinking_tokens") is not None else None
    req["service_tier"] = usage.get("service_tier")
    return req


def _new_request(rec, msg, usage):
    ts = parse_ts(rec.get("timestamp"))
    req = {
        "msg_id": msg.get("id") or rec.get("requestId") or rec.get("uuid"),
        "request_id": rec.get("requestId"),
        "uuid": rec.get("uuid"),
        "session_id": rec.get("sessionId"),
        "ts": ts,
        "ts_iso": iso(ts) if ts else None,
        "model": msg.get("model"),
        "effort": rec.get("effort") if isinstance(rec.get("effort"), str) else None,
        "side": bool(rec.get("isSidechain")),
        "stop_reason": msg.get("stop_reason"),
        "index": 0, "gap_s": None, "cold": False, "rewrite": False,
        "ctx": 0, "new_tokens": 0,
    }
    return _apply_usage(req, usage)


def _finish(req, index, prev_ts, cold_gap_s):
    req["index"] = index
    req["ctx"] = req["input"] + req["cache_write"] + req["cache_read"]
    req["new_tokens"] = req["input"] + req["cache_write"]
    if prev_ts is not None and req["ts"] is not None:
        try:
            req["gap_s"] = (req["ts"] - prev_ts).total_seconds()
        except TypeError:
            req["gap_s"] = None
    req["cold"] = bool(cold_gap_s and req["gap_s"] is not None and req["gap_s"] > cold_gap_s)
    req["rewrite"] = rewrite_rule(index, req["ctx"], req["cache_write"])
    return req


def iter_requests(path, start_offset=0, cold_gap_s=3600):
    """Yield one dict per API request, deduped by ``message.id`` (keep-LAST).

    Keys: ``msg_id request_id uuid session_id ts ts_iso model side stop_reason
    index input cache_write cache_write_5m cache_write_1h split cache_read
    output thinking ctx new_tokens gap_s cold rewrite``.

    ``ctx = input + cache_write + cache_read`` and
    ``new_tokens = input + cache_write`` (what the API had to ingest fresh).
    ``cache_write_5m/1h`` are None when the record carries no
    ``usage.cache_creation`` split -- price those with a TTL default.
    ``cold`` is True when the gap to the previous request of this file exceeds
    ``cold_gap_s`` (the cache TTL); ``rewrite`` applies :func:`rewrite_rule`.
    ``<synthetic>`` models and zero-usage lines are skipped.
    """
    pending = None
    seen = set()
    index = 0
    prev_ts = None
    for rec in iter_records(path, start_offset):
        msg, usage = _usage_of(rec)
        if msg is None:
            continue
        mid = msg.get("id") or rec.get("requestId") or rec.get("uuid")
        if pending is not None and pending["msg_id"] == mid:
            _apply_usage(pending, usage)                      # keep-LAST
            if msg.get("stop_reason"):
                pending["stop_reason"] = msg.get("stop_reason")
            continue
        if pending is not None:
            yield _finish(pending, index, prev_ts, cold_gap_s)
            index += 1
            prev_ts = pending["ts"] or prev_ts
            pending = None
        if mid in seen:                                       # non-contiguous repeat
            continue
        seen.add(mid)
        pending = _new_request(rec, msg, usage)
    if pending is not None:
        yield _finish(pending, index, prev_ts, cold_gap_s)


def read_new_requests(path, start_offset=0, cold_gap_s=3600):
    """``(requests, end_offset)`` for incremental tails (Stop / SessionEnd).

    ``end_offset`` is the byte offset after the last *complete* line, so a
    transcript being appended to while we read never loses or duplicates a row.
    """
    end = int(start_offset or 0)
    reqs = []
    pending = None
    seen = set()
    index = 0
    prev_ts = None
    for offset, rec in iter_records(path, start_offset, with_offset=True):
        end = offset
        msg, usage = _usage_of(rec)
        if msg is None:
            continue
        mid = msg.get("id") or rec.get("requestId") or rec.get("uuid")
        if pending is not None and pending["msg_id"] == mid:
            _apply_usage(pending, usage)
            if msg.get("stop_reason"):
                pending["stop_reason"] = msg.get("stop_reason")
            continue
        if pending is not None:
            reqs.append(_finish(pending, index, prev_ts, cold_gap_s))
            index += 1
            prev_ts = pending["ts"] or prev_ts
            pending = None
        if mid in seen:
            continue
        seen.add(mid)
        pending = _new_request(rec, msg, usage)
    if pending is not None:
        reqs.append(_finish(pending, index, prev_ts, cold_gap_s))
    return reqs, end


def request_cost(req, ttl_default="5m", at=None):
    """``prices.cost_of`` for a request dict from :func:`iter_requests``.

    T8: does *not* pass ``long_context`` even for a request whose own ``ctx``
    crosses ``prices.LONG_CONTEXT_TOKENS``.  Verified against two real >200k
    sessions (Vantage claude-fable-5-1, 442/456 requests over the threshold;
    WSL b2fa9a37 claude-opus-5[1m]): this ledger's cost-state totals track the
    *base* rate, not the published long-context surcharge -- applying the tier
    here overshot Vantage's actual cost-state total by +83 % (T8 log).  The
    tier stays real and tested in ``prices.cost_of`` for a caller that does
    have a request genuinely billed at it; this ledger's own request costing
    does not.
    """
    return prices.cost_of(req, req.get("model"), ttl_default=ttl_default,
                          at=at or req.get("ts_iso"))


# --------------------------------------------------------------------------- tail reads

def _tail_lines(path, window, size):
    """Complete lines from the last ``window`` bytes, plus the start offset."""
    start = max(0, size - window)
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read()
    except OSError:
        return [], 0
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]                    # drop the partial first line
    return lines, start


def tail_last_usage(path, tail_bytes=131072, max_bytes=8 * 1024 * 1024):
    """Last assistant ``usage`` in the file, read from the tail only.

    Returns the same dict shape as :func:`iter_requests` (without ``index`` /
    ``gap_s`` context) or ``None``.  Because usage is keep-LAST, the final
    matching line *is* the final usage of that message -- this is the O(1) read
    the PostToolUse hook uses for ``ctx`` and the handoff threshold.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if not size:
        return None
    window = max(1024, min(int(tail_bytes), size))
    while True:
        lines, start = _tail_lines(path, window, size)
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw or b'"usage"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            msg, usage = _usage_of(rec)
            if msg is None:
                continue
            req = _new_request(rec, msg, usage)
            req["ctx"] = req["input"] + req["cache_write"] + req["cache_read"]
            req["new_tokens"] = req["input"] + req["cache_write"]
            return req
        if start == 0 or window >= max_bytes:
            return None
        window = min(window * 8, max_bytes, size)


def cost_state_last(path, tail_bytes=262144, max_bytes=8 * 1024 * 1024, full_scan=True):
    """Last ``cost-state`` record (session cost ground truth), tail-first.

    ``cost-state`` is written once per turn, so the tail almost always holds
    one.  When it does not and ``full_scan`` is set, the file is streamed once
    (memory-bounded) keeping the last match; otherwise ``None`` is returned.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if not size:
        return None
    window = max(4096, min(int(tail_bytes), size))
    while True:
        lines, start = _tail_lines(path, window, size)
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw or b'"cost-state"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict) and rec.get("type") == "cost-state":
                return rec
        if start == 0 or window >= max_bytes:
            break
        window = min(window * 8, max_bytes, size)
    if not full_scan:
        return None
    last = None
    for rec in iter_records(path):
        if rec.get("type") == "cost-state":
            last = rec
    return last


def cost_state_fresh(path):
    """``(record, fresh, offset)`` -- the last ``cost-state`` with a freshness check.

    Scans tail-first for the last ``cost-state`` record (reusing
    :func:`cost_state_last`'s logic), records its byte offset, then reads
    forward from that offset: if any later assistant record carries usage
    (via :func:`_usage_of`), the record is stale.  Cheap: ≤ 200 ms on a
    50 MB file when the record is fresh (the forward read is short).

    Returns ``(None, False, 0)`` when no ``cost-state`` exists.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, False, 0
    if not size:
        return None, False, 0
    # -- find the last cost-state tail-first, with the byte offset after its line
    last_rec = None
    last_offset = 0
    window = max(4096, min(262144, size))
    while last_rec is None:
        lines, start = _tail_lines(path, window, size)
        end = size
        for raw in reversed(lines):
            line_start = end - len(raw)
            if raw.strip() and b'"cost-state"' in raw:
                try:
                    rec = json.loads(raw.strip())
                except (ValueError, UnicodeDecodeError):
                    rec = None
                if isinstance(rec, dict) and rec.get("type") == "cost-state":
                    last_rec, last_offset = rec, end
                    break
            end = line_start - 1                        # the newline before this line
        if last_rec is None:
            if start == 0 or window >= 8 * 1024 * 1024:
                break
            window = min(window * 8, 8 * 1024 * 1024, size)
    if last_rec is None:                                # not in the last 8 MB: full scan
        for offset, rec in iter_records(path, start_offset=0, with_offset=True):
            if rec.get("type") == "cost-state":
                last_rec, last_offset = rec, offset
        if last_rec is None:
            return None, False, 0
    # -- forward read from the record's end offset: any later assistant with usage?
    for rec in iter_records(path, start_offset=last_offset):
        msg, usage = _usage_of(rec)
        if msg is not None:
            return last_rec, False, last_offset
    return last_rec, True, last_offset


# --------------------------------------------------------------------------- subagents

def _subagents_dir(sid_dir):
    """Resolve ``sid_dir`` to the ``subagents/`` directory path."""
    d = str(sid_dir or "")
    if d.lower().endswith(".jsonl"):
        d = d[:-6]
    if os.path.basename(d) != "subagents":
        d = os.path.join(d, "subagents")
    return d


def subagent_files(sid_dir):
    """Sorted ``agent-*.jsonl`` paths for a session (recursive).

    Walks ``<sid>/subagents/`` recursively so that workflow agents under
    ``subagents/workflows/<wf_id>/`` are included alongside the flat ones.

    ``sid_dir`` may be the session directory (``…/<slug>/<sid>``), its
    ``subagents`` subdirectory, or the main transcript path
    (``…/<slug>/<sid>.jsonl``).
    """
    d = _subagents_dir(sid_dir)
    out = []
    try:
        for dirpath, _dirs, files in os.walk(d):
            for n in files:
                if n.startswith("agent-") and n.endswith(".jsonl"):
                    out.append(os.path.join(dirpath, n))
    except OSError:
        return []
    out.sort()
    return out


def find_agent_transcript(sid_dir, agent_id):
    """Return the path for one agent's transcript, or ``None``.

    Checks the flat path first (``subagents/agent-<id>.jsonl``); when absent,
    scans ``subagents/workflows/*/`` with one ``listdir`` per directory — no
    full ``os.walk``.
    """
    if not agent_id:
        return None
    d = _subagents_dir(sid_dir)
    flat = os.path.join(d, "agent-%s.jsonl" % agent_id)
    if os.path.exists(flat):
        return flat
    wf_dir = os.path.join(d, "workflows")
    try:
        wf_names = os.listdir(wf_dir)
    except OSError:
        return None
    target = "agent-%s.jsonl" % agent_id
    for wf in wf_names:
        wf_path = os.path.join(wf_dir, wf)
        try:
            if target in os.listdir(wf_path):
                return os.path.join(wf_path, target)
        except OSError:
            continue
    return None


def workflow_id_of(path):
    """Return the workflow directory name (``wf_…``) or ``None``.

    A workflow agent lives at ``subagents/workflows/<wf_id>/agent-<id>.jsonl``;
    a flat agent at ``subagents/agent-<id>.jsonl``.
    """
    p = str(path or "").replace("\\", "/")
    parts = p.rsplit("/", 3)      # …, workflows, <wf_id>, agent-<id>.jsonl
    if len(parts) >= 3 and parts[-3] == "workflows":
        return parts[-2]
    return None


def agent_id_from_path(path):
    """``…/agent-abfdf578000000016.jsonl`` -> ``abfdf578000000016``."""
    m = _AGENT_RE.search(str(path or "").replace("\\", "/"))
    return m.group(1) if m else None


def read_meta(agent_jsonl):
    """Sibling ``agent-<id>.meta.json`` as a dict ({} when absent).

    Keys seen on this machine: ``agentType, description, toolUseId, spawnDepth,
    model, requestShape, requestNonInteractive``.
    """
    p = str(agent_jsonl or "")
    if p.lower().endswith(".jsonl"):
        p = p[:-6] + ".meta.json"
    else:
        p = p + ".meta.json"
    try:
        with open(p, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- estimates

def _result_content_tokens(content):
    if isinstance(content, str):
        return est(content)
    total = 0
    for b in content if isinstance(content, list) else [content]:
        if isinstance(b, dict) and b.get("type") == "image":
            continue                                   # images are not chars/4
        total += est(b)
    return total


def tool_result_tokens(path, start_offset=0):
    """chars/4 estimate of what a run's tool results and own output weighed.

    Returns ``{"result_tokens": int, "own_output": int, "n_results": int}``:
    ``result_tokens`` feeds ``retrievals.result_tokens_est`` (the content a
    retriever kept out of its parent) and ``own_output`` feeds
    ``own_output_est``.  Streaming; never loads the file.
    """
    result_tokens = own_output = n_results = 0
    for rec in iter_records(path, start_offset):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            for b in blocks(msg):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    n_results += 1
                    result_tokens += _result_content_tokens(b.get("content", ""))
        elif role == "assistant":
            for b in blocks(msg):
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    own_output += est(b.get("text", ""))
                elif t == "thinking":
                    own_output += est(b.get("thinking", ""))
                elif t == "tool_use":
                    own_output += est(b.get("input") or {})
    return {"result_tokens": result_tokens, "own_output": own_output, "n_results": n_results}
