# Rubric: review decisions for the shelf project
Side score beside the deterministic grade (numbered decisions, a `plan_edit.py … --by developer` option in each, RECOMMENDED ids exist and are listed first); never the pass.
The answer turns a review pause into OUTCOME (what was found), DECISIONS (numbered, lettered options, each with the exact plan command) and RECOMMENDED (option ids such as `1a`).

## Items (1 point each)
R1 The outcome is defensible against the material: it states what the result shows against the task's done-when, with the numbers or facts that decide it.
R2 The options are complete: every real choice the material raises is a decision, and each decision offers the realistic alternatives (accept, reopen, add a task, defer) rather than one path.
R3 The commands are exact: every option's command is a `plan_edit.py` line with a real subcommand, task ids that exist, the flags it needs and `--by developer`, and would run as written.
R4 The recommendation is justified: RECOMMENDED names the options the material supports, and each recommended option carries its reason in one clause.
R5 Each option says what follows from choosing it (what reopens, what is accepted, what is left open).
R6 Nothing outside the contract: OUTCOME, DECISIONS and RECOMMENDED only; no question to anyone, no edit performed, no preamble.

## Penalties (-1 each; the total is never below 0)
- a subcommand, flag, task id or file presented as fact that the material does not contain
