"""Carry audit over a phase's transcripts.

Public:
    audit_phase(conn, cfg, project_root, phase, prev_phase=None) -> dict
    render_md(result) -> str
    main(argv, conn=None)

Walks every session transcript once (streaming, counts only) and reports
reads by file, writes by file, results by kind, whole-plan reads,
tool-source reads, spilled results, noise, carry per request, candidates,
whole reads (Read without offset/limit whose content chars exceed
guard.whole_read_chars), seed floor (retriever seed_ctx medians by type),
outline credit (outlines, the ranged Reads they earned, chars credited),
router (per main session: end context, growth per expert/coder task,
top result kinds), warm pings vs the rewrites they replaced (cost = ping turns +
relay turns + re-arm turns, 3.15 T3; undelivered = warm_undelivered events, wake lines
no ping followed, 3.15 T6), sent waiting
toasts by cause, router turns by cause (loop / relay / re-arm / other, from the
opening user record of each main-transcript turn) with the relay and re-arm turns' cost,
retriever re-asks (briefs sharing a file path or def/class name with an earlier
brief of the same parent run), and flags.
"""

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys

from . import guard as _guard
from . import paths
from . import phases
from . import prices
from . import transcript


# ------------------------------------------------------------------ helpers

def _norm_project(path):
    """Normalize a project path for comparison (same as savings._norm_project)."""
    text = str(path or "").strip()
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text))


def _session_ids_for_phase(conn, project_root, phase):
    """Session ids for *project_root* whose phase matches *phase*.

    Replicates ``ledger_cli._session_ids_for_phase`` logic: sessions
    explicitly tagged then other project sessions overlapping the time span.
    """
    norm = _norm_project(project_root)
    rows = conn.execute(
        "SELECT session_id, project, cwd FROM sessions").fetchall()
    sids_project = []
    for r in rows:
        p = _norm_project(r["project"] or r["cwd"])
        if p == norm:
            sids_project.append(r["session_id"])
    if not sids_project:
        return []
    # 1. explicitly tagged
    tagged = set()
    for sid in sids_project:
        # COALESCE(agent_runs.phase, sessions.phase) = phase
        hit = conn.execute(
            "SELECT 1 FROM sessions s LEFT JOIN agent_runs a ON a.session_id = s.session_id"
            " WHERE s.session_id = ? AND COALESCE(a.phase, s.phase) = ? LIMIT 1",
            (sid, phase)).fetchone()
        if hit:
            tagged.add(sid)
    if not tagged:
        return []
    # 2. time span
    ph = ",".join("?" * len(tagged))
    span = conn.execute(
        "SELECT MIN(ts) AS t0, MAX(ts) AS t1 FROM turns WHERE session_id IN (%s)" % ph,
        list(tagged)).fetchone()
    if not span or span["t0"] is None:
        return sorted(tagged)
    t0, t1 = span["t0"], span["t1"]
    # 3. overlapping project sessions (never a beside session, T28)
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


def _transcript_path_for_session(conn, sid):
    """Return the main transcript path from sessions or agent_runs."""
    row = conn.execute(
        "SELECT transcript_path FROM sessions WHERE session_id=?", (sid,)).fetchone()
    if row and row["transcript_path"]:
        return row["transcript_path"]
    # fallback: derive from projects_dir and slug
    srow = conn.execute(
        "SELECT project, cwd FROM sessions WHERE session_id=?", (sid,)).fetchone()
    if srow:
        proj = srow["project"] or srow["cwd"]
        if proj:
            slug = paths.project_slug(proj)
            return os.path.join(paths.projects_dir(), slug, sid + ".jsonl")
    return None


def _run_id_from_filename(filename):
    """``agent-<run_id>.jsonl`` -> ``<run_id>``."""
    name = os.path.basename(filename)
    if name.startswith("agent-") and name.endswith(".jsonl"):
        return name[6:-6]
    return None


