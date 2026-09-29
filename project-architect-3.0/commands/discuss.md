---
description: Discussion mode — a discuss agent to think with; `stop` first pauses the phase read-only until /proceed; optional model and effort
allowed-tools: Bash({{PY}} tools/discussion.py:*)
argument-hint: [stop] [opus|fable|sonnet] [medium|high|max] [-- topic]
disable-model-invocation: true
---
!`{{PY}} tools/discussion.py status`
Arguments: `$ARGUMENTS`. Words before an optional `--`: an optional `stop` first, then the model and the effort, each
optional, model first: models `opus` (default) | `fable` | `sonnet`; efforts `medium` (default) | `high` | `max`. Text
after `--` is the TOPIC (no `--`: the TOPIC is empty). Agent: `discuss` (medium) | `discuss-high` | `discuss-max`.
Agent-tool `model` override: none for opus, `fable` for fable, `sonnet` for sonnet.

Without `stop` = open mode: no flag is raised, nothing stops, running experts continue. With `stop` as the first word =
stop mode: run `{{PY}} tools/discussion.py on` yourself now (Bash, always permitted); until `/proceed` no
Edit/Write/commit/mutating Bash for any agent — the PreToolUse guard denies them while `.run/DISCUSSION` exists.

Any other word before `--`: print "allowed: stop (first word only), models opus|fable|sonnet, efforts
medium|high|max", in stop mode only run `{{PY}} tools/discussion.py off` (flag down, no record), spawn nothing, and end
the turn.

If you are the pa-session (the router), you never discuss here yourself:
- Open mode: spawn that agent, with that override, in the background with the brief `MODE: open · TOPIC: <TOPIC> ·
  PLAN: PY tools/plan_edit.py show --section Context (Interfaces, Cookbook, Research) · HOW: HOW_WE_WORK.md`, print one
  line — "Discussion open: click the discuss agent below and talk there; say proceed there when done" — and continue
  the loop (do not end the turn waiting on it; if an expert is running, end the turn as step 3 does). On its return:
  if no expert is running, run every `EDITS` line verbatim and commit them at once
  (`bash tools/commit_task.sh router "<what>" phase-ends/current/PHASE_PLAN.md`); else hold them and run and commit
  them at the next task boundary, before step 1. Print its `DECISIONS`.
- Stop mode: if an expert is running, `TaskStop` it and run `{{PY}} tools/status.py set --task T<n> --agent <same>
  --kind relaunch --attempt <k+1>`. Spawn that agent as above with `MODE: stop` in place of `MODE: open`, print the same
  line, and end your turn; spawn nothing else while `.run/DISCUSSION` exists. On its return (after `/proceed`): run
  every `EDITS` line verbatim, commit them, run `{{PY}} tools/discussion.py off` if `.run/DISCUSSION` still exists,
  print its `DECISIONS`, then respawn the stopped task with the same brief plus
  `NOTE: paused for a discussion; run git diff --stat first` and continue the loop.

If you are a plain chat session: think with the developer here about the TOPIC; the model and effort apply only to the
pa-session's spawn. Reply in plain prose, recommendation first. When a conclusion would change the plan, phrase it as
the exact `tools/plan_edit.py` command but do not run it until `/proceed`.
