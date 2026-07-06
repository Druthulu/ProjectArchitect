---
name: project-governance-system
description: This repo governs its own AI sessions via CLAUDE.md → PROJECT_CONTEXT.md → RULES_REGISTRY.md → phase-ends/; defer to that system, don't duplicate it
metadata:
  type: project
---

The **{{PROJECT_NAME}}** repo governs all AI-collaboration sessions through its own in-repo context system (installed by Project Architect 2.0): `CLAUDE.md` (auto-loaded, mandates the load order + protocols) → `PROJECT_CONTEXT.md` (permanent static constitution) → `RULES_REGISTRY.md` (every rule, full text) → `phase-ends/` (living build history + in-flight `CURRENT_PHASE.md`) → `docs/` (evolvable references). **Defer to that system; do not duplicate its content in this memory directory.**

**Why:** The repo-resident context system is the single source of truth and is versioned with the code; memories are a thin, fast-loading complement (developer preferences, working agreements), not a second copy of the rules.

**How to apply:** At session start, follow `CLAUDE.md`'s Session Start Protocol (load order, recite the registry in full, state the next task, wait). When a durable fact belongs to the project, put it in the constitution/registry/PhaseEnd — not here. Keep this directory to `who-is-dev` + working-agreement memories. See [[who-is-dev]].
