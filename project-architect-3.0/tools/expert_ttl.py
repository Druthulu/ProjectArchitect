#!/usr/bin/env python3
"""expert_ttl.py -- the cache TTL for a task's expert: prints exactly ``1h`` or ``5m`` (3.15 T5).

    PY tools/expert_ttl.py [--coder opus55|none] [--project <root>]

``1h`` -> expert-opus55 / expert-fable; ``5m`` -> the ``-5m`` twin (planner-phase).
``--coder none`` -> 5m (the expert never waits on a coder).  Otherwise the project's
finished expert runs (ledger, all roots, deduped by run_id): fewer than
``ttl_choice.min_runs`` -> 1h; mean per-run idle minutes >= ``ttl_choice.idle_min``
-> 1h, else 5m (3.15 T5.1: mean, not median; cost is linear in idle minutes).  Per-run idle
= sum of waits > 300 s, each clipped at 60 min; a wait runs from a non-ping
turn to the next non-ping turn (gap_s of the ping turns between plus the ending
turn); ping turn = the run's first turn 0..60 s after a ``warm_ping`` event of that
run (as ``pa/audit.py`` ``_compute_warm_pings``).  Any error -> 1h.  Exit 0; stdout
carries the answer and nothing else.
"""

import argparse
import contextlib
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPERTS = ("expert-opus55", "expert-fable", "expert-opus55-5m", "expert-fable-5m")
WAIT_S = 300            # a wait longer than the 5m TTL is idle
PING_WINDOW_S = 60      # ping turn: first turn within this after a warm_ping
CLIP_S = 3600           # a wait counts at most 60 min (past it both TTLs pay bounded costs)
FALLBACK = {"min_runs": 5, "idle_min": 15}   # the installed 3.14.2 config has no ttl_choice


def _pa_home():
    """<script>/.. when it holds pa/, else ~/.claude/pa3."""
    return HERE.parent if (HERE.parent / "pa").is_dir() else Path.home() / ".claude" / "pa3"


def _pa():
    root = str(_pa_home())
    if root not in sys.path:
        sys.path.insert(0, root)
    import pa
    import pa.config
    import pa.db
    import pa.transcript
    return pa


def norm(path):
    """Comparable project path: ``/`` separators, no trailing ``/``, ``/mnt/<d>/x`` == ``<d>:/x``,
    drive paths case-folded."""
    s = str(path or "").replace("\\", "/").rstrip("/")
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", s)
    if m:
        s = m.group(1) + ":" + (m.group(2) or "")
    if re.match(r"^[a-zA-Z]:", s):
        s = s.casefold()
    return s


def git_root():
    """The cwd's git root, else the cwd."""
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd()


def idle_s(turns, ping_ts, parse_ts):
    """Idle seconds of one run: ``turns`` = [(ts, gap_s)] by ts; ``ping_ts`` = its warm_ping ts."""
    tts = [parse_ts(t) for t, _g in turns]
    ping_idx = set()
    for ets in (parse_ts(p) for p in ping_ts):
        if ets is None:
            continue
        for i, t in enumerate(tts):
            if t is not None and 0 <= (t - ets).total_seconds() <= PING_WINDOW_S and i not in ping_idx:
                ping_idx.add(i)
                break
    idle, wait, started = 0.0, 0.0, False
    for i, (_t, gap) in enumerate(turns):
        if i in ping_idx:
            wait += gap or 0.0
            continue
        if started:
            wait += gap or 0.0
            if wait > WAIT_S:
                idle += min(wait, CLIP_S)
        started, wait = True, 0.0
    return idle


def _conn_idles(conn, target, seen, parse_ts):
    """``{run_id: idle_min}`` of *conn*'s finished expert runs of project *target* not in *seen*."""
    ph = ",".join("?" * len(EXPERTS))
    rows = conn.execute(
        "SELECT r.run_id, s.project FROM agent_runs r JOIN sessions s ON s.session_id = r.session_id"
        " WHERE r.agent_type IN (%s) AND r.ended IS NOT NULL ORDER BY r.run_id" % ph,
        EXPERTS).fetchall()
    runs = [r[0] for r in rows if r[0] not in seen and norm(r[1]) == target]
    if not runs:
        return {}
    want = set(runs)
    pings = {}
    for ts, rid, detail in conn.execute(
            "SELECT ts, run_id, detail_json FROM events WHERE kind = 'warm_ping' ORDER BY ts, id"):
        try:
            d = json.loads(detail or "{}")
        except ValueError:
            d = {}
        rid = (d.get("run_id") if isinstance(d, dict) else None) or rid
        if rid in want:
            pings.setdefault(rid, []).append(ts)
    out = {}
    for rid in runs:
        turns = [(t[0], t[1]) for t in conn.execute(
            "SELECT ts, gap_s FROM turns WHERE run_id = ? ORDER BY ts, msg_id", (rid,))]
        out[rid] = idle_s(turns, pings.get(rid, []), parse_ts) / 60.0
    return out


def collect(project, cfg=None):
    """``({run_id: idle_min}, ttl_choice)`` for *project* over every ledger root."""
    pa = _pa()
    cfg = pa.config.load() if cfg is None else cfg
    choice = dict(FALLBACK)
    choice.update(cfg.get("ttl_choice") or {})
    readers = pa.db.union_readers(cfg.get("extra_roots") or [], include_local=True)
    target, idles = norm(project), {}
    try:
        for r in readers:
            idles.update(_conn_idles(r["conn"], target, idles, pa.transcript.parse_ts))
    finally:
        for r in readers:
            with contextlib.suppress(Exception):
                r["conn"].close()
    return idles, choice


def choose(coder, project, cfg=None):
    """``"1h"`` or ``"5m"``."""
    if coder == "none":
        return "5m"
    idles, choice = collect(project, cfg)
    if len(idles) < choice["min_runs"]:
        return "1h"
    return "1h" if statistics.mean(idles.values()) >= choice["idle_min"] else "5m"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--coder", default="opus55", help="opus55 (default) or none")
    ap.add_argument("--project", default=None, help="project root (default: the cwd's git root)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:            # --help
            raise
        args = None
    ans = "1h"
    if args is not None:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                ans = choose(args.coder, args.project or git_root())
        except Exception:            # any error -> 1h
            ans = "1h"
    out = getattr(sys.stdout, "buffer", None)
    if out is not None:              # bytes: no \r\n translation on Windows
        sys.stdout.flush()
        out.write((ans + "\n").encode("ascii"))
        out.flush()
    else:
        sys.stdout.write(ans + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
