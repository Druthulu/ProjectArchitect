"""The warmer: one daemon per session that keeps idle subagents' caches warm (3.9.5 T4).

Contract: ``docs/pa3-build/design/warmer.md`` ``## Decision``.  ``python -s -P -X utf8 -m pa.warmer
--project <root> --session <sid> [--pid <claude pid>]``, spawned detached by the SessionStart and
SubagentStart hooks (:func:`start_if_needed`), stopped by SessionEnd (:func:`stop`).

Files under ``<root>/.run/warmer/``: ``<sid>.pid`` (this daemon's pid), ``<sid>.runs`` (JSON lines
the hooks append: ``{"add": id, "agent_type", "role"}`` / ``{"end": id}``), ``<sid>.wake`` (this
daemon appends ``warm <agent id> <idle_s>``; the session's ``Monitor`` on ``tail -n0 -F`` relays
``.`` by SendMessage), ``<sid>.undelivered`` (3.15 T6: JSON lines ``{run_id, line, at}`` of wake
lines no ping followed within ``UNDELIVERED_S``; the Stop hook reads them), ``<sid>.stop``
(SessionEnd), ``<sid>.log``.  State ``<root>/.run/warmer.json``
``{session, updated, runs: {run_id: {last_request, ttl, pings, last_ping, via, agent_type, idle}}}``
(plus ``finished: true`` / ``ended: <epoch>`` when set), epoch seconds.

Lifecycle (2026-09-24): ``{"end": id}`` is advisory: logged, ``ended`` set, the watch continues
(a later ``add`` clears it); SubagentStop fires on an idle expert with a live coder, which starts
again when the hand-back wakes it.  The watch drops only on a transcript gone after it was seen, or
the daemon's exit; ``due()``'s stale-past-TTL skip is the natural end.  A run is ``finished`` (never
pinged) once its ``SubagentHandback`` tool_result has arrived and only pings or harness nudges
follow; SubagentStop fires only when the parent's turn ends, so a finished background coder looked
idle and was pinged.  A harness nudge (:func:`is_nudge`, 3.9.5 T6) is no request: like a ping it
neither resets the count nor clears ``finished``; the base still moves to it.

Timer (binding): base = the run's last ``user`` record (prompt, tool_result, delivered message),
never the assistant record; idle = the last user/assistant record is an assistant record with no
``tool_use``.  A ping landing (text ``.`` or ending ``: .``) rebases and keeps the count; any other
non-nudge user record resets it.  The line is written at ``fire - relay_lead_s`` seconds of
idleness, once per base, up to :func:`cap` pings: ``max_pings_1h`` (1h runs); 5m runs
``clamp(floor(rewrite_usd / ping_usd), 0, max_pings_5m)`` from the run's and the router's last
assistant usage (3.15 T3; floor 0 since T3.1: a ping dearer than the rewrite never fires),
``max_pings_5m_fallback`` while any input is missing or unpriced.

Import budget: hooks import this module, so module level stays ``json, os, sys, time``.
"""

import json
import os
import sys
import time

VIA = "monitor"
TAIL_BYTES = 262144                      # first read of a transcript: its last 256 KB
_WDEFAULTS = {"fire_5m_s": 285, "fire_1h_s": 3300, "relay_lead_s": 25,
               "max_pings_5m": 12,       # the ceiling (3.15 T3); was 3 (max_pings), developer 2026-09-24
               "max_pings_5m_fallback": 3,   # 3.15 T3: an input of cap() missing or unpriced
               "max_pings_1h": 3, "poll_s": 5}
