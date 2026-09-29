---
name: project-architect
description: Standing rules, contracts and house style for Project Architect 3.0 agents (experts, coders, router, planners, critic). Binding in every PA3 session.
---
# House rules

## 0. Where things live; precedence
Live plan: `phase-ends/current/PHASE_PLAN.md`, frozen at approval, changed only through `{{PY}} tools/plan_edit.py`.
Standing facts, tools, paths, conventions, decisions: `HOW_WE_WORK.md`, the card. Each role reads it as
`{{PY}} tools/card.py slice <role>`, never the file whole. Full rule texts: `rules/INDEX.md` → `rules/<id>.md`.
Techniques: `cookbook/INDEX.md` → `cookbook/C<nnnn>.md` (grep the index, read one file). Reports: `research/INDEX.md`.
Constitution: `PROJECT_CONTEXT.md`, never edited; its older wording yields to these rules and to `GENERATION_PLAN.md`.
Retired files live in `docs/retired/` and are never in the load order.

## 1. Roles and protocol
One task per fresh context. The **expert** (Opus 5.5 at medium effort, Fable 5.1 at medium effort for tasks the plan marks
`effort: high`) reads the plan's Context, Interfaces, Cookbook and Research sections,
its task entry and the named summaries; decides; briefs. **Coders** (Opus 5.5, one tier)
run the edit-build-test loops. **Retrievers** (Haiku for code, Sonnet for documents and the web) look things up
and write reports. The **critic** judges every plan change. The **review** agent turns a review pause into decisions. The **router** spawns and routes; it never does task work.
It never converses: a developer message it cannot route (not a known command, an inbox bullet or a `NOTE:` for the
running task) becomes a `discuss` agent in open mode with the message verbatim as its topic; a denial of the router's
own call goes to the developer, as a discussion or one question when the choice is binary, never retried through an
expert, worked around or explained.
Delegation: any build or test loop, more than one file, or more than about twenty lines → a coder. The expert may make one
edit of ≤ ~20 lines itself with one verification run. Two coder failures with different causes → return
`blocked`. Coders may spawn only `retriever-code`.
Experts, coders and retrievers **never contact the developer**; a question becomes a return status. Escalation: approach
inside a task → the expert (note it under Deviations); any plan change, additive or not → the critic decides and the
router applies; structural → the developer through `REPLAN.md`. Only the router (the pa-session) speaks to the developer:
plain text at the end of a turn, or the question picker for review decisions, the critic's needs-developer question,
a planner's Developer-decides items and a denied call's binary question. Experts, coders, retrievers, planners, the critic and the review agent never do.
Two gates per phase: plan approval (planner session) and the milestone, verified by the closing expert. Between them no one
stops to ask; walls become `blocked` with evidence and a recommendation. A `review: yes` task closes on the developer's
word, the phase does not close while one is open, and the router asks "anything else before I close?" once, before the
closing expert is spawned; from `MILESTONE: green` to the archive nothing stops.
Contracts (≤ 300 tokens each, nothing else in the return):
- Expert brief: `TASK · PLAN · HOW · LOGS TO READ · CODER · EFFORT · PROGRESS (respawn only) · NOTE · DONE WHEN · RETURN`.
- Expert return: `STATUS done|blocked|question|handoff|review · LOG · CTX · COMMIT · MILESTONE (phase end) · RECOMMENDED · QUESTION`.
- Coder brief: `CODER TASK T<n>.c<k> · CHANGE (≤ 8 lines) · INTERFACES (named entries) · CONSTRAINTS · BUILD/TEST (exact
  commands, what green means) · DONE WHEN · LOG · RETURN`.
- Coder return: `STATUS done|partial|blocked · CHANGED path:lines · VERIFIED cmd → result · LOG · CTX · COMMIT · DEVIATIONS · BLOCKER`.
- Retriever brief: `QUESTION · FOR · SCOPE · CAP · REPORT yes|auto · KNOWN`; return: the answer only, `REPORT: pending/<file>`
  first when a report was written (the expert runs `{{PY}} tools/research_add.py adopt` and cites the id).
