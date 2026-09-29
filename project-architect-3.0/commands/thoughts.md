---
description: Alias of /discuss (kept one release) — discussion mode with the default model and effort
allowed-tools: Bash({{PY}} tools/discussion.py:*)
argument-hint: [topic]
disable-model-invocation: true
---
!`{{PY}} tools/discussion.py on`
Print "/thoughts is now /discuss (alias kept one release)", then behave exactly as `/discuss` with no model and no
effort (agent `discuss`, no `model` override) and TOPIC = $ARGUMENTS.

Discussion mode is ON when the line above says `DISCUSSION on`. If it does not, run `{{PY}} tools/discussion.py on`
yourself now (Bash) — that command is always permitted. Until the flag is down: no Edit/Write/commit/mutating Bash for
any agent — the PreToolUse guard denies them while `.run/DISCUSSION` exists.

If you are the pa-session (the router): spawn `discuss` in the background with the brief
`TOPIC: $ARGUMENTS · PLAN: PY tools/plan_edit.py show --section Context (Interfaces, Cookbook, Research) · HOW: HOW_WE_WORK.md`,
print one line — "Discussion open: click the discuss agent below and talk there; say proceed there when done" — and end
your turn. You never discuss here yourself. On its return: run every `EDITS` line verbatim, commit them
(`bash tools/commit_task.sh router "<what>" phase-ends/current/PHASE_PLAN.md`), run `{{PY}} tools/discussion.py off` if
`.run/DISCUSSION` still exists, print its `DECISIONS`, and continue where you were.

If you are a plain chat session: think with the developer here about: $ARGUMENTS. Reply in plain prose,
recommendation first. When a conclusion would change the plan, phrase it as the exact `tools/plan_edit.py` command but do
not run it until `/proceed`.
