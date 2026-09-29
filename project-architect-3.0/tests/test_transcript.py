"""pa.transcript: keep-LAST dedupe, tail reads, cost-state, subagent helpers."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pa import transcript as T  # noqa: E402

REF = ("C:/Users/you/.claude/projects/Z--Storage-git-Vantage/"
       "e2eed858-0000-4000-8000-00000000008c/subagents/agent-abfdf578000000016.jsonl")


def _assistant(mid, out, ts="2026-09-12T10:00:00.000Z", model="claude-sonnet-5",
               inp=2, cw=49011, cr=0, split=True, thinking=None, text="hi"):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": cw,
             "cache_read_input_tokens": cr, "output_tokens": out}
    if split:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": 0,
                                   "ephemeral_5m_input_tokens": cw}
    if thinking is not None:
        usage["output_tokens_details"] = {"thinking_tokens": thinking}
    return {"type": "assistant", "uuid": mid + "-u", "requestId": "req_" + mid,
            "sessionId": "sid-1", "timestamp": ts,
            "message": {"id": mid, "role": "assistant", "model": model,
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}], "usage": usage}}


def _write(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


class KeepLastTest(unittest.TestCase):
    """T1 -- a message's usage is the LAST jsonl line, not the first (recon V1)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pa3-tr-")
        self.path = os.path.join(self.dir, "agent-deadbeef.jsonl")

    def test_keep_last_line_per_message_id(self):
        first = _assistant("msg_x", 2)          # placeholder output on line 1
        last = _assistant("msg_x", 164)         # real output on the last line
        _write(self.path, [first, last])
        reqs = list(T.iter_requests(self.path))
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["output"], 164)
        self.assertEqual(reqs[0]["msg_id"], "msg_x")

    def test_derived_fields(self):
        rec = _assistant("msg_a", 10, inp=5, cw=1000, cr=90000, thinking=7)
        _write(self.path, [rec])
        r = list(T.iter_requests(self.path))[0]
        self.assertEqual(r["ctx"], 5 + 1000 + 90000)
        self.assertEqual(r["new_tokens"], 5 + 1000)
        self.assertEqual((r["cache_write_5m"], r["cache_write_1h"]), (1000, 0))
        self.assertTrue(r["split"])
        self.assertEqual(r["thinking"], 7)
        self.assertEqual(r["stop_reason"], "end_turn")

    def test_no_split_leaves_ttl_to_the_caller(self):
        _write(self.path, [_assistant("msg_b", 3, split=False)])
        r = list(T.iter_requests(self.path))[0]
        self.assertFalse(r["split"])
        self.assertIsNone(r["cache_write_5m"])
        self.assertIsNone(r["cache_write_1h"])

    def test_synthetic_and_empty_usage_skipped(self):
        recs = [_assistant("msg_s", 5, model="<synthetic>"),
                _assistant("msg_z", 0, inp=0, cw=0, cr=0, split=False),
                {"type": "user", "message": {"role": "user", "content": "hello"}},
                _assistant("msg_ok", 11)]
        _write(self.path, recs)
        reqs = list(T.iter_requests(self.path))
        self.assertEqual([r["msg_id"] for r in reqs], ["msg_ok"])

    def test_gap_cold_and_rewrite_flags(self):
        recs = [_assistant("m1", 5, ts="2026-09-12T10:00:00.000Z", cw=60000, cr=0),
                _assistant("m2", 5, ts="2026-09-12T10:01:00.000Z", cw=100, cr=60000),
                _assistant("m3", 5, ts="2026-09-12T12:00:00.000Z", cw=61000, cr=100)]
        _write(self.path, recs)
        r1, r2, r3 = list(T.iter_requests(self.path))
        self.assertIsNone(r1["gap_s"])
        self.assertFalse(r1["rewrite"])                 # index 0 is the seed, never a rewrite
        self.assertEqual(r2["gap_s"], 60.0)
        self.assertFalse(r2["cold"])
        self.assertFalse(r2["rewrite"])
        self.assertGreater(r3["gap_s"], 3600)
        self.assertTrue(r3["cold"])                     # gap beyond the 1h TTL
        self.assertTrue(r3["rewrite"])                  # wrote > 50% of its context
        self.assertTrue(T.rewrite_rule(1, 100000, 90000))
        self.assertFalse(T.rewrite_rule(0, 100000, 90000))
        self.assertFalse(T.rewrite_rule(1, 1000, 900))  # below MISS_MIN_CTX

    def test_read_new_requests_offsets_resume(self):
        _write(self.path, [_assistant("m1", 1), _assistant("m2", 2)])
        first, offset = T.read_new_requests(self.path)
        self.assertEqual(len(first), 2)
        self.assertEqual(offset, os.path.getsize(self.path))
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(_assistant("m3", 3)) + "\n")
        more, offset2 = T.read_new_requests(self.path, offset)
        self.assertEqual([r["msg_id"] for r in more], ["m3"])
        self.assertEqual(offset2, os.path.getsize(self.path))

    def test_tail_last_usage_synthetic(self):
        _write(self.path, [_assistant("m1", 1, cr=0, cw=100),
                           _assistant("m2", 2, cr=5000, cw=200),
                           _assistant("m3", 3, cr=9000, cw=300),
                           _assistant("m3", 164, cr=9000, cw=300)])
        u = T.tail_last_usage(self.path, tail_bytes=512)
        self.assertIsNotNone(u)
        self.assertEqual(u["msg_id"], "m3")
        self.assertEqual(u["output"], 164)
        self.assertEqual(u["ctx"], 2 + 300 + 9000)
        self.assertIsNone(T.tail_last_usage(os.path.join(self.dir, "missing.jsonl")))

    def test_cost_state_last_wins(self):
        recs = [_assistant("m1", 1),
                {"type": "cost-state", "totalCostUSD": 1.5, "modelUsage": {}},
                _assistant("m2", 2),
                {"type": "cost-state", "totalCostUSD": 9.25,
                 "modelUsage": {"claude-fable-5-1": {"costUSD": 9.25}}}]
        _write(self.path, recs)
        cs = T.cost_state_last(self.path)
        self.assertIsNotNone(cs)
        self.assertEqual(cs["totalCostUSD"], 9.25)
        # tail window smaller than the file still finds it (tail-first scan)
        self.assertEqual(T.cost_state_last(self.path, tail_bytes=64)["totalCostUSD"], 9.25)
        _write(self.path, [_assistant("m1", 1)])
        self.assertIsNone(T.cost_state_last(self.path))

    def test_cost_state_fresh_when_last(self):
        """cost-state at the end of the file is fresh."""
        recs = [_assistant("m1", 1),
                {"type": "cost-state", "totalCostUSD": 5.0, "modelUsage": {}}]
        _write(self.path, recs)
        rec, fresh, offset = T.cost_state_fresh(self.path)
        self.assertIsNotNone(rec)
        self.assertTrue(fresh)
        self.assertEqual(rec["totalCostUSD"], 5.0)
        self.assertGreater(offset, 0)

    def test_cost_state_stale_when_assistant_follows(self):
        """cost-state is stale when an assistant with usage follows it."""
        recs = [_assistant("m1", 1),
                {"type": "cost-state", "totalCostUSD": 5.0, "modelUsage": {}},
                _assistant("m2", 2)]
        _write(self.path, recs)
        rec, fresh, offset = T.cost_state_fresh(self.path)
        self.assertIsNotNone(rec)
        self.assertFalse(fresh)

    def test_cost_state_fresh_no_record(self):
        """No cost-state record returns (None, False, 0)."""
        _write(self.path, [_assistant("m1", 1)])
        rec, fresh, offset = T.cost_state_fresh(self.path)
        self.assertIsNone(rec)
        self.assertFalse(fresh)
        self.assertEqual(offset, 0)

    def test_subagent_files_and_meta(self):
        sid = os.path.join(self.dir, "sid-1")
        subs = os.path.join(sid, "subagents")
        os.makedirs(subs)
        a = os.path.join(subs, "agent-aaa111.jsonl")
        _write(a, [_assistant("m1", 1)])
        _write(os.path.join(subs, "agent-bbb222.jsonl"), [_assistant("m2", 2)])
        with open(os.path.join(subs, "agent-aaa111.meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"agentType": "retriever-code", "spawnDepth": 2,
                       "toolUseId": "toolu_1"}, fh)
        for arg in (sid, subs, sid + ".jsonl"):
            found = T.subagent_files(arg)
            self.assertEqual([os.path.basename(f) for f in found],
                             ["agent-aaa111.jsonl", "agent-bbb222.jsonl"])
        self.assertEqual(T.agent_id_from_path(a), "aaa111")
        self.assertEqual(T.read_meta(a)["agentType"], "retriever-code")
        self.assertEqual(T.read_meta(os.path.join(subs, "agent-bbb222.jsonl")), {})

    def test_subagent_files_recursive_with_workflows(self):
        """T1.1: subagent_files walks subagents/workflows/<wf>/ recursively."""
        sid = os.path.join(self.dir, "sid-wf")
        subs = os.path.join(sid, "subagents")
        os.makedirs(subs)
        flat = os.path.join(subs, "agent-flat111.jsonl")
        _write(flat, [_assistant("m1", 1)])
        wf_dir = os.path.join(subs, "workflows", "wf_test-1")
        os.makedirs(wf_dir)
        wf1 = os.path.join(wf_dir, "agent-wfagent1.jsonl")
        wf2 = os.path.join(wf_dir, "agent-wfagent2.jsonl")
        _write(wf1, [_assistant("m2", 2)])
        _write(wf2, [_assistant("m3", 3)])
        # also write a .meta.json
        with open(wf1.replace(".jsonl", ".meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"agentType": "coder-opus46", "description": "code task"}, fh)

        for arg in (sid, subs, sid + ".jsonl"):
            found = T.subagent_files(arg)
            basenames = [os.path.basename(f) for f in found]
            self.assertEqual(basenames,
                             ["agent-flat111.jsonl", "agent-wfagent1.jsonl", "agent-wfagent2.jsonl"])

    def test_find_agent_transcript(self):
        """T1.1: find_agent_transcript resolves flat and workflow agents."""
        sid = os.path.join(self.dir, "sid-find")
        subs = os.path.join(sid, "subagents")
        os.makedirs(subs)
        flat = os.path.join(subs, "agent-flat222.jsonl")
        _write(flat, [_assistant("m1", 1)])
        wf_dir = os.path.join(subs, "workflows", "wf_lookup")
        os.makedirs(wf_dir)
        wf_path = os.path.join(wf_dir, "agent-wfagent3.jsonl")
        _write(wf_path, [_assistant("m2", 2)])

        # flat agent found
        self.assertEqual(T.find_agent_transcript(sid, "flat222"), flat)
        # workflow agent found
        self.assertEqual(T.find_agent_transcript(sid, "wfagent3"), wf_path)
        # unknown returns None
        self.assertIsNone(T.find_agent_transcript(sid, "unknown999"))
        # None agent_id returns None
        self.assertIsNone(T.find_agent_transcript(sid, None))

    def test_workflow_id_of(self):
        """T1.1: workflow_id_of extracts the wf directory name."""
        self.assertEqual(T.workflow_id_of(
            "/a/b/subagents/workflows/wf_abc/agent-123.jsonl"), "wf_abc")
        self.assertIsNone(T.workflow_id_of(
            "/a/b/subagents/agent-123.jsonl"))
        self.assertIsNone(T.workflow_id_of(None))
        # Windows backslash paths
        self.assertEqual(T.workflow_id_of(
            "C:\\Users\\.claude\\subagents\\workflows\\wf_win\\agent-456.jsonl"), "wf_win")

    def test_tool_result_tokens_estimate(self):
        recs = [
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-haiku-4-5",
             "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x"}}],
             "usage": {"input_tokens": 1, "output_tokens": 1}}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "y" * 400}]}},
        ]
        _write(self.path, recs)
        est = T.tool_result_tokens(self.path)
        self.assertEqual(est["n_results"], 1)
        self.assertEqual(est["result_tokens"], 100)      # 400 chars / 4
        self.assertGreater(est["own_output"], 0)

    def test_est_and_parse_ts_helpers(self):
        self.assertEqual(T.est("abcd" * 10), 10)
        self.assertEqual(T.est(None), 0)
        self.assertIsNone(T.parse_ts(None))
        self.assertIsNone(T.parse_ts("not-a-date"))
        self.assertEqual(T.iso(T.parse_ts("2026-09-12T10:00:00.000Z")), "2026-09-12T10:00:00Z")
        self.assertEqual(T.fmt_gap(4200), "1h10m")
        self.assertEqual(T.fmt_gap(None), "?")


