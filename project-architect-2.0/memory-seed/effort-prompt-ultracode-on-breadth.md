---
name: effort-prompt-ultracode-on-breadth
description: When breadth-shaped work appears, prompt for Ultracode; pause at EVERY effort transition and WAIT for the actual toggle — never proceed on a verbal yes
metadata:
  type: feedback
---

The moment a **breadth-shaped, parallelizable** stretch appears (the same analysis across many independent items — a fleet-wide audit/survey/dedup, mass generation/matching, an adversarial refute-pass over a set), **prompt the developer to enable Ultracode / Workflow fan-out** rather than grinding it serially. Isolated agents are token-cheaper than the main loop doing them in sequence, and a deterministic gate makes agent-quality variance a throughput risk, not a correctness risk.

**Effort is mapped per-task at plan time AND re-evaluated continuously.** At EVERY transition — into Max, into Ultracode, back to the baseline, or handing off to a task that needs a different level — **STOP, prompt to toggle, and WAIT for the actual `/effort` command to land** (a system-reminder confirms it). **Never proceed on a verbal "yes"** (that's intent, not the mode being on); never launch a Workflow before the toggle lands; never grind deep synthesis at Ultracode/xHigh.

**Why:** The agent cannot toggle its own effort. Two proven failure modes: launching a breadth Workflow on a verbal "enable it" before it was toggled, and continuing from a breadth sweep straight into deep synthesis without switching back to Max. **Registry: E2 / E3.**

**How to apply:** Prompt template — *"🟦 breadth-shaped (~N independent items); Ultracode/Workflow would parallelize this. Enable `/effort ultracode`? I'll flip back to Max for the deep single-thread parts."* The "is this actually breadth, or mine-to-author-with-full-context?" judgment is itself a Max call. See [[effort-discipline]].
