# PhaseEnd — Phase [N]: [Phase Name]

**Date:** [date] · **Project Version:** [X.Y.Z] · **Phase Status:** Complete

> Written only at a developer-confirmed milestone (Tier-1 task, `/effort max`). This is the durable synthesis a fresh session reads — write it as if `CURRENT_PHASE.md` will never be read again (it is archived to `phase-ends/logs/`, out of the load order). Keep it lean: non-derivable content only (target ≤30 KB).

## Build Log

**Files created/changed and complete — do not recreate:**
- `path/to/file` — [what it does] — [depends on / consumed by]
- …

**Tools/packages installed:** [list, or None]

**Verification results:** [the checks run + their literal outcomes — the machine-checkable evidence, per M1/P9]

**Milestone achieved:** [one sentence, the observable proof that the phase gate is green]

**Next:** Phase [N+1] — [Name]. Start with [first task].

## Deviations

| Item | Plan | Actual | Reason |
|---|---|---|---|
| | | | |

## Commit Message

```
feat(phase-[N]): [phase name] complete — [one-line summary]

- [unit]
- [unit]
```
*(No `Co-Authored-By` / AI-attribution trailer — H5.)*

## Rules Added This Phase

| Rule | Reason |
|---|---|
| R[n] — [headline] | [the incident / correction that prompted it] |

*(…or "None — [where the phase's lessons went instead: cookbook / effort-map / a deviation note].")*
**On writing this table: also append each rule's full text to `RULES_REGISTRY.md` §F under `### Phase [N]`, in the same change** (P8 / registry Maintenance Protocol). This table is the origin story; the registry carries the operative text.

## PhaseEnd Changelog

`v[X.Y.Z] → v[X.Y+1.Z]` — [Build Log / Deviations / New Rules deltas in one or two lines]

## Plain-English Recap

[2–5 sentences, plain English, define any term you must use: what this phase WAS, WHY it was needed, and WHAT it did. This is the durable orientation a fresh session (or the developer, months later) reads before the detail. Also make this the last thing said in the phase-close chat message. — The hard stop + fresh-session handoff is enforced by CLAUDE.md's Phase Boundary Protocol; it does not need restating here.]
