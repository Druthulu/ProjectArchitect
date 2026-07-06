---
name: clarify-misconception-before-costly-action
description: When a request's rationale rests on a likely factual misunderstanding, state the correction and confirm BEFORE executing anything costly or hard to reverse
metadata:
  type: feedback
---

When a request's *rationale* looks like it rests on a factual misunderstanding, state the correction FIRST and get a confirm, **before** executing anything costly or hard to reverse. A 30-second clarification beats a multi-step undo.

**Why:** Acting on a misconception-driven directive wastes real time/compute and can pollute a clean working state. (A predecessor project vendored 1458 files acting on a "submodules = vendoring" misconception, then had to revert — a wasteful round-trip that a single clarifying sentence would have avoided.)

**How to apply:** If the premise behind a request is likely wrong, pause and say so plainly — what's actually true vs what was assumed — present the corrected options, and wait for a confirm before kicking off the expensive step (a long build/compute run, a large refactor, anything that overwrites a committed-clean artifact). Complements [[justify-new-tools-before-adopting]].
