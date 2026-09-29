# GenerationEnd — Generation <G>: <name>

<!-- Script-assembled by tools/genend_index.py assemble <G> from GENERATION_PLAN.md and every
     phase-ends/PhaseEnd_Phase<G>.*.md. {{ASSEMBLED:…}} filled by the script; {{AUTHORED:recap}}
     written by the closer of the last phase (its brief carries a GENERATION END addendum).
     `genend_index.py lint` fails while any placeholder remains. -->

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
<!-- Closer of the last phase: 5–8 sentences. What the generation set out to do, what it
     actually delivered, what changed course and why, what the next generation inherits. -->
