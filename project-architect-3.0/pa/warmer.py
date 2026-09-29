"""The warmer: one daemon per session that keeps idle subagents' caches warm (3.9.5 T4).

Contract: ``docs/pa3-build/design/warmer.md`` ``## Decision``.  ``python -s -P -X utf8 -m pa.warmer
--project <root> --session <sid> [--pid <claude pid>]``, spawned detached by the SessionStart and
SubagentStart hooks (:func:`start_if_needed`), stopped by SessionEnd (:func:`stop`).

Files under ``<root>/.run/warmer/``: ``<sid>.pid`` (this daemon's pid), ``<sid>.runs`` (JSON lines
the hooks append: ``{"add": id, "agent_type", "role"}`` / ``{"end": id}``), ``<sid>.wake`` (this
daemon appends ``warm <agent id> <idle_s>``; the session's ``Monitor`` on ``tail -n0 -F`` relays
``.`` by SendMessage), ``<sid>.stop`` (SessionEnd), ``<sid>.log``.  State ``<root>/.run/warmer.json``
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
idleness, once per base, up to ``max_pings_5m`` (5m runs) or ``max_pings_1h`` (1h runs).

Import budget: hooks import this module, so module level stays ``json, os, sys, time``.
"""

import json
import os
import sys
import time

VIA = "monitor"
TAIL_BYTES = 262144                      # first read of a transcript: its last 256 KB
_WDEFAULTS = {"fire_5m_s": 285, "fire_1h_s": 3300, "relay_lead_s": 25,
               "max_pings_5m": 12,       # was 3 (max_pings), developer 2026-09-24
               "max_pings_1h": 3, "poll_s": 5}
_TTL_S = {"5m": 300, "1h": 3600}


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
    return [b.get("tool_use_id") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result"]


def new_run(run_id, agent_type=None, role=None, ttl="5m"):
    return {"run_id": run_id, "agent_type": agent_type, "role": role, "ttl": ttl, "path": None,
            "seen": False, "offset": None, "base": None, "idle": False, "pings": 0,
            "last_ping": None, "handback": None, "finished": False, "ended": None}


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
    return run


def cap(run, s):
    """The run's ping cap by its TTL (``max_pings_1h`` / ``max_pings_5m``)."""
    return s["max_pings_1h"] if run["ttl"] == "1h" else s["max_pings_5m"]


def due(run, now, s):
    """Seconds idle when the wake line is due now, else None."""
    if not run.get("idle") or run.get("base") is None or run.get("finished"):
        return None
    if run["pings"] >= cap(run, s):
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
            idle_s = due(run, now, self.s)
            if idle_s is not None:
                self._fire(run, now, idle_s)
        self._write_state(now)

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
        fsutil.append_line(path(self.root, self.sid, "wake"), "warm %s %d" % (run["run_id"], idle))
        run["pings"] += 1
        run["last_ping"] = now
        self.log("warm %s %d n %d ttl %s" % (run["run_id"], idle, run["pings"], run["ttl"]))
        spool_event(self.sid, "warm_ping",
                    {"run_id": run["run_id"], "session_id": self.sid, "ttl": run["ttl"],
                     "idle_s": idle, "n": run["pings"], "via": VIA,
                     "cap": int(cap(run, self.s))}, run_id=run["run_id"])

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
