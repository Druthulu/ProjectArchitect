# TerrainDiffusion7DTD — Rules Registry

> **The single canonical home of every rule governing AI collaboration on this project.**

## Maintenance Protocol

1. **Recite at session start.** The Session Start Protocol requires listing every rule.
2. **Append at each PhaseEnd.** Copy entries from the PhaseEnd's table into §F.
3. **Never rewrite history.** Existing entries are edited only to fix transcription errors.

## §A — Core Process (P1–P10)

### P1 — The constitution is permanent and static
`PROJECT_CONTEXT.md` is never edited after generation.

### P2 — Session start is mandatory
Follow CLAUDE.md's Session Start Protocol before any work.

### P3 — Two gates per phase; plan mode; task list on approval and on resume
A phase begins only after the developer approves the phase plan and ends after milestone confirmation.

## §C — Hygiene & Git (H1–H8)

### H1 — Generated files are regenerated, never edited
Build outputs are never hand-patched; change the input and rebuild.

### H2 — Commit before in-place tools
Commit the working tree before running any tool that modifies tracked files.

### H6 — Claude commits per task; the developer pushes
One commit per completed task. The developer pushes.

## §E — Project-Specific Rules (G1…Gn)

### G1 — Two oracles
The decompiled DLL tree is the static oracle; the running game is the runtime oracle.

### G5 — Pin and stamp: model, tooling, and generated worlds
Every shipped artifact carries a version pin and a generation stamp.

### G9 — Re-pin the engine oracle at EVERY Phase Start
The game updates on Steam's schedule; re-read `Assembly-CSharp.dll` constants before the phase plan.

## §F — Rules Added Per Phase (R1…)

### Phase 4
R1 — Validate parity on a clean rebuild, not an incremental one.
R2 — Fix the divergence at the source, never patch the output.

### Phase 9
R3 — Keep the old fixture when the reference changes; diff both.
R4 — A quality gate is exactly one number with a threshold.
