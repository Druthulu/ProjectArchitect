# Phase <N> — <name>        (implements GENERATION_PLAN.md phase <G.n>)
Milestone: <a plain sentence; a command can go with it>
Approved: <date>   Planner: <model/effort>   Plan-hash: <sha>

<!-- The three header lines above are written by tools/plan_edit.py approve and frozen there.
     plan_edit.py is the only writer of this file at all: statuses, Changes lines,
     add-task, reopen. Everything above `## Tasks` is immutable after approval; a structural
     change goes through REPLAN.md → Claude drafts the plan again; its approval writes a new
     approved plan and archives this one as PHASE_PLAN.v<k>.md. -->

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
<!-- open calls reserved for the developer; Claude checks this section first -->

## Triage
<!-- one line per seed `Deferred: <id> <text>` item; approve refuses a missing id and writes each
     decision to the discussion index (planned:<phase>/T<n> · deferred:<phase> · dropped):
       - <id>: T<n> [-- reason]
       - <id>: postpone: <phase> -- <reason>
       - <id>: drop -- <reason>
     `- (none)` when the seed lists no Deferred: items. -->

## Tasks

<!-- Task-line grammar (the loose line):
       - T<n> | title: <a few words> | done-when: <observable>
     Everything else is optional at its default; parts are separated by ` | `:
     Status    : positional, after the id: done · next · queued · blocked · superseded (default queued)
     Agent     : positional, after the status: pa-expert (default; the task's effort picks its model)
     Keys      : coder: opus55|none (default opus55) · effort: medium|high (default medium)
                 files: <paths, comma-separated> · verify: <command> · reads: <summaries/ids>
                 deps: <task ids> · est-ctx: <n>k · review: yes|no (default no) · wait-for: <task id or batch>
     IDs are immutable; a reopened T3 becomes T3.1. Indented continuation lines belong to the
     task above them. -->

- T1 | title: the parser | done-when: …
  <optional indented continuation lines belong to the task above>
- T2 | queued | pa-expert | title: … | effort: high | files: a.py, b.py | verify: <cmd> | done-when: …

## Risks
<!-- what could make this phase miss its milestone; the detection for each -->

## Changes
<!-- appended by plan_edit.py append-change; never hand-edited -->
- <date> <router|developer>: <what> — <why>
