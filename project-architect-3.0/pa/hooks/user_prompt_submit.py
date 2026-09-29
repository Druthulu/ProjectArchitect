"""UserPromptSubmit: record a returning subagent's ``<task-notification>``.

The harness resumes the router with a synthetic prompt when a background agent
stops (probe1 fixture)::

    <task-notification><task-id>a5cf…</task-id><tool-use-id>toolu_…</tool-use-id>
    <output-file>…</output-file><status>completed</status><summary>…</summary>
    <note>…</note><result>UNKNOWN.</result>
    <usage><subagent_tokens>2408</subagent_tokens>…</usage></task-notification>

Everything else returns immediately: a developer prompt is none of the ledger's
business and this hook's budget is 60 ms.  The parsed return lands in
``running.json.sessions[sid].last_return`` (statusline line 2/4) and, in a
governed project, the contract status lands in ``.run/status.json.note``.

Returns output only for the renewal-day prompt (3.11 T9): after a switch to an account
without a renewal day, a ``systemMessage`` for the user and ``additionalContext`` that asks
the model to put one question; everything else returns nothing.
"""

import re

from .. import running
from . import agent_of, governed, now_iso, project_config, sid_of, status_patch

_TAG_RE = re.compile(r"<([a-z-]+)>(.*?)</\1>", re.S)
_STATUS_RE = re.compile(r"STATUS\s*[:=]\s*([A-Za-z-]+)", re.I)
_CONTRACT = ("handoff", "done", "blocked", "question", "review", "error")


def parse_notification(prompt):
    """``{tag: text}`` for a ``<task-notification>`` prompt, else ``{}``."""
    text = str(prompt or "")
    if "<task-notification>" not in text:
        return {}
    out = {}
    for name, body in _TAG_RE.findall(text):
        if name == "task-notification":
            continue
        out.setdefault(name, body.strip())
    return out


def contract_status(result_text):
    """``STATUS: <word>`` from a returned contract, lowercased, else None."""
    m = _STATUS_RE.search(str(result_text or "")[:2000])
    if not m:
        return None
    word = m.group(1).lower()
    return word if word in _CONTRACT else None


def _renewal_after_switch(inp, cfg):
    """3.11 T9: after a mid-session switch to an account without a renewal day, ask once (per session and
    account). The account check is the stamp's O(1) fast path unless the credentials changed."""
    if agent_of(inp) or inp.get("pa_probe"):            # a probe replay never asks (H14)
        return None
    sid = sid_of(inp)
    try:
        from .. import accounts, renewal

        email, _changed, _prev = accounts.refresh_account(sid, cfg)
        if not renewal.due(cfg, sid, email):
            return None
        user_line, model_line = renewal.prompt(email)
        renewal.mark(sid, email)
    except Exception:
        return None
    return {"systemMessage": user_line,
            "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": model_line}}


def run(inp, cfg):
    inp = inp or {}
    fields = parse_notification(inp.get("prompt"))
    if not fields:
        return _renewal_after_switch(inp, cfg)

    sid = sid_of(inp)
    agent_id = fields.get("task-id")
    status = fields.get("status")
    result = fields.get("result") or ""
    payload = {"agent_id": agent_id, "status": status, "ts": now_iso(),
               "tool_use_id": fields.get("tool-use-id"),
               "summary": fields.get("summary"),
               "result_head": result[:400],
               "contract": contract_status(result)}
    usage = fields.get("usage")
    if usage:
        m = re.search(r"<subagent_tokens>(\d+)</subagent_tokens>", usage)
        if m:
            payload["subagent_tokens"] = int(m.group(1))
    running.set_last_return(sid, payload)

    root = governed(inp)
    if root and payload["contract"]:
        pcfg = project_config(root)
        note = "%s: STATUS %s" % (agent_id or "agent", payload["contract"])
        fields_out = {"note": note}
        if payload["contract"] == "question":
            fields_out["waiting"] = "question"
        status_patch(root, fields_out)
        if pcfg.get("rhythm") == "review" and payload["contract"] == "review":
            status_patch(root, {"waiting": "REVIEW.md"})
    return None
