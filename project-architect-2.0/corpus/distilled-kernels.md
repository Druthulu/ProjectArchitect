# Distilled Methodology Kernels

> The highest-value engineering discipline distilled from prior projects: the methodology kernels that recur across very different kinds of work. **§1** is the detailed reference version of the registry seed's §M rules, kept here with "transfers to" guidance for when you apply them. **§2** is a few higher-level patterns to promote into a project once its own experience confirms them.
>
> The generic **process and governance rules** live in the registry seed (`templates/RULES_REGISTRY.seed.md`, §A–§M); the concrete **coding techniques and gotchas** live in `engineering-kernels.md`. This file is the methodology layer between them. Consult it at constitution generation or migration and install what fits.

---

## §1 — Core methodology kernels (the §M rules, with application guidance)

### M1 — Machine-checkable done: the gate is the sole arbiter
- **Kernel:** Define every milestone or pass as an observable, machine-checkable gate, and make that gate, never a participant's judgment or self-report, the sole arbiter. Never redefine a term to make a failure pass; report failures as failures, with the output. A green gate (in the right mode and scope) is a hard precondition for closing anything.
- **Transfers to:** any project. Define the gate at generation time (tests green, build reproducible, frame-hash identical, golden-file diff empty).

### M2 — An intermediary's report is a claim, not ground truth
- **Kernel:** A sub-agent's summary, a peer AI's diagnosis, a profiler's finding, a surprising metric, a historical note's premise: all are claims. Verify against the primary artifact (the code that computes it, the raw output, the actual bytes) before any decision rests on them. Confirmed numbers are not a confirmed conclusion.
- **Transfers to:** any agent-orchestrated workflow; any dashboard or metric-driven decision.

### M3 — Implausibly good results are a bug signal
- **Kernel:** Treat suspiciously good results as a measurement, leakage, or coincidence bug until proven otherwise, and pre-register what "implausible" means where you can. Promote a finding from candidate to verified only with three or more consistent datapoints or a controlled before/after diff; two-point agreement is coincidence-prone.
- **Transfers to:** benchmarks, performance wins, "the bug just disappeared," test suites that pass surprisingly fast.

### M4 — Verify the verifier
- **Kernel:** A verification mechanism is code too. Before trusting its PASS/FAIL, check the guard's own correctness (a mis-joined check can vacuously pass on zero rows), check its inputs are current (stale metadata silently weakens a gate), and never trust a tool's "success" message alone: re-open or re-read and confirm the effect actually persisted. A false pass is worse than a failure.
- **Transfers to:** CI checks, save and persistence paths, data-integrity guards, anything that says "OK."

### M5 — Clean-state verification
- **Kernel:** Capture a bit-for-bit baseline before a behavior-preserving change (the baseline run also surfaces pre-existing breakage), and verify any decisive comparison from a clean rebuild, because incremental or stale state can mutate in-place artifacts and fake both diffs and passes. An identical-inputs parity check (on == off, before == after) is the definitive break-check for an additive change.
- **Transfers to:** refactors, cache layers, build systems, savegame and serialization changes.

### M6 — Isolate the variable, then scale
- **Kernel:** Prove a mechanism on the single cleanest minimal case that isolates the variable under test, with confounders off, before promoting it to the full stack. Validate a batch pipeline or toolchain on the cheapest single case before launching the full matrix; it catches bugs in minutes instead of days. Build crash-recovery as unit-granularity resume.
- **Transfers to:** performance experiments, gameplay-mechanic tuning, migration scripts, any fan-out.

### M7 — Observations are not prescriptions
- **Kernel:** Proving a diagnosis's observations is separate from proving its fix: a single-pass observation can be exact while its prescribed fix fails the re-run, so mark single-pass findings diagnostic-grade and prove the fix before acting. A component's value is established only by a with/without re-run on the current system: a "dead"-looking part can be load-bearing through interactions, and an apparent inefficiency can be the price of an optimal configuration.
- **Transfers to:** performance "wins," dead-code removal, dependency pruning, system tuning.