def _role_for_run(conn, run_id):
    """agent_runs.agent_type for a run_id, or None."""
    row = conn.execute(
        "SELECT agent_type FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
    return row["agent_type"] if row else None


# ------------------------------------------------------------------ spill detection

# The harness wraps spilled output in ``<persisted-output>`` and writes
# ``Full output saved to: …/tool-results/<id>.txt``.
_SPILL_RE = re.compile(r"Full output saved to:.*?tool-results[/\\]([a-z0-9]+\.txt)")


def _detect_spill(text):
    """Return the tool-results filename if *text* carries the spill notice."""
    m = _SPILL_RE.search(text or "")
    return m.group(1) if m else None


_MCP_SERVER_RE = re.compile(
    r'from (?:the )?["\'"“]?(\w[\w.-]*)["\'"”]?\s+(?:MCP\s+)?server', re.I)


def _extract_mcp_servers(text):
    """Extract MCP server names from a system-reminder text block."""
    return list(set(m.group(1) for m in _MCP_SERVER_RE.finditer(text or "")))


# ------------------------------------------------------------------ span filter

def _ts_in_span(ts_raw, span):
    """True when *ts_raw* falls within *span* ``(t0_z, t1_z)``, or span is None."""
    if span is None:
        return True
    if not ts_raw:
        return True  # no timestamp -> include
    parsed = transcript.parse_ts(ts_raw)
    ts_z = transcript.iso(parsed) if parsed else str(ts_raw)
    return span[0] <= ts_z <= span[1]


# ------------------------------------------------------------------ classifier helpers

_SCRIPT_RE = re.compile(r"(?:tools/(\w+\.(?:py|sh)))")
_PA_LEDGER_SUB_RE = re.compile(r"pa_ledger\.py\s+(\w+)")
_RUN_SH_RE = re.compile(r"run\.sh\b")
_PLAN_EDIT_SHOW_RE = re.compile(r"plan_edit\.py\s+show\b")
_PLAN_EDIT_SECTION_RE = re.compile(r"--(?:section|task(?:s)?|next)\b")

_TOOL_SOURCE_PREFIXES = None  # set per call

# cat -n line prefix: optional whitespace, digits, then a tab
_LINE_PREFIX_RE = re.compile(r"^\s*\d+\t", re.MULTILINE)


def _classify_bash(text):
    """Classify a Bash tool_use command text into a governed-script label."""
    if not text:
        return "bash other"
    m = _RUN_SH_RE.search(text)
    if m:
        return "run.sh"
    m = _PA_LEDGER_SUB_RE.search(text)
    if m:
        return "pa_ledger.py %s" % m.group(1)
    m = _SCRIPT_RE.search(text)
    if m:
        return "tools/%s" % m.group(1)
    return "bash other"


def _is_whole_plan_read(tool_name, inp):
    """True when this is a whole-plan Read or an unscoped plan_edit show."""
    if tool_name == "Read":
        fp = inp.get("file_path") or inp.get("path") or ""
        if fp.endswith("PHASE_PLAN.md") or fp.endswith("PHASE_PLAN.draft.md"):
            if not inp.get("offset") and not inp.get("limit"):
                return True
    elif tool_name == "Bash":
        cmd = inp.get("command") or ""
        if _PLAN_EDIT_SHOW_RE.search(cmd):
            if not _PLAN_EDIT_SECTION_RE.search(cmd):
                return True
    return False


def _is_tool_source_read(tool_name, inp, project_is_pa):
    """True when the Read targets tool or framework source."""
    if tool_name != "Read":
        return False
    fp = (inp.get("file_path") or inp.get("path") or "").replace("\\", "/")
    if not fp:
        return False
    for prefix in _TOOL_SOURCE_PREFIXES:
        if prefix in fp:
            # when the project IS ProjectArchitect, skip pa/ and tools/ reads
            if project_is_pa:
                return False
            return True
    return False


# ------------------------------------------------------------------ single-pass walker

def _walk_transcript(path, role, accum, span=None):
    """Walk one transcript file, updating *accum* (streaming, counts only).

    *role* is ``"main"`` for the session transcript or the agent_type for a
    subagent transcript.  When *span* is ``(t0_z, t1_z)``, only records whose
    timestamp falls within the range are counted.
    """
    tool_use = {}  # tool_use_id -> (name, input_dict)

    for rec in transcript.iter_records(path):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role_msg = msg.get("role")
        in_span = _ts_in_span(rec.get("timestamp"), span)
        for b in transcript.blocks(msg):
            btype = b.get("type")

            if role_msg == "assistant" and btype == "tool_use":
                name = b.get("name", "")
                inp = b.get("input") or {}
                tid = b.get("id")
                if tid:
                    tool_use[tid] = (name, inp)

            elif role_msg == "user" and btype == "tool_result":
                tid = b.get("tool_use_id")
                name, inp = tool_use.pop(tid, ("?", {}))
                if not in_span:
                    continue
                content = b.get("content", "")
                if isinstance(content, list):
                    text = ""
                    for item in content:
                        if isinstance(item, dict):
                            text += item.get("text", "")
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)
                chars = len(text)

                # --- reads_by_file
                if name == "Read":
                    fp = inp.get("file_path") or inp.get("path") or "?"
                    entry = accum["reads_by_file"].setdefault(fp, {"chars": 0, "n": 0, "roles": {}})
                    entry["chars"] += chars
                    entry["n"] += 1
                    entry["roles"][role] = entry["roles"].get(role, 0) + 1

                # --- writes_by_file
                if name == "Write":
                    fp = inp.get("file_path") or "?"
                    wchars = len(inp.get("content") or "")
                    entry = accum["writes_by_file"].setdefault(fp, {"chars": 0, "n": 0})
                    entry["chars"] += wchars
                    entry["n"] += 1
                elif name == "Edit":
                    fp = inp.get("file_path") or "?"
                    wchars = len(inp.get("old_string") or "") + len(inp.get("new_string") or "")
                    entry = accum["writes_by_file"].setdefault(fp, {"chars": 0, "n": 0})
                    entry["chars"] += wchars
                    entry["n"] += 1

                # --- results_by_kind
                if name == "Bash":
                    label = _classify_bash(inp.get("command") or "")
                else:
                    label = name if name in ("Agent", "Read", "Grep", "Glob", "Write", "Edit") else "other"
                entry = accum["results_by_kind"].setdefault(label, {"chars": 0, "n": 0})
                entry["chars"] += chars
                entry["n"] += 1

                # --- whole_plan_reads
                if _is_whole_plan_read(name, inp):
                    wp = accum["whole_plan_reads"]
                    wp["n"] += 1
                    wp["chars"] += chars
                    wp["roles"][role] = wp["roles"].get(role, 0) + 1

                # --- tool_source_reads
                if _is_tool_source_read(name, inp, accum["_project_is_pa"]):
                    ts_acc = accum["tool_source_reads"]
                    ts_acc["n"] += 1
                    ts_acc["chars"] += chars
                    ts_acc["roles"][role] = ts_acc["roles"].get(role, 0) + 1
                    ts_fp = (inp.get("file_path") or inp.get("path") or "").replace("\\", "/")
                    ts_acc["items"].append({"path": ts_fp, "chars": chars, "role": role})

                # --- whole_reads (Read without offset/limit over threshold)
                if name == "Read" and not inp.get("offset") and not inp.get("limit"):
                    content_chars = len(_LINE_PREFIX_RE.sub("", text))
                    wr_threshold = accum.get("_whole_read_threshold", 20000)
                    if content_chars > wr_threshold:
                        fp = inp.get("file_path") or inp.get("path") or "?"
                        accum["whole_reads"]["items"].append({
                            "file": fp, "role": role,
                            "chars": content_chars, "n": 1,
                        })

                # --- spilled
                spill_file = _detect_spill(text)
                if spill_file:
                    spill_cmd = (inp.get("command") or "")[:80]
                    accum["spilled"]["items"].append({
                        "file": spill_file, "chars": 0, "read_whole": False,
                        "command": spill_cmd, "reader": None,
                        "sid_dir": accum.get("_current_sid_dir", ""),
                    })

                # --- check if this is a Read of a spilled file (read_whole)
                if name == "Read":
                    rp = inp.get("file_path") or inp.get("path") or ""
                    rp_norm = rp.replace("\\", "/")
                    if "tool-results/" in rp_norm and not inp.get("offset") and not inp.get("limit"):
                        for sp in accum["spilled"]["items"]:
                            if sp["file"] in rp_norm:
                                sp["read_whole"] = True
                                sp["reader"] = role

                # --- noise (matched lines per pattern)
                for pat in accum["_noise_patterns"]:
                    if pat in text:
                        entry = accum["noise"].setdefault(pat, {"n": 0, "chars": 0, "lines": 0})
                        entry["n"] += 1
                        for _line in text.split("\n"):
                            if pat in _line:
                                entry["lines"] += 1
                                entry["chars"] += len(_line)
                        break  # one pattern match per result

            # --- MCP instruction blocks
            elif role_msg == "user" and btype == "text" and in_span:
                txt = b.get("text") or ""
                if "<system-reminder>" in txt:
                    servers = _extract_mcp_servers(txt)
                    if servers:
                        mcp = accum["mcp_blocks"]
                        mcp["n"] += 1
                        mcp["chars"] += len(txt)
                        mcp["servers"].update(servers)

        # --- count api requests
        if role_msg == "assistant" and in_span:
            u = msg.get("usage")
            if isinstance(u, dict) and (msg.get("model") or "") != "<synthetic>":
                for k in ("input_tokens", "cache_creation_input_tokens",
                           "cache_read_input_tokens", "output_tokens"):
                    if isinstance(u.get(k), (int, float)) and u.get(k):
                        accum["_total_result_chars"] += 0  # just to track we have requests
                        break

    accum["_total_result_chars"] += sum(
        e["chars"] for e in accum["results_by_kind"].values()) - accum.get("_prev_result_chars", 0)
    accum["_prev_result_chars"] = sum(e["chars"] for e in accum["results_by_kind"].values())


def _walk_transcript_requests_after_read(path, span=None):
    """Return list of api-request counts following each Read in the transcript."""
    counts = []
    tool_use = {}
    since_last_read = None

    for rec in transcript.iter_records(path):
        if not _ts_in_span(rec.get("timestamp"), span):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role_msg = msg.get("role")
        for b in transcript.blocks(msg):
            btype = b.get("type")
            if role_msg == "assistant" and btype == "tool_use":
                tid = b.get("id")
                name = b.get("name", "")
                if tid:
                    tool_use[tid] = name
            elif role_msg == "user" and btype == "tool_result":
                tid = b.get("tool_use_id")
                name = tool_use.pop(tid, "?")
                if name == "Read":
                    if since_last_read is not None:
                        counts.append(since_last_read)
                    since_last_read = 0

        if role_msg == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict) and (msg.get("model") or "") != "<synthetic>":
                for k in ("input_tokens", "cache_creation_input_tokens",
                           "cache_read_input_tokens", "output_tokens"):
                    if isinstance(u.get(k), (int, float)) and u.get(k):
                        if since_last_read is not None:
                            since_last_read += 1
                        break
    if since_last_read is not None:
        counts.append(since_last_read)
    return counts


# ------------------------------------------------------------------ candidates

def _load_candidates(project_root):
    """Parse ``- Tool candidate: <name> | …`` from any ``phase-ends/*/AUDIT.md``."""
    candidates = []
    pe_dir = os.path.join(project_root, "phase-ends")
    if not os.path.isdir(pe_dir):
        return candidates
    cand_re = re.compile(r"^- Tool candidate:\s*(\S+)\s*\|")
    for entry in os.listdir(pe_dir):
        audit_path = os.path.join(pe_dir, entry, "AUDIT.md")
        if not os.path.isfile(audit_path):
            continue
        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    m = cand_re.match(line.strip())
                    if m:
                        candidates.append({"name": m.group(1), "source": audit_path})
        except OSError:
            continue
    return candidates


