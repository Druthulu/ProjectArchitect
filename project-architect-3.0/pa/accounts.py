"""Which account is this session billing? (design A section C, SessionStart row)

Public:
    credentials_path()                          config dir + .credentials.json
    credentials_key()                           "<mtime>:<size>" (content never read)
    claude_json_path()                          $CLAUDE_CONFIG_DIR or <home>, + .claude.json
    auth_status(cfg=None, timeout=3.0, refresh=False) -> dict
                                                .claude.json oauthAccount first, CLI fallback
    parse_auth_output(text) -> dict
    stamp_session(session_id, account, source="auth", extra=None) -> dict
    account_for(session_id) -> (account, source)
    account_row(status, now=None) -> dict        an ``accounts`` table row
    tier_from(subscription_type, rate_limit_tier) -> "max20"|"max5"|"pro"|None
    stamp_tier(conn, email, tier, source="credentials")
    tier_for(conn, email, pa_json) -> str        override > credentials > pa.json > "max5"
    restamp_tier(conn, email)                    tier from ``.credentials.json`` (one read)

``claude auth status`` costs ~210 ms (recon V6), which SessionStart can afford
once but no other hook can, so the result is cached in
``state/auth_cache.json`` under the **mtime+size of ``.credentials.json``** --
the file's *content* is never opened for that key, only ``os.stat``.  A re-login
changes the mtime and the next SessionStart refreshes.  The plan tier is the one
content read (:func:`restamp_tier`, via ``usage_api.read_credentials``; the
token is never kept).

The per-session stamp (``state/accounts/<sid>.json``) lets every later hook
attribute turns to an account without another subprocess or a database read.
"""

import json
import os
import re
import time

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ORG_RE = re.compile(r"(?im)^\s*(?:organization|org|workspace)\s*[:=]\s*(.+?)\s*$")
_ORGID_RE = re.compile(r"(?im)^\s*(?:organization id|org id|org_id)\s*[:=]\s*([A-Za-z0-9\-]+)")
_SUB_RE = re.compile(r"(?im)^\s*(?:subscription|plan|tier|account type)\s*[:=]\s*(.+?)\s*$")


# --------------------------------------------------------------------------- credentials

def config_dir():
    """``CLAUDE_CONFIG_DIR`` when set, else ``<home>/.claude`` (two config dirs exist)."""
    from . import paths

    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.abspath(env) if env else paths.claude_dir()


def credentials_path():
    return os.path.join(config_dir(), ".credentials.json")


def credentials_key():
    """Cache key from ``stat`` alone -- the credentials file is never opened."""
    try:
        st = os.stat(credentials_path())
    except OSError:
        return "absent"
    return "%d:%d" % (int(st.st_mtime), int(st.st_size))


# --------------------------------------------------------------------------- parsing

def parse_auth_output(text):
    """Best-effort parse of ``claude auth status`` (plain text or JSON)."""
    out = {"email": None, "org_id": None, "org_name": None, "subscription": None}
    raw = _ANSI_RE.sub("", str(text or ""))
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            flat = {}

            def _walk(node, prefix=""):
                if isinstance(node, dict):
                    for k, v in node.items():
                        _walk(v, (prefix + "." + str(k)) if prefix else str(k))
                elif node is not None and not isinstance(node, (list, dict)):
                    flat[prefix.lower()] = node

            _walk(data)
            for key, val in flat.items():
                if out["email"] is None and key.endswith("email"):
                    out["email"] = str(val)
                elif out["org_id"] is None and key.endswith(("organizationid", "org_id", "orguuid")):
                    out["org_id"] = str(val)
                elif out["org_name"] is None and key.endswith(("organizationname", "orgname", "organization")):
                    out["org_name"] = str(val)
                elif out["subscription"] is None and key.endswith(("subscriptiontype", "subscription", "plan", "tier")):
                    out["subscription"] = str(val)
    if out["email"] is None:
        m = _EMAIL_RE.search(raw)
        if m:
            out["email"] = m.group(0)
    for key, rx in (("org_name", _ORG_RE), ("org_id", _ORGID_RE), ("subscription", _SUB_RE)):
        if out[key] is None:
            m = rx.search(raw)
            if m:
                out[key] = m.group(1).strip()
    return out


# --------------------------------------------------------------------------- status

def _cache_read():
    from . import fsutil, paths

    return fsutil.read_json(paths.auth_cache_path(), {}) or {}


def _cache_write(entry):
    from . import fsutil, paths

    try:
        fsutil.atomic_write_json(paths.auth_cache_path(), entry, indent=1)
    except Exception:
        pass
    return entry


