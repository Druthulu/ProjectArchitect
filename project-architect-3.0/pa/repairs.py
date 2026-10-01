"""Versioned ledger repairs: one-time steps run once per package version (3.15 T12).

Public:
    STEPS                          ordered ``(id, detect(conn, cfg) -> plan|None, apply(conn, cfg, plan) -> counts)``
    step_ids()                     the step ids (``db.init_schema`` pre-stamps them on a fresh ledger)
    run(cfg=None, conn=None)       every unstamped step; then meta ``repairs_version`` = ``__version__``
    request(cfg=None)              SessionStart: spawn ``python -m pa.repairs`` detached (single flight)
    pop_notice()                   the user lines of finished repairs, once (``state/repairs_notice.json``)
    account_fallback_findings(conn, cfg)   the doctor's read-only list of unrepairable fallback switches

Steps: ``3.15/account-fallback`` (T12: re-file A-B-A fallback sessions to A);
``3.15.1/batch-gap`` (T15: main-run turns a pre-T7 Stop-hook batch left with gap_s NULL get their
real gap / cold / rewrite, from a recalc of the transcript or else from the previous ledger turn);
``3.15.2/savings-account`` (T20: a switch event re-filed to A whose detail still says B gets its
session's B rows up to the next switch re-filed to A; the fallback step now also rewrites the
switch details and the savings).

A step is stamped in meta ``repair.<id>`` (``fresh`` on a new ledger, else ``{ts, result|counts}``)
and never runs again; a failing step logs ``repair_step_failed`` and stays unstamped, but
``repairs_version`` is stamped anyway so a broken step does not respawn on every start.
Before a step applies, the ledger is copied with the sqlite backup API to
``<ledger dir>/backups/ledger.pre-<id>.<UTC stamp>.sqlite``.

Import cost: json, os, sys, time only at module level (SessionStart imports this lazily).
"""

import json
import os
import sys
import time

STEP_ACCOUNT_FALLBACK = "3.15/account-fallback"
STEP_BATCH_GAP = "3.15.1/batch-gap"
STEP_SAVINGS_ACCOUNT = "3.15.2/savings-account"
_LOCK_STALE_S = 1800           # any lock older than 30 min is stale
_START_GRACE_S = 30            # a requester's lock outlives the requester while the child starts


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _off(name):
    return os.environ.get(name, "").strip() not in ("", "0", "false", "False", "no")


def _lock_path():
    from . import paths

    return paths.state_path("repairs.lock")


def _notice_path():
    from . import paths

    return paths.state_path("repairs_notice.json")


# --------------------------------------------------------------------------- step: 3.15/account-fallback
#
# Before 3.15 T1 a failed ``claude auth status`` fell back to ``recalc_default_account`` (B) and
# refresh_account recorded that as a real switch: the session's later turns, samples and window
# instances were filed under B while the developer was still on A (the account in .claude.json).
# A session whose turns run A, then B, then A again (and whose switch came from A) is re-filed to A
# from its first A turn on; any other fallback-shaped switch is only reported (doctor) with the
# account-switch command that fixes it by hand.

def _accounts_ab(cfg):
    from . import accounts

    found = accounts._claude_json_email()
    a = found.get("email") if isinstance(found, dict) else None
    b = cfg.get("recalc_default_account") if isinstance(cfg, dict) else None
    if not a or not b or a == b:
        return None, None
    return a, b


def _has_rows(conn, sid, acct):
    for sql in ("SELECT 1 FROM turns WHERE session_id=? AND account=? LIMIT 1",
                "SELECT 1 FROM utilization WHERE session_id=? AND account=? LIMIT 1",
                "SELECT 1 FROM sessions WHERE session_id=? AND account=? LIMIT 1"):
        if conn.execute(sql, (sid, acct)).fetchone():
            return True
    return False


