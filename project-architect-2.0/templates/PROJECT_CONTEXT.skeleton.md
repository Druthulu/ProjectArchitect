# {{PROJECT_NAME}} — Project Context & Roadmap

> **Version:** 1.0.0
> **Generated:** {{INSTALL_DATE}}
> **Generation:** Gen1 | **Tech Stack:** {{TECH_STACK}}

---

## For Humans — Quick Guide

This is your project's **permanent constitution** — the vision, decisions, architecture, and roadmap. It is written once and **never edited** (rules and state evolve elsewhere). A fresh Claude Code session auto-loads `CLAUDE.md`, which reads this file first.

**The document system (where everything lives):**
- **This file** — permanent vision/decisions/architecture/roadmap. Never edited.
- **`RULES_REGISTRY.md`** — every rule, full text, recited each session.
- **`phase-ends/`** — the append-only build history (`PhaseEnd_Phase*.md`) + the in-flight `CURRENT_PHASE.md`.
- **`docs/`** — evolvable references (effort map, cookbook, ops-setup, the methodology spec).

**To work on this project:** open Claude Code in the repo and say "continue" — the Session Start Protocol takes over. Governance methodology: `docs/project-architect.md`.

---

## Rules & Protocols

The AI collaboration rules and the Session Start / Phase Start / Phase Boundary protocols are **not** in this file (a permanent-static file would freeze them). They live in:
- **`RULES_REGISTRY.md`** — the canonical, full-text rule set (P/E/H/X/M groups + this project's §E rules + accumulated §F phase rules). Recited every session.
- **`CLAUDE.md`** — the operating protocols (session start, mandatory behavior, phase boundary, reasoning/effort).

**Precedence:** where this permanent file's wording ages out of step with them, `CLAUDE.md` + `RULES_REGISTRY.md` govern; `docs/effort-map.md` governs effort language.

---

## Quick Reference Card

- **Project:** {{PROJECT_NAME}} — {{ONE_LINE_WHAT}}
- **Goal:** {{NORTH_STAR_GOAL}}
- **Definition of done (the milestone gate):** {{DONE_GATE}}
- **Stack:** {{TECH_STACK}}
- **Environment:** see `docs/ops-setup.md`.
- **Current generation:** Gen1 — {{GEN1_SCOPE}}

---

## Project Overview

{{OVERVIEW}}

### Generation Map

- **Gen1 — {{GEN1_NAME}}:** {{GEN1_SCOPE}} *(this roadmap)*
- {{FUTURE_GENERATIONS_SKETCH}}

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

## Build Roadmap

*Each phase produces something runnable and testable, and ends with an observable, machine-checkable **Milestone** (the gate that P9/M1 hold it to). Validation is a dedicated phase; enhancement layers are toggleable and added one at a time.*

### Phase 1 — {{PHASE_1_NAME}}
- [ ] {{TASK}}
- [ ] **Milestone:** {{PHASE_1_MILESTONE}}

### Phase 2 — {{PHASE_2_NAME}}
- [ ] {{TASK}}
- [ ] **Milestone:** {{PHASE_2_MILESTONE}}

{{ADDITIONAL_PHASES}}

---

## Enhancement Backlog
*(Toggleable feature-flagged modules to add one at a time, measured before the next — not scheduled into Gen1 phases yet.)*

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
