# {{PROJECT_NAME}} — Project Context & Roadmap

> **Version:** 1.0.0
> **Generated:** {{INSTALL_DATE}}
> **Generation:** {{GENERATION}} | **Tech Stack:** {{TECH_STACK}}

---

## For Humans — Quick Guide

This is your project's **permanent constitution** — the vision, decisions, architecture, and the
generation map. Written once, **never edited**. State, rules and the live plan evolve elsewhere.

**The document system (where everything lives):**

| File | What it is | Who edits it |
|---|---|---|
| **This file** | permanent vision, decisions, architecture, generation map | nobody, after generation |
| `CLAUDE.md` | ≤300 tokens: roles, pointers, the always-rules | the installer |
| `project-architect` skill | the standing rules every agent loads | a planner session |
| `HOW_WE_WORK.md` | developer profile, tools, paths, build/test, standing decisions | any task that changes one (H7) |
| `GENERATION_PLAN.md` | the live roadmap: phases, milestones, open/closed | the planner; `plan_edit.py gen-close` |
| `phase-ends/current/PHASE_PLAN.md` | the approved plan of the open phase | `tools/plan_edit.py` only |
| `phase-ends/` | PhaseEnds, GenerationEnds, task/research indexes | scripts at each close |
| `rules/` `cookbook/` `docs/ops/` | full rule texts, techniques, ops detail — one file per entry, found by grep on the INDEX | the task that earns the entry |

**To work on this project:** run `{{PY}} tools/launch.py` in the repo. It picks the session kind
(planner, router, review), writes the seed, and starts Claude. Governance methodology:
`docs/project-architect.md`.

---

## Rules & Protocols

Not in this file. A permanent-static document would freeze them.

- Standing rules for every agent: the **`project-architect`** skill (`.claude/skills/project-architect/SKILL.md`).
- Full rule texts, one file per rule: **`rules/INDEX.md`** → `rules/<id>.md`.
- Pointers and the always-rules: **`CLAUDE.md`**.

**Precedence:** where this file's wording ages out of step, the project-architect skill and `rules/` govern.

---

## Quick Reference Card

- **Project:** {{PROJECT_NAME}} — {{ONE_LINE_WHAT}}
- **Goal:** {{NORTH_STAR_GOAL}}
- **Definition of done (the milestone gate):** {{DONE_GATE}}
- **Stack:** {{TECH_STACK}}
- **Environment:** see `HOW_WE_WORK.md` and `docs/ops/INDEX.md`.
- **Current generation:** {{GENERATION}} — {{GENERATION_SCOPE}}

---

## Project Overview

{{OVERVIEW}}

### Project Assumptions

{{ASSUMPTIONS}}

---

## Lessons Learned / Known Risks
*(For a migrated or existing-code project: pre-existing debts, dead-ends already tried, and risks discovered from the codebase go here — document reality, not aspiration. Omit if greenfield with none.)*

{{LESSONS_AND_RISKS}}

---

## Project Philosophy

**North Star (one sentence):** {{NORTH_STAR}}

{{PHILOSOPHY}}

---

## Key Decisions

| Decision | Choice | Rejected alternative | Why |
|---|---|---|---|
| {{DECISION}} | | | |

---

## Core Logic / Strategy

{{CORE_LOGIC}}

### Component Inventory

{{COMPONENT_INVENTORY}}

---

## Safety / Guardrails / Error Handling

{{SAFETY}}

---

## Architecture

### Project Structure

```
{{PROJECT_STRUCTURE_TREE}}
```

### Services / Dependency Wiring

{{SERVICE_ARCHITECTURE}}

### Config / Settings

{{CONFIG_STRUCTURE}}

---

## Testing & Validation Strategy

{{TESTING_STRATEGY}}

**Pass criteria:** {{PASS_CRITERIA}}

---

## Generation Map

*The permanent sketch: what each generation is for, and the milestone-shaped phases it is expected
to need. Milestones only — no task checklists. The live phase list (names, scope, dependencies,
open/closed, PhaseEnd links) is `GENERATION_PLAN.md`, and the tasks of the open phase are
`phase-ends/current/PHASE_PLAN.md`. Where the two disagree, the live plan governs.*

### Generation {{GENERATION}} — {{GENERATION_NAME}}
Scope: {{GENERATION_SCOPE}}
Done when: {{GENERATION_DONE}}

- **Phase {{GENERATION}}.1 {{PHASE_1_NAME}}** — milestone: {{PHASE_1_MILESTONE}}
- **Phase {{GENERATION}}.2 {{PHASE_2_NAME}}** — milestone: {{PHASE_2_MILESTONE}}
- {{ADDITIONAL_PHASES}}

Each phase produces something runnable and testable and ends at an observable, machine-checkable
milestone (the gate P9/M1 hold it to). Validation is a phase of its own; enhancement layers are
toggleable and added one at a time.

---

## Enhancement Backlog
*(Toggleable feature-flagged modules to add one at a time, measured before the next — not scheduled into a generation yet.)*

{{ENHANCEMENT_BACKLOG}}

---

## Future Generations
*(Evolutionary leaps, not version bumps — sketched, not phase-detailed.)*

{{FUTURE_GENERATIONS}}

---

## What Success Looks Like

{{SUCCESS_DEFINITION}}

---

## Feature & Architecture Inventory

{{FEATURE_INVENTORY}}

---

## Data Sources / External Dependencies

{{DATA_SOURCES}}

---

## Libraries / Dependencies

{{LIBRARIES}}

---

## Parking Lot
*(Dream features, someday/maybe ideas, out-of-scope thoughts — captured so they're not lost, explicitly not committed.)*

{{PARKING_LOT}}

---

## Notes for Future Phases

{{NOTES}}