def _is_ratified(name, project_root):
    """Check if a candidate named *name* has a ratifying task line."""
    pe_dir = os.path.join(project_root, "phase-ends")
    if not os.path.isdir(pe_dir):
        return False
    target = "tool candidate %s" % name.lower()
    for entry in os.listdir(pe_dir):
        plan_path = os.path.join(pe_dir, entry, "PHASE_PLAN.md")
        if not os.path.isfile(plan_path):
            continue
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("- T") and "| title:" in stripped.lower():
                        if target in stripped.lower():
                            return True
                    elif stripped.startswith("- T") and "|" in stripped:
                        parts = stripped.split("|")
                        for part in parts:
                            if "title:" in part.lower() or part.strip().lower().startswith("tool candidate"):
                                if target in part.lower():
                                    return True
        except OSError:
            continue
    return False


def _measure_candidate(conn, name, session_ids):
    """Sum (carry + emission tokens) of tool_calls rows with script == name."""
    if not session_ids:
        return 0
    ph = ",".join("?" * len(session_ids))
    row = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(kept_tokens, 0) + COALESCE(emission_tokens, 0)), 0) AS total"
        " FROM tool_calls WHERE script = ? AND session_id IN (%s)" % ph,
        [name] + list(session_ids)).fetchone()
    return row["total"] if row else 0


# ------------------------------------------------------------------ carry_per_request

def _carry_per_request(conn, session_ids, phase, prev_phase, project_root,
                       prev_sids_override=None, span=None, prev_span=None):
    """Compute carry-per-request stats.

    When *span* / *prev_span* are ``(t0_z, t1_z)`` tuples the turns queries
    are restricted to ``ts`` within the range.
    """
    if not session_ids:
        return {"n_requests": 0, "prev_n_requests": 0,
                "prices": {"read": 0.0, "write_1h": 0.0, "output": 0.0}}

    ph = ",".join("?" * len(session_ids))
    span_clause = ""
    span_args = []
    if span:
        span_clause = " AND ts>=? AND ts<=?"
        span_args = [span[0], span[1]]

    # total tool_result chars from results_by_kind is passed in separately;
    # here we get api request count from turns
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM turns WHERE kind='api' AND session_id IN (%s)%s" % (ph, span_clause),
        list(session_ids) + span_args).fetchone()
    n_requests = row["n"] if row else 0

    # previous phase
    prev_n = 0
    prev_total_chars = 0
    if prev_phase:
        prev_sids = list(prev_sids_override) if prev_sids_override is not None else _session_ids_for_phase(conn, project_root, prev_phase)
        if prev_sids:
            pph = ",".join("?" * len(prev_sids))
            prev_span_clause = ""
            prev_span_args = []
            if prev_span:
                prev_span_clause = " AND ts>=? AND ts<=?"
                prev_span_args = [prev_span[0], prev_span[1]]
            prow = conn.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE kind='api' AND session_id IN (%s)%s" % (pph, prev_span_clause),
                list(prev_sids) + prev_span_args).fetchone()
            prev_n = prow["n"] if prow else 0

    # blended prices from the phase's turns
    model_costs = {}
    rows = conn.execute(
        "SELECT model, SUM(COALESCE(input,0)+COALESCE(cache_write_5m,0)+COALESCE(cache_write_1h,0)"
        "+COALESCE(cache_read,0)+COALESCE(output,0)) AS total_tokens"
        " FROM turns WHERE kind='api' AND session_id IN (%s)%s GROUP BY model" % (ph, span_clause),
        list(session_ids) + span_args).fetchall()
    total_tokens = sum(r["total_tokens"] for r in rows if r["total_tokens"])
    blended_read = 0.0
    blended_write_1h = 0.0
    blended_output = 0.0
    if total_tokens > 0:
        for r in rows:
            w = (r["total_tokens"] or 0) / total_tokens
            p = prices.price_for(r["model"])
            if p:
                blended_read += w * p["read"]
                blended_write_1h += w * p["write_1h"]
                blended_output += w * p["output"]

    return {
        "n_requests": n_requests,
        "prev_n_requests": prev_n,
        "prices": {"read": blended_read, "write_1h": blended_write_1h, "output": blended_output},
    }


# ------------------------------------------------------------------ seed floor

def _compute_seed_floor(conn, session_ids, prev_phase, project_root,
                        prev_sids_override=None):
    """Seed-ctx medians per retriever type for the phase's sessions.

    Returns ``{agent_type: {n, median, min, max, prev_median}}`` where
    *prev_median* is the median from *prev_phase*'s sessions (``"—"`` when
    absent).
    """
    if not session_ids:
        return {}
    ph = ",".join("?" * len(session_ids))
    rows = conn.execute(
        "SELECT agent_type, seed_ctx FROM agent_runs"
        " WHERE session_id IN (%s) AND agent_type LIKE 'retriever-%%'" % ph,
        list(session_ids)).fetchall()
    by_type = {}
    for r in rows:
        at = r["agent_type"]
        sc = r["seed_ctx"]
        if sc is None:
            continue
        by_type.setdefault(at, []).append(sc)

    # previous phase
    prev_by_type = {}
    if prev_phase:
        prev_sids = list(prev_sids_override) if prev_sids_override is not None else _session_ids_for_phase(conn, project_root, prev_phase)
        if prev_sids:
            pph = ",".join("?" * len(prev_sids))
            prev_rows = conn.execute(
                "SELECT agent_type, seed_ctx FROM agent_runs"
                " WHERE session_id IN (%s) AND agent_type LIKE 'retriever-%%'" % pph,
                list(prev_sids)).fetchall()
            for r in prev_rows:
                at = r["agent_type"]
                sc = r["seed_ctx"]
                if sc is None:
                    continue
                prev_by_type.setdefault(at, []).append(sc)

    result = {}
    for at in sorted(set(list(by_type.keys()) + list(prev_by_type.keys()))):
        vals = by_type.get(at, [])
        pvals = prev_by_type.get(at, [])
        if not vals:
            continue
        result[at] = {
            "n": len(vals),
            "median": int(statistics.median(vals)),
            "min": min(vals),
            "max": max(vals),
            "prev_median": int(statistics.median(pvals)) if pvals else "—",
        }
    return result


# ------------------------------------------------------------------ outline credit

def _compute_outline_credit(conn, session_ids, filter_span):
    """Outlines, outline-reads and credited chars from the phase's ``tool_calls``.

    Same pairing as ``savings._tool_calls_carry_rows``: an ``outline-read``
    subtracts the latest earlier ``outline`` row's emission of its run and path.
    """
    from . import credit as _credit
    out = {"n": 0, "m": 0, "chars": 0}
    if not session_ids:
        return out
    ph = ",".join("?" * len(session_ids))
    sql = ("SELECT run_id, kind, path, file_tokens, emission_tokens, result_tokens"
           " FROM tool_calls WHERE kind IN ('outline', 'outline-read')"
           " AND session_id IN (%s)" % ph)
    args = list(session_ids)
    if filter_span:
        sql += " AND ts >= ? AND ts < ?"
        args += [filter_span[0], filter_span[1]]
    try:
        rows = conn.execute(sql + " ORDER BY ts, kind = 'outline-read'", args).fetchall()
    except sqlite3.Error:
        return out
    em = {}
    for r in rows:
        key = (r["run_id"], r["path"])
        if r["kind"] == "outline":
            out["n"] += 1
            em[key] = r["emission_tokens"] or 0
            continue
        out["m"] += 1
        out["chars"] += 4 * _credit.carry_tokens(
            {"kind": "outline-read", "file_tokens": r["file_tokens"],
             "result_tokens": r["result_tokens"],
             "outline_emission_tokens": em.get(key, 0)}, set(), set(), {})
    return out


# ------------------------------------------------------------------ warmer and toasts