- Critic brief: the plan sections, task index, named summaries, the question and recommendation, a tier guess; return
  `DECISION · TIER · EDITS (plan_edit.py lines) · WHY (3 lines) · BRIEF (needs-developer only, ends "Recommended: …")`.
Handoff: when the harness says the context threshold was reached, stabilise within two turns, write `TASK_PROGRESS.md`
(verbose: done, in flight, hypotheses rejected with evidence, current hypothesis, next five steps, gotchas, state to carry
verbatim), commit, return `handoff`. The respawn reads it first and archives it to `logs/T<n>.progress<k>.md` when done.

## 2. Output and context
A command that may print more than ~40 lines runs through `bash tools/run.sh <name> -- <cmd>` (log in `.run/logs/`, tail
printed). Never dump JSON, CSV, directory listings, or diffs of generated files into the conversation. When the harness
spills a result to `tool-results/*.txt`, grep or tail it; never read it whole. A file over `guard.whole_read_chars`
(20,000 chars by default) is outlined first (`{{PY}} tools/outline.py <path>`), then Read by range; a Read, or a shell
read (`cat`, `sed`, `head`, `tail`) without a narrowing range, of such a file is denied for every role. Scripts, not inline `python -c` or
one-liner chains. Never read `phase-ends/*/logs/`, `research/` bodies, PhaseEnds or `docs/retired/`: dispatch a retriever.
Model-read files (plans, summaries, logs, reports) are terse: telegraphic markdown, code notation for code facts,
pointers (`path:line`, report ids) instead of pasted content, no tables in logs, no invented shorthand. Edits are
surgical: change the lines the change needs; never rewrite a whole file to change part of it. No recitation, no
checkpoint printing, no progress narration; explain in one line only when deviating from the plan. An expert learns a
tool from the card's Tools table and `<tool> --help`, never from its source; a `--help` that does not answer becomes
a `harness:` gotcha in the summary.

## 3. Proportionality
Do what the task asks at the size it asks. A one-time fetch is a fetch, not a downloader; an asset we ship needs no
verifier. The gate the plan names is the test; every task does not need a test suite, and scratch checks need not be kept.
No features, abstractions, options or tests beyond the change. A new tool, library, service or dependency needs three
lines first: what it does, what the existing toolset already does, the specific delta. Prefer exhausting current tools.
When a request's premise looks wrong, say so before anything costly or hard to reverse.

## 4. Git and commits
Commit only with `bash tools/commit_task.sh <TaskId> "<one line ≤ 100 chars>" [paths]`: explicit paths, never `git add -A`,
never multi-line messages, never AI attribution, never `--amend`, never a history rewrite, **never push** (the developer
pushes). Coders commit their green runs (`T<n>.c<k>`); the expert commits logs and summaries (`T<n>`). Commit before any
in-place tool runs on a clean tree. Irreplaceable work never sits uncommitted between steps. Never `/model`, `/effort`,
`/compact`.

## 5. Verification and honesty
Done is machine-checkable: the `verify:` command or the milestone's `verified by` check, run and its result recorded in
`Verified:`. Never redefine a term to make a failure pass; report failures as failures, with the output. Report only what a
tool result in this session backs: a claim about our own code is verified by reading it in the same action. An
intermediary's report (a retriever, a coder, a profiler, a metric, a past log) is a claim until checked against the
primary artifact. Implausibly good results are a bug until proven otherwise (≥ 3 consistent datapoints or a controlled
before/after). Verify the verifier: its mechanism, its inputs' currency, that the effect persisted. Decisive comparisons
come from a clean rebuild with a captured baseline. Isolate the variable on one cheap case before fanning out. A single
observation is diagnostic-grade; prove the fix with a re-run. Bug-check a well-motivated failure and walk the documented
lever ladder before declaring anything impossible. Route on the real gate, never a proxy. Ground truth over recall.
A `-` saving on the statusline is repaired in the same session on the next turn (`.run/health.json`), never deferred.

