# Session-Start Digest — Example Quest Decompilation

> The compressed session-start document. Read this and CURRENT_PHASE.md before any work.

## 0. Where the project stands

The matching decompilation is at ~75% byte-identical. The main executable and all location overlays are split and building.

## 1. Corrections and supersessions of PROJECT_CONTEXT.md

- The compiler triple was pinned at Phase 4 (not Phase 2 as the constitution estimated).

## 2. Phase synopses

**P1 (2026-06-10, v1.1.0)** Project bootstrap: Ghidra DB, splat config, first functions decompiled via MCP. Rules R1-R6.
**P7 (2026-06-14, v1.7.0)** Cookbook founded, permuter harness, LZSS matched, PsyQ library objects linked byte-identical. Rules R17-R19.
**P36 (2026-09-15, v1.36.0)** Wave 6 completed: 82.1% byte-identical. Rules R100-R106.

## 3. Every rule, in full

- **R1 — H1 relaxed while private.** ROM-derived content MAY be committed while the repo is private.
- **R2 — All-in-WSL.** One ext4 clone; no Windows/WSL split.
- **R3 — All tooling under `tools/`.** Including `tools/bin`, `tools/psyq*`, `tools/ghidra_scripts`.
- **R101 — Every commit carries its log line and the headline.**
- **R102 — A tool that restores files restores from its own snapshot, never `git checkout`.**
- **R103 — A failure-cause extractor is negative-controlled against the compiler's real message forms.**

## 4. Where things live (the doc map)

- Rules: `PROJECT_CONTEXT.md` (P/G/H/X) + this digest §3 (R-rules)
- Cookbook: `docs/matching-cookbook.md` (index: `docs/cookbook-index.md`)
- Ops: `docs/SETUP.md`
