# CURRENT_PHASE — Phase [N]: [Phase Name]

> The in-flight phase working file: the approved plan, the per-task checkpoint log, and blockers. **Copied from this template at Phase Start** (so the template is never overwritten); updated after every task; it is the crash/compaction recovery point and is in the Session Start load order. At phase close it is absorbed into `PhaseEnd_Phase[N].md` and `git mv`'d to `phase-ends/logs/PhaseLog_[N].md` (outside the load order). **Work accumulates commit-by-commit** — each task commits its changes together with its checkpoint entry here (P4), so this file never lags the code, and any task boundary is a clean handoff to a fresh session.

- **Started:** [date]
- **Effort baseline:** [xHigh / …] — per-task effort + context bucket annotated below (E1)
- **Context budget:** [projected checkpoint point(s) read off the plan's `ctx:` tags, e.g. "likely reset after T4, ~45%"] — the reset bands and decision rule live in `docs/effort-map.md`
- **Plan approved:** [date] — approved plan-mode plan: [pointer, if kept]
- **Baseline state:** [the observable starting point — e.g. current test-gate status, build hash, %-complete metric]

## Milestone (the phase's machine-checkable gate)

[The single observable, machine-checkable outcome that ends this phase — the M1/P9 gate. e.g. "`make check` green AND the new endpoint round-trips a fixture" / "the level loads and the character reaches the exit under the test-input script".]

## Task checklist

*Mark `[x]` done · `[~]` in progress · `[ ]` pending. Annotate each task with its effort level and a coarse **main-session context-cost bucket** `ctx:S|M|L|U` (Small / Medium / Large / Unpredictable — how much this task will grow THIS session's context, which drives reset decisions). Note: a breadth task routed to sub-agents is `ctx:S` even when large in total work, because the work happens in the isolated agents; a deep in-session debugging task is often `ctx:L` or `ctx:U`. E1.*

- [ ] T1 — [task] *(effort · ctx:S|M|L|U)*
- [ ] T2 — [task] *(effort · ctx:S|M|L|U)*
- [ ] …

## Current task

**NOW:** [the single task in progress]
**NEXT:** [the following task — or, when every checklist task is done but the phase milestone is not yet met, or when the previous session CURRENT_PHASE says to start with plan mode, write `REPLAN (Plan mode + Max)` so a fresh session prompts for a continuation plan within this phase instead of resuming execution]
**Last rules check:** [after which task — P6, every 4 tasks]

## Per-task checkpoint log

*On completing each task, append a rich checkpoint (2 to 4 sentences, not one line): what the task did, the key decision or WHY, any deviation from the plan, and any gotcha or finding worth remembering. Write every entry as if this task is the last thing before a fresh session picks the phase up, because it might be — the final session synthesises the PhaseEnd's what-and-why from this log, the commits, and the code, so the reasoning that would otherwise live only in this session's conversation has to be captured here now (X5). Keep it concise: push long evidence, logs, or code dumps into the commit body or the cookbook, never here, because this file reloads at every session start. Each entry commits with that task's changes (P4).*

- **T1** — [what it did; the key decision and why; any deviation; any finding]. Verified: [the observable result]. Context after: [~% or the ctx bucket that actually landed].

## Blockers / open questions

- [any P5 stop-condition hit, developer-only dependency, or open decision — or "none"]

## Notes / deviations

- [anything worth carrying into the PhaseEnd's Deviations table]