def _aba_since(conn, sid, a, b):
    """The session's first A turn ts when its turns run A, B, A (as a subsequence), else None."""
    runs = []
    first_a = None
    for acct, ts in conn.execute("SELECT account, ts FROM turns WHERE session_id=? AND account IS NOT NULL "
                                 "ORDER BY ts, msg_id", (sid,)).fetchall():
        if acct == a and first_a is None:
            first_a = ts
        if not runs or runs[-1] != acct:
            runs.append(acct)
    want = [a, b, a]
    i = 0
    for acct in runs:
        if i < 3 and acct == want[i]:
            i += 1
    return first_a if i == 3 else None


def _scan(conn, cfg):
    """``{A, B, repair: [...], other: [...]}`` or None when A or B is unknown or equal."""
    a, b = _accounts_ab(cfg)
    if a is None:
        return None
    repair, other, seen = [], [], set()
    rows = conn.execute("SELECT id, ts, session_id, detail_json FROM events WHERE kind='account_change' "
                        "AND session_id IS NOT NULL ORDER BY ts, id").fetchall()
    for ev_id, ts, sid, raw in rows:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("source") != "switch" or d.get("to") != b:
            continue
        frm = d.get("from")
        if not frm or frm == b or d.get("account_source") not in (None, "fallback"):
            continue                    # a file/cli switch is real
        if sid in seen or not _has_rows(conn, sid, b):
            continue
        seen.add(sid)
        cand = {"session_id": sid, "event_id": ev_id, "ts": ts, "from": frm, "to": b, "account": a}
        since = _aba_since(conn, sid, a, b) if frm == a else None
        if since:
            cand["since"] = since
            repair.append(cand)
        else:
            cand["command"] = ("pa_ledger.py account-switch --session %s --from %s --to %s --since %s"
                               % (sid, b, a, ts))
            other.append(cand)
    return {"A": a, "B": b, "repair": repair, "other": other}


def detect_account_fallback(conn, cfg):
    scan = _scan(conn, cfg)
    return scan if scan and scan["repair"] else None


def account_fallback_findings(conn, cfg):
    """Read-only: the fallback-shaped switches the step does not rewrite (each with its command)."""
    scan = _scan(conn, cfg)
    return list(scan["other"]) if scan else []


def _detail(raw):
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _set_detail(conn, ev_id, d, new):
    """Set ``new`` fields on an event's detail, keeping the original from/to as ``$.was``."""
    was = d.get("was") if isinstance(d.get("was"), dict) else {"from": d.get("from"), "to": d.get("to")}
    d = dict(d, **new)
    d["was"] = was
    conn.execute("UPDATE events SET detail_json=? WHERE id=?", (json.dumps(d), ev_id))


def _drop_empty_windows(conn, b, touched):
    """Delete ``b``'s window_instances among ``touched`` (window, resets_at) left with no utilization."""
    n = 0
    for win, ra in sorted(touched, key=lambda t: (str(t[0]), str(t[1]))):
        # resets_at may be stored as text or real on old rows: compare as integers
        left = conn.execute("SELECT 1 FROM utilization WHERE COALESCE(account, ?)=? AND window=? "
                            "AND CAST(resets_at AS INTEGER) IS CAST(? AS INTEGER) LIMIT 1",
                            (b, b, win, ra)).fetchone()
        if not left:
            n += conn.execute("DELETE FROM window_instances WHERE account=? AND window=? "
                              "AND CAST(resets_at AS INTEGER) IS CAST(? AS INTEGER)", (b, win, ra)).rowcount
    return n


