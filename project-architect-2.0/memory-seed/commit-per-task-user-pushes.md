---
name: commit-per-task-user-pushes
description: One commit per completed task, made AFTER updating CURRENT_PHASE.md so the task's code and its log line ship together; Claude commits locally, the developer pushes
metadata:
  type: feedback
---

Commit cadence: **one commit per completed task**, made **after `CURRENT_PHASE.md` (the phase log) is updated** with that task's completed work — so the task's changes and its log entry land in the same commit. Claude makes the commits locally; **the developer pushes.**

**Why:** Per-task commits give fine-grained history and crash recovery; updating the phase log first means every commit is self-describing and the log never lags the code. Keeping push under the developer's control keeps commit authorship, timing, and the final review with them. **Registry: P4 / H6.**

**How to apply:** Task done → update `CURRENT_PHASE.md` progress log → `git add` the task's files + the log by explicit path → commit with a message naming the task → **do not push**. No `Co-Authored-By` trailer ([[no-commit-co-author]]). Never sit on irreplaceable work uncommitted between checkpoints. See [[autonomous-within-phases]].
