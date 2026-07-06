---
name: autonomous-within-phases
description: Two gates per phase (plan approval, milestone confirmation); execute autonomously between them, stopping only for the five enumerated conditions
metadata:
  type: feedback
---

A phase has exactly two human gates: **plan approval** at the start and **milestone confirmation** at the end. Between them, execute the whole phase autonomously — do not stop for per-task permission. Log each task to `CURRENT_PHASE.md` and report at the milestones marked in the plan.

**Stop mid-phase and ask only when** (the five conditions, registry P5): (a) a fix or check fails twice with different claimed root causes; (b) completing the task would violate a rule; (c) a git op would be destructive/irreversible or push without authorization; (d) the plan needs a task added/removed or a toolchain change; (e) blocked on a developer-only action or a dead external dependency.

**Why:** The developer wants momentum within a phase, with control retained at the two decision points that actually matter — not a confirmation prompt after every task. **Registry: P3 / P5.**

**How to apply:** After plan approval, proceed through the tasks, committing each with its `CURRENT_PHASE.md` log line (see [[commit-per-task-user-pushes]]) and briefly reporting results, without waiting for a "go" between tasks. Honor the mid-phase rules check (every 4 tasks) and the milestone gate. Complements [[who-is-dev]] and [[build-tasklist-after-plan-approval]].