_TTL_S = {"5m": 300, "1h": 3600}
# 3.15 T3: the `.` turn itself, per request (the run's ping turn, the router's two relay requests):
# the few new tokens it writes to the cache (the relayed message, the `.` reply, the SendMessage
# call) and its output.  Estimates; small next to the context reads they ride on.
PING_WRITE_TOKENS = 150
PING_OUTPUT_TOKENS = 30
ROUTER_RELAY_REQUESTS = 2                # the relay turn: SendMessage, then the closing `.`
# 3.15 T6: a wake line whose ping has not landed this long after it was written is flagged once
# (event ``warm_undelivered`` + a JSON line in ``<sid>.undelivered``; the Stop hook blocks on it).
UNDELIVERED_S = 120


# --------------------------------------------------------------------------- paths

def warmer_dir(root):
    return os.path.join(root, ".run", "warmer")


def path(root, sid, ext):
    """``<root>/.run/warmer/<sid>.<ext>`` (pid, runs, wake, stop, log)."""
    return os.path.join(warmer_dir(root), "%s.%s" % (sid, ext))


def state_path(root):
    return os.path.join(root, ".run", "warmer.json")


def settings(cfg):
    """``warmer.*`` from the ledger config over the defaults (numbers)."""
    sect = (cfg or {}).get("warmer") if isinstance(cfg, dict) else None
    sect = sect if isinstance(sect, dict) else {}
    out = {}
    for key, val in _WDEFAULTS.items():
        try:
            out[key] = float(sect.get(key, val))
        except (TypeError, ValueError):
            out[key] = float(val)
    return out


# --------------------------------------------------------------------------- process helpers

def _read_pid(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def pid_alive(pid):
    """True when ``pid`` names a live process (Windows: never ``os.kill(pid, 0)``, it terminates)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = k32.OpenProcess(0x00100000 | 0x1000, False, pid)   # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return ctypes.get_last_error() == 5                      # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                                 # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate(pid):
    if not pid_alive(pid) or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
            k32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = k32.OpenProcess(0x0001, False, pid)             # PROCESS_TERMINATE
            if handle:
                k32.TerminateProcess(handle, 0)
                k32.CloseHandle(handle)
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _enabled(root):
    """pa.json ``warmer`` is anything but ``off`` (missing: on)."""
    from . import fsutil

    doc = fsutil.read_json(os.path.join(root, ".claude", "pa.json"), {}) or {}
    val = doc.get("warmer", "on") if isinstance(doc, dict) else "on"
    return not (val is False or str(val).strip().lower() in ("off", "false", "0", "no"))


def start_if_needed(root, sid, cfg=None, claude_pid=None):
    """Spawn the session's daemon unless ``<sid>.pid`` names a live process, pa.json says off, or
    env ``PA_WARMER_OFF=1`` (tests; the same family as ``PA_HOOKS_OFF``)."""
    if os.environ.get("PA_WARMER_OFF") == "1":
        return False
    if not (root and sid) or not _enabled(root):
        return False
    if pid_alive(_read_pid(path(root, sid, "pid"))):
        return False
    from . import notify

    try:
        os.remove(path(root, sid, "stop"))                           # a resumed session's old stop
    except OSError:
        pass
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # -s -P, not -I: -I ignores PYTHONPATH, which is how the installed copy is found
    cmd = [sys.executable, "-s", "-P", "-X", "utf8", "-m", "pa.warmer", "--project", root,
           "--session", sid, "--pid", str(claude_pid or "")]
    return bool(notify.spawn_detached(cmd, {"PYTHONPATH": pkg}))


def stop(root, sid):
    """SessionEnd: write ``<sid>.stop``, terminate the daemon (best effort), remove ``<sid>.pid``."""
    from . import fsutil

    fsutil.atomic_write_text(path(root, sid, "stop"), "%d\n" % int(time.time()))
    pid_file = path(root, sid, "pid")
    _terminate(_read_pid(pid_file))
    try:
        os.remove(pid_file)
    except OSError:
        pass


# --------------------------------------------------------------------------- the timer (pure)

def epoch(ts):
    """ISO timestamp (``…Z``) -> epoch seconds, or None."""
    if not ts:
        return None
    from datetime import datetime

    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _content(rec):
    msg = rec.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def user_text(rec):
    """The text of a user record (str content or its text blocks); tool_results give ``""``."""
    content = _content(rec)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text") or "" for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


_COORDINATOR_PREFIX = "The coordinator sent a message"


def is_ping(rec):
    """The warmer's ping landing.

    Text stripped is exactly ``.``, or ends ``: .`` (the inline coordinator wrapper), or is the
    harness's real relay wrapper (2026-09-24): first line starts with
    ``"The coordinator sent a message"`` and some line of the stripped text is exactly ``.``, e.g.
    ``"The coordinator sent a message while you were working:\\n.\\n\\nAddress this before
    completing your current task."``.
    """
    text = user_text(rec).strip()
    if text == "." or text.endswith(": ."):
        return True
    lines = text.splitlines()
    return bool(lines) and lines[0].startswith(_COORDINATOR_PREFIX) and any(
        line.strip() == "." for line in lines)


_NUDGE_PREFIX = "[Your previous response had no visible output"


def is_nudge(rec):
    """The harness's empty-response nudge (3.9.5 T6): no request, like a ping for the count."""
    return user_text(rec).strip().startswith(_NUDGE_PREFIX)


def has_tool_use(rec):
    content = _content(rec)
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_use"
                                             for b in content)


