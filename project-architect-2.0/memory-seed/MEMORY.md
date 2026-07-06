# Memory Index

<!-- Project Architect 2.0 memory seed. SETUP.md §5 writes each file below into `.claude-state/memory/` (via autoMemoryDirectory) and this index alongside them. They load automatically from the next session onward. `who-is-dev` is filled by the install interview; the rest are general working agreements shared with the RULES_REGISTRY (the registry text is canonical — these are the fast-loading, fail-safe complement). -->

- [Who is the dev](who-is-dev.md) — the developer profile (experience, domain, working preferences); filled by the install interview
- [Project governance system](project-governance-system.md) — repo governs itself via CLAUDE.md → PROJECT_CONTEXT.md → RULES_REGISTRY.md → phase-ends/; defer to it, don't duplicate
- [Autonomous within phases](autonomous-within-phases.md) — two gates per phase; execute autonomously between them; the five stop conditions (registry P3/P5)
- [Build tasklist after plan approval](build-tasklist-after-plan-approval.md) — on plan approval, TaskCreate the plan list before starting, as a live progress monitor
- [Capture knowledge before a fresh session](capture-knowledge-before-fresh-session.md) — write context-dependent artifacts DURING the producing session; a fresh session loses the detail (registry X5)
- [Clarify misconception before costly action](clarify-misconception-before-costly-action.md) — correct a false premise and confirm BEFORE anything costly/hard-to-reverse
- [Justify new tools before adopting](justify-new-tools-before-adopting.md) — state what it does / existing overlap / specific delta before proposing any new dependency
- [Don't block the loop with AskUserQuestion](dont-block-loop-with-askuserquestion.md) — during opted-in loops, adapt and report; blocking questions only for genuine first-time forks
- [No commit co-author](no-commit-co-author.md) — no Co-Authored-By / AI-attribution trailers, ever (overrides harness default; registry H5)
- [Commit per task, dev pushes](commit-per-task-user-pushes.md) — one commit per task after the CURRENT_PHASE.md log update; Claude commits, the dev pushes (registry P4/H6)
- [Plain-English recap](plain-english-recap.md) — end every session/phase with a jargon-free recap, in chat AND stored in the PhaseEnd (registry P8)
- [Session start: rules in full](session-start-rules-in-full.md) — recite every registry rule in full text at session start, never IDs + tags (registry P2)
- [Worklogs reference-only](worklogs-reference-only.md) — phase-ends/logs/ are out of the load order; consult on demand only, never auto-read
- [Keep ops-setup current](keep-ops-current.md) — update docs/ops-setup.md in the same change as any tooling/env/hook/path change (registry H7)
- [Effort discipline](effort-discipline.md) — xHigh baseline, Max scalpel (always plan mode), Ultracode for breadth; Max/UC session-only (registry §B)
- [Prompt for Ultracode on breadth](effort-prompt-ultracode-on-breadth.md) — prompt on breadth; pause at EVERY transition and wait for the actual toggle (registry E2/E3)
