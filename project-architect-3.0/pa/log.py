"""hooks.log appender, the never-raise wrapper and the hook watchdog.

Public:
    log_path(), set_log_path(path)
    log(event, **fields), log_text(message)
    never_raise(fn)                 decorator: swallow + log every exception
    watchdog(budget_ms, note=None)  threading.Timer -> os._exit(0)

Every PA3 entry point is a Claude Code hook: it must exit 0 within its budget
and must never print anything it did not mean to print.  ``never_raise``
guarantees the first; ``watchdog`` guarantees the second by hard-exiting the
process (``os._exit`` skips atexit/finalizers, which is what we want when the
harness is waiting on us).  The log rotates at 1 MB to ``hooks.log.1``.
"""

import json
import os
import sys
import threading
import time

MAX_BYTES = 1024 * 1024
_LOG_PATH = None


def log_path():
    """Current hooks.log path (``pa.paths.hooks_log_path()`` unless overridden)."""
    global _LOG_PATH
    if _LOG_PATH is None:
        from . import paths

        _LOG_PATH = paths.hooks_log_path()
    return _LOG_PATH


def set_log_path(path):
    """Point the logger somewhere else (tests, ``pa install --root``)."""
    global _LOG_PATH
    _LOG_PATH = os.path.abspath(path) if path else None
    return _LOG_PATH


def _rotate(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def log_text(message):
    """Append one raw line; never raises."""
    path = log_path()
    try:
        _rotate(path)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("%s pid=%d %s\n" % (stamp, os.getpid(), message))
    except Exception:
        pass
    return path


def log(event, **fields):
    """Append ``<ts> pid=<pid> <event> {json fields}``; never raises."""
    try:
        blob = json.dumps(fields, ensure_ascii=False, default=str) if fields else ""
    except Exception:
        blob = "{}"
    return log_text(("%s %s" % (event, blob)).rstrip())


def never_raise(fn):
    """Decorator: log and swallow any exception, returning ``None`` instead."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - hooks must never propagate
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                import traceback

                tb = traceback.format_exc(limit=6).replace("\n", " | ")
            except Exception:
                tb = repr(exc)
            log("error", fn=getattr(fn, "__name__", str(fn)), err=repr(exc), tb=tb[:2000])
            return None

    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    wrapper.__doc__ = getattr(fn, "__doc__", None)
    wrapper.__wrapped__ = fn
    return wrapper


def _expire(note):
    try:
        log("watchdog", note=note, argv=sys.argv[1:3])
    finally:
        os._exit(0)


def watchdog(budget_ms, note=None):
    """Start a daemon timer that hard-exits the process after ``budget_ms``.

    Returns the ``threading.Timer`` so the caller can ``.cancel()`` it on a
    normal return.
    """
    t = threading.Timer(max(float(budget_ms), 1.0) / 1000.0, _expire, args=(note,))
    t.daemon = True
    t.start()
    return t
