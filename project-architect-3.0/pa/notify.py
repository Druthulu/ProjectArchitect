"""Detached toasts, the Discord webhook and the toast rate limit (design A C.2).

Public:
    toast(title, body, tag=None, kind=None, session_id=None, cfg=None,
          spawner=None, now=None) -> dict
    discord(webhook, content, timeout=3.0) -> bool
    build_command(title, body, tag, cfg=None) -> (cmd, env, channel)
    set_spawner(fn), spawner()
    allowed_now(kind, session_id, cfg, now=None) -> (bool, reason)
    record_sent(kind, session_id, now=None)
    EXEMPT_KINDS, script_path()

The hook must return in single-digit milliseconds, so nothing here waits on the
notifier: the toast runs in a **detached** child
(``CREATE_NO_WINDOW`` for the PowerShell toast on Windows -- a console, no window;
``DETACHED_PROCESS|CREATE_NO_WINDOW`` for any other child; ``start_new_session=True``
elsewhere) and the Discord POST (3 s timeout) runs inside that same child when a
webhook is configured.

Channels
  * ``win``  -- ``powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
    -File <pa3>/notify_toast.ps1`` with ``PA_TOAST_TITLE/BODY/TAG`` in the
    environment (WinRT ``ToastNotificationManager``; see ``notify_toast.ps1``).
  * ``wsl``  -- the same script through
    ``/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe``, path via
    ``wslpath -w``, variables forwarded with ``WSLENV``.
  * fallbacks -- ``msg.exe * /TIME:15 "<title>: <body>"`` on Windows,
    ``notify-send`` where it exists.

Rate limit: one toast per session per ``notify.toast_min_interval_s`` (20 s),
except :data:`EXEMPT_KINDS` (question, waiting, model_mismatch, new_account,
stop_failure).  The state lives in ``state/notify.json``.
"""

import json
import os
import re
import sys
import time

EXEMPT_KINDS = frozenset(["question", "waiting", "model_mismatch", "new_account",
                          "stop_failure", "handoff"])

_WIN_PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_WSL_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

