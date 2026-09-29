# REPLAN — Phase <N>

<!-- Written by the router when the critic returns `needs-developer`, or when the plan is
     exhausted before the milestone. Critic-brief format. The developer reads this, answers,
     and the router enters planner-phase mode in the same session (detect_state sees this
     file). Delete on replan; the new plan archives the old one as PHASE_PLAN.v<k>.md. -->

Trigger: <what forced the replan — task id, return status, failure>
Question: <the one decision that cannot be made without the developer>
Recommended (expert): <the expert's own recommendation, verbatim>
Critic decision: <continue|edit|needs-developer> — <why, ≤3 lines>
Plan state: done <T…> · next <T…> · blocked <T…> · superseded <T…>
Logs to consult: tasks/T<n>.md, logs/T<n>.md (paths only; do not paste)
Developer decides:
1. <option> — <consequence>
2. <option> — <consequence>
Recommended: <option n, one line why>
