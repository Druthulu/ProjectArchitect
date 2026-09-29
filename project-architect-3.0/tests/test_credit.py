"""pa.credit: consume, rows_for_call, read_row, carry_tokens, path normalisation."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import credit  # noqa: E402


class ConsumeTest(unittest.TestCase):
    """consume reads and deletes note files, ignoring bad ones."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-credit-")
        self.root = os.path.join(self.dir, "proj")
        self.credit_dir = os.path.join(self.root, ".run", "credit")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_dir_returns_empty(self):
        self.assertEqual(credit.consume(self.root), [])

    def test_does_not_create_dir(self):
        credit.consume(self.root)
        self.assertFalse(os.path.isdir(self.credit_dir))

    def test_reads_and_deletes(self):
        os.makedirs(self.credit_dir)
        note = {"ts": "2026-09-22T00:00:00Z", "script": "plan_edit", "kind": "script",
                "path": "pa/foo.py", "file_chars": 400, "removed_chars": 10,
                "added_chars": 20, "anchor_chars": 30, "whole": False}
        with open(os.path.join(self.credit_dir, "1-123.json"), "w") as fh:
            json.dump(note, fh)
        result = credit.consume(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["script"], "plan_edit")
        self.assertEqual(os.listdir(self.credit_dir), [])

    def test_unparsable_ignored(self):
        os.makedirs(self.credit_dir)
        with open(os.path.join(self.credit_dir, "bad.json"), "w") as fh:
            fh.write("not json{{{")
        good = {"ts": "t", "script": "s", "kind": "script", "path": "a.py",
                "file_chars": 100, "removed_chars": 0, "added_chars": 0,
                "anchor_chars": 0, "whole": False}
        with open(os.path.join(self.credit_dir, "good.json"), "w") as fh:
            json.dump(good, fh)
        result = credit.consume(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["script"], "s")
        self.assertEqual(os.listdir(self.credit_dir), [])


class TokensTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(credit.tokens(400), 100)
        self.assertEqual(credit.tokens(0), 0)
        self.assertEqual(credit.tokens(3), 0)

    def test_none(self):
        self.assertEqual(credit.tokens(None), 0)


class RowsForCallTest(unittest.TestCase):
    """rows_for_call builds tool_calls rows from consumed notes."""

    def test_script_emission(self):
        note = {"kind": "script", "script": "plan_edit", "path": "pa/foo.py",
                "file_chars": 400, "removed_chars": 40, "added_chars": 80,
                "anchor_chars": 120, "whole": False}
        rows = credit.rows_for_call(
            [note], session_id="s1", run_id="r1", ts="t1",
            model="claude-opus-4-6", result_chars=200, cfg={})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["emission_tokens"], credit.tokens(40 + 80 + 120))
        self.assertEqual(r["result_tokens"], credit.tokens(200))
        self.assertEqual(r["kept_tokens"], 0)
        self.assertEqual(r["kind"], "script")

    def test_whole_emission(self):
        note = {"kind": "script", "script": "plan_edit", "path": "pa/foo.py",
                "file_chars": 1000, "removed_chars": 0, "added_chars": 0,
                "anchor_chars": 0, "whole": True}
        rows = credit.rows_for_call(
            [note], session_id="s1", run_id="r1", ts="t1",
            model="m", result_chars=0, cfg={})
        self.assertEqual(rows[0]["emission_tokens"], credit.tokens(1000))

    def test_runsh_kept_capped(self):
        note = {"kind": "runsh", "sub": "gate", "path": "",
                "file_chars": 0, "removed_chars": 0, "added_chars": 0,
                "anchor_chars": 0, "whole": False, "kept_chars": 50000}
        cfg = {"credit": {"spill_chars": 20000}}
        rows = credit.rows_for_call(
            [note], session_id="s1", run_id="r1", ts="t1",
            model="m", result_chars=100, cfg=cfg)
        self.assertEqual(rows[0]["kept_tokens"], min(credit.tokens(50000), credit.tokens(20000)))
        # capped at spill_chars
        self.assertEqual(rows[0]["kept_tokens"], credit.tokens(20000))