def _handback_ids(rec):
    """The ids of the ``SubagentHandback`` tool_use blocks of an assistant record."""
    content = _content(rec)
    if not isinstance(content, list):
        return []
    return [b.get("id") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            and b.get("name") == "SubagentHandback"]


def _result_ids(rec):
    content = _content(rec)
    if not isinstance(content, list):
        return []
    # an is_error result (a denied hand-back, 3.15 T18) did not deliver: the run is not finished
    return [b.get("tool_use_id") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("is_error")]


def new_run(run_id, agent_type=None, role=None, ttl="5m"):
    return {"run_id": run_id, "agent_type": agent_type, "role": role, "ttl": ttl, "path": None,
            "seen": False, "offset": None, "base": None, "idle": False, "pings": 0,
            "last_ping": None, "handback": None, "finished": False, "ended": None,
            "pending": None}


def apply_records(run, records):
    """Advance a run's timer over transcript records in order (other record types ignored)."""
    for rec in records:
        if not isinstance(rec, dict):
            continue
        kind = rec.get("type")
        if kind == "user":
            ts = epoch(rec.get("timestamp"))
            if ts is None:
                continue
            ping = is_ping(rec) or is_nudge(rec)     # a nudge counts as a ping here (3.9.5 T6)
            pend = run.get("pending")
            if pend and (is_ping(rec) or (not ping and ts >= pend["at"])):
                run["pending"] = None            # the line was relayed, or a real request came (3.15 T6)
            if not ping:
                run["pings"] = 0                 # a real request resets the count
            if run.get("handback") and run["handback"] in _result_ids(rec):
                run["finished"], run["handback"] = True, None    # the report was delivered
            elif not ping:
                run["finished"] = False          # a resume
            run["base"] = ts
            run["idle"] = False
        elif kind == "assistant":
            run["idle"] = not has_tool_use(rec)
            ids = _handback_ids(rec)
            if ids:
                run["handback"] = ids[-1]
            take_usage(run, rec)
            take_ttl(run, rec)
    return run


def take_ttl(run, rec):
    """Set ``run["ttl"]`` from an assistant record's cache writes (3.15 T4): any 1h write -> "1h",
    else any 5m write -> "5m", else unchanged (the role default stays until a write shows)."""
    msg = rec.get("message")
    usage = msg.get("usage") if isinstance(msg, dict) else None
    cc = usage.get("cache_creation") if isinstance(usage, dict) else None
    if not isinstance(cc, dict):
        return
    for key, ttl in (("ephemeral_1h_input_tokens", "1h"), ("ephemeral_5m_input_tokens", "5m")):
        try:
            if int(cc.get(key) or 0) > 0:
                run["ttl"] = ttl
                return
        except (TypeError, ValueError):
            pass


