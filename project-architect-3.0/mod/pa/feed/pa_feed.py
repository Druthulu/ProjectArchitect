"""The PA 3.2 live feed (3.23 T13d): the monitor process that prints the agents' working lines to the main session.

The watcher (pa-watch) drops one JSON file per utterance into `<dir>/in/`; this script takes the complete ones in name
order, numbers each into `<dir>/log.jsonl`, and prints it as one `{"pa":"feed","n":…}` line, paced to Claude Code's
monitor throttle (a token bucket of 10 events refilled one per 2 s; output within ~200 ms is one event). The mirror here
is conservative: 10 tokens refilled one per 2.1 s, starting full, the refill clock held while the bucket is full; one
record per write at least 0.3 s apart while a token is free, and every queued record in one write once the oldest has
waited 8 s. A nudge file `{"nudge":true}` prints `{"pa":"feed","nudge":true}` and is not logged. `<dir>/alive` is the
heartbeat, rewritten every 5 s. Nothing else ever goes to stdout; errors go to stderr. Standard library only.

A feed never outlives its session and one folder has one process (3.23 T13d.c6b): every ~5 s the script checks the Claude
Code process named by `$CLAUDE_PID` and exits quietly once it is gone (no `CLAUDE_PID`: it runs as before); on start it
writes `<dir>/owner.json` (`{"pid","claudePid","started"}`), reads it back every round and exits quietly once another
process owns the folder (the newest wins: the live session's, or the router's restart). It numbers from the log's last
`n` after taking ownership.

    pa_feed.py --project <root> [--dir <feed folder>]

`--dir` defaults to `<root>/.run/mod/$CLAUDE_CODE_SESSION_ID/feed`; with neither, the script idles silently forever (an
exiting monitor sends an end notice that wakes the model).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable

CAP = 10  # tokens in the bucket (Claude Code: 10)
REFILL_MS = 2100  # one token per (Claude Code: 2000)
GAP_MS = 300  # at least this between writes (Claude Code merges output within ~200 ms)
VALVE_MS = 8000  # the oldest queued record waited this long: print the whole queue in one write
STALE_MS = 5000  # an incomplete file older than this is dropped
ALIVE_MS = 5000  # heartbeat period
LIVE_MS = 5000  # how often the Claude Code process is checked
TICK_S = 0.1


def _dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=True, separators=(',', ':'))


def last_n(log_path: str) -> int:
    """The last `n` in the feed log (0 when absent); a torn last line is skipped."""
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read()
    except OSError:
        return 0
    for line in reversed(tail.splitlines()):
        try:
            n = json.loads(line.decode('utf-8')).get('n')
        except (ValueError, AttributeError):
            continue
        if isinstance(n, int):
            return n
    return 0


def claude_alive(pid: int) -> bool:
    """Whether process `pid` still runs: POSIX `kill(pid, 0)`; Windows `OpenProcess` and `GetExitCodeProcess`."""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


class Feed:
    """One feed folder. `step(now_ms)` does one round and returns False once the script should exit (its Claude Code
    process is gone, or another process owns the folder); `out(text)` is one write to stdout, `err(text)` one stderr
    line. `pid` is this process's id, `claude_pid` the Claude Code process's (None: never checked), `alive_check(pid)`
    the liveness test."""

    def __init__(self, folder: str, out: Callable[[str], None], err: Callable[[str], None], now_ms: int,
                 pid: int | None = None, claude_pid: int | None = None,
                 alive_check: Callable[[int], bool] = claude_alive) -> None:
        self.folder = folder
        self.inbox = os.path.join(folder, 'in')
        self.log = os.path.join(folder, 'log.jsonl')
        self.alive = os.path.join(folder, 'alive')
        self.owner = os.path.join(folder, 'owner.json')
        self.out = out
        self.err = err
        self.pid = os.getpid() if pid is None else pid
        self.claude_pid = claude_pid
        self.alive_check = alive_check
        self.last_live = now_ms
        os.makedirs(self.inbox, exist_ok=True)
        self.started = now_ms
        self._own()
        self.n = last_n(self.log)  # after taking ownership: the previous owner stops numbering
        self.tokens = CAP
        self.filled = now_ms
        self.last_write: int | None = None
        self.last_beat: int | None = None
        self.queue: list[tuple[int, str]] = []  # (queued at ms, the line to print)
        self.seen: dict[str, int] = {}  # file name -> first seen incomplete, ms
        self.taken: set[str] = set()  # numbered but not yet deleted (a locked file on Windows)

    def step(self, now_ms: int) -> bool:
        if self.claude_pid is not None and now_ms - self.last_live >= LIVE_MS:
            self.last_live = now_ms
            if not self.alive_check(self.claude_pid):
                return False
        if not self._owns():
            return False
        self._take(now_ms)
        self._print(now_ms)
        if self.last_beat is None or now_ms - self.last_beat >= ALIVE_MS:
            self.last_beat = now_ms
            self._beat(now_ms)
        return True

    def _own(self) -> None:
        tmp = f'{self.owner}.{self.pid}.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(_dumps({'pid': self.pid, 'claudePid': self.claude_pid, 'started': self.started}) + '\n')
            os.replace(tmp, self.owner)
        except OSError as exc:
            self.err(f'pa feed: cannot write {self.owner}: {exc}')

    def _owns(self) -> bool:
        """False only when owner.json names another process; a missing or unreadable file keeps this one running."""
        try:
            with open(self.owner, encoding='utf-8') as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return True
        if not isinstance(rec, dict):
            return True
        return rec.get('pid') == self.pid and rec.get('started') == self.started

    def _take(self, now_ms: int) -> None:
        try:
            names = sorted(e for e in os.listdir(self.inbox) if e.endswith('.json'))
        except OSError as exc:
            self.err(f'pa feed: cannot list {self.inbox}: {exc}')
            return
        for name in names:
            path = os.path.join(self.inbox, name)
            if name in self.taken:
                self._remove(path, name)
                continue
            rec = self._read(path)
            if rec is None:
                first = self.seen.setdefault(name, now_ms)
                if now_ms - first >= STALE_MS:
                    self.err(f'pa feed: dropped incomplete {name}')
                    self._remove(path, name)
                    self.seen.pop(name, None)
                continue
            self.seen.pop(name, None)
            if not isinstance(rec, dict):
                self.err(f'pa feed: dropped {name}: not a JSON object')
                self._remove(path, name)
                continue
            if rec.get('nudge') is True:
                line = _dumps({'pa': 'feed', 'nudge': True})
            else:
                self.n += 1
                body = {k: v for k, v in rec.items() if k not in ('pa', 'n')}
                try:
                    with open(self.log, 'a', encoding='utf-8', newline='\n') as f:
                        f.write(_dumps({**body, 'n': self.n}) + '\n')
                except OSError as exc:
                    self.n -= 1
                    self.err(f'pa feed: cannot append to {self.log}: {exc}')
                    return
                line = _dumps({'pa': 'feed', 'n': self.n, **body})
            self.taken.add(name)
            self._remove(path, name)
            self.queue.append((now_ms, line))

    @staticmethod
    def _read(path: str) -> object | None:
        """The file's JSON when complete (ends with a newline and parses), else None."""
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        if not data.endswith(b'\n'):
            return None
        try:
            return json.loads(data.decode('utf-8'))
        except ValueError:
            return None

    def _remove(self, path: str, name: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return  # retried next round; a numbered one stays in `taken`
        self.taken.discard(name)

    def _print(self, now_ms: int) -> None:
        if self.tokens >= CAP:
            self.filled = now_ms  # a full bucket does not bank refill time
        else:
            k = (now_ms - self.filled) // REFILL_MS
            if k > 0:
                self.tokens = min(CAP, self.tokens + k)
                self.filled += k * REFILL_MS
        if not self.queue or self.tokens < 1:
            return
        if self.last_write is not None and now_ms - self.last_write < GAP_MS:
            return
        if now_ms - self.queue[0][0] >= VALVE_MS:
            batch, self.queue = self.queue, []
        else:
            batch, self.queue = self.queue[:1], self.queue[1:]
        if self.tokens >= CAP:
            self.filled = now_ms
        self.tokens -= 1
        self.last_write = now_ms
        self.out(''.join(line + '\n' for _, line in batch))

    def _beat(self, now_ms: int) -> None:
        tmp = self.alive + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(_dumps({'pid': os.getpid(), 'started': self.started, 'beat': now_ms, 'n': self.n}) + '\n')
            os.replace(tmp, self.alive)
        except OSError as exc:
            self.err(f'pa feed: cannot write {self.alive}: {exc}')


def _claude_pid() -> int | None:
    try:
        return int(os.environ.get('CLAUDE_PID', ''))
    except ValueError:
        return None


def _idle(claude_pid: int | None) -> None:
    """Idle silently; return once the Claude Code process is gone."""
    while claude_pid is None or claude_alive(claude_pid):
        time.sleep(LIVE_MS / 1000)


def _now() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description='The PA 3.2 live feed monitor process.')
    ap.add_argument('--project', required=True)
    ap.add_argument('--dir', default='')
    args = ap.parse_args(argv)
    folder = args.dir
    claude_pid = _claude_pid()
    if folder == '':
        sid = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
        if sid == '':
            _idle(claude_pid)
            return
        folder = os.path.join(args.project, '.run', 'mod', sid, 'feed')

    def out(text: str) -> None:
        sys.stdout.buffer.write(text.encode('ascii'))
        sys.stdout.buffer.flush()

    def err(text: str) -> None:
        sys.stderr.write(text + '\n')
        sys.stderr.flush()

    feed = Feed(folder, out, err, _now(), claude_pid=claude_pid)
    while True:
        try:
            if not feed.step(_now()):
                return  # quietly: the session is gone or a newer process owns the folder
        except Exception as exc:  # never crash: a monitor that exits sends an end notice
            err(f'pa feed: {type(exc).__name__}: {exc}')
        time.sleep(TICK_S)


if __name__ == '__main__':
    main()
