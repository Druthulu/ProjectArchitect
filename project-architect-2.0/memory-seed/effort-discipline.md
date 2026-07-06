---
name: effort-discipline
description: Effort doctrine — xHigh baseline, Max as a scalpel for deep tasks (always for plan mode), Ultracode for breadth only; Max/Ultracode session-only, xHigh persists
metadata:
  type: feedback
---

The effort doctrine (the default posture; `docs/effort-map.md` governs and holds the per-phase detail):

- **xHigh** for most tasks — the persistent baseline.
- **Max** for deep tasks only — a scalpel for phase planning, PhaseEnd synthesis, architecture, non-obvious debugging, and any judgment whose silent error would invalidate everything downstream. **Plan mode is always Max.**
- **Ultracode / Workflow fan-out** for breadth tasks only (the same analysis across many independent items) — never a standing mode.

Mechanics: `/effort` sets depth (`low..Max`); Ultracode is NOT a deeper level (it caps depth at xHigh and adds parallel agents). **Max and Ultracode are session-only** — they revert to the xHigh baseline each session; re-apply deliberately.

**Why:** Max on routine work adds latency/overthinking with no quality gain; a standing Ultracode caps deep work at xHigh. Reserving each control for its right shape gets both depth and breadth where they matter. **Registry: §B / E1.**

**How to apply:** Recommend xHigh for routine execution; prompt for Max when a deep task or plan-mode session begins; prompt for Ultracode when breadth-shaped work appears — always pausing and waiting for the actual `/effort` toggle (see [[effort-prompt-ultracode-on-breadth]]).