def apply_account_fallback(conn, cfg, plan):
    """Re-file every A-B-A session's B rows from its first A turn to A; refit; rebuild the summary."""
    from . import accounts, fit, fsutil, ledger_cli, paths, running, summary

    a, b = plan["A"], plan["B"]
    counts = {"turns": 0, "events": 0, "sessions": 0, "utilization": 0, "window_instances": 0,
              "details": 0, "stamps": 0, "auth_cache": 0}
    tag = json.dumps(STEP_ACCOUNT_FALLBACK)
    touched = set()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in plan["repair"]:
            sid, since = c["session_id"], c["since"]
            for win, ra in conn.execute("SELECT DISTINCT window, resets_at FROM utilization "
                                        "WHERE session_id=? AND account=? AND ts>=?", (sid, b, since)):
                touched.add((win, ra))
            counts["turns"] += conn.execute("UPDATE turns SET account=? WHERE session_id=? AND account=? "
                                            "AND ts>=?", (a, sid, b, since)).rowcount
            counts["utilization"] += conn.execute("UPDATE utilization SET account=? WHERE session_id=? "
                                                  "AND account=? AND ts>=?", (a, sid, b, since)).rowcount
            counts["sessions"] += conn.execute("UPDATE sessions SET account=? WHERE session_id=? AND account=?",
                                               (a, sid, b)).rowcount
            counts["events"] += conn.execute("UPDATE events SET account=? WHERE session_id=? AND account=? "
                                             "AND (ts>=? OR id=?)", (a, sid, b, since, c["event_id"])).rowcount
            conn.execute("UPDATE events SET detail_json=json_set(detail_json, '$.repaired', json(?)) WHERE id=?",
                         (tag, c["event_id"]))
            # T20: the switch events' detail follows, so a recalc's account_timeline re-derives A
            for ev_id, raw in conn.execute("SELECT id, detail_json FROM events WHERE kind='account_change' "
                                           "AND session_id=? AND (ts>=? OR id=?)",
                                           (sid, since, c["event_id"])).fetchall():
                d = _detail(raw)
                new = {k: a for k in ("from", "to") if d.get(k) == b}
                if new:
                    _set_detail(conn, ev_id, d, new)
                    counts["details"] += 1
        counts["window_instances"] += _drop_empty_windows(conn, b, touched)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    for c in plan["repair"]:
        sid = c["session_id"]
        ledger_cli.write_savings(conn, sid, cfg)          # T20: measured savings follow the turns
        stamp = fsutil.read_json(accounts._stamp_path(sid), {}) or {}
        if stamp.get("account") == b:
            accounts.stamp_session(sid, a, source="repair")
            counts["stamps"] += 1
        try:
            if running.session(sid):
                running.patch_session(sid, {"account": a})
        except Exception:
            pass
    cache = fsutil.read_json(paths.auth_cache_path(), {}) or {}
    if cache.get("email") == b and cache.get("source") != "auth":
        try:
            os.remove(paths.auth_cache_path())
            counts["auth_cache"] = 1
        except OSError:
            pass
    fit.refit_all(conn, cfg, account=a)
    fit.refit_all(conn, cfg, account=b)
    summary.rebuild(conn, cfg)
    return counts


# --------------------------------------------------------------------------- step: 3.15.1/batch-gap
#
# Before 3.15 T7 the Stop hook read each batch of main-session requests from its stored offset and
# gave the batch's first request index 0, gap_s NULL, cold 0, rewrite 0: a cold resume after a long
# idle was booked warm.  A main-run turn with gap_s NULL and an earlier turn of the same run is one of
# those.  Its session is re-ingested from its transcript when one is found (``recalc``); any row that
# still matches takes its gap from the previous ledger turn of the run, cold by the Stop hook's
# threshold, rewrite by the analyzers' rule as a non-first request.

_SQL_BATCH_GAP = ("SELECT t.session_id, t.msg_id FROM turns t WHERE t.kind='api' AND t.run_id=t.session_id "
                  "AND t.gap_s IS NULL AND t.ts IS NOT NULL AND EXISTS (SELECT 1 FROM turns p "
                  "WHERE p.run_id=t.run_id AND p.kind='api' AND p.ts IS NOT NULL AND p.ts<t.ts)")
_COLD_GAP_S = 3600             # the Stop hook's cold threshold (transcript.read_new_requests default)