TOAST_CAUSES = ("question", "review", "replan", "permission", "input", "discussion",
                "crash", "idle", "stop", "subagent-stop", "model", "other")


def _events_in_scope(conn, kind, session_ids, filter_span):
    """``(ts, run_id, detail dict)`` of *kind* events for the phase's sessions, by ts."""
    if not session_ids:
        return []
    ph = ",".join("?" * len(session_ids))
    sql = ("SELECT ts, run_id, detail_json FROM events WHERE kind = ?"
           " AND session_id IN (%s)" % ph)
    args = [kind] + list(session_ids)
    if filter_span:
        sql += " AND ts >= ? AND ts < ?"
        args += [filter_span[0], filter_span[1]]
    try:
        rows = conn.execute(sql + " ORDER BY ts, id", args).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        try:
            d = json.loads(r["detail_json"] or "{}")
        except ValueError:
            d = {}
        out.append((r["ts"], d.get("run_id") or r["run_id"], d if isinstance(d, dict) else {}))
    return out


def _ttl_s(ttl):
    return 3600 if ttl == "1h" else 300


def _compute_warm_pings(conn, session_ids, filter_span):
    """Warmer pings vs the cache rewrites they replaced (C0009).

    Ping turn = the run's first turn within 60 s after a ``warm_ping`` event.  A
    warmed wait = non-ping turn -> next non-ping turn with >= 1 ping turn between
    and ``sum(gap_s)`` (ping turns + ending turn) over the run's TTL.  A warmed wait
    is past the cap (``m``) when the highest event ``n`` inside it is >= the event's
    ``cap`` and the ending turn's ``gap_s`` exceeds the TTL: the designed cold, never
    in ``k`` or ``b`` (its rewrite was not replaced, C0009).  ``k`` = the other warmed
    waits whose ending turn has ``rewrite = 1``; ``b`` prices their ending turn's ctx
    as a cache write at that TTL (3.9.5 T8).  ``u`` = ``warm_undelivered`` events (3.15 T6).
    """
    out = {"n": 0, "r": 0, "w": 0, "k": 0, "m": 0, "a": 0.0, "b": 0.0, "u": 0}
    out["u"] = len(_events_in_scope(conn, "warm_undelivered", session_ids, filter_span))
    pings = _events_in_scope(conn, "warm_ping", session_ids, filter_span)
    if not pings:
        return out
    out["n"] = len(pings)
    by_run = {}
    for ts, rid, d in pings:
        # events without ``cap`` were all written under max_pings 3, before 3.9.5 T8
        by_run.setdefault(rid, []).append((transcript.parse_ts(ts), d.get("ttl"),
                                           d.get("n") or 0, d.get("cap") or 3))
    out["r"] = len(by_run)
    for rid, evs in sorted(by_run.items(), key=lambda x: str(x[0])):
        try:
            turns = conn.execute(
                "SELECT ts, gap_s, rewrite, ctx, model, cost_usd, ttl_assumed,"
                " cache_write_5m, cache_write_1h FROM turns WHERE run_id = ?"
                " ORDER BY ts, msg_id", (rid,)).fetchall()
        except sqlite3.Error:
            continue
        tts = [transcript.parse_ts(t["ts"]) for t in turns]
        ping_idx = set()
        for ets, _ttl, _n, _cap in evs:
            if ets is None:
                continue
            for i, t in enumerate(tts):
                if t is not None and 0 <= (t - ets).total_seconds() <= 60 and i not in ping_idx:
                    ping_idx.add(i)
                    out["a"] += turns[i]["cost_usd"] or 0.0
                    break
        ev_ttl = next((e[1] for e in evs if e[1] in ("5m", "1h")), None)
        start, pinged, wait = None, 0, 0.0
        for i, t in enumerate(turns):
            if i in ping_idx:
                pinged += 1
                wait += t["gap_s"] or 0.0
                continue
            if start is not None and pinged:
                wait += t["gap_s"] or 0.0
                ttl = ev_ttl or (t["ttl_assumed"] if t["ttl_assumed"] in ("5m", "1h") else
                                 ("1h" if (t["cache_write_1h"] or 0) > (t["cache_write_5m"] or 0)
                                  else "5m"))
                if wait > _ttl_s(ttl):
                    out["w"] += 1
                    t0, t1 = tts[start], tts[i]
                    inside = [e for e in evs if e[0] is not None and t0 is not None
                              and t1 is not None and t0 <= e[0] <= t1]
                    top = max(inside, key=lambda e: e[2]) if inside else None
                    if top and top[2] >= top[3] and (t["gap_s"] or 0.0) > _ttl_s(ttl):
                        out["m"] += 1            # past the cap: the designed cold
                        start, pinged, wait = i, 0, 0.0
                        continue
                    if t["rewrite"]:
                        out["k"] += 1
                    ctx = t["ctx"] or 0
                    out["b"] += prices.cost_of(
                        {"cache_write": ctx}, t["model"], ttl_default=ttl, at=t["ts"],
                        long_context=ctx > prices.LONG_CONTEXT_TOKENS)[0]
            start, pinged, wait = i, 0, 0.0
    return out


def _compute_toasts(conn, session_ids, filter_span):
    """Sent waiting-level toasts by cause; an unlisted cause counts as ``other``."""
    out = dict((c, 0) for c in TOAST_CAUSES)
    for _ts, _rid, d in _events_in_scope(conn, "toast", session_ids, filter_span):
        if d.get("sent") in (1, True) and d.get("level") == "waiting":
            c = d.get("cause")
            out[c if c in out else "other"] += 1
    return out


def _user_text(msg):
    """Joined text blocks of a user message (string or list content)."""
    return "".join(b.get("text", "") for b in transcript.blocks(msg)
                   if isinstance(b, dict) and b.get("type") == "text")


def _turn_cause(text):
    """Cause of a router turn from its opening user text (see ``_compute_router_turns``)."""
    if "<task-notification>" in text and "<event>warm " in text:
        return "relay"
    if "<event>[Monitor expired" in text:
        return "re-arm"
    if ("<task-notification>" in text
            or text.startswith("Another Claude session sent a message")
            or "<cross-session-message" in text):
        return "loop"
    return "other"


def _compute_router_turns(conn, session_ids, filter_span):
    """Router turns by cause over each phase session's main transcript, and the relay and re-arm cost.

    A turn = a user record that is not a tool_result, meta or not (a subagent
    hand-back arrives as an ``isMeta`` user record and opens a real turn; its
    timestamp inside *filter_span*) plus every assistant record until the next
    such user record; a turn with no assistant record is not counted.  Cause from
    that user record's text: ``<task-notification>`` with ``<event>warm `` -> relay;
    ``<event>[Monitor expired`` -> re-arm; any other ``<task-notification>``, text
    starting ``Another Claude session sent a message`` or containing
    ``<cross-session-message`` -> loop; else other.  ``cost`` = sum of
    ``turns.cost_usd`` (``run_id`` = the session) whose ``msg_id`` is one of the relay
    turns' assistant ``message.id`` values (deduped); ``rearm_cost`` the same over the
    re-arm turns (3.15 T3: the warm-ping verdict counts both).
    """
    out = {"loop": 0, "relay": 0, "re-arm": 0, "other": 0, "cost": 0.0, "rearm_cost": 0.0}
    for sid in session_ids:
        tp = _transcript_path_for_session(conn, sid)
        if not tp or not os.path.isfile(tp):
            continue
        relay_ids, rearm_ids = set(), set()
        cause, ids = None, None

        def _close():
            if cause is not None and ids:
                out[cause] += 1
                if cause == "relay":
                    relay_ids.update(ids)
                elif cause == "re-arm":
                    rearm_ids.update(ids)

        for rec in transcript.iter_records(tp):
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user":
                blks = transcript.blocks(msg)
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blks):
                    continue
                _close()
                if _ts_in_span(rec.get("timestamp"), filter_span):
                    cause, ids = _turn_cause(_user_text(msg)), set()
                else:
                    cause, ids = None, None
            elif role == "assistant" and ids is not None:
                ids.add(msg.get("id") or rec.get("uuid") or "")
        _close()
        for key, mids in (("cost", relay_ids), ("rearm_cost", rearm_ids)):
            mids.discard("")
            if not mids:
                continue
            ph = ",".join("?" * len(mids))
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM turns WHERE run_id = ?"
                    " AND msg_id IN (%s)" % ph, [sid] + sorted(mids)).fetchone()
                out[key] += row["c"] or 0.0
            except sqlite3.Error:
                pass
    return out


