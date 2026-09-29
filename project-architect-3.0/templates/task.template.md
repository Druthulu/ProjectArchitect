# T<n> — <title>

<!-- Task summary: written once, at the end of the task. ≤150 lines, no tool output, no tables.
     This is what the next expert reads. The full record goes to logs/T<n>.md.
     tools/task_log.py finish lints this file; `plan_edit.py set-status <T> done` refuses
     without the `Verified:` line. -->

Status: done | expert: expert-opus55 | ctx-at-completion: 142k | commit: abc123 | coder runs: c1 opus55 done (def456), c2 —
Done: <≤2 lines — what is now true that was not before>
Files:
- <path> — <one line>
Decisions:
- binding: <decision that outlives this phase; routed at phase end: a norm → `rules_add.py add`, a contract → the
  product doc and its test, an environment fact → a Tools-table row or `docs/ops/`, a scope matter → a
  `Next task needs:` line for the planner>
- <decision local to this task>
Deviations:
- <what the plan said, what was done instead, why>
Findings:
- <fact discovered that the next task needs>
Gotchas:
- generalizable: <technique worth a cookbook entry — cookbook_add.sh>
- workflow: <repeatable procedure worth a skill — skill_add.py>
Research: R<N>-<nnn> (<subject>), R<N>-<nnn> (<subject>)
Rule candidate: <optional, one line; adjudicated at the next planner session via rules_add.py>
Next task needs: <what T<n+1> must know or must not repeat>
Recommended: <only if status != done — the one next action>
Verified: <verify command> → <result>
Review:
  ran: <commands run and hosts>
  results: <paths to result files>
  seen: <the Done: line from the summary>
  decisions:
  - <decision> — Recommended: <option>
  edits:
  - <plan_edit.py command>
<!-- task_log.py review T<n> assembles REVIEW.md from this block -->
Full log: phase-ends/current/logs/T<n>.md
