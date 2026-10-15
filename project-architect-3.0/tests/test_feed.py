"""The live feed script (3.23 T13d.c1a): pacing, numbering across a restart, incomplete and stale files, nudges, alive."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(__file__), '..', 'mod', 'pa', 'feed', 'pa_feed.py')
_spec = importlib.util.spec_from_file_location('pa_feed', _PATH)
assert _spec is not None and _spec.loader is not None
pa_feed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa_feed)


def _rec(i: int) -> dict:
    return {'agent': 'A1', 'type': 'coder-opus55', 'desc': 'T1.c1 x', 'planning': False, 'text': f'line {i}\nmore', 'at': i}


class FeedCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, 'feed')
        self.writes: list[tuple[int, str]] = []
        self.errs: list[str] = []
        self.now = 0
        self.seq = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def feed(self) -> 'pa_feed.Feed':
        return pa_feed.Feed(self.dir, lambda t: self.writes.append((self.now, t)), self.errs.append, self.now)

    def drop(self, obj: object, raw: str | None = None) -> str:
        self.seq += 1
        name = f'{1_700_000_000_000 + self.now:013d}-{self.seq:06d}.json'
        with open(os.path.join(self.dir, 'in', name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(raw if raw is not None else json.dumps(obj) + '\n')
        return name

    def run_until(self, feed: 'pa_feed.Feed', end_ms: int) -> None:
        while self.now <= end_ms:
            feed.step(self.now)
            self.now += 100

    def lines(self) -> list[dict]:
        return [json.loads(l) for _, t in self.writes for l in t.splitlines()]

    def test_burst_of_30_is_paced_and_complete(self) -> None:
        feed = self.feed()
        for i in range(30):
            self.drop(_rec(i))
        self.run_until(feed, 20_000)
        got = self.lines()
        self.assertEqual([r['n'] for r in got], list(range(1, 31)))
        self.assertEqual([r['text'] for r in got], [f'line {i}\nmore' for i in range(30)])
        for _, t in self.writes:  # one JSON line per record, ASCII, newline-escaped text
            self.assertTrue(t.isascii())
        counts = [t.count('\n') for _, t in self.writes]
        times = [ms for ms, _ in self.writes]
        # the first 10 single writes go 0.3 s apart
        self.assertEqual(counts[:10], [1] * 10)
        self.assertEqual(times[:10], [i * 300 for i in range(10)])
        # every gap is at least 0.3 s; singles after the bucket drains come no faster than one per 2.1 s
        self.assertTrue(all(b - a >= 300 for a, b in zip(times, times[1:])))
        drained = [t for t, c in zip(times, counts) if c == 1][11:]
        self.assertTrue(all(b - a >= 2100 for a, b in zip(drained, drained[1:])))
        # the rest goes as one write, once the oldest waited 8 s
        self.assertGreater(counts[-1], 1)
        self.assertEqual(counts[:-1], [1] * (len(counts) - 1))
        self.assertGreaterEqual(times[-1], 8000)
        # Claude Code's own bucket (10, one per 2 s) never suppresses a write
        tokens, last = 10.0, 0
        for ms in times:
            tokens = min(10.0, tokens + (ms - last) / 2000)
            last = ms
            self.assertGreaterEqual(tokens, 1.0)
            tokens -= 1
        # the log holds all 30, numbered; in/ is empty
        with open(os.path.join(self.dir, 'log.jsonl'), encoding='utf-8') as f:
            log = [json.loads(l) for l in f]
        self.assertEqual([r['n'] for r in log], list(range(1, 31)))
        self.assertEqual(os.listdir(os.path.join(self.dir, 'in')), [])

    def test_numbering_continues_across_a_restart(self) -> None:
        feed = self.feed()
        for i in range(3):
            self.drop(_rec(i))
        self.run_until(feed, 1000)
        self.writes.clear()
        feed2 = self.feed()
        self.drop(_rec(9))
        self.run_until(feed2, 2000)
        self.assertEqual([r['n'] for r in self.lines()], [4])

    def test_incomplete_waits_and_stale_is_dropped(self) -> None:
        feed = self.feed()
        name = self.drop(None, raw='{"agent":"A1","text":"half')
        self.run_until(feed, 1000)
        self.assertEqual(self.writes, [])
        with open(os.path.join(self.dir, 'in', name), 'w', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(_rec(1)) + '\n')
        self.run_until(feed, 2000)
        self.assertEqual([r['n'] for r in self.lines()], [1])
        stale = self.drop(None, raw='{"agent":"A1"')
        self.run_until(feed, 8000)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'in', stale)))
        self.assertEqual(len(self.lines()), 1)
        self.assertTrue(any('dropped incomplete' in e for e in self.errs))

    def test_nudge_prints_and_is_not_logged(self) -> None:
        feed = self.feed()
        self.drop({'nudge': True})
        self.drop(_rec(1))
        self.run_until(feed, 1000)
        got = self.lines()
        self.assertEqual(got[0], {'pa': 'feed', 'nudge': True})
        self.assertEqual(got[1]['n'], 1)
        self.assertEqual(list(got[1])[:2], ['pa', 'n'])
        with open(os.path.join(self.dir, 'log.jsonl'), encoding='utf-8') as f:
            self.assertEqual([json.loads(l)['n'] for l in f], [1])

    def test_alive_is_written(self) -> None:
        feed = self.feed()
        self.drop(_rec(1))
        self.run_until(feed, 5000)
        with open(os.path.join(self.dir, 'alive'), encoding='utf-8') as f:
            alive = json.load(f)
        self.assertEqual(alive['started'], 0)
        self.assertEqual(alive['beat'], 5000)
        self.assertEqual(alive['n'], 1)
        self.assertEqual(alive['pid'], os.getpid())

    # 3.23 T13d.c6b: a feed never outlives its session; one folder, one process.
    def test_exits_when_claude_is_gone(self) -> None:
        live = {'up': True}
        feed = pa_feed.Feed(self.dir, lambda t: self.writes.append((self.now, t)), self.errs.append, self.now,
                            claude_pid=4242, alive_check=lambda pid: pid == 4242 and live['up'])
        for _ in range(60):  # 6 s, two checks, still running
            self.assertTrue(feed.step(self.now))
            self.now += 100
        live['up'] = False
        stopped = None
        for _ in range(60):
            if not feed.step(self.now):
                stopped = self.now
                break
            self.now += 100
        self.assertIsNotNone(stopped)
        self.assertLessEqual(stopped - 6000, pa_feed.LIVE_MS)
        self.assertEqual(self.errs, [])

    def test_newer_process_takes_the_folder_and_numbering_continues(self) -> None:
        mk = lambda pid: pa_feed.Feed(self.dir, lambda t: self.writes.append((self.now, t)), self.errs.append,
                                      self.now, pid=pid)
        first = mk(101)
        for i in range(3):
            self.drop(_rec(i))
        self.run_until(first, 1000)
        with open(os.path.join(self.dir, 'owner.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'pid': 101, 'claudePid': None, 'started': 0})
        second = mk(202)
        for i in range(3, 5):
            self.drop(_rec(i))
        self.assertFalse(first.step(self.now))  # no longer the owner: exits, numbers nothing
        while self.now <= 3000:
            self.assertTrue(second.step(self.now))
            self.now += 100
        self.assertEqual([r['n'] for r in self.lines()], [1, 2, 3, 4, 5])
        with open(os.path.join(self.dir, 'log.jsonl'), encoding='utf-8') as f:
            self.assertEqual([json.loads(l)['n'] for l in f], [1, 2, 3, 4, 5])

    def test_claude_alive_sees_this_process(self) -> None:
        self.assertTrue(pa_feed.claude_alive(os.getpid()))


if __name__ == '__main__':
    unittest.main()
