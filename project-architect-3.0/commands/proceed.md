---
description: End discussion mode; record decisions; continue
allowed-tools: Bash({{PY}} tools/discussion.py:*)
disable-model-invocation: true
---
!`{{PY}} tools/discussion.py off --new-record`
Discussion mode is OFF (fallback: run `{{PY}} tools/discussion.py off --new-record` yourself). Write the decisions to the
discussion file printed above (`phase-ends/current/discussions/D<n>.md`: one decision per line, then the plan edits as
exact `plan_edit.py … --by developer` commands, then open items).

If you are the `discuss` agent: return `DECISIONS · EDITS · RECORD` now (its exact shape is in your instructions) and end.
If you are the pa-session and a discuss agent is running: do nothing else, its return carries the decisions. Otherwise
run the plan_edit.py commands you wrote, commit them, and continue exactly where you were (router: the loop; expert or
planner: the task or plan).
