# PhaseEnd — Phase <N>: <name> (implements Gen <G.n>)

<!-- Script-assembled by tools/phaseend_index.py assemble <N> from phase-ends/current/:
     PHASE_PLAN.md, tasks/*.md, research/INDEX.md, discussions/*.md. Every {{ASSEMBLED:…}}
     block is filled by the script — never hand-written. The two {{AUTHORED:…}} blocks come
     from current/RECAP.md (Claude's phase report, written by the mod at the close) and are the
     only prose in the file. -->

Approved: {{ASSEMBLED:approved}}   Closed: {{ASSEMBLED:closed}}   Planner: {{ASSEMBLED:planner}}
Tasks: {{ASSEMBLED:task-counts}}

## Milestone
{{ASSEMBLED:milestone}}
Confirmed by the developer at the close.

## Tasks
{{ASSEMBLED:tasks}}
<!-- one line per task: T<n> | <status> | <Done: line> | tasks/T<n>.md | logs/T<n>.md | commit <sha> -->

## Decisions that still bind
{{ASSEMBLED:binding-candidates}}
<!-- every `binding:` line from tasks/*.md, verbatim, with its task id -->

{{AUTHORED:binding}}

## Rules proposed
{{ASSEMBLED:rule-candidates}}
<!-- every `Rule candidate:` line, with its task id; adjudicated at the next plan approval -->

## Cookbook entries added
{{ASSEMBLED:cookbook}}

## Research
{{ASSEMBLED:research}}
<!-- research/INDEX.md lines for this phase; ids only, never content -->

## Audit
{{ASSEMBLED:audit}}
<!-- seed median/max/n, previous phase, growth, suspects with sizes/deltas, governed files -->

## Discussions
{{ASSEMBLED:discussions}}

## Deferred
{{ASSEMBLED:deferred}}
<!-- superseded and blocked tasks + unconsumed `Next task needs:` lines + deferred discussion items + inbox later: lines -->

## Changes
{{ASSEMBLED:changes}}

## Plain-English Recap
{{AUTHORED:recap}}