def _batch_gap_rows(conn, sids=None):
    sql, args = _SQL_BATCH_GAP, ()
    if sids is not None:
        sids = sorted(sids)
        sql += " AND t.session_id IN (%s)" % ",".join("?" * len(sids))
        args = tuple(sids)
    plan = {}
    for sid, msg in conn.execute(sql + " ORDER BY t.session_id, t.ts, t.msg_id", args).fetchall():
        plan.setdefault(sid, []).append(msg)
    return plan


def detect_batch_gap(conn, cfg):
    plan = _batch_gap_rows(conn)
    return plan or None


def _iso_s(ts):
    from datetime import datetime

    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def apply_batch_gap(conn, cfg, plan):
    """Recalc each planned session from its transcript; fix the rest from the turn rows; refit."""
    from . import fit, ledger_cli, summary, transcript

    counts = {"turns": 0, "sessions_recalc": 0, "sessions_rows": 0, "unresolved": 0}
    sids = sorted(plan)
    roots = ledger_cli.projects_roots(None)
    stats = {"sessions": 0, "runs": 0, "turns": 0, "retrievals": 0, "savings": 0,
             "residuals": 0, "cost": 0.0, "files": 0, "groups": {}}
    for sid in sids:
        item = ledger_cli.find_transcript(sid, roots)
        if not item:
            continue
        opts = ledger_cli.build_parser().parse_args(["recalc", "--session", sid])
        ledger_cli.recalc_session_file(conn, item["path"], item["slug"], item["sid"], cfg, opts, stats)
        counts["sessions_recalc"] += 1

    left = _batch_gap_rows(conn, sids)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sid, msgs in sorted(left.items()):
            fixed = 0
            for msg in msgs:
                row = conn.execute("SELECT ts, ctx, cache_write FROM turns WHERE msg_id=?", (msg,)).fetchone()
                if not row:
                    continue
                prev = conn.execute("SELECT MAX(ts) FROM turns WHERE run_id=? AND kind='api' AND ts<?",
                                    (sid, row[0])).fetchone()
                if not prev or prev[0] is None:
                    continue
                gap = _iso_s(row[0]) - _iso_s(prev[0])
                rewrite = transcript.rewrite_rule(1, int(row[1] or 0), int(row[2] or 0))
                conn.execute("UPDATE turns SET gap_s=?, cold=?, rewrite=? WHERE msg_id=?",
                             (gap, int(gap > _COLD_GAP_S), int(rewrite), msg))
                fixed += 1
            counts["sessions_rows"] += 1 if fixed else 0
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    still = _batch_gap_rows(conn, sids)
    counts["unresolved"] = sum(len(v) for v in still.values())
    counts["turns"] = sum(len(v) for v in plan.values()) - counts["unresolved"]
    for sid in sids:
        ledger_cli.write_savings(conn, sid, cfg)
    accts = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT account FROM turns WHERE session_id IN (%s) AND account IS NOT NULL"
        % ",".join("?" * len(sids)), tuple(sids)).fetchall()})
    for acct in accts:
        fit.refit_all(conn, cfg, account=acct)
    summary.rebuild(conn, cfg)
    return counts


# --------------------------------------------------------------------------- step: 3.15.2/savings-account
#
# Every insert site writes an account_change event with ``events.account`` = detail ``to``.  A
# mismatch is an event re-filed by hand (T2) or by a pre-T20 3.15 run whose detail still names B:
# a later recalc's account_timeline then put the session's turns back on B (3.15.1 did, T20).  Each
# such event's session gets its B rows from the event to the session's next switch re-filed to A
# (A = events.account), the detail set to A (``$.was`` keeps the original), its savings rewritten.

def detect_savings_account(conn, cfg):
    plan = []
    for ev_id, ts, sid, acct, raw in conn.execute(
            "SELECT id, ts, session_id, account, detail_json FROM events WHERE kind='account_change' "
            "AND session_id IS NOT NULL AND account IS NOT NULL ORDER BY ts, id").fetchall():
        to = _detail(raw).get("to")
        if to and to != acct:
            plan.append({"event_id": ev_id, "ts": ts, "session_id": sid, "A": acct, "B": to})
    return {"events": plan} if plan else None


