---
name: worklogs-reference-only
description: phase-ends/logs/ are on-demand archives, deliberately outside the Session Start load order — never auto-read them; consult only when researching a past mechanism
metadata:
  type: feedback
---

The archived worklogs in `phase-ends/logs/PhaseLog_<N>.md` (each the frozen final `CURRENT_PHASE.md` of a closed phase) are a granular session-by-session trail — dead-ends, exact addresses/values, mechanism working notes.

**Do NOT read `phase-ends/logs/` at session start.** They are deliberately OUTSIDE the Session Start load order (they don't match the `PhaseEnd_Phase*.md` glob, by design) — auto-reading them would re-bloat context with exactly the detail the `PhaseEnd_Phase<N>.md` synthesis compresses away. The PhaseEnd is the authoritative summary.

**Why:** The granular trail has real archaeological value at near-zero cost, *as long as it stays out of the load order*. **Registry: P8 (the archive step).**

**How to apply:** Consult a worklog ONLY on demand — when researching how a specific past mechanism or decision actually worked. Check the worklog BEFORE reaching for external web research (a prior session may have already solved it). See [[project-governance-system]] and [[capture-knowledge-before-fresh-session]].
