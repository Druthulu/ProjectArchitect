# phase-ends/ — the append-only build history

This directory is the project's living record. Three artifact types, distinct roles:

## `PhaseEnd_Phase[N].md` — the synthesis (append-only, kept forever)

One per completed phase, written at the confirmed milestone (`PhaseEnd.template.md`). **In the Session Start load order** — read in numeric order every session to reconstruct build history, current version, and each phase's added rules. Never edited after commit; never discarded. A complete synthesis in its own right — written as if the worklog will never be read.

## `CURRENT_PHASE.md` — the in-flight working file (transient)

Copied from `CURRENT_PHASE.template.md` at Phase Start; holds the approved plan, per-task progress log, and blockers. **In the load order** while it exists (it doesn't exist between phases). The crash/compaction recovery point — updated after every task, committed with that task's work (P4). At phase close it is absorbed into the PhaseEnd and archived (below).

## `logs/PhaseLog_[N].md` — archived worklogs (on-demand only)

At phase close, `CURRENT_PHASE.md` is `git mv`'d here (converted to a posterity log, never deleted). **Deliberately OUTSIDE the Session Start load order** — the filename doesn't match the `PhaseEnd_Phase*.md` glob, by design. Auto-reading these would re-bloat context with exactly the granular detail the PhaseEnd compresses away. **Consult on demand only** — when researching how a specific past mechanism or decision actually worked. Check a worklog before reaching for external research (a prior session may have already solved it).

## Load order summary (Session Start Protocol)

```
PROJECT_CONTEXT.md  →  RULES_REGISTRY.md  →  phase-ends/PhaseEnd_Phase*.md (version-sorted)  →  phase-ends/CURRENT_PHASE.md (if present)  →  docs/effort-map.md
                                                                                                ^ NEVER phase-ends/logs/
```

**Read PhaseEnds in version/natural numeric order (`sort -V` semantics), NOT lexical/glob-default order.** With decimal sub-phases this matters: lexically, `PhaseEnd_Phase5.md` sorts *after* `PhaseEnd_Phase5.9.md` (because after "5." the char 'm' > '9'), which would load Phase 5 out of sequence. Version sort puts `Phase5` before `Phase5.5` correctly. Any project with an integer phase plus decimal sub-phases hits this.

## Numbering

Phases are numbered by the roadmap; half-phases (`Phase3_5` / `Phase3.5`) and generation labels are fine as long as numeric sort still reflects chronology. A migration into this system is recorded as a normal phase in the project's own numbering (see `docs/project-architect.md` Mode 4). Record any numbering anomaly (a skipped or inserted phase) in the affected PhaseEnd rather than renumbering history.