def apply_savings_account(conn, cfg, plan):
    """Re-file each mismatched switch's B rows up to the session's next switch to A; savings; refit."""
    from . import fit, ledger_cli, summary

    counts = {"turns": 0, "utilization": 0, "events": 0, "sessions": 0, "details": 0,
              "window_instances": 0}
    sids, accts = set(), set()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in plan["events"]:
            sid, a, b, since = c["session_id"], c["A"], c["B"], c["ts"]
            nxt = conn.execute("SELECT MIN(ts) FROM events WHERE kind='account_change' AND session_id=? "
                               "AND ts>?", (sid, since)).fetchone()[0]
            rng, args = "ts>=?", (since,)
            if nxt is not None:
                rng, args = "ts>=? AND ts<?", (since, nxt)
            touched = set(conn.execute("SELECT DISTINCT window, resets_at FROM utilization WHERE session_id=? "
                                       "AND account=? AND " + rng, (sid, b) + args).fetchall())
            for tbl in ("turns", "utilization", "events"):
                counts[tbl] += conn.execute("UPDATE %s SET account=? WHERE session_id=? AND account=? AND %s"
                                            % (tbl, rng), (a, sid, b) + args).rowcount
            counts["sessions"] += conn.execute("UPDATE sessions SET account=? WHERE session_id=? AND account=?",
                                               (a, sid, b)).rowcount
            row = conn.execute("SELECT detail_json FROM events WHERE id=?", (c["event_id"],)).fetchone()
            _set_detail(conn, c["event_id"], _detail(row[0] if row else None), {"to": a})
            counts["details"] += 1
            counts["window_instances"] += _drop_empty_windows(conn, b, touched)
            sids.add(sid)
            accts.update((a, b))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    for sid in sorted(sids):
        ledger_cli.write_savings(conn, sid, cfg)
    for acct in sorted(accts):
        fit.refit_all(conn, cfg, account=acct)
    summary.rebuild(conn, cfg)
    return counts


STEPS = [
    (STEP_ACCOUNT_FALLBACK, detect_account_fallback, apply_account_fallback),
    (STEP_BATCH_GAP, detect_batch_gap, apply_batch_gap),
    (STEP_SAVINGS_ACCOUNT, detect_savings_account, apply_savings_account),
]


_NOTICE_TEXT = {STEP_ACCOUNT_FALLBACK: "re-filed or cleared",
                STEP_BATCH_GAP: "(turns' gaps recomputed)",
                STEP_SAVINGS_ACCOUNT: "re-filed to the switch's account, savings rewritten"}


def step_ids():
    return [step[0] for step in STEPS]


# --------------------------------------------------------------------------- runner

