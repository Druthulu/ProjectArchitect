# Rubric: a plan-change judgement for the shelf project
Side score beside the deterministic grade (contract fields, DECISION and TIER values, EDITS grammar); never the pass.
The answer judges one proposed change to a phase plan and returns DECISION (continue | edit | needs-developer), TIER (additive | subtractive | milestone), EDITS (`python tools/plan_edit.py` lines), WHY (three lines) and, for needs-developer only, BRIEF ending `Recommended: …`.

## Items (1 point each)
C1 The decision is defensible against the material: the plan, its milestone, its standing constraints and the proposal support it.
C2 The tier is right: additive when nothing is removed or weakened; subtractive when a task, file, check or clause is dropped or weakened; milestone when the milestone line or its checks change.
C3 The edits would run: every line is a `plan_edit.py` command with a real subcommand (set-status, reopen, add-task, append-change), the flags it needs, task ids that exist in the plan and quoting a shell accepts; continue carries no edit.
C4 The edits do what the decision says: they fix the problem the proposal raises, keep each done-when, verify and milestone clause covered, and change nothing else.
C5 WHY names the wall: the specific task, file, clause or fact in the material that decides the case, not a restatement of the decision.
C6 Escalation is right: needs-developer only when the choice is the developer's (product intent, risk, money, scope) and its BRIEF ends with a recommendation; otherwise the case is decided here.
C7 Nothing outside the contract: no preamble, no extra sections, no questions; WHY stays within three lines.

## Penalties (-1 each; the total is never below 0)
- a subcommand, flag, task id or file presented as fact that the material does not contain
