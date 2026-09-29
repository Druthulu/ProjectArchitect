# phase-ends/ — the build record

Two things live here: the **open phase**, as a folder of small files, and the **closed phases**,
as one assembled synthesis each plus their archived folders. Nothing in this directory except the
seed pointer is in any session's load order. Everything is reachable by grep on an index.

## Layout

```
phase-ends/
  README.md                  this file
  TASK_INDEX.md              cumulative, one line per task, every phase
  RESEARCH_INDEX.md          cumulative, one line per research report, every phase
  LEGACY_INDEX.md            one line per pre-PA3 PhaseEnd (written at migration)
  PhaseEnd_Phase<N>.md       one per closed phase, script-assembled + an authored recap
  GenerationEnd_<G>.md       one per closed generation
  current/                   the open phase
    PHASE_PLAN.md            frozen at approval; tools/plan_edit.py is the only writer
    INBOX.md REPLAN.md REVIEW.md TASK_PROGRESS.md    transient; exist only while in use
    tasks/INDEX.md  tasks/T<n>.md                    summaries, ≤150 lines, written once
    logs/T<n>.md  logs/T<n>.c<k>.md  logs/T<n>.progress<k>.md   full expert log · coder runs · archived handoffs
    research/INDEX.md  research/R<N>-<nnn>.md        one report per question answered
    discussions/INDEX.md  discussions/D<n>.md        /proceed records; inbox-<ts>.md, review-<ts>.md
  phase-<N>/                 an archived current/, moved whole by phaseend_index.py --archive (git mv)
  logs/PhaseLog_*.md         PA2 worklogs kept from migration; never read automatically
```

Flat per phase, not per task: a report written for Phase 12/T3 is wanted by Phase 20/T5, so the
index carries the task as a column instead of the folder binding the file to the wrong key.

## In the load order

Nothing here. A session starts from `.run/seed.md`, which the launcher writes: pointers, the task
lines of the open plan, the next task, and whether `INBOX.md` exists. No PhaseEnd, no plan body,
no log, no report is read at session start, ever.

## On demand

| Want | Do |
|---|---|
| what a phase produced | read `PhaseEnd_Phase<N>.md` (one file, already a synthesis) |
| which phase touched a subject | `grep -i <subject> phase-ends/TASK_INDEX.md` |
| what was already researched | `grep -i <subject> phase-ends/RESEARCH_INDEX.md` → the report id → its file |
| what one task did | `phase-ends/*/tasks/T<n>.md` (the summary; ≤150 lines) |
| how it actually went | `phase-ends/*/logs/T<n>.md` — dispatch a retriever; never Read it whole |
| pre-PA3 history | `LEGACY_INDEX.md` → `logs/PhaseLog_*.md` |

Experts cite report ids and never read the bodies. The router reads none of these files at all.
A retriever-digest reads a full log when a question needs it, and writes what it found to
`research/` so the next one does not start blind.

## Numbering

Phases are numbered by `GENERATION_PLAN.md` (`<G>.<n>`); the PhaseEnd file uses the project's own
phase number. Decimal sub-phases and migration phases (`24.5`) are fine: every consumer sorts with
`sort -V` semantics, so `Phase5` precedes `Phase5.5`. Record a numbering anomaly in the affected
PhaseEnd; never renumber history.