def _backup(conn, step_id):
    import sqlite3

    from . import paths

    d = os.path.join(paths.ledger_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "ledger.pre-%s.%s.sqlite" % (step_id.replace("/", "_"),
                                                         time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
    dst = sqlite3.connect(path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return path


def _append_notice(line):
    from . import fsutil

    path = _notice_path()
    lines = fsutil.read_json(path, []) or []
    if not isinstance(lines, list):
        lines = []
    lines.append(line)
    fsutil.atomic_write_json(path, lines, indent=1)


def pop_notice():
    """The pending notice lines (oldest first), deleting the file; ``[]`` when none."""
    path = _notice_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = json.load(fh)
    except (OSError, ValueError):
        lines = []
    try:
        os.remove(path)
    except OSError:
        pass
    return [str(x) for x in lines if x] if isinstance(lines, list) else []


def run(cfg=None, conn=None):
    """Run every unstamped step; returns ``{step id: counts | None (nothing) }`` of the steps run."""
    from . import __version__, config, db, log

    if cfg is None:
        cfg = config.load()
    own = conn is None
    if own:
        from .hooks import open_db
        conn = open_db()
    results = {}
    try:
        for step_id, detect, apply in STEPS:
            key = "repair.%s" % step_id
            if db.get_meta(conn, key) is not None:
                continue
            try:
                plan = detect(conn, cfg)
                if plan is None:
                    db.set_meta(conn, key, json.dumps({"ts": _now(), "result": "nothing"}))
                    log.log("repair_step", step=step_id, result="nothing")
                    results[step_id] = None
                    continue
                backup = _backup(conn, step_id)
                counts = apply(conn, cfg, plan)
                db.set_meta(conn, key, json.dumps({"ts": _now(), "counts": counts}))
                log.log("repair_step", step=step_id, result="applied", backup=backup, **counts)
                results[step_id] = counts
                done = ", ".join("%s %d" % (k, v) for k, v in counts.items() if v)
                if done:
                    what = _NOTICE_TEXT.get(step_id, "re-filed or cleared")
                    _append_notice("PA3 repaired the usage ledger (%s): %s %s; backup %s"
                                   % (step_id, done, what, backup))
            except Exception as exc:
                log.log("repair_step_failed", step=step_id, exc=type(exc).__name__, msg=str(exc)[:200])
        db.set_meta(conn, "repairs_version", __version__)
    finally:
        if own:
            db.close(conn)
    return results


# --------------------------------------------------------------------------- detached request (single flight)

def _lock_stale(lock):
    """True when ``lock`` is missing, older than 30 min, or its owner is dead."""
    try:
        age = time.time() - os.path.getmtime(lock)
        with open(lock, encoding="utf-8") as fh:
            parts = fh.read().split()
    except OSError:
        return True
    if age > _LOCK_STALE_S:
        return True
    try:
        pid = int(parts[0])
    except (IndexError, ValueError):
        return True
    from . import warmer

    if warmer.pid_alive(pid):
        return False
    role = parts[1] if len(parts) > 1 else "repairs"
    return role != "requester" or age > _START_GRACE_S


def _take_lock(lock):
    """Create ``lock`` exclusively (taking over a stale one); False when it is held."""
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    for _attempt in (0, 1):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not _lock_stale(lock):
                return False
            try:
                os.remove(lock)
            except OSError:
                return False
            continue
        except OSError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("%d requester\n" % os.getpid())
        return True
    return False


def _write_lock(lock, role):
    from . import fsutil

    fsutil.atomic_write_text(lock, "%d %s\n" % (os.getpid(), role))


def request(cfg=None):
    """Spawn one detached ``python -m pa.repairs`` unless one holds the lock; True when spawned.

    Honours ``PA_LEDGER_OFF`` / ``PA_HOOKS_OFF``; never raises.
    """
    try:
        if _off("PA_LEDGER_OFF") or _off("PA_HOOKS_OFF"):
            return False
        lock = _lock_path()
        if not _take_lock(lock):
            return False
        from . import notify

        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # -s -P, not -I: -I ignores PYTHONPATH, which is how the installed copy is found
        cmd = [sys.executable, "-s", "-P", "-X", "utf8", "-m", "pa.repairs"]
        if notify.spawn_detached(cmd, {"PYTHONPATH": pkg}):
            return True
        try:
            os.remove(lock)
        except OSError:
            pass
    except Exception as exc:
        try:
            from . import log

            log.log("repairs_request_failed", exc=type(exc).__name__, msg=str(exc)[:200])
        except Exception:
            pass
    return False


def main(argv=None):
    """``python -m pa.repairs``: run the steps, then release the lock; prints nothing."""
    lock = _lock_path()
    try:
        _write_lock(lock, "repairs")
        run()
    except Exception as exc:
        try:
            from . import log

            log.log("repairs_failed", exc=type(exc).__name__, msg=str(exc)[:200])
        except Exception:
            pass
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
