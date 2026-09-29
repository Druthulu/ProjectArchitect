# CLAUDE.md — Example Quest Decompilation

Matching decompilation of **Example Quest** (PS1, SLUS-00000 USA). Goal: byte-for-byte identical binaries from C source.

## Session Start Protocol — Do This First, Every Session

Read `phase-ends/DIGEST.md` in full (the session-start digest), then `CURRENT_PHASE.md`.

## Decomp fail-safes (decomp-architect, Phase 0.5)

- **Never commit game-derived bytes.** ROM dumps, extracted payloads, build output stay out of git.
- **Never `git clean -x`.** Regenerable assets take hours to rebuild.
