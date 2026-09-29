# Phase 24 — Ground-Texture Expansion        (implements GENERATION_PLAN.md phase 3.4)
Milestone: every biome mask builds and the gate is green — verified by: python -m unittest discover tests
Approved: 2026-09-12   Planner: claude-fable-5-1[1m]/xhigh   Plan-hash: 0000000000000000000000000000000000000000000000000000000000000000

## Context
The mask builder handles two biomes; the generation needs four. Nothing else in the
pipeline changes this phase.

## Rationale
Masks first, blending second: the blend weights cannot be checked until every mask exists.

## Interfaces
- `build_mask(biome: str, seed: int) -> Mask` — pa/textures.py:41
- `Mask = {"biome": str, "weights": list[float]}` — pa/textures.py:12

## Cookbook
C0187, C0203

## Research
R24-001 (mask seams), R24-002 (weight normalisation)

## Developer decides
- Whether the fourth biome ships this generation or the next.

## Tasks
- T1 | done       | expert-opus55     | coder: opus46 | effort: medium | title: mask builder for four biomes | files: pa/textures.py, pa/masks.py | done-when: build_mask returns a Mask for every biome | verify: python -m unittest tests.test_masks | reads: — | deps: — | est-ctx: 80k | review: no | wait-for: —
- T2 | done       | expert-fable-high | coder: sonnet | effort: high | title: blend weights | files: pa/blend.py | done-when: weights sum to 1.0 within 1e-6 | verify: python -m unittest tests.test_blend | reads: tasks/T1.md | deps: T1 | est-ctx: 120k | review: yes | wait-for: —
- T3 | superseded | expert-opus55     | coder: none | effort: medium | title: seam report | files: — | done-when: the seam report names every seam | verify: python tools/analysis/seams.py --check | reads: tasks/T1.md | deps: T1 | est-ctx: 40k | review: no | wait-for: —
- T3.1 | next     | expert-opus55     | coder: opus46 | effort: medium | title: seam report, per-biome | files: pa/seams.py | done-when: the report lists seams per biome and the gate stays green | verify: python -m unittest tests.test_seams | reads: tasks/T1.md, tasks/T2.md | deps: T2 | est-ctx: 60k | review: no | wait-for: —
  Reopened from T3: the first report collapsed the four biomes into one row, which
  hides the only seam class the milestone cares about.
  Keep the old output format; add a biome column.
- T4 | queued     | expert-fable      | coder: opus46 | effort: high | title: overnight bake | files: pa/bake.py | done-when: the overnight bake finishes and the gate is green | verify: python -m unittest tests.test_bake | reads: tasks/T3.1.md | deps: T3.1 | est-ctx: 150k | review: yes | wait-for: tools/run.sh --wait bake --max 3000
- T5 | blocked    | expert-opus55     | coder: none | effort: medium | title: bake report | files: docs/ops/bake.md | done-when: the report quotes the bake numbers | verify: grep -c seams docs/ops/bake.md | reads: tasks/T4.md | deps: T4 | est-ctx: 30k | review: no | wait-for: —

## Risks
- The bake is long; T4 carries a wait-for so the router does not sit on it.

## Changes
- 2026-09-12 planner: plan approved — four biomes, five tasks
- 2026-09-13 router: T3 superseded by T3.1 — the seam report hid the seam class the milestone needs