class CarryTokensTest(unittest.TestCase):
    """carry_tokens: first-per-path, the guard, the runsh cap."""

    def test_first_per_path(self):
        row = {"kind": "script", "path": "pa/foo.py", "file_tokens": 100, "result_tokens": 10}
        seen = set()
        carry = credit.carry_tokens(row, seen, set(), {})
        self.assertEqual(carry, 90)
        self.assertIn("pa/foo.py", seen)
        # second note for same path -> 0
        carry2 = credit.carry_tokens(row, seen, set(), {})
        self.assertEqual(carry2, 0)

    def test_guard_read_path(self):
        row = {"kind": "script", "path": "pa/foo.py", "file_tokens": 100, "result_tokens": 10}
        carry = credit.carry_tokens(row, set(), {"pa/foo.py"}, {})
        self.assertEqual(carry, 0)

    def test_runsh_cap(self):
        row = {"kind": "runsh", "path": "", "kept_tokens": 5000}
        cfg = {"credit": {"spill_chars": 20000}}
        carry = credit.carry_tokens(row, set(), set(), cfg)
        self.assertEqual(carry, min(5000, credit.tokens(20000)))
        self.assertEqual(carry, 5000)

    def test_runsh_over_spill(self):
        row = {"kind": "runsh", "path": "", "kept_tokens": 99999}
        cfg = {"credit": {"spill_chars": 20000}}
        carry = credit.carry_tokens(row, set(), set(), cfg)
        self.assertEqual(carry, credit.tokens(20000))

    def test_outline_credits_nothing(self):
        row = {"kind": "outline", "path": "pa/foo.py", "file_tokens": 5000,
               "emission_tokens": 100, "result_tokens": 100}
        self.assertEqual(credit.carry_tokens(row, set(), set(), {}), 0)

    def test_outline_read_credit(self):
        """file_tokens - the outline's emission - result_tokens, unguarded, floor 0."""
        row = {"kind": "outline-read", "path": "pa/foo.py", "file_tokens": 5000,
               "outline_emission_tokens": 100, "result_tokens": 900}
        self.assertEqual(credit.carry_tokens(row, set(), {"pa/foo.py"}, {}), 4000)
        row["result_tokens"] = 6000
        self.assertEqual(credit.carry_tokens(row, set(), set(), {}), 0)

    def test_outline_note_row(self):
        note = {"kind": "outline", "script": "outline.py", "path": "pa/foo.py",
                "file_chars": 90000, "cap_chars": 80000, "emitted_chars": 400}
        rows = credit.rows_for_call([note], session_id="s", run_id="r", ts="t",
                                    model="m", result_chars=400, cfg={})
        self.assertEqual(rows[0]["kind"], "outline")
        self.assertEqual(rows[0]["file_tokens"], 20000)
        self.assertEqual(rows[0]["emission_tokens"], 100)


class PathNormTest(unittest.TestCase):
    """Both Z:/ and /mnt/z/ normalise to the same relative key."""

    def test_windows_path(self):
        self.assertEqual(credit._rel_path("Z:/Storage/git/Proj/pa/foo.py"),
                         "Storage/git/Proj/pa/foo.py")

    def test_wsl_path(self):
        self.assertEqual(credit._rel_path("/mnt/z/Storage/git/Proj/pa/foo.py"),
                         "Storage/git/Proj/pa/foo.py")

    def test_backslashes(self):
        self.assertEqual(credit._rel_path("Z:\\Storage\\git\\Proj\\pa\\foo.py"),
                         "Storage/git/Proj/pa/foo.py")

    def test_same_after_norm(self):
        win = credit._rel_path("Z:/Storage/git/Proj/pa/foo.py")
        wsl = credit._rel_path("/mnt/z/Storage/git/Proj/pa/foo.py")
        self.assertEqual(win, wsl)


class ResultCharsTest(unittest.TestCase):
    """result_chars: str, Bash dict, Read dict, JSON string."""

    def test_plain_str(self):
        self.assertEqual(credit.result_chars("hello world"), 11)

    def test_bash_dict(self):
        resp = {"stdout": "abc", "stderr": "de", "interrupted": False, "exitCode": 0}
        self.assertEqual(credit.result_chars(resp), 5)  # "abc"(3) + "de"(2)

    def test_read_dict(self):
        resp = {"type": "text", "file": {"filePath": "/a/b.py", "content": "hello world",
                                         "numLines": 1, "startLine": 1, "totalLines": 1}}
        expected = len("text") + len("/a/b.py") + len("hello world")
        self.assertEqual(credit.result_chars(resp), expected)

    def test_json_string(self):
        inner = {"stdout": "abc", "stderr": "de"}
        resp = json.dumps(inner)
        self.assertEqual(credit.result_chars(resp), 5)  # parsed then recursed

    def test_none(self):
        self.assertEqual(credit.result_chars(None), 0)

    def test_number(self):
        self.assertEqual(credit.result_chars(42), 0)

    def test_list(self):
        self.assertEqual(credit.result_chars(["ab", "cde"]), 5)


class ReadRowTest(unittest.TestCase):
    def test_shape(self):
        r = credit.read_row("s1", "r1", "t1", "m", "pa/foo.py", 500)
        self.assertEqual(r["tool"], "Read")
        self.assertEqual(r["kind"], "read")
        self.assertEqual(r["path"], "pa/foo.py")
        self.assertEqual(r["emission_tokens"], 0)
        self.assertEqual(r["result_tokens"], credit.tokens(500))

    def test_outline_read_shape(self):
        r = credit.read_row("s1", "r1", "t1", "m", "pa/foo.py", 500,
                            kind="outline-read", file_tokens=5000)
        self.assertEqual(r["kind"], "outline-read")
        self.assertEqual(r["file_tokens"], 5000)
        self.assertNotEqual(r["id"], credit.read_row("s1", "r1", "t1", "m", "pa/foo.py", 5)["id"])


if __name__ == "__main__":
    unittest.main()