_SPAWNER = None
_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW alone: the notifier gets a console (a console-less process
                                 # cannot raise a WinRT toast, 2026-09-20) but never a window, so nothing flashes
                                 # or takes focus (3.2 T6 lever A, developer-confirmed 2026-09-20). CREATE_NEW_CONSOLE
                                 # + -WindowStyle Hidden (3.1) flashed the console and stole the cursor.

# Runs detached when a Discord webhook is configured: fires the toast child
# first (so the desktop notification is not delayed by the POST) and then posts.
_CHILD_CODE = (
    "import json,os,subprocess,urllib.request as u\n"
    "cmd=json.loads(os.environ.get('PA_TOAST_CMD') or '[]')\n"
    "if cmd:\n"
    "    try: subprocess.Popen(cmd, creationflags=(0x10 if os.name == 'nt' else 0))\n"
    "    except Exception: pass\n"
    "hook=os.environ.get('PA_DISCORD_WEBHOOK') or ''\n"
    "if hook:\n"
    "    body=json.dumps({'content':(os.environ.get('PA_TOAST_TITLE') or '')+' - '"
    "+(os.environ.get('PA_TOAST_BODY') or '')}).encode('utf-8')\n"
    "    try:\n"
    "        u.urlopen(u.Request(hook,data=body,headers={'Content-Type':'application/json'}),timeout=3).read()\n"
    "    except Exception: pass\n"
)


# --------------------------------------------------------------------------- plumbing

def set_spawner(fn):
    """Replace the process spawner (tests record the command instead of running it)."""
    global _SPAWNER
    _SPAWNER = fn
    return fn


def spawner():
    return _SPAWNER


def script_path():
    """``notify_toast.ps1`` next to this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify_toast.ps1")


def _exists(path):
    try:
        return bool(path) and os.path.exists(path)
    except OSError:
        return False


def _powershell_exe(machine):
    if machine == "wsl":
        return _WSL_PS if _exists(_WSL_PS) else None
    if _exists(_WIN_PS):
        return _WIN_PS
    return "powershell.exe" if os.name == "nt" else None


def _win_script_path(machine):
    """Script path as Windows sees it (``wslpath -w`` under WSL)."""
    path = script_path()
    if machine != "wsl":
        return path
    try:
        import subprocess

        out = subprocess.run(["wslpath", "-w", path], capture_output=True, timeout=3)
        text = (out.stdout or b"").decode("utf-8", "replace").strip()
        if out.returncode == 0 and text:
            return text
    except Exception:
        pass
    return path


def _which(name):
    paths = os.environ.get("PATH", "").split(os.pathsep)
    exts = [""] if os.name != "nt" else os.environ.get("PATHEXT", ".EXE").split(os.pathsep)
    for d in paths:
        for ext in exts:
            cand = os.path.join(d, name + ext.lower() if ext else name)
            if _exists(cand):
                return cand
    return None


def build_command(title, body, tag=None, cfg=None, machine=None):
    """``(cmd, env, channel)`` for the detached notifier child."""
    from . import paths

    machine = machine or paths.machine_tag()
    env = {"PA_TOAST_TITLE": str(title or "PA3"),
           "PA_TOAST_BODY": str(body or ""),
           "PA_TOAST_TAG": str(tag or "pa3")}
    channel = (cfg or {}).get("notify", {}).get("toast_channel", "auto") if isinstance(cfg, dict) else "auto"
    exe = _powershell_exe(machine)
    if exe and channel in ("auto", "toast", "powershell"):
        cmd = [exe, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
               "-ExecutionPolicy", "Bypass", "-File", _win_script_path(machine)]
        if machine == "wsl":
            wslenv = os.environ.get("WSLENV", "")
            extra = "PA_TOAST_TITLE/u:PA_TOAST_BODY/u:PA_TOAST_TAG/u"
            env["WSLENV"] = (wslenv + ":" + extra) if wslenv else extra
        return cmd, env, ("toast-wsl" if machine == "wsl" else "toast-win")
    if machine == "wsl" and _which("notify-send"):
        return (["notify-send", str(title or "PA3"), str(body or "")], env, "notify-send")
    msg = _which("msg.exe") or _which("msg")
    if msg:
        return ([msg, "*", "/TIME:15", "%s: %s" % (title or "PA3", body or "")], env, "msg")
    return ([], env, "none")


def spawn_detached(cmd, env=None):
    """Start any command detached (the focus tab, 3.1 T12); tests see it through the spawner."""
    return _spawn(list(cmd or []), dict(env or {}))


def _spawn(cmd, env):
    """Start ``cmd`` detached; returns True when the child was started."""
    if not cmd:
        return False
    fn = _SPAWNER
    if fn is not None:
        fn(list(cmd), dict(env))
        return True
    import subprocess

    full = dict(os.environ)
    full.update(env)
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "env": full, "close_fds": True}
    if os.name == "nt":
        if str(cmd[0]).lower().endswith("powershell.exe"):
            flags = _NO_WINDOW                         # a console without a window: toasts need the console
        else:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)               # noqa: S603 - fixed argv
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- rate limit

def _state_path():
    from . import paths

    return paths.state_path("notify.json")


def allowed_now(kind, session_id, cfg=None, now=None):
    """``(allowed, reason)`` for the per-session toast rate limit."""
    kind = str(kind or "info")
    if kind in EXEMPT_KINDS:
        return True, "exempt"
    interval = 20
    if isinstance(cfg, dict):
        try:
            interval = float(((cfg.get("notify") or {}).get("toast_min_interval_s", 20)))
        except (TypeError, ValueError):
            interval = 20
    if interval <= 0:
        return True, "no-limit"
    from . import fsutil

    state = fsutil.read_json(_state_path(), {}) or {}
    last = ((state.get("sessions") or {}).get(str(session_id or "-")) or {}).get("last")
    now = float(now if now is not None else time.time())
    try:
        last = float(last)
    except (TypeError, ValueError):
        return True, "first"
    if now - last < interval:
        return False, "rate-limited (%.0fs < %.0fs)" % (now - last, interval)
    return True, "ok"


def record_sent(kind, session_id, now=None):
    """Stamp the rate-limit state after a toast went out."""
    from . import fsutil

    now = float(now if now is not None else time.time())

    def _patch(data):
        data = data if isinstance(data, dict) else {}
        sessions = data.setdefault("sessions", {})
        sessions[str(session_id or "-")] = {"last": now, "kind": str(kind or "info")}
        if len(sessions) > 64:                            # bounded: drop the oldest
            for sid, _v in sorted(sessions.items(), key=lambda kv: kv[1].get("last", 0))[:32]:
                sessions.pop(sid, None)
        data["updated"] = now
        return data

    try:
        fsutil.locked_update(_state_path(), _patch, timeout_ms=500, default={})
    except Exception:
        pass
    return now


# --------------------------------------------------------------------------- public

def toast(title, body, tag=None, kind=None, session_id=None, cfg=None,
          spawner=None, now=None):
    """Fire a desktop toast (and the Discord webhook) from a detached child.

    Returns ``{"sent", "reason", "cmd", "channel", "kind"}``; never raises and
    never blocks for longer than the spawn itself.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    notify_cfg = cfg.get("notify") or {}
    kind = str(kind or "info")
    result = {"sent": False, "reason": "", "cmd": [], "channel": "none", "kind": kind}
    if not notify_cfg.get("toast", True) and not notify_cfg.get("discord_webhook"):
        result["reason"] = "toasts disabled"
        return result
    ok, why = allowed_now(kind, session_id, cfg, now=now)
    if not ok:
        result["reason"] = why
        return result

    cmd, env, channel = build_command(title, body, tag or kind, cfg=cfg)
    if not notify_cfg.get("toast", True):
        cmd, channel = [], "none"
    webhook = notify_cfg.get("discord_webhook")
    events = notify_cfg.get("discord_events") or []
    want_discord = bool(webhook) and (not events or kind in events)
    result["channel"] = channel

    if want_discord:
        child_env = dict(env)
        child_env["PA_DISCORD_WEBHOOK"] = str(webhook)
        child_env["PA_TOAST_CMD"] = json.dumps(cmd)
        py = sys.executable or "python"
        full = [py, "-I", "-X", "utf8", "-c", _CHILD_CODE]
        result["channel"] = channel + "+discord"
        result["cmd"] = full
        sent = _do_spawn(full, child_env, spawner)
    else:
        result["cmd"] = cmd
        sent = _do_spawn(cmd, env, spawner) if cmd else False

    result["sent"] = bool(sent)
    result["reason"] = "sent" if sent else (why if not cmd else "spawn failed")
    if sent:
        record_sent(kind, session_id, now=now)
    return result


def _do_spawn(cmd, env, spawner_fn):
    if spawner_fn is not None:
        try:
            spawner_fn(list(cmd), dict(env))
            return True
        except Exception:
            return False
    return _spawn(cmd, env)


def discord(webhook, content, timeout=3.0):
    """POST ``{"content": …}`` to a Discord webhook (blocking, 3 s cap)."""
    if not webhook:
        return False
    import urllib.request

    data = json.dumps({"content": str(content or "")[:1900]}).encode("utf-8")
    req = urllib.request.Request(str(webhook), data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=float(timeout)).read()   # noqa: S310
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- text helpers

_MD_RE = re.compile(r"[*_`#>]+")
_WS_RE = re.compile(r"\s+")


def plain(text, limit=240):
    """Strip markdown noise and collapse whitespace for a toast body."""
    s = _MD_RE.sub("", str(text or ""))
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit]