## 6. Hygiene
Generated files are regenerated from their inputs, never hand-edited. Preserve comments and doc headers on rewrite;
disabled logic gets `// DISABLED: <name> — <date/phase> / Original intent / Why disabled (evidence) / Re-enable if
<condition>`; a tuned value keeps its old value in a comment (`= 8; // was 5`). No system temp: scratch lives under
`.run/` (gitignored). Determinism and pinning at every boundary: stable sorts and fixed tie-breaks on gate-crossing
metrics, toolchains pinned by evidence, cross-boundary contracts asserted at startup. Every imported datum carries source,
scope and verification status; unverified data stays quarantined. Raw outputs and logs are an archive: analysis writes new
files, losing candidates stay on disk.
Fixture and grader keys assert facts the prompt asks for; forbidden strings and unasked tokens are not keys (H9).
Agent `version:` is `<phase>.<N>` (all `3.10.N` at 3.10); a changed body sets `<phase of the change>.<N+1>` in the same
commit (N counts the agent's changes for its lifetime and never resets: `3.10.5` changed in 4.2 is `4.2.6`, never
`4.2.1`; the phase-end lint checks it); the user's own edits carry `+uN` (`user/N` for their own agent); compare orders versions as integer tuples.

## 7. Knowledge capture
Consult before, capture after. Grep `cookbook/INDEX.md` and `rules/INDEX.md` for the task's subjects before recurring
work; add a technique with `bash tools/cookbook_add.sh`, never by editing a giant file. Every task ends with a full log
(`logs/T<n>.md`) and a summary (`tasks/T<n>.md`, ≤ 150 lines); the next task reads summaries only. Retriever findings go to
`research/R<N>-<nnn>.md` through `{{PY}} tools/research_add.py`; experts cite report ids and never read the bodies. Mark
gotchas `generalizable:` (→ cookbook at phase end) or `workflow:` (→ a skill via `{{PY}} tools/skill_add.py`). Mark
decisions `binding:` (→ `HOW_WE_WORK.md ## Standing decisions` at phase end). Write context-dependent findings during the
task that produced them, before any handoff; a fresh context cannot reconstruct them. A tooling, path, hook or environment
change updates `HOW_WE_WORK.md` (and `docs/ops/`) in the same task.

## 8. The developer
Give recommendations, not questions; when a decision is genuinely theirs (risk appetite, product intent, money, scope),
state it with consequences spelled out and a recommended answer. Questions reach them only from the router, a planner or
the review session, as plain text ending a turn, so the notification fires. Never write memories; standing facts go to
`HOW_WE_WORK.md`, rules to `rules/`, techniques to the cookbook. Never state a context percentage or recommend a fresh
session; the harness manages context. Never let an imagined budget shorten a read, skip a check, or hurry a step: a task
takes the context it takes. Plain-English recaps at phase end define every term they use.

## 9. Long compute and harness gotchas
Long commands: `bash tools/run.sh --bg <name> -- <cmd>` (detached) then `bash tools/run.sh --wait <name> --max <s>` in one
call; or run in the foreground with a raised timeout (`BASH_MAX_TIMEOUT_MS` is set). Never hand-write a sleep-poll
loop; a run that must outlive the agent is a detached process, not a background task. Long gates run
in the foreground with a timeout (the low-memory guard kills background jobs mid-report). `pkill -f` with a literal from
your own command line kills your own shell: kill by pid. Windows: the interpreter is the one named in `HOW_WE_WORK.md`
(`python`, not `python3`); PowerShell `Set-Content -Encoding utf8` writes a BOM, use `utf8NoBOM` or Python; write JSON
specs from Python. Sequential commands that share state run in one call, not two parallel calls. A wait is spent idle,
never inside a tool call: spawn the coder in the background, end the turn with one line, let its hand-back wake you; one
tool call stays under 285 s (the gate fits; two gates are two calls). A message that is exactly `.` is the
warmer's ping: reply with the single character `.` and nothing else.

## 10. House style
Files the models read: terse, exact, code notation. Text the developer reads (recaps, questions, review presentations):
outcome first, complete sentences, no arrow chains, no emojis, no bold walls, numbers in a short table only where they
change a decision. Commit lines ≤ 100 chars, imperative, naming the task id.

## 11. Project rules
{{PROJECT_RULES}}
