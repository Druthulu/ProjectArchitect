---
name: build-tasklist-after-plan-approval
description: On plan approval AND on resuming an in-flight phase, build the harness task list (one TaskCreate per plan task) so the developer has a live progress monitor
metadata:
  type: feedback
---

Build the harness **task list** (one TaskCreate per plan task) at two moments, both **before doing the work**:

1. **On plan approval** (the Phase Start gate / ExitPlanMode) — right after writing `CURRENT_PHASE.md`, create the full task set from the approved plan (T1, T2, …), mark the first `in_progress`, and proceed.
2. **On resuming an in-flight phase in a fresh session** — when the Session Start Protocol finds an existing `CURRENT_PHASE.md`, automatically rebuild the task list from its checklist (completed tasks `completed`, the current task `in_progress`) so the monitor is restored. Do this without being asked — the developer used to have to request "build task list from CURRENT_PHASE" every resume. Then branch: if the plan still has unfinished tasks, prompt for Auto-Mode + the next task's effort and resume; if the plan is exhausted but the phase milestone is unmet (the `NEXT` pointer says replan, or all tasks are done), prompt for Plan mode + Max and replan the remaining work within the same phase (a fresh session may need a new plan when the previous one runs out but the phase continues).

Keep statuses current thereafter (mark `completed` as each passes; add follow-ups discovered mid-work).

**Why:** The developer monitors phase progress via the task list (TaskList / the spinner), not the prose plan or `CURRENT_PHASE.md`. Rebuilding it on resume (not just on first approval) makes intra-session phase resumption one step instead of a manual request each time. **Registry: P3.**

**How to apply:** The task list is in addition to — not instead of — the `CURRENT_PHASE.md` per-task log (the committable crash-recovery trail); keep the two in lockstep. See [[autonomous-within-phases]] and [[effort-prompt-ultracode-on-breadth]] (the effort prompt on resume).
