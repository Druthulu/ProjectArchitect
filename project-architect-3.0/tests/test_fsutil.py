"""pa.fsutil: atomic writes, JSON reads, locked_update under concurrency."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import fsutil  # noqa: E402


class AtomicWriteTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-fs-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_atomic_write_json_round_trip(self):
        p = os.path.join(self.dir, "nested", "running.json")
        payload = {"schema": 1, "sessions": {"s1": {"ctx": 210437, "note": "café"}}}
        fsutil.atomic_write_json(p, payload)
        self.assertEqual(fsutil.read_json(p), payload)
        with open(p, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw)                       # LF only
        self.assertEqual(json.loads(raw.decode("utf-8")), payload)

    def test_no_temp_files_left_behind(self):
        p = os.path.join(self.dir, "summary.json")
        fsutil.atomic_write_json(p, {"a": 1})
        fsutil.atomic_write_json(p, {"a": 2})
        self.assertEqual(sorted(os.listdir(self.dir)), ["summary.json"])
        self.assertEqual(fsutil.read_json(p)["a"], 2)

    def test_read_json_defaults_on_missing_and_corrupt(self):
        missing = os.path.join(self.dir, "nope.json")
        self.assertIsNone(fsutil.read_json(missing))
        self.assertEqual(fsutil.read_json(missing, {}), {})
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(fsutil.read_json(bad, {"fallback": True}), {"fallback": True})
        empty = os.path.join(self.dir, "empty.json")
        open(empty, "w").close()
        self.assertEqual(fsutil.read_json(empty, {}), {})

    def test_append_line_and_sizes(self):
        p = os.path.join(self.dir, "spool", "s1.jsonl")
        fsutil.append_line(p, json.dumps({"n": 1}))
        fsutil.append_line(p, json.dumps({"n": 2}) + "\n")
        with open(p, "rb") as fh:
            raw = fh.read()
        self.assertEqual(raw.count(b"\n"), 2)
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(fsutil.file_size(p), len(raw))
        self.assertEqual(fsutil.file_size(os.path.join(self.dir, "gone")), 0)

    def test_disk_free_mb(self):
        self.assertGreater(fsutil.disk_free_mb(self.dir), 0)
        self.assertGreater(fsutil.disk_free_mb(os.path.join(self.dir, "not-there.json")), 0)


class LockedUpdateTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-lock-")
        self.path = os.path.join(self.dir, "counter.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_two_threads_two_hundred_increments_each(self):
        fsutil.atomic_write_json(self.path, {"n": 0})
        errors = []

        def bump():
            try:
                for _ in range(200):
                    fsutil.locked_update(self.path, lambda d: dict(d, n=d.get("n", 0) + 1),
                                         timeout_ms=10000)
            except BaseException as exc:                     # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=bump) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual(errors, [])
        self.assertEqual(fsutil.read_json(self.path)["n"], 400)

    def test_returning_none_means_no_write(self):
        fsutil.atomic_write_json(self.path, {"n": 7})
        result = fsutil.locked_update(self.path, lambda d: None)
        self.assertEqual(result["n"], 7)
        self.assertEqual(fsutil.read_json(self.path)["n"], 7)

    def test_missing_file_starts_from_the_default(self):
        p = os.path.join(self.dir, "new.json")
        out = fsutil.locked_update(p, lambda d: dict(d, created=True), default={"n": 0})
        self.assertEqual(out, {"n": 0, "created": True})
        self.assertEqual(fsutil.read_json(p), {"n": 0, "created": True})

    def test_lock_is_exclusive_and_times_out(self):
        with fsutil.file_lock(self.path, timeout_ms=1000):
            done = []

            def grab():
                try:
                    with fsutil.file_lock(self.path, timeout_ms=50):
                        done.append("acquired")
                except TimeoutError:
                    done.append("timeout")

            t = threading.Thread(target=grab)
            t.start()
            t.join(30)
            self.assertEqual(done, ["timeout"])
        with fsutil.file_lock(self.path, timeout_ms=1000):   # released again
            pass


if __name__ == "__main__":
    unittest.main()
