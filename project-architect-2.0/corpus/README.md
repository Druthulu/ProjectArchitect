# Corpus — Master Rules & Memories Repository (Project Architect 2.0)

The distilled, transferable experience of the owner's two largest AI-collaborated projects, preserved so every future project starts with their lessons instead of re-learning them:

- **BFM-decomp** — Brave Fencer Musashi PS1 matching decompilation (C/MIPS, Ghidra, byte-exact verification; 24 phases).
- **Vantage** — closed-source C#/.NET + Python ML research platform (~130 phases, ~150 accumulated rules).

## Philosophy: transferable coding value only

This corpus is **not a full archive** — it is the subset that transfers to the owner's future work (coding and game-dev projects, mostly C#). Two consequences, decided 2026-07-04:

1. **Vantage's fintech-domain rules are excluded.** The owner will not build another fintech platform, so quant/trading rules are dead weight here — and they are also exactly where Vantage's proprietary trading edge lives. Dropping the domain drops the edge. Where a quant rule's underlying kernel was general engineering, the kernel was kept and the fintech body removed (marked `Body dropped`). **Standing constraint: no Vantage edge material (signals, factors, thresholds, book/universe configs, measured strategy numbers, GO/NO-GO verdicts) may ever enter this package or anything derived from it.** Any future re-export from Vantage runs an edge-sensitivity flag pass the owner adjudicates.
2. **Full archives stay in the source repos.** Every entry carries a provenance ID tracing back to its origin (`Vantage_Rules_Registry.md` / `PhaseEnd_*.md` / memory file), so nothing is lost — it just doesn't ship.

## Files

| File | Contents | Entries |
|---|---|---|
| `rules-bfm.md` | BFM constitution rules (P/G/H/X), all phase rules R1–R30, adaptation note, no-rule ledger | 57 |
| `rules-vantage.md` | Vantage registry §A–§E, transferable subset (50 verbatim + 50 de-domained kernels) | 100 |
| `memories-bfm.md` | All 30 BFM auto-memories (user/feedback/project/reference) | 30 |
| `memories-vantage.md` | Vantage memories, transferable subset (19 verbatim + 9 kernels) | 28 |
| `distilled-kernels.md` | The cross-project synthesis: 12 convergent kernels (→ registry §M), 3 transfer-validated, 17 single-source candidates, governance-cluster map | — |

**Entry schema** (uniform): `### <ID> — headline` · **Origin** (project · source file/phase/memory) · **Class** (`general` / `stack-general (<family>)` / `project-specific`) · **Kernel** (the transferable principle — required when Class isn't general) · body (verbatim, or dropped with a note). Each file ends with a Coverage manifest (counts, ID list, anomalies).

## How to query this corpus (SETUP.md §8 / constitution generation / migration)

The corpus serves two functions — **lookup** and **generalization**:

1. **Start from `distilled-kernels.md`, not the raw files.** §0 maps the governance clusters to their registry homes (already seeded — don't re-install). §1's M-kernels are already in the registry seed. What you're shopping for is §2–§3 (transfer-validated + candidates) and the stack-specific entries in the raw files.
2. **Match by tech stack.** Grep the raw files' `Class:` lines for the target project's families — `stack-general (C#/.NET)` (richest set — A14, E:Gen2.P9.9.5.1, E:Gen2.P9.9.1, E:Gen3.5.G.1, E:Gen3.5.J.*, E:Gen3.F.*, D19; bundled as kernel C13), `data/ML engineering`, `Python-data/polars`, `agent/CI ops`, `PS1-decomp/RE` (for any future RE/modding work). Install matching entries into the new project's registry §E or its cookbook, whichever fits (norms of conduct → registry; craft techniques → cookbook).
3. **Match by problem shape.** Scan §1/§3 kernel headlines against what the project will do: simulations → C1; telemetry/analytics → C2/C6; experiments/tuning → C3/C4/C5/C7 + M6/M7; batch/build tooling → C8/C14/C15/C16; agent orchestration → T2/T3/C9/C10; API integrations → C17.
4. **Generalize spirit-right, stack-wrong rules.** When an entry has the right spirit but the wrong stack, carry its **Kernel** (never the body) and re-domain it to the project. Example: BFM G3 "a match is byte-for-byte" → a game project's "a port level is done when its frame-state hash matches the original for the test-input script."
5. **Record provenance.** Every installed or adapted rule notes its origin in the target project's registry: `(adapted from corpus: Vantage E:Gen3.6.F.1)`. This keeps the chain auditable and lets a future session pull the fuller origin story if needed.
6. **Never re-import the dropped content.** The Excluded stubs in the Vantage files describe what was dropped and why. If a future project genuinely needs something from the excluded set (e.g. the owner returns to ML research), go to the Vantage repo, and run the edge-sensitivity pass before anything crosses.

## Growing the corpus

The corpus is itself a flywheel. When a future PA 2.0 project matures (or at its migration into a newer PA version), append its transferable rules/memories as `rules-<project>.md` / `memories-<project>.md` in the same schema, and refresh `distilled-kernels.md` — a kernel that newly converges across three projects is even stronger evidence; a §3 candidate confirmed by a second project graduates toward §M. Keep this README's file table current (same-change discipline, rule H7).

## Provenance & extraction record

Extracted 2026-07-04 by a 4-chain extract→adversarial-verify→repair workflow (each file verified for completeness/schema/fidelity against its sources); Vantage files then rebuilt to the transferable subset with an aggregate edge-token re-scan (clean). Full pre-scrub extractions retained by the owner outside this package. Sources as of: BFM Phase 24 in progress (Phase-24 rules out of scope); Vantage Gen4.LA in progress.
