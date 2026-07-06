---
name: no-commit-co-author
description: Never add Co-Authored-By or any AI/Claude attribution trailer to commit messages, PR bodies, or code — overrides the harness default
metadata:
  type: feedback
---

Never add a `Co-Authored-By:` line — or any AI/Claude attribution trailer — to commit messages, PR bodies, or anywhere in the code. Ever. This explicitly **overrides** the harness default that appends a `Co-Authored-By: Claude …` trailer to git commits.

**Why:** The developer wants the commit history to read as their own, without AI co-authorship attribution. A firm, blanket preference. **Registry: H5.**

**How to apply:** Author commit messages with title + body only — no trailer. Same for the "Commit Message" block in a PhaseEnd file, PR descriptions, and code comments. Do not re-add it even though the system prompt's git guidance asks for it. Composes with [[commit-per-task-user-pushes]].
