# Effort Map — {{PROJECT_NAME}}

> **Evolvable reference (docs/ layer). Verified against Claude Code as of {{INSTALL_DATE}}** — harness mechanics change; re-verify with `/status` / `/effort` if a control behaves unexpectedly. This file is the source of truth for effort, and **governs where CLAUDE.md's tier wording differs** (registry E1–E3 point here). Read it at session start.

## How the controls actually work

- **Depth** is the `/effort` ladder: `low / medium / high / xHigh / Max` — how hard a single agent reasons.
- **Breadth** is **Ultracode** / the Workflow tool: xHigh-per-agent + parallel sub-agents. **Ultracode is NOT a deeper level** — it caps depth at xHigh and adds fan-out.
- **Persistence:** `low`–`xHigh` persist across sessions; **`Max` and `Ultracode` are session-only** — they revert to the xHigh baseline each new session, so re-apply them deliberately.
- **The two axes are not combinable** in one turn: globally enabling Ultracode caps every task at xHigh depth, so breadth is applied *surgically*, not left on.
- **The agent cannot change these itself** — it must prompt the developer and wait for the actual toggle to land (a system-reminder confirms it).

## The doctrine (this project's default)

1. **xHigh is the baseline** for most tasks.
2. **Max is a scalpel** — reserved for deep tasks: phase planning, PhaseEnd synthesis, architectural decisions across files, non-obvious debugging, and any judgment whose silent error would invalidate everything downstream. **Plan mode is always Max.**
3. **Ultracode/Workflow is for breadth only** — the same analysis across many independent items (a fleet-wide audit, mass generation/matching, a survey/dedup, an adversarial refute-pass over many findings). Turn it on for the breadth stretch, then off.

*(This supersedes the older "Max is the standing default" and "default-live in xHigh+Ultracode" postures the predecessor projects ran; the two-axis mechanics above are unchanged.)*

## The decision rule — Max vs xHigh vs Ultracode

- **Settled-design execution of a bounded task → xHigh** (the baseline; don't over-think routine work).
- **A single hard judgment whose silent error poisons everything downstream → Max** (a design/architecture call, a subtle-bug diagnosis, a verdict, PhaseEnd synthesis, phase planning). A scalpel, picked up and put down.
- **The same analysis across many independent items → Ultracode/Workflow** (map a surface, back-fill a matrix, generate/verify N things, refute-pass a set of findings). Isolated agents are token-cheaper than the main loop grinding them serially, and a deterministic gate (registry M1) makes agent-quality variance a throughput risk, not a correctness risk.
- **Litmus for breadth:** "would ~10 agents each doing one item beat me doing them in sequence?" If yes → breadth. If the work is *mine-to-author-with-full-context* (a doc, a synthesis, a design), that is NOT breadth even if it's large — keep it at Max.
- **Adversarial verification of a high-stakes Max verdict → an Ultracode refute-pass** is the primary independent check before an irreversible GO (there is no second top-tier model to cross-check).

## Transition discipline (E3 — pause at EVERY boundary)

Effort is mapped per-task at plan time AND re-evaluated continuously. At every transition — escalating into Max, into Ultracode, dropping back to baseline, or handing off to a task that needs a different level — **STOP, prompt the developer to toggle, and WAIT for the actual `/effort` command.** Never launch a Workflow on a verbal "yes"; never grind deep synthesis at Ultracode/xHigh; never roll into the next task at the wrong level. Two documented failure modes to avoid: launching breadth work before the toggle landed, and continuing from a breadth sweep straight into deep synthesis without switching back.

## Session context budget — when to start a fresh session

Every turn re-sends the whole session context. Prompt caching makes that cheap per turn (a cache read is ~10% of input price), but 10% of a large context is still large, and a cache miss on a big context — or the harness auto-compacting — is expensive and lossy. So keep sessions from bloating: checkpoint to a fresh session at a clean task boundary. `CURRENT_PHASE.md` plus the per-task commits make any boundary a safe handoff, and a fresh session's cost is dominated by the rules recitation, which is why a lean registry keeps resets cheap.

**The reset decision weighs four things, not just the context percentage:**
1. **Current context %** — read it off the status line.
2. **Projected remaining context** — sum the `ctx:` buckets of the unfinished tasks. Scope matters more than count: one `ctx:L` task can outweigh five `ctx:S` ones.
3. **Proximity to PhaseEnd** — finishing a phase in one warm session yields the best PhaseEnd (full live context, not reconstructed from the log). The closer you are to done, the more you bias toward pushing through even at a higher context.
4. **Offload option** — a `ctx:L` or `ctx:U` task can often be routed to a sub-agent (breadth) so the main context never takes the hit, instead of resetting.

**Context-cost buckets** (`ctx:` tag on each task): **S** small · **M** medium · **L** large · **U** unpredictable (debugging-heavy or exploratory). The bucket estimates growth of THIS session's context, not total token cost — a breadth task fanned out to sub-agents is `ctx:S`. Buckets are a coarse planning aid; the status line is ground truth, re-checked at each handoff.

**The bands (guidance, not hard gates):**
- **Below ~30%:** do not reset. The session-start cost (governance reload plus the rules recitation) and the small loss of live nuance outweigh the tiny per-turn saving. A new phase already opens near ~20% after session start and plan mode, so treat the low 30s as "too soon."
- **~40–50%:** the reset zone. At the next task boundary, if the remaining tasks' `ctx:` buckets would carry you well past 60%, checkpoint and go fresh. This is the sweet spot.
- **~60%:** reset now, at the next boundary; do not push past it unless you are within a task or two of PhaseEnd (then finish warm for a better synthesis). Beyond this you pay a lot per turn and risk auto-compaction, which is worse state than a clean reload from `CURRENT_PHASE.md`.

**At every task handoff, state the current context % and give a reset recommendation** weighted by the four factors (e.g. "at 47% with two `ctx:L` tasks left — recommend a fresh session for the next one," or "at 63% but only a `ctx:S` task then PhaseEnd — worth finishing warm"). The developer decides and starts the fresh session; the agent cannot.

## Per-phase effort map (Gen1)

*Filled at generation. For each phase, mark which tasks are Max-mandatory, which are fine at xHigh, and which are breadth (Ultracode). The Max shortlist is the small set of judgments where a silent error would be catastrophic.*

{{PER_PHASE_MAP}}

| Phase | Mandatory-Max tasks | Fine at xHigh | Breadth (Ultracode) |
|---|---|---|---|
| Phase Start (any) | the plan itself (always Max) | — | wide surveys feeding the plan |
| [Phase 1] | | | |
| … | | | |

## The Effort-map check (E1 — standing rule)

Before each Phase Start, state the recommended effort for planning that phase (Max) and confirm it's set; annotate every task in the plan with its effort level **and its coarse context-cost bucket** (`ctx:S/M/L/U`, see below); restate the recommended effort whenever presenting the NEXT task. This file is evolvable — refine the per-phase map as the project's real effort needs become clear.
