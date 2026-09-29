# Rubric: a phase-plan draft for the shelf project
Side score beside the deterministic grade (lint, milestone clauses covered, `files:` and `verify:` per task); never the pass.
The material holds the task and the project's plan files: `GENERATION_PLAN.md` (the phase line with its milestone clauses, the standing constraints), `PROJECT_CONTEXT.md`, `HOW_WE_WORK.md`, `PHASE_SEED.md` (the template and the task-line grammar).
The P-items come with the task (the must-haves list), 1 point each.

## Structure (1 point each)
S1 Every template section of `PHASE_SEED.md` is present in its order (header, Context, Rationale, Interfaces, Cookbook, Research, Developer decides, Tasks, Risks, Changes) and the header's `Approved:` line stays a placeholder.
S2 Context and Interfaces name the modules, functions and test files the milestone clauses name, with the signatures the tasks add or change; every task's `files:` covers what its done-when changes.
S3 Risks name what could make the phase miss its milestone and how each is detected; Developer decides holds recommendations with consequences, not open questions.

## Penalties (-1 each; the total is never below 0)
- a tool, flag, module, function or path presented as existing that the material contradicts
- a task that breaks a standing constraint (stdlib only, tests with every behaviour change, `storage.FORMAT` stays 2, old documents keep loading)
- a task that starts the work, edits a generated file by hand, or pushes
- scope pulled in from another phase of the generation plan
