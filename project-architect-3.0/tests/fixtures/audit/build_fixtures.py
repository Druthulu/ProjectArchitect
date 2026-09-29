"""Build the audit test fixture jsonl files.  Run once to regenerate."""
import json
import os

DIR = os.path.dirname(__file__)


def _msg(role, content, **kw):
    rec = {"message": {"role": role, "content": content}}
    if kw:
        rec["message"].update(kw)
    return rec


def _tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tool_result(tid, text):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def _assistant_usage(model="claude-opus-4-6[1m]"):
    return {"model": model, "usage": {
        "input_tokens": 5000, "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 2000, "output_tokens": 500}}


def build_main():
    records = []
    # 1. Read of PHASE_PLAN.md whole (no offset/limit)
    records.append(_msg("assistant", [
        _tool_use("tu1", "Read", {"file_path": "/proj/phase-ends/current/PHASE_PLAN.md"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu1", "# Phase Plan\nThis is the plan content " + "x" * 1000)
    ]))
    # api request
    records.append(_msg("assistant", "thinking...", **_assistant_usage()))

    # 2. Spilled result (Bash that got spilled)
    spill_text = ("<persisted-output>\nOutput too large (25.0KB). Full output saved to: "
                  "C:\\Users\\you\\.claude\\projects\\test\\sess1\\tool-results\\spill001.txt\n\n"
                  "Preview (first 2KB):\nsome preview content")
    records.append(_msg("assistant", [
        _tool_use("tu2", "Bash", {"command": "cat bigfile.txt"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu2", spill_text)
    ]))
    records.append(_msg("assistant", "ok", **_assistant_usage()))

    # 3. Later Read of the spilled file (whole, no offset/limit) = read_whole
    records.append(_msg("assistant", [
        _tool_use("tu3", "Read", {"file_path": "C:\\Users\\you\\.claude\\projects\\test\\sess1\\tool-results\\spill001.txt"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu3", "the full spilled content " + "y" * 2000)
    ]))
    records.append(_msg("assistant", "got it", **_assistant_usage()))

    # 4. Noise result (git warning)
    records.append(_msg("assistant", [
        _tool_use("tu4", "Bash", {"command": "git add file.txt"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu4", "warning: in the working copy of 'file.txt', LF will be replaced by CRLF")
    ]))
    records.append(_msg("assistant", "added", **_assistant_usage()))

    # 5. Write
    records.append(_msg("assistant", [
        _tool_use("tu5", "Write", {"file_path": "/proj/out.py", "content": "print('hello')\n" * 10})
    ]))
    records.append(_msg("user", [
        _tool_result("tu5", "File written successfully")
    ]))
    records.append(_msg("assistant", "done", **_assistant_usage()))

    # 6. plan_edit.py show without a section (whole-plan read via Bash)
    records.append(_msg("assistant", [
        _tool_use("tu6", "Bash", {"command": "python tools/plan_edit.py show"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu6", "## Context\nstuff\n## Tasks\nmore stuff " + "z" * 500)
    ]))
    records.append(_msg("assistant", "read plan", **_assistant_usage()))

    # 7. Bash run.sh tail
    records.append(_msg("assistant", [
        _tool_use("tu7", "Bash", {"command": "bash tools/run.sh gate -- python -m pytest"})
    ]))
    records.append(_msg("user", [
        _tool_result("tu7", "OK\n3 passed in 1.2s")
    ]))
    records.append(_msg("assistant", "tests pass", **_assistant_usage()))

    path = os.path.join(DIR, "main.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_subagent():
    records = []
    # A Read of a summary file
    records.append(_msg("assistant", [
        _tool_use("stu1", "Read", {"file_path": "/proj/tasks/T1.md"})
    ]))
    records.append(_msg("user", [
        _tool_result("stu1", "# T1 Summary\n" + "summary " * 50)
    ]))
    records.append(_msg("assistant", "read summary", **_assistant_usage("claude-fable-5-1[1m]")))

    path = os.path.join(DIR, "agent-sub001.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_audit_md():
    content = """# Audit — 2026-09-20, phase 3.5
## Verdicts
- config.py | leave | acceptable reads
## Tool candidates
- Tool candidate: mytool | does: automates X | replaces: manual | occurrences: 5 in 3.5 | saving: $1 (10k tokens) | build: S | status: proposed
"""
    path = os.path.join(DIR, "AUDIT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def build_phase_plan():
    content = """## Tasks
- T1 | done | expert-fable | coder: opus46 | effort: low | title: tool candidate mytool
- T2 | queued | expert-fable | coder: opus46 | effort: low | title: something else
"""
    path = os.path.join(DIR, "PHASE_PLAN.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    build_main()
    build_subagent()
    build_audit_md()
    build_phase_plan()
    print("Fixtures built in", DIR)