def take_usage(holder, rec):
    """Keep ``holder["ctx"]`` (input + cache_read + cache_creation) and ``holder["model"]`` from
    an assistant record's usage (3.15 T3); synthetic or empty usage is skipped."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    usage, model = msg.get("usage"), msg.get("model")
    if not isinstance(usage, dict) or not model or str(model).startswith("<"):
        return
    ctx = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        try:
            ctx += int(usage.get(key) or 0)
        except (TypeError, ValueError):
            pass
    if ctx > 0:
        holder["ctx"], holder["model"] = ctx, model


def _rates(model, ctx):
    """$/token rates of ``model`` at context ``ctx`` (the long-context tier as ``prices.cost_of``),
    or None when unpriced."""
    from . import prices

    row = prices.price_for(model)
    if not row:
        return None
    long_ctx = ctx > prices.LONG_CONTEXT_TOKENS and row.get("window") == "1m"
    return {k: row[k] * (prices.LONG_CONTEXT_MULT.get(k, 1.0) if long_ctx else 1.0) / 1e6
            for k in ("write_5m", "read", "output")}


def cap_inputs(run, router=None):
    """``{ctx, model, router_ctx, router_model, rewrite_usd, ping_usd}`` of :func:`cap`; a missing
    or unpriced input leaves its field and the two costs None.

    ``rewrite_usd = ctx * (write_5m - read)``: the rewrite a ping saves, net of the read it pays
    anyway.  ``ping_usd = ctx * read + 2 * router_ctx * router_read`` plus the `.` turn's small write
    and output per request (:data:`PING_WRITE_TOKENS`, :data:`PING_OUTPUT_TOKENS`).
    """
    router = router or {}
    out = {"ctx": run.get("ctx"), "model": run.get("model"), "router_ctx": router.get("ctx"),
           "router_model": router.get("model"), "rewrite_usd": None, "ping_usd": None}
    if not (out["ctx"] and out["model"] and out["router_ctx"] and out["router_model"]):
        return out
    r, rr = _rates(out["model"], out["ctx"]), _rates(out["router_model"], out["router_ctx"])
    if not r or not rr:
        return out

    def turn(rt):
        return PING_WRITE_TOKENS * rt["write_5m"] + PING_OUTPUT_TOKENS * rt["output"]

    out["rewrite_usd"] = out["ctx"] * (r["write_5m"] - r["read"])
    out["ping_usd"] = (out["ctx"] * r["read"] + turn(r)
                       + ROUTER_RELAY_REQUESTS * (out["router_ctx"] * rr["read"] + turn(rr)))
    return out


def cap(run, s, router=None, inputs=None):
    """The run's ping cap (3.15 T3): 1h runs ``max_pings_1h``; 5m runs
    ``clamp(floor(rewrite_usd / ping_usd), 0, max_pings_5m)`` over :func:`cap_inputs`, or
    ``max_pings_5m_fallback`` while an input is missing or unpriced.  Cap 0 (T3.1): one ping costs
    more than the rewrite it saves, so :func:`due` never fires and no wake line is written."""
    if run["ttl"] == "1h":
        return int(s["max_pings_1h"])
    inputs = inputs if inputs is not None else cap_inputs(run, router)
    if not inputs["rewrite_usd"] or not inputs["ping_usd"] or inputs["ping_usd"] <= 0:
        return int(s["max_pings_5m_fallback"])
    return int(max(0, min(int(inputs["rewrite_usd"] // inputs["ping_usd"]), s["max_pings_5m"])))   # floor was 1 (T3)


def due(run, now, s, router=None):
    """Seconds idle when the wake line is due now, else None."""
    if not run.get("idle") or run.get("base") is None or run.get("finished"):
        return None
    if run["pings"] >= cap(run, s, router):
        return None                              # cold until a real request
    if run["last_ping"] is not None and run["base"] <= run["last_ping"]:
        return None                              # once per base: the last ping has not landed
    fire = s["fire_1h_s"] if run["ttl"] == "1h" else s["fire_5m_s"]
    idle_s = now - run["base"]
    if idle_s < fire - s["relay_lead_s"]:
        return None
    if idle_s > _TTL_S.get(run["ttl"], 300):
        return None                              # the cache is gone: a ping would pay the rewrite
    return idle_s


def read_new(p, offset, tail=True):
    """``(records, new_offset)`` of the complete JSON lines after ``offset``.

    ``offset`` None (or past the end: the file was replaced) reads the last :data:`TAIL_BYTES`
    when ``tail``, else from 0.  A partial last line stays for the next read.
    """
    size = os.path.getsize(p)
    skip_first = False
    if offset is None or size < offset:
        start = max(0, size - TAIL_BYTES) if tail else 0
        skip_first = start > 0
    else:
        start = offset
    if size == start:
        return [], start
    with open(p, "rb") as fh:
        fh.seek(start)
        data = fh.read(size - start)
    if skip_first:
        cut = data.find(b"\n")
        if cut < 0:
            return [], None
        data, start = data[cut + 1:], start + cut + 1
    end = data.rfind(b"\n")
    if end < 0:
        return [], start
    out = []
    for raw in data[:end + 1].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw.decode("utf-8", "replace")))
        except ValueError:
            continue
    return out, start + end + 1


# --------------------------------------------------------------------------- the daemon

class Warmer(object):
    """One session's daemon; :meth:`poll` is one tick (tests drive it with a fixed ``now``)."""

    def __init__(self, root, sid, claude_pid=None, cfg=None):
        from . import config, paths

        self.root, self.sid = root, sid
        self.cfg = cfg if cfg is not None else config.load()
        self.s = settings(self.cfg)
        try:
            self.claude_pid = int(claude_pid) if claude_pid else None
        except (TypeError, ValueError):
            self.claude_pid = None
        self.pid = os.getpid()
        self.session_dir = os.path.join(paths.projects_dir(), paths.project_slug(root), sid)
        self.registry = (os.path.join(paths.claude_dir(), "sessions", "%d.json" % self.claude_pid)
                         if self.claude_pid else None)
        self.runs = {}
        self.runs_offset = 0
        self.router = {"ctx": None, "model": None}   # the main transcript's last usage (3.15 T3)
        self.main_path, self.main_offset = None, None
        self.main_seen = False
        self.registry_seen = False
        self.started = time.time()
        self.last_state = None

    # ---- files

    def log(self, msg):
        from . import fsutil

        if not os.path.isdir(warmer_dir(self.root)):
            return                               # the project is gone: never recreate it
        try:
            fsutil.append_line(path(self.root, self.sid, "log"),
                               "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg))
        except OSError:
            pass

    def start(self):
        from . import fsutil, paths

        fsutil.atomic_write_text(path(self.root, self.sid, "pid"), "%d\n" % self.pid)
        self.log("start pid %d claude_pid %s" % (self.pid, self.claude_pid))
        doc = fsutil.read_json(paths.running_path(), {}) or {}
        agents = (((doc.get("sessions") or {}).get(self.sid) or {}).get("agents") or {})
        n = 0
        for aid, entry in sorted(agents.items()):
            if aid == self.sid:
                continue                         # the main session: the Monitor re-arm covers it
            entry = entry if isinstance(entry, dict) else {}
            self.add(aid, entry.get("agent_type"), entry.get("role"), quiet=True)
            n += 1
        self.log("adopted %d" % n)

    def add(self, run_id, agent_type=None, role=None, quiet=False):
        from . import config

        run = self.runs.get(run_id)
        if run is not None and run.get("ended") is not None:
            run["ended"] = None                  # SubagentStart again: the run resumed
            self.log("resume %s" % run_id)
        if run is None:
            if not role:
                role = config.role_from_agent_type(agent_type, self.cfg)
            run = self.runs[run_id] = new_run(run_id, agent_type, role,
                                              config.ttl_for_role(role, self.cfg))
            if not quiet:
                self.log("add %s %s %s" % (run_id, agent_type, role))
        return run

    def end(self, run_id, why="end"):
        if self.runs.pop(run_id, None) is not None:
            self.log("%s %s" % (why, run_id))

    def _transcript(self, run_id):
        flat = os.path.join(self.session_dir, "subagents", "agent-%s.jsonl" % run_id)
        if os.path.exists(flat):
            return flat
        return self._ledger_path("SELECT transcript_path FROM agent_runs WHERE run_id=?", run_id)

    def _main_transcript(self):
        flat = self.session_dir + ".jsonl"
        if os.path.exists(flat):
            return flat
        return self._ledger_path("SELECT transcript_path FROM sessions WHERE session_id=?", self.sid)

    def _ledger_path(self, sql, key):
        try:
            from . import db, paths

            if not os.path.exists(paths.db_path()):
                return None
            conn = db.connect(paths.db_path(), readonly=True, create=False)
            try:
                row = conn.execute(sql, (key,)).fetchone()
            finally:
                db.close(conn)
            p = row[0] if row else None
            return p if p and os.path.exists(p) else None
        except Exception:
            return None

    # ---- one tick

    def exit_reason(self):
        if os.path.exists(path(self.root, self.sid, "stop")):
            return "stop file"
        owner = _read_pid(path(self.root, self.sid, "pid"))
        if owner != self.pid:
            return "pid file names %s" % (owner or "no pid")
        if self.registry:
            if os.path.exists(self.registry):
                self.registry_seen = True
            elif self.registry_seen:
                return "session pid %d gone" % self.claude_pid
            elif time.time() - self.started >= self.s["poll_s"]:
                return "session pid %d not registered" % self.claude_pid
        if self._main_transcript():
            self.main_seen = True
        elif self.main_seen:
            return "main transcript gone"
        return None

    def poll(self, now=None):
        now = time.time() if now is None else now
        self._read_runs(now)
        self._read_main()
        for run_id in sorted(self.runs):
            run = self.runs[run_id]
            p = run["path"] if run["path"] and os.path.exists(run["path"]) else self._transcript(run_id)
            if not p:
                if run["seen"]:
                    self.end(run_id, "gone")
                continue
            if p != run["path"]:
                run["path"], run["offset"] = p, None
            run["seen"] = True
            try:
                records, run["offset"] = read_new(p, run["offset"])
            except OSError:
                continue
            apply_records(run, records)
            pend = run.get("pending")
            if pend and not pend.get("flagged") and now - pend["at"] > UNDELIVERED_S:
                self._undelivered(run, now)
            idle_s = due(run, now, self.s, self.router)
            if idle_s is not None:
                self._fire(run, now, idle_s)
        self._write_state(now)

    def _read_main(self):
        """Keep :attr:`router` from the main transcript's new assistant records (3.15 T3)."""
        p = self._main_transcript()
        if not p:
            return
        if p != self.main_path:
            self.main_path, self.main_offset = p, None
        try:
            records, self.main_offset = read_new(p, self.main_offset)
        except OSError:
            return
        for rec in records:
            if isinstance(rec, dict) and rec.get("type") == "assistant" and not rec.get("isSidechain"):
                take_usage(self.router, rec)

    def _read_runs(self, now):
        p = path(self.root, self.sid, "runs")
        if not os.path.exists(p):
            return
        try:
            lines, self.runs_offset = read_new(p, self.runs_offset, tail=False)
        except OSError:
            return
        for line in lines:
            if not isinstance(line, dict):
                continue
            if line.get("add"):
                self.add(str(line["add"]), line.get("agent_type"), line.get("role"))
            elif line.get("end"):
                run = self.runs.get(str(line["end"]))   # advisory: keep watching the transcript
                if run is not None:
                    run["ended"] = now
                    self.log("end %s (advisory)" % run["run_id"])

    def _fire(self, run, now, idle_s):
        from . import fsutil
        from .hooks import spool_event

        idle = int(idle_s)
        inputs = cap_inputs(run, self.router)
        fsutil.append_line(path(self.root, self.sid, "wake"), "warm %s %d" % (run["run_id"], idle))
        run["pings"] += 1
        run["last_ping"] = now
        self.log("warm %s %d n %d ttl %s" % (run["run_id"], idle, run["pings"], run["ttl"]))
        spool_event(self.sid, "warm_ping",
                    {"run_id": run["run_id"], "session_id": self.sid, "ttl": run["ttl"],
                     "idle_s": idle, "n": run["pings"], "via": VIA,
                     "cap": cap(run, self.s, inputs=inputs), "cap_inputs": inputs},
                    run_id=run["run_id"])
        run["pending"] = {"at": now, "idle": idle, "n": run["pings"]}     # 3.15 T6

    def _undelivered(self, run, now):
        """Flag the run's pending wake line once (3.15 T6): no ping landed ``UNDELIVERED_S`` after it."""
        from . import fsutil
        from .hooks import spool_event

        pend = run["pending"]
        pend["flagged"] = True
        age = int(now - pend["at"])
        line = "warm %s %d" % (run["run_id"], pend["idle"])
        self.log("undelivered %s age %d" % (run["run_id"], age))
        spool_event(self.sid, "warm_undelivered",
                    {"run_id": run["run_id"], "session_id": self.sid, "idle_s": pend["idle"],
                     "n": pend["n"], "age_s": age},
                    run_id=run["run_id"])
        fsutil.append_line(path(self.root, self.sid, "undelivered"),
                           json.dumps({"run_id": run["run_id"], "line": line, "at": pend["at"]}))

    def state(self):
        out = {}
        for rid, r in sorted(self.runs.items()):
            out[rid] = {"last_request": r["base"], "ttl": r["ttl"], "pings": r["pings"],
                        "last_ping": r["last_ping"], "via": VIA, "agent_type": r["agent_type"],
                        "idle": bool(r["idle"])}
            if r.get("finished"):
                out[rid]["finished"] = True
            if r.get("ended") is not None:
                out[rid]["ended"] = r["ended"]
        return out

    def _write_state(self, now):
        from . import fsutil

        runs = self.state()
        if runs == self.last_state:
            return
        fsutil.atomic_write_json(state_path(self.root),
                                 {"session": self.sid, "updated": now, "runs": runs})
        self.last_state = runs

    # ---- the loop

    def finish(self, reason):
        if _read_pid(path(self.root, self.sid, "pid")) == self.pid:
            try:
                os.remove(path(self.root, self.sid, "pid"))
            except OSError:
                pass
        self.log("exit %s" % reason)

    def run(self):
        self.start()
        reason = "error"
        try:
            while True:
                why = self.exit_reason()
                if why:
                    reason = why
                    break
                try:
                    self.poll()
                except Exception as exc:          # one bad tick never kills the daemon
                    self.log("poll error %s" % str(exc)[:200])
                time.sleep(self.s["poll_s"])
        finally:
            self.finish(reason)
        return 0


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="python -m pa.warmer",
                                 description="The per-session cache warmer daemon.")
    ap.add_argument("--project", required=True, help="the project root")
    ap.add_argument("--session", required=True, help="the session id")
    ap.add_argument("--pid", default="", help="the Claude Code process id (its sessions/<pid>.json)")
    args = ap.parse_args(argv)
    return Warmer(os.path.abspath(args.project), args.session, args.pid or None).run()


if __name__ == "__main__":
    sys.exit(main())
