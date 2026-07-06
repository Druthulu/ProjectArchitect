---
name: capture-knowledge-before-fresh-session
description: Write context-dependent knowledge artifacts (cookbook entries, verified findings, distillations, doc corrections) DURING the session that produced them, before any handoff
metadata:
  type: feedback
---

Findings whose quality depends on the current session's **full live context** — cookbook entries, verified diagnoses with their evidence, distillations, corrections to existing docs — must be written **NOW, before ending or handing off to a fresh session**. A fresh session inherits only the compressed `CURRENT_PHASE.md` / PhaseEnd summaries and loses the rich detail: the exact diffs, the mechanism, the evidence, the dead-ends already tried.

**Why:** A summary-only future session produces a thin or wrong artifact because the nuance is gone. Doing the capture in-session, while the context is live, produces a far richer and correct result. **Registry: X5.**

**How to apply:** At a session boundary, split remaining work into (a) **context-dependent knowledge capture → do it NOW** (cookbook/findings/distillation write-ups, doc corrections, the "why this is irreducible" verdicts with their evidence) and (b) **mechanical/continuable work → safe to defer** (build the next tool, run the next batch). The handoff file carries pointers + the headline; the durable detail lives in the cookbook/docs, authored in-session. Extends [[project-governance-system]] and the flywheel (X4).
