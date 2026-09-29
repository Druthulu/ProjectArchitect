"""Crash-safe small-file helpers used by every hook and the statusline.

Public:
    ensure_dir(path)
    read_json(path, default=None), read_text(path, default="")
    atomic_write_json(path, obj, indent=None), atomic_write_text(path, text)
    append_line(path, text)
    file_lock(path, timeout_ms=2000)          context manager on <path>.lock
    locked_update(path, fn, timeout_ms=2000, default=None)
    file_size(path), mtime(path)
    disk_free_mb(path)                        (lazy ``shutil`` import; re-exported by pa.db)

Everything is best-effort by design: readers return the default instead of
raising, writers use ``tmp + os.replace`` so a killed hook can never leave a
half-written ``running.json``/``summary.json`` behind.  Locking uses
``msvcrt.locking`` on Windows and ``fcntl.flock`` elsewhere, on a sibling
``<path>.lock`` file, plus an in-process mutex so threads of one process
serialize even where the OS grants same-process locks.
"""

import json
import os
import threading
import time

_PROC_LOCKS = {}
_PROC_LOCKS_GUARD = threading.Lock()


# --------------------------------------------------------------------------- basics

def ensure_dir(path):
    """Create ``path``'s directory; returns the directory (never raises)."""
    d = os.path.dirname(os.path.abspath(path)) if not os.path.isdir(path) else path
    try:
        if d:
            os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (OSError, ValueError):
        return default


def read_json(path, default=None):
    """Parse a JSON file; returns ``default`` when missing or corrupt."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return default
    if not raw.strip():
        return default
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return default


def atomic_write_text(path, text):
    """Write ``text`` to ``path`` via ``<path>.tmp.<pid>`` + ``os.replace``."""
    path = os.path.abspath(path)
    ensure_dir(path)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    return path


def atomic_write_json(path, obj, indent=None):
    """Serialize ``obj`` as UTF-8 JSON and write it atomically."""
    return atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent, default=str))


def append_line(path, text):
    """Append one newline-terminated line (spool/log writers)."""
    ensure_dir(path)
    line = text if text.endswith("\n") else text + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
    return path


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def disk_free_mb(path):
    """Free megabytes on the filesystem holding ``path`` (0.0 if unknown).

    ``shutil`` is imported inside the function to keep this module's import
    cost on the statusline hot path at zero.
    """
    import shutil

    p = os.path.abspath(path or ".")
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free / (1024.0 * 1024.0)
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- locking

def _proc_lock(key):
    with _PROC_LOCKS_GUARD:
        lk = _PROC_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PROC_LOCKS[key] = lk
        return lk


class file_lock(object):
    """Exclusive lock on ``<path>.lock``; raises ``TimeoutError`` on expiry.

    Usage::

        with file_lock(running_json, timeout_ms=2000):
            ...
    """

    def __init__(self, path, timeout_ms=2000):
        self.target = os.path.abspath(path)
        self.lock_path = self.target + ".lock"
        self.timeout_ms = int(timeout_ms)
        self._fd = None
        self._proc = _proc_lock(self.lock_path.casefold())
        self._proc_held = False

    # -- os-level helpers --------------------------------------------------
    def _try_lock(self, fd):
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self, fd):
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    # -- context manager ---------------------------------------------------
    def acquire(self):
        deadline = time.monotonic() + self.timeout_ms / 1000.0
        if not self._proc.acquire(timeout=max(self.timeout_ms / 1000.0, 0.001)):
            raise TimeoutError("in-process lock timeout: %s" % self.lock_path)
        self._proc_held = True
        try:
            ensure_dir(self.lock_path)
            self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            delay = 0.001
            while True:
                try:
                    self._try_lock(self._fd)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("lock timeout: %s" % self.lock_path)
                    time.sleep(delay)
                    delay = min(delay * 2, 0.05)
        except BaseException:
            self.release()
            raise

    def release(self):
        if self._fd is not None:
            self._unlock(self._fd)
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._proc_held:
            self._proc_held = False
            try:
                self._proc.release()
            except RuntimeError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def locked_update(path, fn, timeout_ms=2000, default=None):
    """Read ``path`` as JSON, apply ``fn(data)``, write the result atomically.

    ``fn`` may mutate and return the object, or return a new one; returning
    ``None`` means "no change, do not write".  The whole read-modify-write runs
    under ``file_lock(path)`` so concurrent hooks cannot lose updates.
    """
    with file_lock(path, timeout_ms=timeout_ms):
        data = read_json(path, default if default is not None else {})
        updated = fn(data)
        if updated is None:
            return data
        atomic_write_json(path, updated)
        return updated
