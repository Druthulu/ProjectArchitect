# Generation {{GENERATION}} — {{GENERATION_NAME}}

Goal: <one line — what this generation delivers>
Done-criteria: <observable, machine-checkable; the union of the phase milestones>

## Phases

<!-- Line grammar (parsed by launch.py, genend_index.py, plan_edit.py gen-close):
     - <G>.<n> <name> | milestone: … | scope: … | depends: — | status: open|closed | phase-end: phase-ends/PhaseEnd_Phase<N>.md
     Order = build order. `depends:` names phase ids or `—`. Only plan_edit.py gen-close|gen-open flips `status:`. -->

- {{GENERATION}}.1 <name> | milestone: <machine-checkable gate> | scope: <one line> | depends: — | status: open | phase-end: phase-ends/PhaseEnd_Phase{{GENERATION}}.1.md
- {{GENERATION}}.2 <name> | milestone: … | scope: … | depends: {{GENERATION}}.1 | status: open | phase-end: phase-ends/PhaseEnd_Phase{{GENERATION}}.2.md

## Ordering rationale

- <why this phase precedes that one; what each phase's output unblocks>

## Standing constraints

- <constraints binding on every phase of this generation: stack, oracles, non-goals>

## Changes

<!-- - <date> planner|developer: <what> — <why> -->
