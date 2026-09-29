# Vantage — Consolidated Rules Registry

> **Status:** Living document. Created 2026-06-03 by consolidating rules across all PhaseEnd files.

## Maintenance Protocol (how this file is kept current)

Append new rules at each PhaseEnd close.

## Section A — Core AI Collaboration Rules

### A1 — The constitution is permanent
`Vantage_Project_Context.md` is never edited after generation.

### A2 — Session start is mandatory
Follow the Session Start Protocol before any work.

## Section B — Reasoning & Model Protocol

### B1 — Effort-map check
State the recommended effort before each task.

## Section C — Project-Specific Constraints

### C1 — Backtest parity
Any change to the engine must maintain backtest determinism.

### C2 — No live trading without the developer
Live-mode activation requires explicit developer confirmation.

## Section D — Saved feedback memories (active)

### D1 — Solo developer profile
The developer is experienced with C# and quantitative finance.

### D2 — Commit discipline
One commit per task; the developer pushes.

### D3 — Test before commit
Run the xUnit suite before committing engine changes.

## Section E — Rules Added Per Phase (headline index)

### Gen2 / core build (Phases 1–12)
R1–R8: core build rules for the .NET engine.

### Original Gen3 ML stock engine (Gen3.A–F.7)
R9–R15: ML pipeline rules.

### Gen3.5 replanning (Gen3.5.0.1 onward)
R16–R22: multi-timeframe expansion rules.
