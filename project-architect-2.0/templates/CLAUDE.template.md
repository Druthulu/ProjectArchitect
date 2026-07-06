# CLAUDE.md — {{PROJECT_NAME}}

{{PROJECT_TAGLINE}}

> This file is auto-loaded by Claude Code every session. It carries the **protocols** and points at the **rules**. The single canonical rule set lives in `RULES_REGISTRY.md`; the permanent project constitution is `PROJECT_CONTEXT.md`. Where this file or the registry differs from the (never-edited) constitution's older wording, **this file and the registry govern** (the precedence chain). Governance methodology reference: `docs/project-architect.md`.

## Session Start Protocol — Do This First, Every Session

Before doing any work, read these files in this order:

1. `PROJECT_CONTEXT.md` in full (the permanent constitution: vision, decisions, architecture, roadmap).
2. `RULES_REGISTRY.md` in full (every rule — you will recite these).
3. Every `phase-ends/PhaseEnd_Phase*.md` in **version/natural numeric order** (`sort -V` — `Phase5` before `Phase5.5`, NOT lexical order, which mis-sorts decimal sub-phases). The build history. **Do NOT read `phase-ends/logs/`** — those are on-demand worklog archives, deliberately outside this load order.
4. `phase-ends/CURRENT_PHASE.md` if it exists (the in-flight phase state).
5. `docs/effort-map.md` (the effort doctrine + this project's per-phase effort map).
6. {{DOMAIN_SESSION_START_EXTRAS}}

After reading, do a preflight appropriate to the NEXT task (e.g. `git status`; verify any external oracle/service the next task needs with a cheap call, per X3). Then state — and nothing else:

- Current phase number and name.
- Which tasks in the current phase are already complete.
- Which single task is **NEXT**.
- The recommended **effort level** for that next task per `docs/effort-map.md`, and confirm the developer has it set (the Effort-map check, E1).
- **Every rule in `RULES_REGISTRY.md`, in full text** (P/E/H/X/M groups + every §E project rule + every §F phase rule) — not IDs, not short tags. The full wording is the point.

Then branch on the phase state:

- **New phase (no `CURRENT_PHASE.md`):** prompt the developer to enable **Plan mode** and set `/effort max` (Phase Start planning is always Max, done in plan mode). Wait for both, then run the Phase Start Protocol — produce the phase plan, get approval.

- **Resuming an in-flight phase (`CURRENT_PHASE.md` exists):** first rebuild the harness task list from its checklist (one TaskCreate per plan task; completed tasks done, the current task in_progress) so the developer has the live monitor back — this is automatic. Then read the checkpoint and branch on what comes next:
    - **The plan still has unfinished tasks:** prompt the developer to enable **Auto-Mode** and set `/effort` to the level the next task needs per `docs/effort-map.md`; wait for the confirmation and the actual effort toggle (E3), then resume autonomous execution from the next task (P3/P5).
    - **The plan is exhausted but the phase milestone is not yet met** (all checklist tasks done, more work still required — or the checkpoint's NEXT pointer says to replan): prompt the developer to enable **Plan mode** and set `/effort max`; wait, then plan the continuation work as a fresh Gate-1 plan, append the approved tasks to `CURRENT_PHASE.md`, rebuild the task list, and continue. The phase number does not change — this is more work within the same phase.

Either way: wait for the developer before doing anything. Do not summarize the project, do not list all remaining tasks, and do not start working before the confirmation above. (The agent cannot enable Plan mode or Auto-Mode or change its own effort — it prompts and waits for each.)

---

## Mandatory Behavior — Violations Are Never Acceptable

1. **One task at a time; commit per task after the phase log** (P4). Execute plan tasks in order, one at a time. When a task completes, write its **rich checkpoint** into `CURRENT_PHASE.md` FIRST (2 to 4 sentences: what it did, the key decision and why, any deviation, any finding, per the template), then commit the task's changes + the checkpoint together. Assume every task boundary might be the handoff to a fresh session, so the log must let a later session synthesise the PhaseEnd from it alone. The only permitted grouping is ≤3 pure terminal commands with no decision-making. Presenting a whole (or half) phase checklist as implementation is a violation.
2. **Autonomy between the two gates** (P3/P5). A phase begins only on plan approval and ends only on milestone confirmation; between them, execute autonomously without per-task permission. Stop only for the enumerated conditions (P5): a fix failing twice with different root causes; a rule conflict; a destructive/unauthorized git op; a plan or toolchain change; a developer-only block or dead dependency.
3. **Plan mode every phase** (P3). Produce the phase plan in harness plan mode (at Max), get approval, then write `CURRENT_PHASE.md` and build the harness task list (one TaskCreate per plan task) before executing.
4. **Files on disk, not chat** (X-group). All code, config, and PhaseEnd content is written to disk with the Write/Edit tools. Chat is reasoning and questions only.
5. **Explain before coding** (X1). Before non-trivial code or a real design choice, state the approach and why. Skip the ceremony for trivial edits and say so.
6. **Never overwrite blind** (H-group). Read the current version of any file before overwriting it; state what changes and why; preserve comments and doc-headers.
7. **Mid-phase rules check** (P6). After every 4 completed tasks, re-read `RULES_REGISTRY.md` and state: "Rules check — re-read complete. Continuing with [next task]."
8. **Verify every checkbox before closing a phase** (P7). Walk the checklist explicitly, including wiring: registrations, config bindings, gitignore entries, doc updates.

---

## Fail-safe Hard Rules (duplicated so they survive even if the above is skipped)

- **Never edit `PROJECT_CONTEXT.md`.** It is permanent and static (P1). Corrections go in `CURRENT_PHASE.md` and the PhaseEnd, and rule changes in `RULES_REGISTRY.md` — never in the constitution.
- **Milestone honesty** (P9 + M1). Only an observable, machine-checkable outcome counts as success — the gate is the arbiter, never a self-report or "it ran without errors." Never redefine a term to make a failure pass; report failures as failures, with the output.
- **Claude commits per task; the developer pushes** (H5/H6). No AI-attribution trailers. Never `git push` from the agent unless explicitly directed.
- {{DOMAIN_FAILSAFES}}

---

## Reasoning & Model Protocol

Effort is set with `/effort` on the ladder `low / medium / high / xHigh / Max` (depth — how hard one agent reasons). **Ultracode** is NOT a deeper level — it runs at xHigh and adds multi-agent orchestration (breadth). **Max and Ultracode are session-only** (re-apply each session; xHigh is the highest that persists). `docs/effort-map.md` is the evolvable source of truth and governs where this section's wording differs.

**Doctrine (this project's default):** **xHigh** is the baseline for most tasks. **Max** is a scalpel for deep tasks — and is **always** the level for plan mode / Phase Start planning. **Ultracode/Workflow fan-out** is for breadth tasks (the same analysis across many independent items) only, never a standing mode. Pause and prompt for a toggle at every transition, and **wait for the actual `/effort` command to land** — the agent cannot toggle its own settings (E1–E3).

- **Tier 1 — Max, STOP and request it:** phase planning, PhaseEnd synthesis, architectural decisions across files, non-obvious debugging, any task with multiple valid approaches whose choice has lasting consequences. State it, wait for the toggle, and after the task remind the developer that Max is session-only.
- **Tier 2 — recommend xHigh:** multi-file features (3+ files coordinated), complex logic with edge cases, comprehensive test suites, uncertain-approach tasks.
- **Tier 3 — proceed at the baseline:** single-file clear-requirements work, boilerplate, terminal commands, small edits/renames/config.
- **Breadth:** when a breadth-shaped stretch appears, prompt for Ultracode/Workflow rather than grinding serially (E2) — and the "is this actually breadth?" judgment is itself a Max call.

## Session context budget

Keep sessions from bloating: a large accumulated context taxes every turn and is costly and lossy on a cache miss or auto-compaction. `CURRENT_PHASE.md` + per-task commits make any task boundary a clean handoff to a fresh session. **At every task handoff, state the current context % (from the status line) and give a fresh-session recommendation**, weighted by four factors: current %, the remaining tasks' `ctx:` buckets, proximity to PhaseEnd (finish a nearly-done phase warm for a richer synthesis), and whether a heavy task can be offloaded to a sub-agent instead of resetting. Guidance bands: do not reset below ~30%; reset at the next boundary in the ~40–50% zone if remaining work would carry you well past 60%; by ~60% reset now unless within a task or two of PhaseEnd. The developer starts the fresh session; the agent only flags it. Full rule and the `ctx:` buckets: `docs/effort-map.md`.

---

## Phase Boundary Protocol

When the developer confirms a phase milestone (against its machine-checkable gate):

1. Verify every checkbox (P7).
2. Demonstrate the milestone with its observable proof (command output / hash / screenshot instruction) and get explicit confirmation — the second gate.
3. 🟡 PhaseEnd creation is a Tier-1 task — confirm `/effort max` is set and wait.
4. Propose any new rules (P10); the developer adjudicates each.
5. Write `phase-ends/PhaseEnd_Phase[N].md` per `phase-ends/PhaseEnd.template.md`, ending with the `## Plain-English Recap` as its final section. Append ratified rules to `RULES_REGISTRY.md` §F.
6. Run `bash tools/backup-claude-state.sh` to sweep agent state into `.claude-state/` (H8), then `git mv phase-ends/CURRENT_PHASE.md phase-ends/logs/PhaseLog_[N].md` (R-equivalent: archive, don't delete).
7. Commit (the developer pushes). 
8. End the message with the plain-English recap as the last words, then **🛑 HARD STOP** — do not preview the next phase, do not continue. The next phase gets a fresh session.

---

## Project-Specific Constraints

- **Document disabled logic** with the structured comment: `// DISABLED: [name] — [date/phase] / Original intent / Why disabled (evidence) / Re-enable if [condition]` (H3).
- **Effort-map check** before each Phase Start and every task hand-off (E1); annotate every plan task with its effort.
- **The flywheel** (X4): consult `docs/{{COOKBOOK_NAME}}` before recurring work; feed the generalizable lesson back into both the cookbook and the tooling after — especially after a hard-won win.
- **Keep `docs/ops-setup.md` current** in the same change as any tooling/env/hook/path change (H7).
- **Clarify a misconception before a costly/hard-to-reverse action**; **justify a new tool's delta before adopting it** (memory-ratified; §E′).
{{GENERATED_CONSTRAINTS}}

---

## Environment

{{ENVIRONMENT}}

- **Agent state is repo-self-contained** (H8): memory in `.claude-state/memory/` (via `autoMemoryDirectory` in `.claude/settings.local.json`), transcripts auto-copied by the SessionEnd hook, `tools/backup-claude-state.sh` swept at every phase boundary. `.claude-state/` is private-repo-only.
- **No system temp** (H4): all scratch lives under the repo (`.run/`, gitignored).

---

## Reminders

- `PROJECT_CONTEXT.md` is permanent and static — never edit it. Current state = `RULES_REGISTRY.md` (rules) + `phase-ends/` (build history + in-flight phase).
- All PhaseEnd files are kept for the life of the project; none are discarded.
- The PhaseEnd file is the deliverable that ends a phase. After it lands, a fresh session reconstructs full state by reading the constitution + registry + all PhaseEnds.
