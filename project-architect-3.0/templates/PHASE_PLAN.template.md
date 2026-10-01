# Phase <N> — <name>        (implements GENERATION_PLAN.md phase <G.n>)
Milestone: <machine-checkable gate> — verified by: <command or check>
Approved: <date>   Planner: <model/effort>   Plan-hash: <sha>

<!-- The three header lines above are written by tools/plan_edit.py approve and frozen there.
     plan_edit.py is the only writer of this file at all: statuses, Changes lines,
     add-task, reopen. Everything above `## Tasks` is immutable after approval; a structural
     change goes through REPLAN.md → planner-phase, which writes a new approved plan and
     archives this one as PHASE_PLAN.v<k>.md. -->

## Context
<!-- what exploration found: architecture, key files/methods (file:line), constraints, gotchas,
     generated-vs-hand-edited. The expert reads this section and its own task entry, nothing else. -->

## Rationale
<!-- why this shape; alternatives rejected and why -->

## Interfaces
<!-- signatures / record shapes for every entry point the tasks touch, with file:line.
     A task that edits an interface not listed here returns `question`. -->

## Cookbook
<!-- entry ids the tasks must consult: C0187, C0231 -->

## Research
<!-- report ids consulted while planning: R24-003 (subject), R24-004 (subject) -->

## Developer decides
<!-- open calls reserved for the developer; the critic checks this section first -->

## Triage
<!-- one line per seed `Deferred: <id> <text>` item; approve refuses a missing id and writes each
     decision to the discussion index (planned:<phase>/T<n> · deferred:<phase> · dropped):
       - <id>: T<n> [-- reason]
       - <id>: postpone: <phase> -- <reason>
       - <id>: drop -- <reason>
     `- (none)` when the seed lists no Deferred: items. -->

## Tasks

<!-- Task-line grammar. Three positional fields, then order-insensitive `key: value` pairs
     separated by ` | `:
       - <id> | <status> | <agent> | <key>: <value> | <key>: <value> | …
     Positional : id | status | agent
     Statuses  : done · next · queued · blocked · superseded
     Agent     : expert-opus55 (default, medium) · expert-fable (effort: high: only for a judgment no test can arbitrate, at most one task in five, named in ## Rationale; a line naming the retired expert-fable-high runs expert-fable) · each has a 5m-TTL twin, expert-opus55-5m / expert-fable-5m: the 1h agent is the default, the -5m twin when `tools/expert_ttl.py` prints 5m
     Keys      : title: <a few words naming the task; the statusline and the expert's label show it>
                 coder: opus55|none (sonnet accepted in old plans) · effort: medium|high · files: <paths, comma-separated>
                 done-when: <observable> · verify: <command the expert runs before done>
                 reads: <summaries/ids or —> · deps: <task ids or —> · est-ctx: <n>k
                 review: yes|no · wait-for: <task id, batch name, or —>
     IDs are immutable; a reopened T3 becomes T3.1. Indented continuation lines belong to the
     task above them. -->

- T1 | next   | expert-opus55 | title: the parser | coder: opus55 | effort: medium | files: a.py, b.py | done-when: … | verify: <cmd> | reads: — | deps: — | est-ctx: 80k | review: no | wait-for: —
  <optional indented continuation lines belong to the task above>
- T2 | queued | expert-fable | title: … | coder: opus55 | effort: high | …

## Risks
<!-- what could make this phase miss its milestone; the detection for each -->

## Changes
<!-- appended by plan_edit.py append-change; never hand-edited -->
- <date> <router|critic|developer|planner>: <what> — <why>
