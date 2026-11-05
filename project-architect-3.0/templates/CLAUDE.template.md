# {{PROJECT_NAME}} — Project Architect 3.0
{{PROJECT_TAGLINE}}

Roles: Claude (the partner) · expert · coder · retriever. Rules: the `project-architect` skill.
Standing facts and tools: `HOW_WE_WORK.md`. Live plan: `phase-ends/current/PHASE_PLAN.md`
(changed only via `tools/plan_edit.py`). Constitution: `PROJECT_CONTEXT.md` (never edited).
Roadmap: `GENERATION_PLAN.md`. Full rule texts: `rules/INDEX.md`. Techniques: `cookbook/INDEX.md` (grep it).
Older documents: `docs/PRE_PA3_INDEX.md` lists this project's documents from before PA3 (grep it); the memory archive
is `.claude-state/memory/gen*.md`. Before you change something or explain why it's that way, check it for a doc on it.
Drive: `.claude/pa.json` `drive`. In `coop` work runs only as a batch the developer and Claude lock in; nobody is reminded.

Every agent, always: never push; no AI trailers; never `/compact`, `/model`, `/effort`;
never Read `phase-ends/*/logs/`, `research/`, PhaseEnds or `tool-results/*.txt` whole: grep/tail or dispatch
a retriever; commands that may print >40 lines run via `tools/run.sh`; commit via `tools/commit_task.sh`;
a one-time fetch is a fetch, not a tool.

Fail-safes: {{FAILSAFE_1}} · {{FAILSAFE_2}}