### M8 — Bug-check a failure, and exhaust the lever ladder, before declaring anything dead
- **Kernel:** A well-motivated experiment that fails gets bug-checked (is something silently killing the result?) and its spirit-preserving alternatives theorized before any negative verdict, never try/fail/move-on. Symmetrically, never conclude something is impossible or unfixable before walking the full documented lever ladder: recurring "impossible" verdicts are usually knowledge-map incompleteness, not true impossibility. And audit whether an already-applied lever is itself causing the artifact before stacking more.
- **Transfers to:** "flaky" tests, "impossible" bugs, "the engine can't do that" verdicts.

### M9 — The real gate, not the proxy
- **Kernel:** A proxy metric can improve while the true objective worsens, and routing decisions made on a proxy mislead. Measure and decide on the real binding gate; use proxies for triage only.
- **Transfers to:** any optimization loop, model or tool routing, CI shards versus the full suite.

### M10 — Determinism and pinning at every boundary
- **Kernel:** Anything feeding a comparison, a gate, or a cross-boundary hand-off must be deterministic and pinned. Pass/fail-crossing metrics use a stable sort and a fixed tie-break (an unstable tie wobbles verdicts across runs); order shared-resource contention by a semantic key, never an arbitrary or ordinal ID; pin toolchain versions and flags by evidence, making silent defaults explicit; assert cross-boundary contracts (column order, formats, shapes) at startup, and regenerate their parity fixtures when either side changes.
- **Transfers to:** build reproducibility, netcode and lockstep determinism, savegame formats, native-to-managed interop, model export.

### M11 — Provenance and scope tags on every imported datum
- **Kernel:** Every externally-sourced or cross-variant datum carries provenance: source, scope, and verification status. Data valid in one scope is never assumed valid in another; unverified data stays quarantined in a clearly-labeled side channel and never merges into the canonical dataset until independently verified. Record the source for every imported fact.
- **Transfers to:** imported assets and data, community-sourced facts, cross-platform ports, config migrated between environments.

### M12 — Preserve the raw record; be exhaustive in search, narrow in confirmation
- **Kernel:** Raw experimental outputs and granular worklogs are an immutable archive. Analysis and verdict steps are read-only and write new files; losing candidates stay on disk (they are the raw material for later re-decisions and re-analysis under new criteria); at an intermediate gate, "not provably better" is not "worse," so carry point-competitive candidates forward and reserve strict pruning for the final decision. Archive worklogs outside the auto-load path: the synthesis loads at start, the detail is consulted on demand.
- **Transfers to:** experiment grids, benchmark runs, design explorations, playtest data.

---

## §2 — Higher-level patterns (promote when your project confirms them)

### Escalate a tool-internal problem to the tool's own source and community
- **Kernel:** When a residual is internal to a third-party tool (an optimizer, scheduler, or allocator doing something no input-level change reaches), research the tool's actual source code and its practitioner community through a precisely-briefed research agent (treating fetched content as untrusted data), after first checking your own past worklogs and before more brute force or a human-in-the-loop service. A proven escalation tier.
- **Transfers to:** engine internals (Unity, Unreal), compiler and JIT behavior, framework "magic."

### Isolated parallel agents beat the serial main loop on breadth
- **Kernel:** For N independent work items, isolated agents are cheaper in tokens than the main conversation working serially: the main loop re-sends its entire growing context every turn (roughly quadratic) while each agent starts fresh and small (roughly linear, and in parallel). The biggest additional saving is trimming what each agent reads; main-loop hand-work wins only for a small, high-value set (about five or fewer). Pair it with a deterministic gate (M1) so agent-quality variance is a throughput risk, never a correctness risk.
- **Transfers to:** any agent-orchestrated bulk work (audits, migrations, mass edits, reviews).

### An adversarial refute-pass is the primary independent check on a high-stakes verdict
- **Kernel:** Before an irreversible GO or verdict from one deep-reasoning pass, run an independent adversarial pass (skeptics recomputing from the raw artifacts and hunting framing over-reach). When independent reviewers propose opposite corrections, adjudicate from first principles, not by counting votes.
- **Transfers to:** architecture decisions, release GOs, migration verdicts, research conclusions.

---

## Coverage
- **§1 methodology kernels:** 12 (M1–M12), the detailed reference for the registry seed's §M group.
- **§2 higher-level patterns:** 3, added at generation or migration when the work is agent-heavy or high-stakes.
- Governance and process rules are not duplicated here; they live in the registry seed. Concrete coding techniques live in `engineering-kernels.md`.
