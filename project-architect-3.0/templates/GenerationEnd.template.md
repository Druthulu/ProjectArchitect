# GenerationEnd — Generation <G>: <name>

<!-- Script-assembled by tools/genend_index.py assemble <G> from GENERATION_PLAN.md and every
     phase-ends/PhaseEnd_Phase<G>.*.md. {{ASSEMBLED:…}} filled by the script; {{AUTHORED:recap}}
     comes from `## Generation Recap` in the last phase's RECAP.md (Claude's report at the close). -->

Goal: {{ASSEMBLED:goal}}
Done-criteria: {{ASSEMBLED:done-criteria}}
Opened: {{ASSEMBLED:opened}}   Closed: {{ASSEMBLED:closed}}   Phases: {{ASSEMBLED:phase-count}}

## Phases
{{ASSEMBLED:phases}}
<!-- one line per phase: <N> | <name> | milestone: … | closed <date> | phase-ends/PhaseEnd_Phase<N>.md -->

## Decisions that still bind
{{ASSEMBLED:binding-union}}
<!-- union of every PhaseEnd ## Decisions that still bind, deduplicated, phase id kept -->

## Deferred
{{ASSEMBLED:deferred-union}}
<!-- union of every PhaseEnd ## Deferred, minus anything a later phase closed -->

## Plain-English Recap
{{AUTHORED:recap}}
