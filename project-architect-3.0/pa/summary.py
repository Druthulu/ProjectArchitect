"""``summary.json`` -- everything the statusline reads (design A section B.3).

Public:
    rebuild(conn, cfg=None, windows_only=False, sessions_only=False) -> dict
    accounts_block(conn, cfg), sessions_block(conn, cfg), alerts_block(conn, cfg)
    projects_block(conn, cfg=None, readers=None, accounts=None)     (T12, per-project usage/savings)
    window_duration(window, cfg), window_started_at(window, resets_at, cfg)
    period_start(cfg, now=None)
    read(), write(doc)
    request_rebuild(cfg, sessions_only=False)   (T5: hooks; detached single-flight rebuild)
    run_rebuild(sessions_only=False), main()     (the child: ``python -m pa.summary --rebuild``)

Only this module writes ``summary.json``, always under
``fsutil.locked_update``; the statusline only ever reads it.  ``windows_only``
(statusline sampler, on a percent change) and ``sessions_only`` (Stop hook)
rewrite one block and leave the rest of the document untouched, so two writers
racing cannot drop each other's work.

v0 scope: the ``windows`` entries carry ``pct_last``/``pct_ts``/``resets_at``/
``started_at``/``cost_in_window`` from the ``utilization`` samples, with
``pct_per_dollar: null`` and ``fit_quality: "none"`` -- the calibration fit
(``pa/fit.py``, design E.3) is a later milestone and fills those two in place.
"""

import json
import os
import time

SCHEMA = 1
SESSION_WINDOW_H = 48
WEEKS_FIELDS = ("weeks_used", "weeks_saved", "unrated_usd", "unrated_saved_usd")  # fix-9


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _iso(epoch):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OSError):
        return None