_LOOKUP_TOKEN_RE = re.compile(r"[\w./\\:~-]+")
_LOOKUP_DEF_RE = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")
_LOOKUP_EXT_RE = re.compile(r"[^./\\]\.[A-Za-z]{1,5}$")
_LOOKUP_LINE_RE = re.compile(r":\d+(?:-\d+)?$")


def _brief_lookups(text):
    """File paths and ``def``/``class`` names a retriever brief asks about.

    Paths = tokens with ``/`` or ``\\`` or a dotted file extension of 1-5 letters,
    normalized: forward slashes, lowercase, ``:line`` / ``:a-b`` suffix and leading
    ``./`` stripped.  Names = identifiers after ``def `` or ``class ``.
    """
    out = set()
    for tok in _LOOKUP_TOKEN_RE.findall(text or ""):
        tok = _LOOKUP_LINE_RE.sub("", tok.rstrip(".:"))
        if "/" in tok or "\\" in tok or _LOOKUP_EXT_RE.search(tok):
            tok = tok.replace("\\", "/").lower()
            while tok.startswith("./"):
                tok = tok[2:]
            if tok.strip("/."):
                out.add(tok)
    out.update("def:" + n for n in _LOOKUP_DEF_RE.findall(text or ""))
    return out


def _compute_retriever_reasks(conn, session_ids, filter_span):
    """Retriever briefs that repeat a lookup of an earlier brief from the same run.

    Scope: ``agent_runs`` with ``agent_type LIKE 'retriever-%'`` in the phase's
    sessions, ``started`` inside *filter_span*.  Brief = the text of the first user
    record of the run's ``transcript_path`` (missing file -> the run is skipped, not
    in ``m``).  Lookups per ``_brief_lookups``.  Grouped by ``parent_run_id`` (a None
    parent is its own group, never a repeat); in ``started`` order a brief is a
    re-ask (``n``) when it shares a lookup with an earlier brief of the same parent.
    ``m`` = briefs read.
    """
    out = {"n": 0, "m": 0}
    if not session_ids:
        return out
    ph = ",".join("?" * len(session_ids))
    sql = ("SELECT run_id, parent_run_id, transcript_path FROM agent_runs"
           " WHERE agent_type LIKE 'retriever-%%' AND session_id IN (%s)" % ph)
    args = list(session_ids)
    if filter_span:
        sql += " AND started >= ? AND started <= ?"
        args += [filter_span[0], filter_span[1]]
    try:
        rows = conn.execute(sql + " ORDER BY started, run_id", args).fetchall()
    except sqlite3.Error:
        return out
    seen = {}
    for r in rows:
        tp = r["transcript_path"]
        if not tp or not os.path.isfile(tp):
            continue
        brief = None
        for rec in transcript.iter_records(tp):
            msg = rec.get("message")
            if isinstance(msg, dict) and msg.get("role") == "user":
                brief = _user_text(msg)
                break
        if brief is None:
            continue
        out["m"] += 1
        parent = r["parent_run_id"]
        if parent is None:
            continue
        looks = _brief_lookups(brief)
        prior = seen.setdefault(parent, set())
        if looks & prior:
            out["n"] += 1
        prior.update(looks)
    return out


# ------------------------------------------------------------------ router

def _compute_router(conn, session_ids, accum, filter_span, router_ctx_flag):
    """Per main session: ctx_end, growth per expert/coder task, top result kinds.

    Returns a list of ``{label, session_id, requests, ctx_end,
    growth: [(task_id, delta)], top_kinds: [(kind, chars, n)]}``.

    Selection rule: a session earns a row only when (a) it has at least one
    request inside *filter_span*, and (b) its label is ``pa-session`` or
    ``main`` (no agent type) OR it spawned at least one agent run inside the
    span. This drops sessions tagged for the phase whose turns all lie
    outside the span, and headless runs (e.g. ``bench-*``) that spawned
    nothing.
    """
    result = []
    for sid in session_ids:
        # The main thread's run_id == session_id
        main_run_id = sid

        # label: the session's agent_type from agent_runs, or "main"
        ar_row = conn.execute(
            "SELECT agent_type FROM agent_runs WHERE run_id=?", (main_run_id,)).fetchone()
        label = (ar_row["agent_type"] if ar_row and ar_row["agent_type"] else "main")

        # span filter
        span_clause = ""
        span_args = []
        if filter_span:
            span_clause = " AND ts>=? AND ts<=?"
            span_args = [filter_span[0], filter_span[1]]

        # requests: count of api turns in the main thread, inside the span
        req_row = conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE run_id=? AND session_id=?"
            " AND kind='api'%s" % span_clause,
            [main_run_id, sid] + span_args).fetchone()
        requests = req_row["n"] if req_row else 0
        if requests == 0:
            continue  # rule (a): no request inside the span

        if label not in ("pa-session", "main"):
            # exclude the session's own identity row (run_id == session_id)
            spawn_clause = " AND run_id!=?"
            spawn_args = [sid, main_run_id]
            if filter_span:
                spawn_clause += " AND started>=? AND started<=?"
                spawn_args += [filter_span[0], filter_span[1]]
            spawned = conn.execute(
                "SELECT 1 FROM agent_runs WHERE session_id=?%s LIMIT 1" % spawn_clause,
                spawn_args).fetchone()
            if not spawned:
                continue  # rule (b): no agent run spawned inside the span

        # ctx_end: the session's last turn's ctx (main thread only)
        last = conn.execute(
            "SELECT ctx FROM turns WHERE run_id=? AND session_id=?%s"
            " ORDER BY ts DESC LIMIT 1" % span_clause,
            [main_run_id, sid] + span_args).fetchone()
        ctx_end = last["ctx"] if last and last["ctx"] else 0

        # growth: per expert (or coder when no expert) agent_runs of the session
        expert_runs = conn.execute(
            "SELECT run_id, task_id, kind, started, ended FROM agent_runs"
            " WHERE session_id=? AND kind='expert' ORDER BY started",
            (sid,)).fetchall()
        if not expert_runs:
            expert_runs = conn.execute(
                "SELECT run_id, task_id, kind, started, ended FROM agent_runs"
                " WHERE session_id=? AND kind='coder' ORDER BY started",
                (sid,)).fetchall()
        growth_items = []
        for er in expert_runs:
            task_id = er["task_id"] or er["run_id"][:8]
            started = er["started"]
            ended = er["ended"]
            if not started or not ended:
                continue
            # last main turn before started
            before = conn.execute(
                "SELECT ctx FROM turns WHERE run_id=? AND session_id=? AND ts<?"
                " ORDER BY ts DESC LIMIT 1",
                (main_run_id, sid, started)).fetchone()
            # first main turn after ended
            after = conn.execute(
                "SELECT ctx FROM turns WHERE run_id=? AND session_id=? AND ts>?"
                " ORDER BY ts ASC LIMIT 1",
                (main_run_id, sid, ended)).fetchone()
            if before and after and before["ctx"] and after["ctx"]:
                delta = after["ctx"] - before["ctx"]
                growth_items.append((task_id, delta))

        # top_kinds: _classify_bash over the session's own tool results only (main transcript)
        # We use the results_by_kind already accumulated per session for main transcript only
        # But accum aggregates all sessions. We need per-session data.
        # Walk the main transcript again for this session only for top_kinds.
        tp = _transcript_path_for_session(conn, sid)
        session_kinds = {}
        if tp and os.path.isfile(tp):
            session_kinds = _walk_session_kinds(tp, filter_span)

        top_kinds_sorted = sorted(session_kinds.items(), key=lambda x: -x[1]["chars"])[:5]
        top_kinds = [(k, v["chars"], v["n"]) for k, v in top_kinds_sorted]

        result.append({
            "label": label,
            "session_id": sid,
            "requests": requests,
            "ctx_end": ctx_end,
            "growth": growth_items,
            "top_kinds": top_kinds,
        })
    return result


