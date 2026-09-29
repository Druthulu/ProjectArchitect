"""Phase identity and time spans from the plan files and git history.

Public:
    plan_phase_id(root) -> str | None
    BESIDE, is_beside(tag) -> bool, session_phase(root, sid, status=None) -> str | None
    beside_session(conn, sid) -> bool
    restamp_runs(conn, sid, tag) -> int, heal_run_phases(conn, sid=None) -> int
    plan_hash(path) -> str | None
    phase_span(root, phase) -> (t0_iso, t1_iso | None)
    span_end_or_now(t1) -> str
    phase_sessions(conn, root, phase) -> list[str]
    beside_costs(conn, root, phase, span=None) -> list[dict]

Stdlib only; no caching across calls.
"""

import os
import re
import subprocess


# ------------------------------------------------------------------ plan identity

_PHASE_RE = re.compile(r"^#\s+Phase\s+(\S+)")
_HASH_RE = re.compile(r"Plan-hash:\s*(\S+)")


def plan_phase_id(root):
    """The ``<id>`` from the first ``# Phase <id> -- ...`` line in the current plan.

    Returns None when no plan is open.
    """
    plan = os.path.join(root, "phase-ends", "current", "PHASE_PLAN.md")
    try:
        with open(plan, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _PHASE_RE.match(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


# T28: only the router_session owns the phase; any other session is tagged ``beside <phase>``
BESIDE = "beside "


def is_beside(tag):
    """True when *tag* is a ``beside <phase>`` stamp."""
    return isinstance(tag, str) and tag.startswith(BESIDE)


def session_phase(root, sid, status=None):
    """The phase stamp for session *sid*: ``<phase>`` on the owner, ``beside <phase>`` elsewhere.

    phase = ``status["phase"]`` else ``plan_phase_id(root)``; None when no phase.
    Owner = ``status["router_session"]``; no owner means every session owns it.
    """
    status = status or {}
    phase = status.get("phase") or (plan_phase_id(root) if root else None)
    if not phase:
        return None
    owner = status.get("router_session")
    if not owner or sid == owner:
        return phase
    return BESIDE + phase


_BESIDE_SQL = ("SELECT 1 FROM sessions WHERE session_id=? AND phase LIKE 'beside %'"
               " UNION ALL SELECT 1 FROM agent_runs WHERE session_id=? AND phase LIKE 'beside %'"
               " LIMIT 1")


def beside_session(conn, sid):
    """True when *sid*'s ``sessions.phase`` or any of its ``agent_runs.phase`` is beside-tagged."""
    return conn.execute(_BESIDE_SQL, (sid, sid)).fetchone() is not None


_RESTAMP_TABLES = ("agent_runs", "savings", "tool_calls")


def restamp_runs(conn, sid, tag):
    """T31: restamp *sid*'s agent_runs/savings/tool_calls rows in *tag*'s family to *tag*.

    Family = ``P`` or ``beside P`` with P = *tag* minus BESIDE; other phases stay.
    Returns rows changed.
    """
    if not tag:
        return 0
    base = tag[len(BESIDE):] if is_beside(tag) else tag
    n = 0
    for table in _RESTAMP_TABLES:
        cur = conn.execute(
            "UPDATE %s SET phase=? WHERE session_id=? AND phase IS NOT ? AND phase IN (?, ?)" % table,
            (tag, sid, tag, base, BESIDE + base))
        n += max(cur.rowcount, 0)
    return n


def heal_run_phases(conn, sid=None):
    """Restamp runs of every session (or *sid*) with a non-null ``sessions.phase`` to it (T31)."""
    sql = "SELECT session_id, phase FROM sessions WHERE phase IS NOT NULL"
    args = ()
    if sid:
        sql += " AND session_id=?"
        args = (sid,)
    return sum(restamp_runs(conn, r[0], r[1]) for r in conn.execute(sql, args).fetchall())


def plan_hash(path):
    """The ``Plan-hash:`` value from *path*, or None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _HASH_RE.search(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


# ------------------------------------------------------------------ time span

_APPROVED_RE = re.compile(r"^Approved:\s*(\S+)")


def _to_utc_z(iso_str):
    """Normalize an ISO-8601 timestamp to a UTC ``Z`` string for SQL comparison.

    Handles offset timestamps (``-06:00``), naive (treated as local), and
    already-UTC ``Z`` strings.
    """
    import datetime as _dt
    s = str(iso_str).strip()
    if s.endswith("Z"):
        return s
    try:
        parsed = _dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return s
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # treat as local time
    utc = parsed.astimezone(_dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _approved_date(path):
    """The ``Approved:`` date from a plan file, or None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _APPROVED_RE.match(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def _git_pickaxe_t0(root, hash_val):
    """The oldest commit time from ``git log --reverse -S<hash> -- phase-ends`` (the approval)."""
    try:
        result = subprocess.run(
            ["git", "log", "--reverse", "--format=%cI", "-S%s" % hash_val, "--", "phase-ends"],
            cwd=root, capture_output=True, text=True, timeout=15)
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if lines:
            return lines[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _git_archive_time(root, phase):
    """The commit time that added ``phase-ends/phase-<N>/PHASE_PLAN.md`` (the archive)."""
    archived_path = "phase-ends/phase-%s/PHASE_PLAN.md" % phase
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--diff-filter=A", "--", archived_path],
            cwd=root, capture_output=True, text=True, timeout=15)
        line = result.stdout.strip()
        if line:
            return line
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # was .now().astimezone().isoformat()


def _plan_files(phase_dir):
    """All ``PHASE_PLAN*.md`` files in *phase_dir* (the main plan and any v<k> replans)."""
    try:
        return [os.path.join(phase_dir, f) for f in os.listdir(phase_dir)
                if f.startswith("PHASE_PLAN") and f.endswith(".md")]
    except OSError:
        return []


def phase_span(root, phase):
    """``(t0_iso, t1_iso)`` for *phase*: git-derived when available, else Approved-date fallback.

    Collects every ``PHASE_PLAN*.md`` in the phase's folder (including v<k> replans)
    and uses the earliest approval as t0.  When the phase is archived, t1 is the
    archive commit's time; otherwise t1 is ``None`` (the phase is still open --
    callers that need an upper bound for selection call ``span_end_or_now``).
    """
    archived_dir = os.path.join(root, "phase-ends", "phase-%s" % phase)
    current_dir = os.path.join(root, "phase-ends", "current")
    is_archived = os.path.isdir(archived_dir) and os.path.isfile(
        os.path.join(archived_dir, "PHASE_PLAN.md"))
    phase_dir = archived_dir if is_archived else current_dir

    # collect hashes from every plan version and find the earliest t0
    t0 = None
    earliest_approved = None
    for pf in _plan_files(phase_dir):
        h = plan_hash(pf)
        if h:
            candidate = _git_pickaxe_t0(root, h)
            if candidate and (t0 is None or candidate < t0):
                t0 = candidate
        # also track approved dates as fallback
        ad = _approved_date(pf)
        if ad and (earliest_approved is None or ad < earliest_approved):
            earliest_approved = ad

    # t1: for archived phases, the commit that added phase-ends/phase-<N>/PHASE_PLAN.md;
    # an open phase has no end yet.
    t1 = None
    if is_archived:
        t1 = _git_archive_time(root, phase)
        if not t1:
            t1 = _now_iso()

    # fallbacks
    if not t0:
        if earliest_approved:
            t0 = earliest_approved + "T00:00:00"
        else:
            t0 = _now_iso()

    return _to_utc_z(t0), (_to_utc_z(t1) if t1 else None)


def span_end_or_now(t1):
    """*t1* when given, else the current UTC time.

    For callers that need a concrete upper bound (session/record selection)
    when ``phase_span`` reports an open phase (``t1 is None``).
    """
    return t1 if t1 else _now_iso()


# ------------------------------------------------------------------ session selection

def _norm_project(path):
    """Normalize a project path (same as savings._norm_project)."""
    text = str(path or "").strip()
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text))


def phase_sessions(conn, root, phase):
    """Session ids for *phase*: tagged sessions union project sessions inside the span.

    Returns a sorted list.
    """
    norm = _norm_project(root)
    rows = conn.execute(
        "SELECT session_id, project, cwd, phase FROM sessions").fetchall()

    sids_project = []
    tagged = set()
    for r in rows:
        p = _norm_project(r["project"] or r["cwd"])
        if p != norm:
            continue
        sids_project.append(r["session_id"])
        if r["phase"] == phase:
            tagged.add(r["session_id"])

    if not sids_project:
        return []

    # also check agent_runs.phase
    for sid in sids_project:
        if sid in tagged:
            continue
        hit = conn.execute(
            "SELECT 1 FROM agent_runs WHERE session_id=? AND phase=? LIMIT 1",
            (sid, phase)).fetchone()
        if hit:
            tagged.add(sid)

    # time span: git-derived for the phase; an open phase's upper bound is now
    t0, t1 = phase_span(root, phase)
    t1 = span_end_or_now(t1)

    # project sessions with turns inside [t0, t1]
    result = set(tagged)
    for sid in sids_project:
        if sid in result or beside_session(conn, sid):
            continue
        hit = conn.execute(
            "SELECT 1 FROM turns WHERE session_id=? AND ts>=? AND ts<=? LIMIT 1",
            (sid, t0, t1)).fetchone()
        if hit:
            result.add(sid)

    return sorted(result)


def beside_costs(conn, root, phase, span=None):
    """Project sessions stamped ``beside <phase>`` (sessions or agent_runs), T28.

    Returns ``[{session_id, cost_usd, n_turns, started}]`` sorted by started, sid;
    cost/turns = api turns, inside *span* ``(t0, t1)`` when given.
    """
    norm = _norm_project(root)
    tag = BESIDE + phase
    rows = conn.execute(
        "SELECT session_id, project, cwd, started FROM sessions WHERE phase=?"
        " OR session_id IN (SELECT session_id FROM agent_runs WHERE phase=?)",
        (tag, tag)).fetchall()
    out = []
    for r in rows:
        if _norm_project(r["project"] or r["cwd"]) != norm:
            continue
        sql = ("SELECT COALESCE(SUM(cost_usd), 0) AS c, COUNT(*) AS n FROM turns"
               " WHERE session_id=? AND COALESCE(kind, 'api')='api'")
        args = [r["session_id"]]
        if span:
            sql += " AND ts>=? AND ts<=?"
            args += [span[0], span[1]]
        t = conn.execute(sql, args).fetchone()
        out.append({"session_id": r["session_id"], "cost_usd": round(t["c"] or 0.0, 6),
                    "n_turns": t["n"], "started": r["started"]})
    out.sort(key=lambda d: (d["started"] or "", d["session_id"]))
    return out