def _epoch(val):
    """Accept an epoch int, a numeric string or an ISO stamp (recon H2)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    text = str(val).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        pass
    from . import transcript

    ts = transcript.parse_ts(text)
    return int(ts.timestamp()) if ts else None


# --------------------------------------------------------------------------- config helpers

def window_duration(window, cfg=None):
    table = {"five_hour": 18000, "default": 604800}
    if isinstance(cfg, dict):
        table.update(((cfg.get("fit") or {}).get("window_durations_s") or {}))
    name = str(window or "")
    if name in table:
        return int(table[name])
    return int(table.get("default", 604800))


def window_started_at(window, resets_at, cfg=None):
    ep = _epoch(resets_at)
    return (ep - window_duration(window, cfg)) if ep else None


def period_start(cfg=None, now=None, account=None):
    """First day of the current billing period from the account's ``renewal_day``; None when the account has
    none (3.11 T9: never guessed; the month figures then show a dash)."""
    from . import config as _config

    day = _config.renewal_day_for(cfg, account)
    if day is None:
        return None
    t = time.gmtime(now if now is not None else time.time())
    year, month = t.tm_year, t.tm_mon
    if t.tm_mday < day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return "%04d-%02d-%02d" % (year, month, min(max(day, 1), 28))


def _cutoff_iso(hours):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600.0))


def era_start(conn, cfg=None):
    """The PA3 era boundary: ``statusline.lifetime_since`` when set, else
    ``meta.created``.  Returns an ISO string or None (no boundary)."""
    from . import config as _config, db as _db

    override = _config.get(cfg, "statusline.lifetime_since") if cfg else None
    if override and isinstance(override, str):
        return override
    return _db.get_meta(conn, "created")


def _pre_era_sids(conn, era_iso):
    """Session ids that predate the PA3 era: ``account_source='assumed'`` or
    ``started < era_iso``."""
    if not era_iso:
        return set()
    try:
        rows = conn.execute(
            "SELECT session_id FROM sessions "
            "WHERE account_source = 'assumed' OR (started IS NOT NULL AND started < ?)",
            (era_iso,)).fetchall()
    except Exception:
        return set()
    return {r[0] if isinstance(r, (list, tuple)) else r["session_id"] for r in rows}


# --------------------------------------------------------------------------- blocks

def _labels(cfg):
    out = {}
    if isinstance(cfg, dict):
        for email, row in (cfg.get("accounts") or {}).items():
            if isinstance(row, dict):
                out[email] = row.get("label")
    return out


def _cost_in_window(conn, account, window, started_at, readers=None):
    """Σ over sessions of (last sample cost − cost at the window start), design E.4.

    ``readers``: optional ``db.union_readers`` list; when given, sessions from
    every reader for the same account are included (deduped by session_id).
    """
    if not started_at:
        return 0.0
    start_iso = _iso(started_at)
    total = 0.0
    seen_sids = set()

    def _sum_conn(c, default_account=None):
        nonlocal total
        rows = c.execute(
            "SELECT DISTINCT session_id FROM utilization "
            "WHERE COALESCE(account, ?) IS ? AND window=? AND ts>=?",
            (default_account, account, window, start_iso)).fetchall()
        for (sid,) in rows:
            if sid in seen_sids:
                continue
            seen_sids.add(sid)
            last = c.execute(
                "SELECT session_cost FROM utilization WHERE session_id=? AND window=? "
                "AND session_cost IS NOT NULL ORDER BY ts DESC LIMIT 1", (sid, window)).fetchone()
            if not last or last[0] is None:
                continue
            base = c.execute(
                "SELECT session_cost FROM utilization WHERE session_id=? AND window=? AND ts<=? "
                "AND session_cost IS NOT NULL ORDER BY ts DESC LIMIT 1",
                (sid, window, start_iso)).fetchone()
            baseline = float(base[0]) if base and base[0] is not None else 0.0
            total += max(0.0, float(last[0]) - baseline)

    _sum_conn(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _sum_conn(rc, default_account=rd.get("account"))
    return round(total, 6)


def _renewal_day_of(cfg, email):
    from . import config as _config

    return _config.renewal_day_for(cfg, email)


def _saved_for_accounts(conn, since_iso, era_iso=None):
    """``{account: (measured, modeled)}`` over savings rows since ``since_iso``.

    Measured: per-turn attribution from ``by_account`` in the detail (T9);
    falls back to ``sessions.account`` for pre-T9 rows.
    Modeled: still from ``sessions.account`` (no per-turn split).
    """
    from . import savings as _savings
    out = {}
    try:
        sql = ("SELECT v.measured_saved_usd, v.measured_detail_json,"
               " v.modeled_saved_usd, s.account"
               " FROM savings v JOIN sessions s ON s.session_id = v.session_id"
               " WHERE COALESCE(v.updated, '') >= ?")
        params = [since_iso]
        if era_iso:
            sql += (" AND COALESCE(s.account_source, '') != 'assumed'"
                    " AND (s.started IS NULL OR s.started >= ?)")
            params.append(era_iso)
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return out
    for r in rows:
        session_account = r[3] if isinstance(r, (list, tuple)) else r["account"]
        modeled = float((r[2] if isinstance(r, (list, tuple)) else r["modeled_saved_usd"]) or 0.0)
        detail_json = r[1] if isinstance(r, (list, tuple)) else r["measured_detail_json"]
        measured_usd = r[0] if isinstance(r, (list, tuple)) else r["measured_saved_usd"]
        ba = _savings.detail_by_account(detail_json, session_account, measured_usd)
        for acct, val in ba.items():
            prev = out.get(acct, (0.0, 0.0))
            out[acct] = (prev[0] + val, prev[1])
        # modeled stays on session_account (no per-turn split)
        prev = out.get(session_account, (0.0, 0.0))
        out[session_account] = (prev[0], prev[1] + modeled)
    return out


def _lifetime_for_account(conn, email, readers=None, era_iso=None):
    """``{cost_used, cost_saved_measured, ratio}`` over all time for ``email``.

    ``cost_used`` = Σ ``turns.cost_usd`` (kind='api'); ``cost_saved_measured`` =
    per-turn attribution from ``by_account`` in the detail (T9), falling back to
    ``sessions.account`` for pre-T9 rows; both over every reader (the T9 union)
    when ``readers`` is given, local ``conn`` only otherwise, deduped by each
    table's primary key (``turns.msg_id``, ``savings.run_id``) so a session
    double-read across roots is counted once.  ``ratio`` = saved / used, or None.
    When ``era_iso`` is set, pre-era sessions (``account_source='assumed'`` or
    ``started < era_iso``) are excluded.
    """
    from . import savings as _savings

    seen_msg = set()
    used = 0.0

    def _sum_turns(c, default_account=None, era=None):
        nonlocal used
        sql = "SELECT msg_id, cost_usd FROM turns WHERE kind='api' AND COALESCE(account, ?) IS ?"
        params = [default_account, email]
        if era:
            sql += (" AND session_id NOT IN"
                    " (SELECT session_id FROM sessions"
                    " WHERE account_source = 'assumed'"
                    " OR (started IS NOT NULL AND started < ?))")
            params.append(era)
        for row in c.execute(sql, params).fetchall():
            mid = row[0] if isinstance(row, (list, tuple)) else row["msg_id"]
            if mid in seen_msg:
                continue
            seen_msg.add(mid)
            cost = row[1] if isinstance(row, (list, tuple)) else row["cost_usd"]
            used += float(cost or 0.0)

    seen_run = set()
    saved = 0.0

    def _sum_savings(c, default_account=None, era=None):
        nonlocal saved
        try:
            sql = ("SELECT v.run_id, v.measured_saved_usd, v.measured_detail_json,"
                   " s.account FROM savings v"
                   " JOIN sessions s ON s.session_id = v.session_id")
            if era:
                sql += (" WHERE COALESCE(s.account_source, '') != 'assumed'"
                        " AND (s.started IS NULL OR s.started >= ?)")
                rows = c.execute(sql, (era,)).fetchall()
            else:
                rows = c.execute(sql).fetchall()
        except Exception:
            return
        for row in rows:
            rid = row[0] if isinstance(row, (list, tuple)) else row["run_id"]
            if rid in seen_run:
                continue
            seen_run.add(rid)
            measured_usd = row[1] if isinstance(row, (list, tuple)) else row["measured_saved_usd"]
            detail_json = row[2] if isinstance(row, (list, tuple)) else row["measured_detail_json"]
            session_account = row[3] if isinstance(row, (list, tuple)) else row["account"]
            ba = _savings.detail_by_account(detail_json, session_account or default_account,
                                            measured_usd)
            saved += float(ba.get(email, 0.0))

    _sum_turns(conn, era=era_iso)
    _sum_savings(conn, era=era_iso)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        rd_era = era_start(rc)                  # each reader uses its own meta.created
        _sum_turns(rc, default_account=rd.get("account"), era=rd_era)
        _sum_savings(rc, default_account=rd.get("account"), era=rd_era)

    ratio = round(saved / used, 3) if used > 0 else None
    return {"cost_used": round(used, 6), "cost_saved_measured": round(saved, 6), "ratio": ratio}


def accounts_block(conn, cfg=None, readers=None):
    """The ``accounts`` block: one entry per account with its live windows.

    ``readers``: optional ``db.union_readers`` list forwarded to
    :func:`_cost_in_window`, the fit functions and :func:`_lifetime_for_account`
    so the window fields (``cost_in_window``, ``cost_saved_measured``,
    ``pct_saved``) and the ``lifetime`` block aggregate across all machines.
    """
    labels = _labels(cfg)
    out = {}
    emails = []
    for row in conn.execute("SELECT email, label FROM accounts ORDER BY email"):
        emails.append(row[0])
        if row[1] and not labels.get(row[0]):        # config label wins, db label fills in
            labels[row[0]] = row[1]
    for (acct,) in conn.execute("SELECT DISTINCT account FROM utilization"):
        if acct and acct not in emails:
            emails.append(acct)
    era_iso = era_start(conn, cfg)
    for email in emails:
        pstart = period_start(cfg, account=email)
        saved = _saved_for_accounts(conn, pstart, era_iso=era_iso) if pstart else {}
        windows = {}
        rows = conn.execute("SELECT DISTINCT window FROM utilization WHERE account IS ? "
                            "ORDER BY window", (email,)).fetchall()
        for (win,) in rows:
            last = conn.execute(
                "SELECT ts, pct, resets_at FROM utilization WHERE account IS ? AND window=? "
                "ORDER BY ts DESC LIMIT 1", (email, win)).fetchone()
            if not last:
                continue
            started = window_started_at(win, last["resets_at"], cfg)
            inst = conn.execute(
                "SELECT pct_per_dollar, fit_method, fit_n, fit_crossings, fit_quality, fit_detail, "
                "started_at FROM window_instances WHERE account IS ? AND window=? AND resets_at IS ?",
                (email, win, last["resets_at"])).fetchone()
            # drop-aware start (T21.1.1.1.1): the instance's started_at when it lies inside
            # [resets - duration, resets]; else the nominal resets - duration
            inst_started = _epoch(inst["started_at"]) if inst else None
            resets_now = _epoch(last["resets_at"])
            if (inst_started and resets_now and started is not None
                    and started <= inst_started <= resets_now):
                started = inst_started
            # -- fit fields: use instance fit when quality != none, else pooled
            inst_quality = (inst["fit_quality"] if inst else None) or "none"
            ppd = inst["pct_per_dollar"] if inst else None
            f_method = inst["fit_method"] if inst else None
            f_n = inst["fit_n"] if inst else None
            f_crossings = inst["fit_crossings"] if inst else None
            f_quality = inst_quality
            # Parse fit_detail (per-family weights JSON, T2.1)
            fit_detail_raw = (inst["fit_detail"] if inst else None) if inst else None
            fit_detail = None
            if fit_detail_raw:
                try:
                    fit_detail = json.loads(fit_detail_raw)
                except (ValueError, TypeError):
                    pass
            # T30.c2: a young instance (under a day or few samples) borrows the
            # previous instance's fit; pooled past stays the fallback for none
            fit_reason = None
            prev_fit = None
            try:
                from . import fit as _fit
                _young, fit_reason = _fit.instance_is_young(
                    conn, cfg, email, win, last["resets_at"], started)
                if _young:
                    prev_fit = _fit.previous_instance_fit(conn, email, win, last["resets_at"])
            except Exception:
                prev_fit = None
            if prev_fit:
                ppd = prev_fit["slope"]
                f_method = "previous"
                f_n = prev_fit.get("n")
                f_crossings = prev_fit.get("crossings")
                f_quality = prev_fit.get("quality")
                fit_detail = prev_fit.get("families")
            elif inst_quality == "none":
                # try pooled slope over past instances of this account+window
                try:
                    from . import fit as _fit
                    past = conn.execute(
                        "SELECT pct_per_dollar, fit_se, fit_n, fit_crossings, fit_span, "
                        "fit_method, fit_quality, fit_detail FROM window_instances "
                        "WHERE account IS ? AND window=? ORDER BY resets_at",
                        (email, win)).fetchall()
                    past_fits = [{"slope": r["pct_per_dollar"], "se": r["fit_se"],
                                  "n": r["fit_n"], "crossings": r["fit_crossings"],
                                  "span_pct": r["fit_span"], "method": r["fit_method"],
                                  "quality": r["fit_quality"],
                                  "families": json.loads(r["fit_detail"]) if r["fit_detail"] else None}
                                 for r in past if r["pct_per_dollar"]]
                    pooled = _fit.pooled_slope(past_fits, cfg)
                    if pooled and pooled.get("slope"):
                        ppd = pooled["slope"]
                        f_method = "pooled"
                        f_n = pooled.get("n")
                        f_crossings = pooled.get("crossings")
                        f_quality = "band"
                    # also try pooled per-family weights
                    pw = _fit.pooled_weights(past_fits, cfg)
                    if pw and not fit_detail:
                        fit_detail = pw
                except Exception:
                    pass
            # T32.1: past fit.ratio_min_pct the direct meter/cost ratio is the rate
            cost_in_window = _cost_in_window(conn, email, win, started, readers=readers)
            try:
                from . import config as _config
                ratio_min = float(_config.get(cfg or {}, "fit.ratio_min_pct", 5))
            except Exception:
                ratio_min = 5.0
            pct_last = last["pct"]
            # T32.1.c2: the denominator is ledger turns cost (kind='api') since started, not
            # _cost_in_window (utilization.session_cost: the harness cost-state, [1m] premium)
            # fix-8: ledger_cost is computed whenever started is known (the Pace line's paid)
            c_now, ledger_cost = {}, 0.0
            ratio_ok = pct_last is not None and float(pct_last) >= ratio_min
            if ratio_ok or started is not None:
                try:
                    from . import fit as _fit
                    axes = _fit.cost_axes(conn, email, started, [time.time()], readers=readers)
                    c_now = axes[0] if axes else {}
                    ledger_cost = sum(float(v or 0.0) for v in c_now.values())
                except Exception:
                    c_now, ledger_cost = {}, 0.0
            ledger_cost_in_window = ledger_cost if started is not None else None
            if ratio_ok and ledger_cost > 0:
                target = float(pct_last) / 100.0
                ppd = target / ledger_cost
                f_method = "ratio"
                f_quality = "ok"
                fit_reason = "ratio: pct %.1f over $%.2f" % (float(pct_last), ledger_cost)
                if fit_detail:
                    try:
                        denom = 0.0
                        for _fam, _info in fit_detail.items():
                            _w = _info.get("weight") if isinstance(_info, dict) else _info
                            denom += float(_w or 0.0) * float(c_now.get(_fam, 0.0))
                        if denom > 0:
                            k = target / denom
                            scaled = {}
                            for _fam, _info in fit_detail.items():
                                _i = dict(_info) if isinstance(_info, dict) else {"weight": _info}
                                _w = float(_i.get("weight") or 0.0) * k
                                _i["weight"] = _w
                                _i["dollars_per_window"] = (1.0 / _w) if _w > 0 else None
                                scaled[_fam] = _i
                            fit_detail = scaled
                    except Exception:
                        pass
            dpw = (1.0 / ppd) if ppd and ppd > 0 else None
            # -- cost_saved_measured via savings.window_saved_measured (union)
            csm = None
            csm_by_family = None
            started_iso = _iso(started) if started else None
            if started_iso:
                try:
                    from . import savings as _savings
                    csm = _savings.window_saved_measured(conn, email, started_iso, None)
                    csm_by_family = _savings.window_saved_measured(
                        conn, email, started_iso, None, by_family=True)
                    for _rd in (readers or []):
                        _rc = _rd.get("conn")
                        if _rc is None or _rc is conn:
                            continue
                        try:
                            _da = _rd.get("account")
                            _v = _savings.window_saved_measured(
                                _rc, _da if _da == email else email, started_iso, None)
                            if _v:
                                csm = (csm or 0.0) + _v
                            _vf = _savings.window_saved_measured(
                                _rc, _da if _da == email else email, started_iso, None,
                                by_family=True)
                            if _vf:
                                if csm_by_family is None:
                                    csm_by_family = {}
                                for _fk, _fv in _vf.items():
                                    csm_by_family[_fk] = csm_by_family.get(_fk, 0.0) + _fv
                        except Exception:
                            pass
                except Exception:
                    pass
            # -- pct_saved: per-family sum when weights are available (T2.1)
            ps = None
            if fit_detail and csm_by_family:
                try:
                    from . import fit as _fit
                    ps = _fit.pct_saved(csm_by_family, fit_detail)
                except Exception:
                    pass
            if ps is None and ppd and csm is not None:
                try:
                    from . import fit as _fit
                    ps = _fit.pct_saved(csm, ppd)
                except Exception:
                    pass
            # T32.1.c3: the pooled per-family rates for this account's tier convert saved $
            # by model mix; pct_saved (instance/previous/ratio view) stays as is
            pool_rates = pool_meta = ps_pooled = None
            try:
                from . import fit as _fit
                from . import config as _config
                _tier = _config.tier_for(cfg or {}, email)
                pool_rates = _fit.pool_rates(conn, _tier, win)
                pool_meta = _fit.pool_meta(conn, _tier, win)
                if pool_rates and csm_by_family:
                    ps_pooled = _fit.pct_saved(csm_by_family, pool_rates)
            except Exception:
                pass
            # -- pace = (pct_last/100) / (elapsed/duration) when elapsed > 1% of duration
            pace = None
            dur = window_duration(win, cfg)
            resets_ep = _epoch(last["resets_at"])
            if resets_ep and dur and last["pct"] is not None:
                elapsed = resets_ep - (started or (resets_ep - dur))
                # use time since window started, capped at now
                now_ep = time.time()
                elapsed = now_ep - (started or (resets_ep - dur))
                if elapsed > 0.01 * dur and dur > 0:
                    pace = (float(last["pct"]) / 100.0) / (elapsed / dur)
            windows[win] = {
                "resets_at": _epoch(last["resets_at"]),
                "started_at": started,
                "pct_last": last["pct"],
                "pct_ts": last["ts"],
                "cost_in_window": cost_in_window,
                "ledger_cost_in_window": ledger_cost_in_window,
                "cost_saved_measured": csm,
                "cost_saved_measured_by_family": csm_by_family,
                "cost_saved_modeled": None,
                "pct_per_dollar": ppd,
                "fit_method": f_method,
                "fit_n": f_n,
                "fit_crossings": f_crossings,
                "fit_quality": f_quality,
                "fit_reason": fit_reason,
                "fit_detail": fit_detail,
                "dollars_per_window": dpw,
                "pct_saved": ps,
                "pool_rates": pool_rates,
                "pool_meta": pool_meta,
                "pct_saved_pooled": ps_pooled,
                "pace": pace,
            }
        if not pstart:
            cost_row = (0.0,)                        # 3.11 T9: no renewal day, no period to sum
        elif era_iso:
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE kind='api' AND account IS ? AND ts>=?"
                " AND session_id NOT IN"
                " (SELECT session_id FROM sessions"
                " WHERE account_source = 'assumed' OR (started IS NOT NULL AND started < ?))",
                (email, pstart, era_iso)).fetchone()
        else:
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE kind='api' AND account IS ? AND ts>=?",
                (email, pstart)).fetchone()
        measured, modeled = saved.get(email, (0.0, 0.0))
        period = ({"start": pstart,
                   "renewal_day": _renewal_day_of(cfg, email),
                   "cost_used": round(float(cost_row[0] or 0.0), 6),
                   "cost_saved_measured": round(measured, 6),
                   "cost_saved_modeled": round(modeled, 6)} if pstart else
                  {"start": None, "renewal_day": None, "unknown": True})
        out[email] = {
            "label": labels.get(email),
            "era_start": era_iso,
            "windows": windows,
            "period": period,
            "lifetime": _lifetime_for_account(conn, email, readers=readers, era_iso=era_iso),
        }
        # month and lifetime weeks: each seven-day instance at its own rate (fix-9.c2); no month
        # weeks without a renewal day (3.11 T9)
        wcache = {}
        for blk, since in (("lifetime", None), ("period", pstart)):
            if blk == "period" and not pstart:
                continue
            wk = _weeks_by_window(conn, email, None, since, readers=readers, cache=wcache,
                                  era_iso=era_iso)
            for fld in WEEKS_FIELDS:
                out[email][blk][fld] = wk[fld]
    return out


# --------------------------------------------------------------------------- projects (T12)

def _project_key(path):
    """Normalize a session's ``project``/``cwd`` into the ``projects`` dict key.

    Mirrors ``pa.statusline._project_key`` (kept a separate three-line copy so the
    statusline's hot path never imports this module at module scope) and
    ``pa.savings._norm_project``.
    """
    text = str(path or "").strip()
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text))


def _project_label(key, raw):
    text = str(raw or key or "").rstrip("/\\")
    return os.path.basename(text) or text


def _project_sessions(conn, email, readers=None, era_iso=None):
    """``{project_key: {"label": str, "sids": set()}}`` over every session of ``email``,
    deduped by session_id across ``conn`` + ``readers`` (T12).
    When ``era_iso`` is set, pre-era sessions are excluded."""
    out = {}
    seen = set()
    pre = _pre_era_sids(conn, era_iso) if era_iso else set()

    def _collect(c, default_account=None, exclude=None):
        try:
            rows = c.execute(
                "SELECT session_id, project, cwd FROM sessions WHERE COALESCE(account, ?) IS ?",
                (default_account, email)).fetchall()
        except Exception:
            return
        for row in rows:
            sid = row["session_id"]
            if sid in seen:
                continue
            if exclude and sid in exclude:
                continue
            seen.add(sid)
            raw = row["project"] or row["cwd"]
            key = _project_key(raw)
            if not key:
                continue
            slot = out.setdefault(key, {"label": _project_label(key, raw), "sids": set()})
            slot["sids"].add(sid)

    _collect(conn, exclude=pre)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        rd_era = era_start(rc)
        rd_pre = _pre_era_sids(rc, rd_era) if rd_era else set()
        _collect(rc, default_account=rd.get("account"), exclude=rd_pre)
    return out


def _project_cost_by_family(conn, account, sids, started_at, readers=None):
    """``{family: usd}`` cost of ``sids``' api turns since ``started_at`` (T12)."""
    if not started_at or not sids:
        return {}
    from . import fit as _fit

    start_iso = _iso(started_at)
    wanted = list(sids)
    placeholders = ",".join("?" * len(wanted))
    seen_ids = set()
    out = {}

    def _sum_conn(c, default_account=None):
        try:
            rows = c.execute(
                "SELECT msg_id, model, cost_usd FROM turns WHERE kind='api' AND "
                "COALESCE(account, ?) IS ? AND ts>=? AND session_id IN (%s)" % placeholders,
                [default_account, account, start_iso] + wanted).fetchall()
        except Exception:
            return
        for row in rows:
            mid = row["msg_id"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            fam = _fit.family_of(row["model"])
            out[fam] = out.get(fam, 0.0) + float(row["cost_usd"] or 0.0)

    _sum_conn(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _sum_conn(rc, default_account=rd.get("account"))
    return out


def _project_lifetime(conn, email, sids, readers=None):
    """``{cost_used, cost_saved_measured}`` for a project's sessions, all time (T12).

    Mirrors :func:`_lifetime_for_account`, restricted to ``sids``; the project's own
    install cutoff (T10.1) is not applied yet, so this inherits the root's."""
    if not sids:
        return {"cost_used": 0.0, "cost_saved_measured": 0.0}
    wanted = list(sids)
    placeholders = ",".join("?" * len(wanted))
    seen_msg, used = set(), 0.0
    seen_run, saved = set(), 0.0

    def _sum(c, default_account=None):
        nonlocal used, saved
        try:
            for row in c.execute(
                    "SELECT msg_id, cost_usd FROM turns WHERE kind='api' AND "
                    "COALESCE(account, ?) IS ? AND session_id IN (%s)" % placeholders,
                    [default_account, email] + wanted).fetchall():
                mid = row["msg_id"]
                if mid in seen_msg:
                    continue
                seen_msg.add(mid)
                used += float(row["cost_usd"] or 0.0)
        except Exception:
            pass
        try:
            for row in c.execute(
                    "SELECT v.run_id, v.measured_saved_usd FROM savings v JOIN sessions s "
                    "ON s.session_id = v.session_id WHERE COALESCE(s.account, ?) IS ? "
                    "AND v.session_id IN (%s)" % placeholders,
                    [default_account, email] + wanted).fetchall():
                rid = row["run_id"]
                if rid in seen_run:
                    continue
                seen_run.add(rid)
                saved += float(row["measured_saved_usd"] or 0.0)
        except Exception:
            pass

    _sum(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _sum(rc, default_account=rd.get("account"))
    return {"cost_used": round(used, 6), "cost_saved_measured": round(saved, 6)}


def _project_period(conn, email, sids, pstart, readers=None):
    """``{start, cost_used, cost_saved_measured}`` for a project's sessions since ``pstart``.

    Mirrors the account's ``period`` in :func:`accounts_block`, restricted to ``sids``."""
    if not sids or not pstart:
        return {"start": pstart, "cost_used": 0.0, "cost_saved_measured": 0.0}
    wanted = list(sids)
    placeholders = ",".join("?" * len(wanted))
    seen_msg, used = set(), 0.0
    seen_run, saved = set(), 0.0

    def _sum(c, default_account=None):
        nonlocal used, saved
        try:
            for row in c.execute(
                    "SELECT msg_id, cost_usd FROM turns WHERE kind='api' AND "
                    "COALESCE(account, ?) IS ? AND ts>=? AND session_id IN (%s)" % placeholders,
                    [default_account, email, pstart] + wanted).fetchall():
                mid = row["msg_id"]
                if mid in seen_msg:
                    continue
                seen_msg.add(mid)
                used += float(row["cost_usd"] or 0.0)
        except Exception:
            pass
        try:
            for row in c.execute(
                    "SELECT v.run_id, v.measured_saved_usd FROM savings v JOIN sessions s "
                    "ON s.session_id = v.session_id WHERE COALESCE(s.account, ?) IS ? "
                    "AND COALESCE(v.updated, '') >= ? AND v.session_id IN (%s)" % placeholders,
                    [default_account, email, pstart] + wanted).fetchall():
                rid = row["run_id"]
                if rid in seen_run:
                    continue
                seen_run.add(rid)
                saved += float(row["measured_saved_usd"] or 0.0)
        except Exception:
            pass

    _sum(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _sum(rc, default_account=rd.get("account"))
    return {"start": pstart, "cost_used": round(used, 6), "cost_saved_measured": round(saved, 6)}


def _rated_windows(conn, email, readers=None):
    """``[(start_iso, end_iso, rate)]`` of this account's rated seven-day instances, in
    reset order; ``rate = (max pct / 100) / account cost`` (fix-9, developer 2026-09-27).

    Instances: distinct ``resets_at`` of this account's ``seven_day`` samples, spanning
    ``[started_at, resets_at)``; < 10 samples or max pct < 1 are ignored; of two that
    overlap the one with more samples is kept.  The account cost is its api turns in the
    span up to the last sample; zero cost has no rate."""
    try:
        rows = conn.execute(
            "SELECT resets_at, COUNT(*) AS n, MAX(pct) AS mx, MAX(ts) AS last_ts FROM utilization "
            "WHERE account IS ? AND window='seven_day' AND resets_at IS NOT NULL "
            "GROUP BY resets_at", (email,)).fetchall()
    except Exception:
        rows = []
    cands = []
    for row in rows:
        resets = _epoch(row["resets_at"])
        if resets is None or (row["n"] or 0) < 10 or float(row["mx"] or 0.0) < 1:
            continue
        started = None
        try:
            inst = conn.execute(
                "SELECT started_at FROM window_instances WHERE account IS ? AND "
                "window='seven_day' AND resets_at IS ?", (email, row["resets_at"])).fetchone()
            started = _epoch(inst["started_at"]) if inst else None
        except Exception:
            started = None
        if not started:
            started = resets - 604800
        cands.append({"start": started, "end": resets, "n": int(row["n"]),
                      "pct": float(row["mx"]), "last_ts": row["last_ts"]})
    # overlap: more samples wins, ties to the earlier reset; then back into reset order
    kept = []
    for c in sorted(cands, key=lambda c: (-c["n"], c["end"])):
        if all(c["end"] <= k["start"] or k["end"] <= c["start"] for k in kept):
            kept.append(c)
    kept.sort(key=lambda c: c["end"])
    if not kept:
        return []
    acct = _turns_ts_cost(conn, email, "ts>=? AND ts<?",
                          [_iso(kept[0]["start"]), _iso(kept[-1]["end"])], readers)
    out = []
    for k in kept:
        s_iso, e_iso = _iso(k["start"]), _iso(k["end"])
        last = k["last_ts"] or e_iso
        acost = sum(c for ts, c in acct if s_iso <= ts < e_iso and ts <= last)
        if acost > 0:
            out.append((s_iso, e_iso, (k["pct"] / 100.0) / acost))
    return out


def _turns_ts_cost(conn, email, where, args, readers=None):
    """``[(ts, cost)]`` of this account's api turns matching ``where``, msg_id-deduped
    across ``conn`` + ``readers``."""
    seen, out = set(), []

    def _one(c, default_account=None):
        try:
            for r in c.execute(
                    "SELECT msg_id, ts, cost_usd FROM turns WHERE kind='api' AND "
                    "COALESCE(account, ?) IS ? AND " + where,
                    [default_account, email] + list(args)).fetchall():
                if r["msg_id"] in seen:
                    continue
                seen.add(r["msg_id"])
                out.append((r["ts"] or "", float(r["cost_usd"] or 0.0)))
        except Exception:
            pass

    _one(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _one(rc, default_account=rd.get("account"))
    return out


def _saved_ts(conn, email, sids, readers=None, era_iso=None):
    """``[(t, usd)]`` of this account's ``savings`` rows (``measured_saved_usd``, deduped by
    ``run_id`` across readers), ``t`` = the run's ``agent_runs.ended``, else its
    ``started``, else the session's ``started`` ('' when none).  ``era_iso``: pre-era
    sessions excluded (the :func:`_lifetime_for_account` rule, fix-9.c3)."""
    seen, out = set(), []
    flt, args = "", []
    if sids is not None:
        wanted = sorted(sids)
        flt = " AND v.session_id IN (%s)" % ",".join("?" * len(wanted))
        args = wanted
    elif era_iso:
        flt = " AND v.session_id NOT IN " + _PRE_ERA_SQL
        args = [era_iso]

    def _one(c, default_account=None):
        base = ("FROM savings v JOIN sessions s ON s.session_id = v.session_id{join} "
                "WHERE COALESCE(s.account, ?) IS ?" + flt)
        try:
            rows = c.execute(
                "SELECT v.run_id, v.measured_saved_usd, "
                "COALESCE(r.ended, r.started, s.started) AS t "
                + base.format(join=" LEFT JOIN agent_runs r ON r.run_id = v.run_id"),
                [default_account, email] + args).fetchall()
        except Exception:
            try:                                  # no agent_runs table: the session's start
                rows = c.execute("SELECT v.run_id, v.measured_saved_usd, s.started AS t "
                                 + base.format(join=""), [default_account, email] + args).fetchall()
            except Exception:
                return
        for r in rows:
            if r["run_id"] in seen:
                continue
            seen.add(r["run_id"])
            out.append((r["t"] or "", float(r["measured_saved_usd"] or 0.0)))

    _one(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _one(rc, default_account=rd.get("account"))
    return out


_PRE_ERA_SQL = ("(SELECT session_id FROM sessions WHERE account_source = 'assumed'"
                " OR (started IS NOT NULL AND started < ?))")   # _lifetime_for_account's rule


def _weeks_by_window(conn, email, sids, since_iso, readers=None, cache=None, era_iso=None):
    """``{weeks_used, weeks_saved, unrated_usd, unrated_saved_usd, instances}`` for spend
    and savings since ``since_iso`` (None: all time) of ``sids`` (None: every session of
    the account), each in the rated seven-day instance (:func:`_rated_windows`) that holds
    its time, at that instance's own rate (fix-9).  Cost rows are timed by turn ``ts``;
    savings rows by the run's return (:func:`_saved_ts`), so the saved dollars are the same
    rows ``cost_saved_measured`` sums (fix-9.c2).  Rows outside every rated span are
    ``unrated_usd`` / ``unrated_saved_usd``.  ``cache``: a dict reused across calls of one
    rebuild for the account's rated windows.  ``era_iso`` (``sids`` None only): pre-era
    sessions are excluded like the account's dollars (fix-9.c3); the rated windows' rates
    still use every turn of the account."""
    if cache is not None and email in cache:
        rated = cache[email]
    else:
        rated = _rated_windows(conn, email, readers=readers)
        if cache is not None:
            cache[email] = rated
    since = since_iso or ""
    if sids is not None:
        wanted = sorted(sids)
        if not wanted:
            return {"weeks_used": 0.0, "weeks_saved": 0.0, "unrated_usd": 0.0,
                    "unrated_saved_usd": 0.0, "instances": len(rated)}
        cost = _turns_ts_cost(conn, email, "ts>=? AND session_id IN (%s)"
                              % ",".join("?" * len(wanted)), [since] + wanted, readers)
    elif era_iso:
        cost = _turns_ts_cost(conn, email, "ts>=? AND session_id NOT IN " + _PRE_ERA_SQL,
                              [since, era_iso], readers)
    else:
        cost = _turns_ts_cost(conn, email, "ts>=?", [since], readers)
    saved = [(t, v) for t, v in _saved_ts(conn, email, sids, readers, era_iso=era_iso)
             if t >= since]

    def _split(rows):
        weeks, unrated = 0.0, 0.0
        for t, usd in rows:
            rate = next((r for s_iso, e_iso, r in rated if s_iso <= t < e_iso), None)
            if rate is None:
                unrated += usd
            else:
                weeks += usd * rate
        return weeks, unrated

    wu, uu = _split(sorted(cost))
    ws, us = _split(sorted(saved))
    return {"weeks_used": round(wu, 6), "weeks_saved": round(ws, 6),
            "unrated_usd": round(uu, 6), "unrated_saved_usd": round(us, 6),
            "instances": len(rated)}


def projects_block(conn, cfg=None, readers=None, accounts=None):
    """The ``projects`` block (T12): one entry per project with turns in the current
    window instances or savings rows -- the usage share of the account's live windows
    (weighted per family by the window's fit, the "hogging" figure) and measured
    savings, both scoped to the project's own sessions.

    ``accounts``: the already-built :func:`accounts_block` result (its ``windows``
    carry the fit weights and ``started_at`` this reuses); computed fresh when omitted.
    """
    if accounts is None:
        accounts = accounts_block(conn, cfg, readers=readers)
    from . import fit as _fit
    from . import savings as _savings

    era_iso = era_start(conn, cfg)
    out = {}
    wcache = {}                                   # rated seven-day windows per account (fix-9)
    for email, ablock in accounts.items():
        windows = ablock.get("windows") or {}
        acct_keys = []
        for pkey, pinfo in _project_sessions(conn, email, readers=readers, era_iso=era_iso).items():
            entry = out.setdefault(pkey, {"label": pinfo["label"], "by_account": {},
                                          "cost_in_window": {},
                                          "pct_used": {}, "pct_est": {}, "cost_saved_measured": {},
                                          "lifetime": None})
            acct_keys.append(pkey)
            # per-account slice (T10): this account's view of the project
            acct_slice = entry["by_account"].setdefault(
                email, {"cost_in_window": {}, "pct_used": {}, "pct_est": {},
                        "cost_saved_measured": {}})
            active = False
            for win in ("five_hour", "seven_day"):
                winfo = windows.get(win)
                if not isinstance(winfo, dict):
                    continue
                started = winfo.get("started_at")
                cost_by_fam = _project_cost_by_family(conn, email, pinfo["sids"], started,
                                                       readers=readers)
                acct_slice["cost_in_window"][win] = cost_by_fam
                fit_detail = winfo.get("fit_detail")
                ppd = winfo.get("pct_per_dollar")
                pu = None
                if fit_detail and cost_by_fam:
                    try:
                        pu = _fit.pct_saved(cost_by_fam, fit_detail)
                    except Exception:
                        pass
                if pu is None and ppd and cost_by_fam:
                    try:
                        pu = _fit.pct_saved(sum(cost_by_fam.values()), ppd)
                    except Exception:
                        pass
                acct_slice["pct_est"][win] = pu            # the fit's absolute estimate, kept for the report
                acct_slice["pct_used"][win] = pu           # replaced below by the apportioned share
                started_iso = _iso(started) if started else None
                csm = None
                if started_iso:
                    try:
                        csm = _savings.window_saved_measured(conn, email, started_iso, None,
                                                              project=pkey)
                        for rd in (readers or []):
                            rc = rd.get("conn")
                            if rc is None or rc is conn:
                                continue
                            v = _savings.window_saved_measured(
                                rc, rd.get("account") or email, started_iso, None,
                                project=pkey)
                            if v:
                                csm = (csm or 0.0) + v
                    except Exception:
                        pass
                acct_slice["cost_saved_measured"][win] = csm
                if cost_by_fam or csm:
                    active = True
            acct_slice["lifetime"] = _project_lifetime(conn, email, pinfo["sids"], readers=readers)
            # period: cost_used and cost_saved_measured since period_start, scoped to this project's sids
            pstart = period_start(cfg, account=email)
            acct_slice["period"] = (_project_period(conn, email, pinfo["sids"], pstart, readers=readers)
                                    if pstart else {"start": None, "unknown": True})   # 3.11 T9
            # month and lifetime weeks: each seven-day instance at its own rate (fix-9)
            for blk, since in (("lifetime", None), ("period", pstart)):
                if blk == "period" and not pstart:
                    continue
                wk = _weeks_by_window(conn, email, pinfo["sids"], since, readers=readers,
                                      cache=wcache)
                for fld in WEEKS_FIELDS:
                    acct_slice[blk][fld] = wk[fld]
            # per-account windows: cost_used and net_saved per window for the render path (T1.c7)
            pw = {}
            for win in ("five_hour", "seven_day"):
                fam = acct_slice["cost_in_window"].get(win) or {}
                cu = round(sum(fam.values()), 6) if fam else 0.0
                ns = acct_slice["cost_saved_measured"].get(win)
                pw[win] = {"cost_used": cu, "net_saved": round(float(ns), 6) if ns is not None else None}
            acct_slice["windows"] = pw
            if acct_slice["lifetime"].get("cost_saved_measured"):
                active = True
            if acct_slice["period"].get("cost_used") or acct_slice["period"].get("cost_saved_measured"):
                active = True
            if active:
                entry["_active"] = True
        # the meter's own reading apportioned across this account's projects by their weighted
        # spend: the shares add up to line 1's percentage, so a project can never read above it
        # (2026-09-20: one project showed 28 % of 5h beside a 24 % meter); only the weights'
        # ratios matter here, not the young fit's absolute slope
        for win in ("five_hour", "seven_day"):
            winfo = windows.get(win)
            meter = None
            if isinstance(winfo, dict) and winfo.get("pct_last") is not None:
                try:
                    meter = float(winfo.get("pct_last"))
                except (TypeError, ValueError):
                    meter = None
            weights = {}
            for pk in acct_keys:
                acct_s = out[pk].get("by_account", {}).get(email, {})
                est = acct_s.get("pct_est", {}).get(win)
                if est is None:
                    fam = acct_s.get("cost_in_window", {}).get(win) or {}
                    est = sum(fam.values()) if fam else None
                weights[pk] = est
            total = sum(v for v in weights.values() if v)
            if meter is None or not total:
                continue                          # no sample or no spend: the estimate stays
            for pk in acct_keys:
                v = weights.get(pk)
                if v is None:
                    continue
                out[pk]["by_account"][email]["pct_used"][win] = round(meter * v / total, 3) if v else 0.0

    # all-accounts totals for each project (T10)
    for pkey, entry in out.items():
        if not entry.get("_active"):
            continue
        by_account = entry.get("by_account") or {}
        # cost_in_window: merge family dicts across accounts
        for win in ("five_hour", "seven_day"):
            merged = {}
            for acct_s in by_account.values():
                for fam, usd in (acct_s.get("cost_in_window", {}).get(win) or {}).items():
                    merged[fam] = merged.get(fam, 0.0) + usd
            entry["cost_in_window"][win] = merged
        # cost_saved_measured: sum across accounts
        for win in ("five_hour", "seven_day"):
            total_csm = None
            for acct_s in by_account.values():
                v = acct_s.get("cost_saved_measured", {}).get(win)
                if v is not None:
                    total_csm = (total_csm or 0.0) + v
            entry["cost_saved_measured"][win] = total_csm
        # lifetime: sum cost_used, cost_saved_measured; recompute ratio
        lt_used = 0.0
        lt_saved = 0.0
        for acct_s in by_account.values():
            lt = acct_s.get("lifetime") or {}
            lt_used += lt.get("cost_used", 0.0)
            lt_saved += lt.get("cost_saved_measured", 0.0)
        lt_ratio = lt_saved / lt_used if lt_used > 0 else None
        entry["lifetime"] = {"cost_used": round(lt_used, 6),
                             "cost_saved_measured": round(lt_saved, 6),
                             "ratio": round(lt_ratio, 4) if lt_ratio is not None else None}
        entry["period"] = {}
        for blk in ("lifetime", "period"):          # weeks (fix-9): sum across accounts
            for fld in WEEKS_FIELDS:
                entry[blk][fld] = round(sum((a.get(blk) or {}).get(fld, 0.0)
                                            for a in by_account.values()), 6)
        # period: sum cost_used, cost_saved_measured across accounts
        p_used = 0.0
        p_saved = 0.0
        p_start = None
        for acct_s in by_account.values():
            p = acct_s.get("period") or {}
            p_used += p.get("cost_used", 0.0)
            p_saved += p.get("cost_saved_measured", 0.0)
            ps = p.get("start")
            if ps and (p_start is None or ps < p_start):
                p_start = ps
        entry["period"].update({"start": p_start, "cost_used": round(p_used, 6),
                                "cost_saved_measured": round(p_saved, 6)})
        # 3.11 T9: accounts with no renewal day add no month figures; the statusline shows a dash for them
        unknown = sorted(e for e, a in by_account.items() if (a.get("period") or {}).get("unknown"))
        if unknown:
            entry["period"]["unknown_accounts"] = unknown
        # windows: sum cost_used and net_saved per window
        pw_total = {}
        for win in ("five_hour", "seven_day"):
            cu = 0.0
            ns = None
            for acct_s in by_account.values():
                aw = (acct_s.get("windows") or {}).get(win) or {}
                cu += aw.get("cost_used", 0.0)
                awns = aw.get("net_saved")
                if awns is not None:
                    ns = (ns or 0.0) + awns
            pw_total[win] = {"cost_used": round(cu, 6),
                             "net_saved": round(ns, 6) if ns is not None else None}
        entry["windows"] = pw_total
        # pct_used/pct_est: largest-cost account's values per window (T10: ledger_cli compat)
        for win in ("five_hour", "seven_day"):
            best_email = None
            best_cost = 0.0
            for ae, acct_s in by_account.items():
                fam = acct_s.get("cost_in_window", {}).get(win) or {}
                cost_total = sum(fam.values()) if fam else 0.0
                if cost_total > best_cost:
                    best_cost = cost_total
                    best_email = ae
            if best_email:
                entry["pct_used"][win] = by_account[best_email].get("pct_used", {}).get(win)
                entry["pct_est"][win] = by_account[best_email].get("pct_est", {}).get(win)

    return {k: {kk: vv for kk, vv in v.items() if kk != "_active"}
            for k, v in out.items() if v.get("_active")}


def sessions_block(conn, cfg=None, hours=SESSION_WINDOW_H, accounts=None):
    """Sessions seen in the last ``hours`` with their cost and savings.

    ``accounts``: the already-built :func:`accounts_block` result; when given,
    each session gets a ``windows`` dict with ``cost_used`` and ``net_saved`` per
    window (T1.c7), so the statusline can render windowed pairs without sqlite.
    """
    cutoff = _cutoff_iso(hours)
    out = {}
    wcache = {}                                   # rated seven-day windows per account (fix-9.c2)
    rows = conn.execute(
        "SELECT session_id, account, kind, project, phase, started, ended, cost_usd, "
        "cost_source FROM sessions WHERE COALESCE(ended, started, '') >= ? "
        "ORDER BY started", (cutoff,)).fetchall()
    for row in rows:
        sid = row["session_id"]
        sv = conn.execute(
            "SELECT COALESCE(SUM(measured_saved_usd),0), COALESCE(SUM(modeled_saved_usd),0) "
            "FROM savings WHERE session_id=?", (sid,)).fetchone()
        measured = float(sv[0] or 0.0) if sv else 0.0
        modeled = float(sv[1] or 0.0) if sv else 0.0
        cost = float(row["cost_usd"] or 0.0)
        entry = {"account": row["account"], "kind": row["kind"], "project": row["project"],
                 "phase": row["phase"], "started": row["started"], "ended": row["ended"],
                 "cost_usd": cost, "cost_source": row["cost_source"],
                 "saved_measured": round(measured, 6), "saved_modeled": round(modeled, 6),
                 "ratio": (round(measured / cost, 3) if cost > 0 else None)}
        # windowed cost and net per session (T1.c7)
        wins = {}
        acct = row["account"]
        ablock = (accounts or {}).get(acct) if acct else None
        if isinstance(ablock, dict):
            for win in ("five_hour", "seven_day"):
                winfo = (ablock.get("windows") or {}).get(win)
                if not isinstance(winfo, dict):
                    continue
                started_at = winfo.get("started_at")
                if started_at is None:
                    continue
                started_iso = _iso(started_at)
                if not started_iso:
                    continue
                crow = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM turns "
                    "WHERE kind='api' AND session_id=? AND ts>=?",
                    (sid, started_iso)).fetchone()
                cu = float(crow[0] or 0.0) if crow else 0.0
                ns = None
                try:
                    from . import savings as _savings
                    detail = _savings.window_saved_measured(
                        conn, acct, started_iso, None, detail=True, session=sid)
                    if isinstance(detail, dict):
                        ns = float(detail.get("net") or 0.0)
                    elif detail is not None:
                        ns = float(detail)
                except Exception:
                    pass
                # T32.1.c3: the session's saved $ by family through the account's pool rates
                nsf = psp = None
                try:
                    from . import savings as _savings
                    nsf = _savings.window_saved_measured(
                        conn, acct, started_iso, None, by_family=True, session=sid)
                    prates = winfo.get("pool_rates")
                    if prates and nsf:
                        from . import fit as _fit
                        psp = _fit.pct_saved(nsf, prates)
                except Exception:
                    pass
                wins[win] = {"cost_used": round(cu, 6),
                             "net_saved": round(ns, 6) if ns is not None else None,
                             "net_saved_by_family": nsf,
                             "pct_saved_pooled": psp}
        entry["windows"] = wins
        # session-wide vanilla-net-v4 walk; the statusline's fewer/lasts figure (3.9.8 T4)
        try:
            from . import savings as _savings
            sd = _savings.window_saved_measured(conn, acct, None, None, detail=True, session=sid)
            if isinstance(sd, dict):
                entry["saved_net"] = round(float(sd.get("net") or 0.0), 6)
            elif sd is not None:
                entry["saved_net"] = round(float(sd), 6)
            else:
                entry["saved_net"] = None
        except Exception:
            entry["saved_net"] = None
        entry["started"] = row["started"]          # kept for the straddle check on the render path
        # the straddle piece's weeks: each seven-day instance at its own rate (fix-9.c2)
        if acct:
            wk = _weeks_by_window(conn, acct, [sid], None, cache=wcache)
            for fld in WEEKS_FIELDS:
                entry[fld] = wk[fld]
        out[sid] = entry
    return out


def alerts_block(conn, cfg=None, hours=24):
    """Recent ``model_mismatch`` / ``account_change`` events for statusline line 4."""
    import json as _json

    cutoff = _cutoff_iso(hours)
    out = []
    rows = conn.execute(
        "SELECT ts, session_id, run_id, kind, detail_json FROM events "
        "WHERE kind IN ('model_mismatch','account_change','error','memory_linked') AND ts>=? "
        "ORDER BY ts DESC LIMIT 20", (cutoff,)).fetchall()
    for row in rows:
        try:
            detail = _json.loads(row["detail_json"]) if row["detail_json"] else {}
        except ValueError:
            detail = {}
        entry = {"kind": row["kind"], "ts": row["ts"], "session_id": row["session_id"],
                 "run_id": row["run_id"]}
        if isinstance(detail, dict):
            for key in ("agent", "agent_type", "expected", "seen", "from", "to", "error"):
                if key in detail:
                    entry[key] = detail[key]
        out.append(entry)
    return out


# --------------------------------------------------------------------------- seed monitor (T10)

def _median_int(vals):
    """Integer median without importing ``statistics``."""
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) // 2
    return s[mid]


def _seed_suspects(project_dir):
    """Suspect files that feed the expert seed, sized with ``os.stat`` only (T10)."""
    suspects = []
    claude_md = os.path.join(project_dir, "CLAUDE.md")
    if os.path.isfile(claude_md):
        suspects.append({"path": "CLAUDE.md", "size": os.path.getsize(claude_md)})

    skills_dir = os.path.join(project_dir, ".claude", "skills")
    if os.path.isdir(skills_dir):
        try:
            for name in sorted(os.listdir(skills_dir)):
                skill_md = os.path.join(skills_dir, name, "SKILL.md")
                if os.path.isfile(skill_md):
                    suspects.append({"path": ".claude/skills/%s/SKILL.md" % name,
                                     "size": os.path.getsize(skill_md)})
        except OSError:
            pass

    mem_repo = os.path.join(project_dir, ".claude-state", "memory", "MEMORY.md")
    if os.path.isfile(mem_repo):
        suspects.append({"path": ".claude-state/memory/MEMORY.md",
                         "size": os.path.getsize(mem_repo)})
    else:
        try:
            from . import paths as _paths
            slug = _paths.project_slug(project_dir)
            mem_user = os.path.join(_paths.projects_dir(), slug, "memory", "MEMORY.md")
            if os.path.isfile(mem_user):
                suspects.append({"path": "~/.claude/projects/%s/memory/MEMORY.md" % slug,
                                 "size": os.path.getsize(mem_user)})
        except Exception:
            pass

    return suspects


def _seed_for_projects(conn, projects, cfg=None):
    """Compute expert seed stats per project and phase, add alerts (T10).

    One cheap SQL + ``os.stat``-only suspect sizing; safe for the hook path.
    """
    if not projects:
        return []
    try:
        rows = conn.execute(
            "SELECT s.project, s.cwd, ar.phase, ar.seed_ctx "
            "FROM agent_runs ar "
            "JOIN sessions s ON s.session_id = ar.session_id "
            "WHERE ar.kind = 'expert' AND ar.seed_ctx IS NOT NULL AND ar.seed_ctx > 0"
        ).fetchall()
    except Exception:
        return []

    grouped = {}                              # {pkey: {phase: [seed_ctx, ...]}}
    dirs = {}                                 # {pkey: project_dir}
    for row in rows:
        raw = row["project"] or row["cwd"]
        pkey = _project_key(raw)
        if not pkey or pkey not in projects:
            continue
        phase = str(row["phase"] or "")
        if not phase:
            continue
        grouped.setdefault(pkey, {}).setdefault(phase, []).append(int(row["seed_ctx"]))
        if pkey not in dirs:
            dirs[pkey] = str(raw or "")

    from . import config as _config
    max_tokens = int(_config.get(cfg, "seed.max_tokens", 40000) or 40000)
    growth_threshold = float(_config.get(cfg, "seed.growth_pct", 20) or 20)
    alerts = []

    for pkey, phases in grouped.items():
        sorted_phases = sorted(phases.keys())
        if not sorted_phases:
            continue
        current_phase = sorted_phases[-1]
        vals = phases[current_phase]
        med = _median_int(vals)
        mx = max(vals)
        n = len(vals)

        prev_phase = sorted_phases[-2] if len(sorted_phases) > 1 else None
        prev_median = None
        growth = None
        if prev_phase is not None:
            prev_vals = phases[prev_phase]
            prev_median = _median_int(prev_vals)
            if prev_median > 0:
                growth = round((med - prev_median) / prev_median * 100, 1)

        projects[pkey]["seed"] = {
            "phase": current_phase, "median": med, "max": mx, "n": n,
            "prev_phase": prev_phase, "prev_median": prev_median, "growth_pct": growth,
        }

        if mx > max_tokens or (growth is not None and growth >= growth_threshold):
            suspects = _seed_suspects(dirs.get(pkey, pkey))
            alerts.append({
                "kind": "seed_growth", "project": pkey,
                "phase": current_phase, "median": med, "max": mx,
                "suspects": suspects,
            })
    return alerts


# --------------------------------------------------------------------------- document

def read():
    from . import fsutil, paths

    doc = fsutil.read_json(paths.summary_path(), None)
    return doc if isinstance(doc, dict) else {}


def write(doc):
    from . import fsutil, paths

    fsutil.atomic_write_json(paths.summary_path(), doc, indent=1)
    return doc


def _modeled_block(conn, cfg, doc):
    """Compute the rolling replay per account and upsert per-session savings rows."""
    from . import db, replay
    import json as _json

    params = replay.params_from_cfg(cfg)
    accounts = doc.get("accounts") or {}
    sessions = doc.get("sessions") or {}
    now = _now_iso()

    for email, ablock in accounts.items():
        turns, seeds = replay.account_turns(conn, email)
        if not turns:
            continue
        p = dict(params)                # rolling mutates params (side-channel)
        results = replay.rolling(turns, seeds, p)
        sums = replay.session_sums(results)
        assume_json = _json.dumps(
            replay.assumptions(p, results), ensure_ascii=False, default=str)

        for sid, modeled in sums.items():
            db.upsert_savings(conn, {
                "run_id": sid, "session_id": sid,
                "modeled_saved_usd": round(modeled, 6),
                "assumptions_json": assume_json,
                "updated": now,
            })

        period = ablock.get("period")
        if isinstance(period, dict) and period.get("start"):   # 3.11 T9: no period without a renewal day
            pstart = period.get("start", "")
            period["cost_saved_modeled"] = round(replay.sum_since(results, pstart), 6)

        for w, winfo in (ablock.get("windows") or {}).items():
            started_at = winfo.get("started_at")
            if started_at is not None:
                started_iso = _iso(started_at)
                if started_iso:
                    winfo["cost_saved_modeled"] = round(
                        replay.sum_since(results, started_iso), 6)

        for sid, modeled in sums.items():
            if str(sid) in sessions:
                sessions[str(sid)]["saved_modeled"] = round(modeled, 6)


def rebuild(conn, cfg=None, windows_only=False, sessions_only=False, readers=None):
    """Rewrite ``summary.json`` (or one block of it) under the file lock.

    ``readers``: optional ``db.union_readers`` list forwarded to
    :func:`accounts_block` so window fields aggregate across machines.
    Hooks call without ``readers`` (local only); the sampler's change path and
    ``recalc``/``report`` pass them.
    """
    from . import fsutil, paths, prices

    def _patch(doc):
        doc = doc if isinstance(doc, dict) else {}
        doc["schema"] = SCHEMA
        doc["updated"] = _now_iso()
        doc["prices_version"] = prices.PRICES_VERSION
        doc.setdefault("meta", {})["era_start"] = era_start(conn, cfg)
        if not sessions_only:
            doc["accounts"] = accounts_block(conn, cfg, readers=readers)
            doc["projects"] = projects_block(conn, cfg, readers=readers, accounts=doc["accounts"])
        if not windows_only:
            doc["sessions"] = sessions_block(conn, cfg, accounts=doc.get("accounts"))
            doc["alerts"] = alerts_block(conn, cfg)
        doc.setdefault("accounts", {})
        doc.setdefault("projects", {})
        doc.setdefault("sessions", {})
        doc.setdefault("alerts", [])
        if not sessions_only:
            seed_alerts = _seed_for_projects(conn, doc["projects"], cfg)
            doc["alerts"].extend(seed_alerts)
        if not windows_only:
            _modeled_block(conn, cfg, doc)
        return doc

    try:
        return fsutil.locked_update(paths.summary_path(), _patch, timeout_ms=2000, default={})
    except Exception:
        from . import log

        log.log("summary_rebuild_failed")
        return None


def patch_session(sid, fields):
    """Merge one session's fields without a full rebuild (SessionStart)."""
    from . import fsutil, paths

    def _patch(doc):
        doc = doc if isinstance(doc, dict) else {}
        doc.setdefault("schema", SCHEMA)
        doc.setdefault("accounts", {})
        doc.setdefault("alerts", [])
        sessions = doc.setdefault("sessions", {})
        entry = sessions.get(str(sid))
        entry = entry if isinstance(entry, dict) else {}
        entry.update({k: v for k, v in (fields or {}).items() if v is not None})
        sessions[str(sid)] = entry
        doc["updated"] = _now_iso()
        return doc

    try:
        return fsutil.locked_update(paths.summary_path(), _patch, timeout_ms=1000, default={})
    except Exception:
        return None


# --------------------------------------------------------------------------- detached rebuild (T5)
#
# A full rebuild costs seconds to tens of seconds on a real ledger, so the Stop /
# SubagentStop / SessionEnd hooks only *request* one.  Single flight: the lock file
# ``summary.rebuild.lock`` holds ``"<pid> <requester|rebuild>"``; a held lock turns a
# request into the dirty stamp ``summary.rebuild.dirty`` (``full`` or ``sessions``),
# which the running child consumes by rebuilding again before it releases the lock.

_LOCK_STALE_S = 600            # any lock older than 10 min is stale
_START_GRACE_S = 30            # a requester's lock outlives the requester while the child starts


def _rebuild_files():
    from . import paths

    d = paths.ledger_dir()
    return os.path.join(d, "summary.rebuild.lock"), os.path.join(d, "summary.rebuild.dirty")


def _lock_stale(lock):
    """True when ``lock`` is missing, older than 10 min, or its owner is dead."""
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
    role = parts[1] if len(parts) > 1 else "rebuild"
    return role != "requester" or age > _START_GRACE_S


def _write_lock(lock, role):
    from . import fsutil

    fsutil.atomic_write_text(lock, "%d %s\n" % (os.getpid(), role))


def _take_lock(lock):
    """Create ``lock`` exclusively (taking over a stale one); False when it is held."""
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


def _mark_dirty(dirty, sessions_only):
    from . import fsutil

    if not sessions_only or fsutil.read_text(dirty, "").strip() != "full":
        fsutil.atomic_write_text(dirty, "sessions\n" if sessions_only else "full\n")


def _take_dirty(dirty):
    """``None`` (no stamp), ``True`` (sessions only) or ``False`` (full); removes the stamp."""
    try:
        with open(dirty, encoding="utf-8") as fh:
            mode = fh.read().strip()
        os.remove(dirty)
    except OSError:
        return None
    return mode == "sessions"


def rebuild_command(sessions_only=False):
    """``(cmd, env)`` of the detached rebuild child (``python -m pa.summary --rebuild``)."""
    import sys

    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # -s -P, not -I: -I ignores PYTHONPATH, which is how the installed copy is found
    cmd = [sys.executable, "-s", "-P", "-X", "utf8", "-m", "pa.summary", "--rebuild"]
    if sessions_only:
        cmd.append("--sessions-only")
    return cmd, {"PYTHONPATH": pkg}


def request_rebuild(cfg=None, sessions_only=False):
    """Hooks: ask for a ``summary.json`` rebuild without waiting for it (single flight).

    Lock free (or stale) -> take it and spawn one detached rebuild child; lock held ->
    write the dirty stamp (``full`` wins over ``sessions``) and spawn nothing.
    True when a child was spawned.
    """
    try:
        lock, dirty = _rebuild_files()
        if not _take_lock(lock):
            _mark_dirty(dirty, sessions_only)
            return False
        from . import notify

        cmd, env = rebuild_command(sessions_only)
        if notify.spawn_detached(cmd, env):
            return True
        try:
            os.remove(lock)
        except OSError:
            pass
    except Exception:
        from . import log

        log.log("summary_request_failed")
    return False


def run_rebuild(sessions_only=False):
    """The child: rebuild, rebuild again while a dirty stamp exists, release the lock.

    Writes nothing to stdout; failures go to the hooks log.
    """
    from . import config, log
    from .hooks import close_db, open_db

    lock, dirty = _rebuild_files()
    try:
        _write_lock(lock, "rebuild")
        cfg = config.load()
        pending = _take_dirty(dirty)
        if pending is False:
            sessions_only = False
        while True:
            conn = open_db(create=False)
            if conn is None:
                break
            try:
                rebuild(conn, cfg, sessions_only=sessions_only)
            finally:
                close_db(conn)
            pending = _take_dirty(dirty)
            if pending is None:
                break
            sessions_only = pending
            _write_lock(lock, "rebuild")                   # fresh mtime: a long loop is not stale
    except Exception as exc:
        log.log("summary_rebuild_child_failed", error=str(exc)[:200])
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def main(argv=None):
    if argv is None:
        import sys

        argv = sys.argv[1:]
    if "--rebuild" in argv:
        run_rebuild(sessions_only="--sessions-only" in argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