def _walk_session_kinds(path, span=None):
    """Walk a main transcript and return results_by_kind dict (kind -> {chars, n})."""
    tool_use = {}
    kinds = {}
    for rec in transcript.iter_records(path):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role_msg = msg.get("role")
        in_span = _ts_in_span(rec.get("timestamp"), span)
        for b in transcript.blocks(msg):
            btype = b.get("type")
            if role_msg == "assistant" and btype == "tool_use":
                tid = b.get("id")
                name = b.get("name", "")
                inp = b.get("input") or {}
                if tid:
                    tool_use[tid] = (name, inp)
            elif role_msg == "user" and btype == "tool_result":
                tid = b.get("tool_use_id")
                name, inp = tool_use.pop(tid, ("?", {}))
                if not in_span:
                    continue
                content = b.get("content", "")
                if isinstance(content, list):
                    text = ""
                    for item in content:
                        if isinstance(item, dict):
                            text += item.get("text", "")
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)
                chars = len(text)
                if name == "Bash":
                    label = _classify_bash(inp.get("command") or "")
                else:
                    label = name if name in ("Agent", "Read", "Grep", "Glob", "Write", "Edit") else "other"
                entry = kinds.setdefault(label, {"chars": 0, "n": 0})
                entry["chars"] += chars
                entry["n"] += 1
    return kinds


# ------------------------------------------------------------------ main entry