@unittest.skipUnless(os.path.exists(REF), "reference transcript not on this machine: %s" % REF)
class ReferenceTranscriptTest(unittest.TestCase):
    """T1 on the real Vantage subagent file (recon V1 ground truth)."""

    def test_twenty_requests_and_output_sum(self):
        reqs = list(T.iter_requests(REF))
        self.assertEqual(len(reqs), 20)
        self.assertEqual(sum(r["output"] for r in reqs), 3109)

    def test_placeholder_output_is_not_kept(self):
        by_id = {r["msg_id"]: r for r in T.iter_requests(REF)}
        self.assertIn("msg_01SYNTH00000000000000001", by_id)
        self.assertEqual(by_id["msg_01SYNTH00000000000000001"]["output"], 164)

    def test_tail_last_usage_matches_the_last_request(self):
        last = list(T.iter_requests(REF))[-1]
        tail = T.tail_last_usage(REF)
        self.assertEqual(tail["msg_id"], last["msg_id"])
        self.assertEqual(tail["output"], last["output"])
        self.assertEqual(tail["ctx"], last["ctx"])

    def test_meta_of_the_reference_agent(self):
        meta = T.read_meta(REF)
        self.assertEqual(meta.get("agentType"), "general-purpose")
        self.assertEqual(meta.get("spawnDepth"), 1)


if __name__ == "__main__":
    unittest.main()
