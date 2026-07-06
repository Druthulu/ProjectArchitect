---
name: dont-block-loop-with-askuserquestion
description: During an explicitly opted-in loop, adapt autonomously and report instead of halting with a blocking question at every inflection; reserve questions for genuine first-time forks
metadata:
  type: feedback
---

During an explicitly opted-into `/loop` (or any standing "keep going" directive), do NOT halt with a blocking AskUserQuestion at every ROI inflection to ask "should we continue / where do I point the effort?" The developer already set the direction; keep it running, adapt autonomously (do the sensible next thing — the machine-checkable gate guarantees correctness, M1), and report compactly.

**Why:** Re-confirming a direction the developer already gave wastes turns and reads as not listening. A genuine first-time strategic fork is worth an AskUserQuestion; a per-cycle "keep going?" gate is not.

**How to apply:** AskUserQuestion is for a genuine first-time strategic fork the developer hasn't weighed in on. It is NOT for re-confirming an already-given direction, nor for per-cycle continuation gates. When cheap options run dry mid-loop, pick the next-most-sensible option and state the pivot + reasoning ("here's what I'm doing + why, redirect if you want"), rather than blocking. Extends [[who-is-dev]] (recommendation-first, autonomous-within-phases).