def audit_phase(conn, cfg, project_root, phase, prev_phase=None,
                sids=None, prev_sids=None, span=None, prev_span=None):
    """Run the carry audit over *phase*'s transcripts.

    Returns a dict of blocks (reads_by_file, writes_by_file, results_by_kind,
    whole_plan_reads, tool_source_reads, spilled, noise, carry_per_request,
    candidates, flags).

    *sids* / *prev_sids*: when given, override the internal session lookup.
    *span* / *prev_span*: ``(t0_z, t1_z)`` UTC boundaries; when given, only
    records whose timestamp falls inside the range are counted.  ``t1_z`` may
    be ``None`` for an open phase; the returned ``span`` keeps that ``None``
    for ``render_md`` to print, while record filtering uses now instead.
    """
    # normalize span boundaries to UTC Z so DB and transcript comparisons agree
    def _norm_span(s):
        if not s:
            return s
        return tuple(transcript.iso(transcript.parse_ts(v)) if transcript.parse_ts(v) else v
                     for v in s)
    span = _norm_span(span)
    prev_span = _norm_span(prev_span)

    # effective spans for record filtering: an open phase's upper bound is now
    filter_span = (span[0], phases.span_end_or_now(span[1])) if span else None
    filter_prev_span = (prev_span[0], phases.span_end_or_now(prev_span[1])) if prev_span else None

    global _TOOL_SOURCE_PREFIXES
    cfg = cfg if isinstance(cfg, dict) else {}
    audit_cfg = cfg.get("audit") or {}
    noise_patterns = audit_cfg.get("noise_patterns") or []
    file_chars_flag = audit_cfg.get("file_chars_flag", 500000)
    growth_flag = audit_cfg.get("growth_flag", 0.2)
    guard_cfg = cfg.get("guard") or {}
    whole_read_chars = guard_cfg.get("whole_read_chars", 20000)
    router_ctx_flag = audit_cfg.get("router_ctx_flag", 300000)

    install = paths.install_dir().replace("\\", "/")
    _TOOL_SOURCE_PREFIXES = ("tools/", ".claude/", install + "/",
                             "project-architect-3.0/pa/")
    project_is_pa = _guard.is_project_pa(project_root)

    session_ids = list(sids) if sids is not None else _session_ids_for_phase(conn, project_root, phase)
    # T28: beside sessions get their own row, never a phase total
    beside = phases.beside_costs(conn, project_root, phase, filter_span)
    beside_ids = {b["session_id"] for b in beside}
    session_ids = [s for s in session_ids if s not in beside_ids]

    accum = {
        "reads_by_file": {},
        "writes_by_file": {},
        "results_by_kind": {},
        "whole_plan_reads": {"n": 0, "chars": 0, "roles": {}},
        "tool_source_reads": {"n": 0, "chars": 0, "roles": {}, "items": []},
        "whole_reads": {"items": []},
        "spilled": {"items": []},
        "noise": {},
        "mcp_blocks": {"n": 0, "chars": 0, "servers": set()},
        "_noise_patterns": noise_patterns,
        "_project_is_pa": project_is_pa,
        "_whole_read_threshold": whole_read_chars,
        "_total_result_chars": 0,
        "_prev_result_chars": 0,
    }

    all_requests_after_read = []

    for sid in session_ids:
        tp = _transcript_path_for_session(conn, sid)
        if not tp or not os.path.isfile(tp):
            continue
        sid_dir = paths.session_dir(tp)
        accum["_current_sid_dir"] = sid_dir

        # main transcript
        _walk_transcript(tp, "main", accum, span=filter_span)
        all_requests_after_read.extend(_walk_transcript_requests_after_read(tp, span=filter_span))

        # subagent files
        for sf in transcript.subagent_files(tp):
            run_id = _run_id_from_filename(sf)
            role = "main"
            if run_id:
                r = _role_for_run(conn, run_id)
                if r:
                    role = r
            _walk_transcript(sf, role, accum, span=filter_span)
            all_requests_after_read.extend(_walk_transcript_requests_after_read(sf, span=filter_span))

    # --- finalize spilled: disk chars
    for sp in accum["spilled"]["items"]:
        sid_dir = sp.get("sid_dir", "")
        if sid_dir:
            disk_path = os.path.join(sid_dir, "tool-results", sp["file"])
            try:
                sp["chars"] = os.path.getsize(disk_path)
            except OSError:
                pass

    # --- carry_per_request
    total_result_chars = sum(e["chars"] for e in accum["results_by_kind"].values())
    cpr = _carry_per_request(conn, session_ids, phase, prev_phase, project_root,
                             prev_sids_override=prev_sids,
                             span=filter_span, prev_span=filter_prev_span)
    n_requests = cpr["n_requests"]
    chars_per_request = total_result_chars / n_requests if n_requests else 0

    prev_chars_per_request = 0
    if prev_phase:
        prev_sids_resolved = list(prev_sids) if prev_sids is not None else _session_ids_for_phase(conn, project_root, prev_phase)
        if prev_sids_resolved and cpr["prev_n_requests"]:
            # approximate: we don't walk prev transcripts, use tool_calls table
            prev_ph = ",".join("?" * len(prev_sids_resolved))
            prev_row = conn.execute(
                "SELECT COALESCE(SUM(result_tokens), 0) AS total FROM tool_calls"
                " WHERE session_id IN (%s)" % prev_ph,
                list(prev_sids_resolved)).fetchone()
            prev_result_chars = (prev_row["total"] if prev_row else 0) * 4  # tokens -> chars approx
            prev_chars_per_request = prev_result_chars / cpr["prev_n_requests"] if cpr["prev_n_requests"] else 0

    growth = 0.0
    if prev_chars_per_request > 0:
        growth = (chars_per_request - prev_chars_per_request) / prev_chars_per_request

    median_after_read = 0
    if all_requests_after_read:
        median_after_read = int(statistics.median(all_requests_after_read))

    carry_result = {
        "chars_per_request": round(chars_per_request, 1),
        "n_requests": n_requests,
        "prev_chars_per_request": round(prev_chars_per_request, 1),
        "prev_n_requests": cpr["prev_n_requests"],
        "growth": round(growth, 4),
        "prices": cpr["prices"],
        "median_requests_after_read": median_after_read,
    }

    # --- candidates
    raw_candidates = _load_candidates(project_root)
    candidates = []
    for c in raw_candidates:
        name = c["name"]
        ratified = _is_ratified(name, project_root)
        measured = _measure_candidate(conn, name, session_ids) if ratified else 0
        candidates.append({"name": name, "ratified": ratified, "measured": measured})

    # --- whole_reads (aggregate from transcript walk)
    wr_items = accum["whole_reads"]["items"]
    whole_reads_result = {
        "threshold": whole_read_chars,
        "n": len(wr_items),
        "items": wr_items,
    }

    # --- seed_floor (retriever seed_ctx medians by type from agent_runs)
    seed_floor_result = _compute_seed_floor(conn, session_ids, prev_phase, project_root,
                                            prev_sids_override=prev_sids)

    # --- outline credit (tool_calls kinds outline / outline-read)
    outline_credit_result = _compute_outline_credit(conn, session_ids, filter_span)

    # --- router (per main session: ctx_end, growth, top_kinds)
    router_result = _compute_router(conn, session_ids, accum, filter_span,
                                    router_ctx_flag)

    # --- warmer pings and toasts (events kinds warm_ping / toast)
    warm_result = _compute_warm_pings(conn, session_ids, filter_span)
    toasts_result = _compute_toasts(conn, session_ids, filter_span)
    router_turns_result = _compute_router_turns(conn, session_ids, filter_span)
    reasks_result = _compute_retriever_reasks(conn, session_ids, filter_span)

    # --- top-25 reads/writes
    reads_sorted = sorted(accum["reads_by_file"].items(), key=lambda x: -x[1]["chars"])
    top_reads = reads_sorted[:25]
    if len(reads_sorted) > 25:
        other_chars = sum(v["chars"] for _, v in reads_sorted[25:])
        other_n = sum(v["n"] for _, v in reads_sorted[25:])
        top_reads.append(("other", {"chars": other_chars, "n": other_n, "roles": {}}))

    writes_sorted = sorted(accum["writes_by_file"].items(), key=lambda x: -x[1]["chars"])
    top_writes = writes_sorted[:25]
    if len(writes_sorted) > 25:
        other_chars = sum(v["chars"] for _, v in writes_sorted[25:])
        other_n = sum(v["n"] for _, v in writes_sorted[25:])
        top_writes.append(("other", {"chars": other_chars, "n": other_n}))

    # --- flags
    flags = []
    for fp, v in reads_sorted:
        if v["chars"] > file_chars_flag:
            flags.append("- flag: %s read %d× / %d chars > audit.file_chars_flag"
                         % (fp, v["n"], v["chars"]))
    if growth > growth_flag and prev_phase:
        flags.append("- flag: carry per request grew %.0f%% vs %s"
                     % (growth * 100, prev_phase))
    spill_read_whole = sum(1 for sp in accum["spilled"]["items"] if sp["read_whole"])
    if spill_read_whole:
        flags.append("- flag: %d spilled results read whole" % spill_read_whole)
    wp = accum["whole_plan_reads"]
    if wp["n"]:
        roles_str = ", ".join(sorted(wp["roles"].keys()))
        flags.append("- flag: %d whole-plan reads by %s" % (wp["n"], roles_str))
    ts_acc = accum["tool_source_reads"]
    if ts_acc["n"]:
        roles_str = ", ".join(sorted(ts_acc["roles"].keys()))
        flags.append("- flag: %d tool-source reads by %s" % (ts_acc["n"], roles_str))
    total_noise_chars = sum(v["chars"] for v in accum["noise"].values())
    if total_noise_chars > 50000:
        flags.append("- flag: noise %d chars" % total_noise_chars)
    # whole_reads flag
    if wr_items:
        roles_set = sorted(set(item["role"] for item in wr_items))
        flags.append("- flag: %d whole reads over %s by %s"
                     % (len(wr_items), _fmt(whole_read_chars), ", ".join(roles_set)))
        for item in wr_items:
            flags.append("  file: %s  role: %s  chars: %s"
                         % (item["file"], item["role"], _fmt(item["chars"])))
    # router ctx flag
    for r in router_result:
        if r["ctx_end"] > router_ctx_flag:
            flags.append("- flag: router end context %s over audit.router_ctx_flag"
                         % _fmt(r["ctx_end"]))

    return {
        "phase": phase,
        "span": span,
        "n_sessions": len(session_ids),
        "beside": beside,
        "reads_by_file": top_reads,
        "writes_by_file": top_writes,
        "results_by_kind": sorted(accum["results_by_kind"].items(), key=lambda x: -x[1]["chars"]),
        "whole_plan_reads": accum["whole_plan_reads"],
        "tool_source_reads": accum["tool_source_reads"],
        "spilled": {
            "n": len(accum["spilled"]["items"]),
            "chars": sum(sp["chars"] for sp in accum["spilled"]["items"]),
            "read_whole": spill_read_whole,
            "items": [{"file": sp["file"], "chars": sp["chars"],
                       "read_whole": sp["read_whole"],
                       "command": sp.get("command", ""),
                       "reader": sp.get("reader")}
                      for sp in accum["spilled"]["items"]],
        },
        "noise": accum["noise"],
        "mcp_blocks": {
            "n": accum["mcp_blocks"]["n"],
            "chars": accum["mcp_blocks"]["chars"],
            "servers": sorted(accum["mcp_blocks"]["servers"]),
        },
        "whole_reads": whole_reads_result,
        "carry_per_request": carry_result,
        "seed_floor": seed_floor_result,
        "outline_credit": outline_credit_result,
        "router": router_result,
        "warm_pings": warm_result,
        "toasts": toasts_result,
        "router_turns": router_turns_result,
        "retriever_reasks": reasks_result,
        "candidates": candidates,
        "flags": flags,
    }


# ------------------------------------------------------------------ render

