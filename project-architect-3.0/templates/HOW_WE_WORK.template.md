# How we work — {{PROJECT_NAME}}

<!-- The card: standing facts for every agent. Never appended to; the cap is
     card.max_chars in .claude/pa.json (default 7000), enforced by the archive.
     Each role prints its slice with `tools/card.py slice <role>`.
     Written at install (interview) and edited in the same task as whatever changed it (H7). -->

## Project <!-- roles: expert coder router planner review critic discuss auditor curator -->
{{PROJECT_NAME}} — {{PROJECT_TAGLINE}}. Constitution `PROJECT_CONTEXT.md`; roadmap `GENERATION_PLAN.md`;
rules `rules/INDEX.md`; techniques `cookbook/INDEX.md`; ops detail `docs/ops/INDEX.md`.

## Developer <!-- roles: router planner review discuss auditor curator -->
Who: {{DEVELOPER_NAME}}. Experience: {{DEVELOPER_EXPERIENCE}}. Domain: {{DEVELOPER_DOMAIN}}.
Preferences: recommendations, not questions. Plain-English recaps at phase end. A notification
whenever anything waits on them. Autonomy: {{AUTONOMY_POSTURE}}. Notification channel: {{NOTIFY_CHANNEL}}.
The developer pushes; agents never do. They ratify rules at the next planner session.

## Rhythm <!-- roles: router planner review discuss auditor curator -->
{{RHYTHM}}  <!-- autonomous: the router runs the phase end to end, stopping only at the two gates.
                  review: tasks marked `review: yes` end the turn with REVIEW.md and wait. -->
Gates: plan approval (planner session) and the milestone (verified by the closer expert).

## Tools <!-- roles: expert coder router planner review critic discuss auditor curator -->
PY = {{PY}}

| Name | Command | Purpose |
|---|---|---|
| launch | `PY tools/launch.py --seed-only` | the pa-session's state detector; writes `.run/seed.md`; never typed by the developer |
| plan_edit | `PY tools/plan_edit.py` | the only writer of `PHASE_PLAN.md` (status, Changes, add/reopen) |
| status | `PY tools/status.py` | `.run/status.json` for the statusline; INBOX consume; waiting flags |
| task_log | `PY tools/task_log.py` | lint and finish a task summary (`Verified:` required) |
| research_add | `PY tools/research_add.py` | allocate a report id, write the index line |
| rules_add | `PY tools/rules_add.py` | add, supersede, promote, retire a rule |
| skill_add | `PY tools/skill_add.py` | turn a `workflow:` gotcha into `.claude/skills/<name>/SKILL.md` |
| cookbook_add | `bash tools/cookbook_add.sh` | add a cookbook entry and its index line |
| phaseend_index | `PY tools/phaseend_index.py` | assemble, lint and archive a PhaseEnd |
| genend_index | `PY tools/genend_index.py` | assemble and lint a GenerationEnd |
| commit_task | `bash tools/commit_task.sh` | the only commit path; explicit paths, no trailers, never pushes |
| run | `bash tools/run.sh` | any command that may print >40 lines; `--bg` / `--wait` for long compute |
| expert_ttl | `PY tools/expert_ttl.py --coder <coder>` | prints `1h` or `5m`: a task's expert agent or its `-5m` twin |
| {{PROJECT_TOOL}} | `{{PROJECT_TOOL_COMMAND}}` | {{PROJECT_TOOL_PURPOSE}} |

## Skills <!-- roles: expert planner -->
<!-- one line per captured workflow; the SKILL.md is the canonical text -->
- {{SKILL_NAME}} — <what it automates, when to invoke it>

## Paths <!-- roles: expert coder router planner review critic discuss auditor curator -->
- Oracles (the ground truth X3 names): {{ORACLES}}
- Data: {{DATA_PATHS}}
- Generated (never hand-edited, H1): {{GENERATED_PATHS}}
- Hand-edited: {{HAND_EDITED_PATHS}}
- Scratch: `.run/` (gitignored; never the system temp)

## Build / run / test <!-- roles: expert coder router planner review critic discuss auditor curator -->
- Build: `{{BUILD_COMMAND}}`
- Run: `{{RUN_COMMAND}}`
- Test / the gate: `{{TEST_COMMAND}}` — green means {{GREEN_MEANS}}
- Modes: {{TEST_MODES}}
Long gates go through `tools/run.sh` with a raised timeout, never a poll loop.

## Conventions & house style <!-- roles: expert coder router planner review critic discuss auditor curator -->
- {{PROJECT_CONVENTION}}
<!-- project-specific only; the general house style is in the project-architect skill -->

## Environment <!-- roles: expert coder router planner review critic discuss auditor curator -->
- OS / shells: {{OS_AND_SHELLS}}
- Python: {{PY}} ({{PY_VERSION}})
- Pins: {{PINS_SUMMARY}} — detail in `docs/ops/INDEX.md`
- Harness gotchas that bite here: {{HARNESS_GOTCHAS}}

## Docs map <!-- roles: expert coder router planner review critic discuss auditor curator -->
- `docs/ops/INDEX.md` — setup, env, pins, per-topic ops notes
- `cookbook/INDEX.md` — techniques, grep by tag
- `rules/INDEX.md` — full rule texts
- `phase-ends/TASK_INDEX.md`, `phase-ends/RESEARCH_INDEX.md` — what was done and what was learned
- `docs/research-archive/` — migrated legacy reports
- `docs/retired/` — everything moved out of the load order