def claude_json_path():
    """``$CLAUDE_CONFIG_DIR/.claude.json`` when set, else ``<home>/.claude.json``."""
    from . import paths

    env = os.environ.get("CLAUDE_CONFIG_DIR")
    base = os.path.abspath(env) if env else paths.home()
    return os.path.join(base, ".claude.json")


def _claude_json_email():
    """``{email, org_id, org_name}`` from ``oauthAccount`` in ``.claude.json``, or None.

    Reads only ``oauthAccount.emailAddress`` (and the org uuid/name); never raises.
    """
    try:
        with open(claude_json_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        acct = data.get("oauthAccount") if isinstance(data, dict) else None
        email = acct.get("emailAddress") if isinstance(acct, dict) else None
        if not isinstance(email, str) or not email:
            return None
        org_id = acct.get("organizationUuid")
        org_name = acct.get("organizationName")
        return {"email": email,
                "org_id": org_id if isinstance(org_id, str) else None,
                "org_name": org_name if isinstance(org_name, str) else None}
    except Exception:
        return None


def _running_claude_linux():
    """Executable of the nearest ``claude*`` ancestor (<= 6 up via /proc), or None."""
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            return None
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                argv0 = fh.read().split(b"\0", 1)[0].decode("utf-8", "replace")
            if os.path.basename(argv0).startswith("claude"):
                return os.readlink("/proc/%d/exe" % pid)
            with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as fh:
                stat = fh.read()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except Exception:
            return None
    return None


def _claude_binary():
    """Absolute path of the ``claude`` CLI, or None."""
    import shutil

    found = shutil.which("claude")
    if found:
        return os.path.abspath(found)
    from . import paths

    local = os.path.join(paths.home(), ".local", "bin",
                         "claude.exe" if os.name == "nt" else "claude")
    if os.path.isfile(local):
        return local
    if os.path.isdir("/proc"):
        return _running_claude_linux()
    return None


def auth_status(cfg=None, timeout=3.0, refresh=False):
    """Account of the current config dir: ``{email, org_id, org_name,
    subscription, source, key, ts}``.

    ``source`` is ``auth`` (fresh subprocess), ``cache`` (credentials unchanged),
    ``config`` (``recalc_default_account`` when the CLI is unavailable) or
    ``unknown``.  Never raises and never blocks longer than ``timeout``.
    Only a ``source: auth`` result is written to the auth cache; the config
    fallback is returned but never cached (3.15 T1).
    ``oauthAccount.emailAddress`` in :func:`claude_json_path` wins (``via:
    claude_json``, no subprocess); the cache and the CLI (absolute path, ``via:
    cli``) serve only when it has none.  A CLI failure logs ``auth_status_fail``.
    """
    key = credentials_key()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    found = _claude_json_email()
    if found:
        entry = {"key": key, "ts": stamp, "source": "auth", "via": "claude_json",
                 "email": found["email"], "org_id": found.get("org_id"),
                 "org_name": found.get("org_name"), "subscription": None}
        return _cache_write(entry)

    cached = _cache_read()
    if not refresh and cached.get("key") == key and cached.get("email"):
        out = dict(cached)
        out["source"] = "cache"
        return out

    import subprocess

    text = ""
    rc = -1
    fail = None
    exe = _claude_binary()
    t0 = time.monotonic()
    try:
        if exe is None:
            raise FileNotFoundError("claude")
        proc = subprocess.run([exe, "auth", "status"],                 # noqa: S603
                              capture_output=True, timeout=float(timeout))
        rc = proc.returncode
        text = (proc.stdout or b"").decode("utf-8", "replace")
        if not text.strip():
            text = (proc.stderr or b"").decode("utf-8", "replace")
    except Exception as exc:
        text = ""
        fail = type(exc).__name__

    parsed = parse_auth_output(text)
    if fail is None and (rc != 0 or not parsed.get("email")):
        fail = "rc=%d" % rc
    if fail is not None:
        from . import log
        log.log("auth_status_fail", exc=fail,
                elapsed_s=round(time.monotonic() - t0, 2), path=exe)
    entry = {"key": key, "ts": stamp, "rc": rc,
             "source": "auth" if parsed.get("email") else "unknown"}
    if parsed.get("email"):
        entry["via"] = "cli"
    entry.update(parsed)
    if not entry.get("email"):
        fallback = (cfg or {}).get("recalc_default_account") if isinstance(cfg, dict) else None
        if fallback:
            entry["email"] = fallback
            entry["source"] = "config"
        elif cached.get("email"):
            entry = dict(cached)
            entry["source"] = "cache"
            entry["key"] = key
    if entry.get("email") and entry.get("source") == "auth":
        _cache_write(entry)
    return entry


def account_source_of(status):
    """``file`` (auth via .claude.json), ``cli`` (auth otherwise) or ``fallback`` (3.15 T12)."""
    status = status if isinstance(status, dict) else {}
    if status.get("source") != "auth":
        return "fallback"
    return "file" if status.get("via") == "claude_json" else "cli"


def account_row(status, now=None):
    """An ``accounts`` table row from :func:`auth_status` output."""
    status = status if isinstance(status, dict) else {}
    now = now or (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    return {"email": status.get("email"), "org_id": status.get("org_id"),
            "org_name": status.get("org_name"), "subscription": status.get("subscription"),
            "first_seen": now, "last_seen": now}


# --------------------------------------------------------------------------- tier

TIERS = ("max20", "max5", "pro")
_RATE_TIER_MAP = {"default_claude_max_20x": "max20", "default_claude_max_5x": "max5"}


def tier_from(subscription_type, rate_limit_tier):
    """The plan tier from the credentials keys; None for team, enterprise or unknown."""
    tier = _RATE_TIER_MAP.get(rate_limit_tier) if isinstance(rate_limit_tier, str) else None
    if tier:
        return tier
    if subscription_type == "pro":
        return "pro"
    return None


def stamp_tier(conn, email, tier, source="credentials"):
    """Record ``tier`` on the ``accounts`` row of ``email``.

    ``source="credentials"`` never overwrites an ``override`` row, and a None
    tier is no change (the pa.json fallback stays in force).  ``source="override"``
    with a None tier clears the row's tier and tier_source.  Returns the row's
    tier after the call (None when unset).
    """
    if not email or conn is None:
        return None
    from . import db

    row = conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                       (email,)).fetchone()
    if source == "credentials":
        if tier is None or (row is not None and row[1] == "override"):
            return row[0] if row is not None else None
    if tier is None:
        db.upsert_account(conn, {"email": email, "tier": None, "tier_source": None})
        return None
    db.upsert_account(conn, {"email": email, "tier": tier, "tier_source": source})
    return tier


def tier_for(conn, email, pa_json):
    """The account's tier: override row > credentials row > pa.json ``tier`` > "max5"."""
    if conn is not None and email:
        try:
            row = conn.execute("SELECT tier, tier_source FROM accounts WHERE email=?",
                               (email,)).fetchone()
        except Exception:
            row = None
        if row is not None and row[0] in TIERS:
            return row[0]
    fallback = pa_json.get("tier") if isinstance(pa_json, dict) else None
    return fallback if fallback in TIERS else "max5"


def restamp_tier(conn, email):
    """Read the credentials once and stamp the mapped tier (never raises)."""
    try:
        from . import usage_api

        creds = usage_api.read_credentials(config_dir())
        tier = tier_from(creds.get("subscription_type"), creds.get("rate_limit_tier"))
        return stamp_tier(conn, email, tier, source="credentials")
    except Exception:
        return None


def _restamp_soft(conn, email):
    """:func:`restamp_tier` on ``conn`` or a ledger connection of its own (fail-soft)."""
    own_conn = False
    try:
        if conn is None:
            from .hooks import open_db
            conn = open_db()
            own_conn = True
        restamp_tier(conn, email)
    except Exception:
        pass
    finally:
        if own_conn and conn is not None:
            try:
                from .hooks import close_db
                close_db(conn)
            except Exception:
                pass


# --------------------------------------------------------------------------- per-session stamp

def _stamp_path(session_id):
    from . import paths

    return paths.state_path("accounts", "%s.json" % (session_id or "unknown"))


def stamp_session(session_id, account, source="auth", extra=None):
    """Record the account of a session so later hooks need no subprocess.

    Also records ``cred_key`` (``credentials_key()``) so :func:`refresh_account`
    can detect a mid-session re-login without running a subprocess on every turn.
    """
    from . import fsutil

    row = {"session_id": session_id, "account": account, "source": source,
           "cred_key": credentials_key(),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if isinstance(extra, dict):
        row.update(extra)
    try:
        fsutil.atomic_write_json(_stamp_path(session_id), row, indent=1)
    except Exception:
        pass
    return row


def account_for(session_id):
    """``(account, source)`` from the per-session stamp (``(None, "unknown")``)."""
    from . import fsutil

    row = fsutil.read_json(_stamp_path(session_id), {}) or {}
    acct = row.get("account")
    return (acct, row.get("source") or "unknown") if acct else (None, "unknown")


def refresh_account(session_id, cfg=None, conn=None):
    """Re-check the credentials key; re-stamp and update the ledger on a switch.

    Returns ``(account, changed, previous)``.  When the credentials key has not
    changed since the stamp was written, returns the stamped account immediately
    with no subprocess.  When it *has* changed (or the stamp has no ``cred_key``),
    runs :func:`auth_status` to detect the current account.

    If the account changed: re-stamps the session (``source="switch"``), updates
    ``sessions.account`` in the ledger, patches the running entry's ``account``
    field, and inserts an ``events:account_change`` event.

    With a stamp, only an auth-sourced email (``source: auth``) is acted on: any
    other source (config/cache/unknown) or no email keeps the stamp, its
    ``cred_key`` and the ledger unchanged, so the next key check retries (3.15 T1).
    Without a stamp, any email (config included) stamps the session.

    Never raises; on any failure returns ``(stamped_account, False, None)``.
    """
    from . import fsutil

    try:
        stamp = fsutil.read_json(_stamp_path(session_id), {}) or {}
    except Exception:
        stamp = {}

    stamped = stamp.get("account")
    stamp_key = stamp.get("cred_key")  # None on old stamps
    current_key = credentials_key()

    # fast path: key unchanged, no re-check needed
    if stamp_key is not None and stamp_key == current_key:
        return (stamped, False, None)

    # slow path: key changed (or old stamp without cred_key) -- re-detect
    try:
        status = auth_status(cfg, refresh=True)
    except Exception:
        return (stamped, False, None)

    new_email = status.get("email")
    if not new_email:
        return (stamped, False, None)
    if stamped and status.get("source") != "auth":
        return (stamped, False, None)   # no auth-sourced email: keep the stamp, retry next key check

    # a re-login may change the plan: restamp the tier of the current account
    _restamp_soft(conn, new_email)

    if new_email == stamped:
        # same account, just refresh the key in the stamp
        stamp_session(session_id, new_email,
                      source=stamp.get("source") or "auth")
        return (new_email, False, None)

    # account genuinely changed
    try:
        stamp_session(session_id, new_email, source="switch")
    except Exception:
        pass

    # update sessions.account in the ledger
    own_conn = False
    try:
        if conn is None:
            from .hooks import open_db, close_db
            conn = open_db()
            own_conn = True
        from . import db
        db.upsert_session(conn, {"session_id": session_id, "account": new_email})
        db.insert_event(conn, "account_change",
                        {"from": stamped, "to": new_email,
                         "source": "switch", "cred_key": current_key,
                         "account_source": account_source_of(status)},
                        session_id=session_id, account=new_email)
    except Exception:
        pass
    finally:
        if own_conn and conn is not None:
            try:
                from .hooks import close_db
                close_db(conn)
            except Exception:
                pass

    # update the running entry's account
    try:
        from . import running
        running.patch_session(session_id, {"account": new_email})
    except Exception:
        pass

    return (new_email, True, stamped)


# --------------------------------------------------------------------------- timeline

def account_timeline(conn, session_id):
    """Load ``account_change`` events once, return ``(ts) -> account``.

    Before the first event: the event's ``from``; between events: the earlier
    event's ``to``; after the last: its ``to``; no events: ``sessions.account``.
    Recalc calls this once per session so the cost is one SQL, not one per turn.
    """
    rows = conn.execute(
        "SELECT ts, detail_json FROM events"
        " WHERE session_id=? AND kind='account_change' ORDER BY ts",
        (session_id,)).fetchall()
    if not rows:
        try:
            srow = conn.execute("SELECT account FROM sessions WHERE session_id=?",
                                (session_id,)).fetchone()
        except Exception:
            srow = None
        fallback = srow["account"] if srow else None
        return lambda ts: fallback

    events = []
    for r in rows:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        events.append((r["ts"], detail.get("from"), detail.get("to")))

    def _resolve(ts):
        if not ts or ts < events[0][0]:
            return events[0][1]  # before first event: "from"
        acct = events[0][2]
        for evt_ts, _frm, to in events:
            if ts >= evt_ts:
                acct = to
            else:
                break
        return acct

    return _resolve


def account_at(conn, session_id, ts):
    """The account in force at ``ts`` for the session (one SQL per call)."""
    return account_timeline(conn, session_id)(ts)