def render_md(result):
    """Compact markdown tables, flags, price line.  Target: <= 60 lines."""
    lines = []
    phase = result.get("phase", "?")
    span = result.get("span")
    n_sessions = result.get("n_sessions", 0)
    cpr_hdr = result.get("carry_per_request") or {}
    n_requests = cpr_hdr.get("n_requests", 0)
    if span:
        span_end = span[1] if span[1] is not None else "open"
        lines.append("### Carry audit — phase %s (%s → %s UTC, %d sessions, %d requests)"
                     % (phase, span[0], span_end, n_sessions, n_requests))
    else:
        lines.append("### Carry audit — phase %s" % phase)
    for b in result.get("beside") or []:
        lines.append("- beside %s: session %s $%.2f (%d turns), not attributed"
                     % (phase, b["session_id"][:8], b["cost_usd"] or 0.0, b["n_turns"]))
    lines.append("")

    # reads_by_file (top rows)
    lines.append("| file (read) | chars | n |")
    lines.append("|---|---|---|")
    for fp, v in (result.get("reads_by_file") or [])[:10]:
        name = os.path.basename(fp) if fp != "other" else "other"
        lines.append("| %s | %s | %d |" % (name, _fmt(v["chars"]), v["n"]))

    # writes_by_file (top rows)
    lines.append("| file (write) | chars | n |")
    lines.append("|---|---|---|")
    for fp, v in (result.get("writes_by_file") or [])[:8]:
        name = os.path.basename(fp) if fp != "other" else "other"
        lines.append("| %s | %s | %d |" % (name, _fmt(v["chars"]), v["n"]))

    # results_by_kind
    lines.append("| result kind | chars | n |")
    lines.append("|---|---|---|")
    for kind, v in (result.get("results_by_kind") or [])[:10]:
        lines.append("| %s | %s | %d |" % (kind, _fmt(v["chars"]), v["n"]))
    mcp = result.get("mcp_blocks") or {}
    if mcp.get("n"):
        servers = ", ".join(mcp.get("servers", []))
        lines.append("| MCP blocks (%s) | %s | %d |" % (servers, _fmt(mcp["chars"]), mcp["n"]))

    # spilled
    sp = result.get("spilled") or {}
    if sp.get("n"):
        lines.append("- spilled: %d results, %s chars on disk, %d read whole"
                     % (sp["n"], _fmt(sp["chars"]), sp["read_whole"]))

    # whole_reads
    wr = result.get("whole_reads") or {}
    wr_t = wr.get("threshold", 20000)
    wr_n = wr.get("n", 0)
    lines.append("- whole reads over %s: %d" % (_fmt(wr_t), wr_n))

    # seed_floor
    sf = result.get("seed_floor") or {}
    if sf:
        parts = []
        for at in sorted(sf.keys()):
            v = sf[at]
            prev_str = ("{:,}".format(v["prev_median"])
                        if isinstance(v["prev_median"], int) else v["prev_median"])
            parts.append("%s {:,} (n %d, prev %s)".format(v["median"])
                         % (at, v["n"], prev_str))
        lines.append("- seed floor: %s" % " · ".join(parts))
    else:
        lines.append("- seed floor: (no retriever runs)")

    # outline credit
    oc = result.get("outline_credit") or {}
    lines.append("- outline credit: %d outlines · %d followed by a ranged read · %s chars credited"
                 % (oc.get("n", 0), oc.get("m", 0), _fmt(oc.get("chars", 0))))

    # warmer pings vs rewrites replaced; toasts by cause
    # 3.15 T3: cost = ping turns + relay turns + re-arm turns (the router's share of a ping)
    wp = result.get("warm_pings") or {}
    rt = result.get("router_turns") or {}
    w = wp.get("w", 0)
    relay_c, rearm_c = rt.get("cost", 0.0), rt.get("rearm_cost", 0.0)
    total = wp.get("a", 0.0) + relay_c + rearm_c
    cheaper = "n/a" if not w else ("yes" if total < wp.get("b", 0) else "no")
    lines.append("- warm pings: %d pings over %d runs, %d warmed waits over the TTL,"
                 " rewrites across warmed waits %d, waits past the cap %d,"
                 " cost: pings $%.4f + relay $%.4f + re-arm $%.4f = $%.4f"
                 " vs rewrites replaced $%.4f, cheaper than one rewrite: %s, undelivered %d"
                 % (wp.get("n", 0), wp.get("r", 0), w, wp.get("k", 0), wp.get("m", 0),
                    wp.get("a", 0.0), relay_c, rearm_c, total, wp.get("b", 0.0), cheaper,
                    wp.get("u", 0)))
    tc = result.get("toasts") or {}
    lines.append("- toasts by cause (waiting): %s"
                 % ", ".join("%s %d" % (c, tc.get(c, 0)) for c in TOAST_CAUSES))
    rt = result.get("router_turns") or {}
    lines.append("- router turns by cause: loop %d, relay %d, re-arm %d, other %d,"
                 " relay cost $%.4f" % (rt.get("loop", 0), rt.get("relay", 0),
                                        rt.get("re-arm", 0), rt.get("other", 0),
                                        rt.get("cost", 0.0)))
    ra = result.get("retriever_reasks") or {}
    lines.append("- retriever re-asks: %d of %d retriever briefs repeat a lookup of the same run"
                 % (ra.get("n", 0), ra.get("m", 0)))

    # router
    for r in (result.get("router") or []):
        growth_parts = []
        for tid, delta in (r.get("growth") or []):
            growth_parts.append("%s +%s" % (tid, _fmt(delta)))
        growth_str = ", ".join(growth_parts) if growth_parts else "(none)"
        top_parts = []
        for kind, chars, n in (r.get("top_kinds") or []):
            top_parts.append("%s %s/%d" % (kind, _fmt(chars), n))
        top_str = ", ".join(top_parts) if top_parts else "(none)"
        lines.append("- router: %s %s · requests %d · ctx at end %s"
                     " · growth %s · top: %s"
                     % (r["label"], r["session_id"][:8], r["requests"],
                        _fmt(r["ctx_end"]), growth_str, top_str))

    # noise
    noise = result.get("noise") or {}
    if noise:
        total_lines = sum(v.get("lines", 0) for v in noise.values())
        total_chars = sum(v["chars"] for v in noise.values())
        parts = ", ".join("%s: %d lines/%s" % (pat, v.get("lines", 0), _fmt(v["chars"]))
                         for pat, v in sorted(noise.items(), key=lambda x: -x[1]["chars"]))
        lines.append("- noise: %d lines %s chars — %s" % (total_lines, _fmt(total_chars), parts))

    # carry_per_request
    cpr = result.get("carry_per_request") or {}
    pr = cpr.get("prices") or {}
    lines.append("- price: read $%.2f/Mtok · 1h write $%.2f/Mtok · output $%.2f/Mtok (phase model mix)"
                 % (pr.get("read", 0), pr.get("write_1h", 0), pr.get("output", 0)))
    lines.append("- median requests after a read: %d" % cpr.get("median_requests_after_read", 0))
    if cpr.get("prev_chars_per_request"):
        lines.append("- carry/request: %.0f chars, %d requests (prev %.0f chars, %d requests, growth %.1f%%)"
                     % (cpr["chars_per_request"], cpr.get("n_requests", 0),
                        cpr["prev_chars_per_request"], cpr.get("prev_n_requests", 0),
                        cpr["growth"] * 100))
    else:
        lines.append("- carry/request: %.0f chars, %d requests"
                     % (cpr.get("chars_per_request", 0), cpr.get("n_requests", 0)))

    # candidates
    for c in (result.get("candidates") or []):
        status = "ratified" if c["ratified"] else "proposed"
        measured = " measured=%d tok" % c["measured"] if c["measured"] else ""
        lines.append("- candidate: %s (%s%s)" % (c["name"], status, measured))

    # flags (with detail lines under spilled and tool-source)
    for f in (result.get("flags") or []):
        lines.append(f)
        if "spilled" in f and "read whole" in f:
            sp = result.get("spilled") or {}
            for item in sp.get("items", []):
                if item.get("read_whole"):
                    lines.append("  command: %s  reader: %s"
                                 % (item.get("command") or "?", item.get("reader") or "?"))
        elif "tool-source reads" in f:
            ts = result.get("tool_source_reads") or {}
            for item in ts.get("items", []):
                lines.append("  %s  %s chars  %s"
                             % (item["path"], _fmt(item["chars"]), item["role"]))

    return "\n".join(lines) + "\n"


def _fmt(n):
    """Format a number with k/M suffixes."""
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1000:
        return "%.1fk" % (n / 1000)
    return str(n)


# ------------------------------------------------------------------ CLI

def main(argv=None, conn=None):
    """CLI entry point for ``pa_ledger.py audit``."""
    ap = argparse.ArgumentParser(prog="audit", description="Carry audit for a phase")
    ap.add_argument("--phase", required=True, help="Phase id (e.g. 3.5)")
    ap.add_argument("--project", default=".", help="Project root directory")
    ap.add_argument("--prev", default=None, help="Previous phase id for growth comparison")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--md", action="store_true", help="Markdown output (default)")
    fmt.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    close_conn = False
    if conn is None:
        db_file = paths.db_path()
        conn = sqlite3.connect("file:%s?mode=ro" % db_file, uri=True)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        from . import config
        cfg = config.load()
    except Exception:
        cfg = {}

    try:
        result = audit_phase(conn, cfg, os.path.abspath(args.project),
                             args.phase, prev_phase=args.prev)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(render_md(result), end="")
    finally:
        if close_conn:
            conn.close()
