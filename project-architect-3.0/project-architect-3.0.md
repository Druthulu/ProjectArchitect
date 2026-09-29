# Project Architect 3.0

The methodology that turns Claude Code into a governed project architect: it takes a rough idea,
an existing codebase, or an old-methodology project and produces a scoped, phased, documented
build — then runs the build phase by phase with a living document system, pinned models, measured
costs and hard stops between phases so any fresh session can reconstruct exactly where the
project is and how to work on it.

---

## For humans

Two commands install the system. Once per machine, from the package folder:

    python pa_install.py --root --yes

Once per project, from the project's own root:

    python pa_install.py --project . --yes

After that you open a terminal in the project and type `claude`. The session reads a seed,
determines where the project is (planning a generation, planning a phase, the work loop, a
review pause) and starts. You interact at a few moments: approving a plan, answering a
review's decisions, responding when the critic says the developer must decide, answering the
close question, allowing a command outside the allow rules, and opening
`/discuss [model] [effort]` when you want to think alongside the system (Opus 5.5 at medium unless you opt
into Fable or Sonnet, or high or max effort; `/thoughts` stays an alias for one release). Everything else — spawning experts,
briefing coders, running tests, committing, closing phases — happens without a keypress.

The roles in one paragraph: a planner drafts the plan you approve; a router spawns one expert
per task and never does task work itself; the expert thinks, decides and briefs coders (which
run the edit-build-test loops) and retrievers (which look things up); a critic judges every
plan change an expert proposes; a review agent turns a
review pause into numbered decisions for you.

A day in the life: you launch with `claude`, the router reads the seed and picks the next
task, spawns the expert in the background and waits. The expert reads the plan, spawns a
coder, waits idle in the background until the coder's hand-back wakes it (a per-session warmer
daemon pings idle agents with `.` before the cache TTL, at 285 s or 3,300 s, relayed by the
router's Monitor; the agent answers `.`), writes the task log and summary, and returns done. The router marks the task done and moves to
the next. When every task is done the closer verifies the milestone, writes the phase-end
record and archives the phase. You relaunch for the next one.

Where to look: `HOW_WE_WORK.md` for standing decisions, tools and environment;
`phase-ends/current/PHASE_PLAN.md` for the live plan; `phase-ends/TASK_INDEX.md` for what
was done; the cookbook (`cookbook/INDEX.md`, grep by tag) for techniques; `rules/INDEX.md`
for every rule in force.

---

## Philosophy

The creed, carried from 1.3.0 and amended by three generations of experience:

- The constitution is permanent. The rules, the cookbook, the standing decisions and the
  phase records are the living system. Never conflate the layers.
- History is append-only. A rule is never deleted; it is superseded with a note. A phase
  record is never rewritten; a correction is a new record. A file is never removed from the
  repository; it moves to `docs/retired/`.
- Rules are living. They are proposed at phase ends, adjudicated by the planner, and added
  to `rules/` as individual files. The expert writes `Rule candidate:` in the task summary;
  the phase planner adjudicates.
- Every phase milestone is an observable, machine-checkable outcome. The gate, not anyone's
  say-so, is the arbiter.
- Generations are evolutionary leaps, not version bumps. A new generation only when a
  fundamentally new capability requires the prior generation stable and proven. Most projects
  need two or three.
- Knowledge compounds only if captured: consult the cookbook before, feed it after, and write
  context-dependent findings during the session that produced them.

New in 3.0: one task per fresh context (the expert knows its task, not the whole phase). The
plan is the contract between the developer and the system; `plan_edit.py` is the only writer.
Nothing enters a context that a pointer could replace: a summary id instead of a pasted body,
a `file:line` instead of a code block, a report id instead of a retriever's raw output. The
plan's `reads:` line names what the expert loads; everything else is a retriever dispatch.

---

## The document system

The five layers of 2.0 are replaced by a deeper structure. Precedence runs top to bottom;
where wording differs, the higher layer governs.

1. `CLAUDE.md` (about 190 tokens): the roles line, pointers to the project-architect skill,
   `HOW_WE_WORK.md`, the live plan and the constitution. It is not the rules; it points at
   them. The rules themselves live in `.claude/skills/project-architect/SKILL.md` and each agent
   file names that skill under `skills:`. Leaf agents (retrievers, the critic) carry
   `omitClaudeMd: true`, which drops `CLAUDE.md` and the auto-memory index from their
   context; the critic keeps the skill because it must quote the rules.
2. The project-architect skill: eleven numbered sections (precedence, roles, output, proportionality,
   git, verification, hygiene, knowledge, the developer, long compute, house style) plus a
   project-rules section that holds project-specific additions. This is what governs every
   expert and coder.
3. `HOW_WE_WORK.md`: standing decisions, environment, tools, the docs map. The living
   reference for how this project runs on this machine. Target size about 3,500 tokens.
4. `PROJECT_CONTEXT.md`: the constitution. Permanent and static; never edited after
   generation.
5. `GENERATION_PLAN.md`: the generation-level roadmap (phases, milestones, scope, ordering
   rationale). Changed only at generation boundaries.
6. `phase-ends/current/PHASE_PLAN.md` and its `tasks/`, `logs/`, `research/`, `discussions/`
   directories: the live work of the current phase. The plan is frozen at approval and changed
   only through `plan_edit.py`. Task summaries, logs and research reports are written by the
   agents that do the work. At phase close, `phaseend_index.py archive` moves `current/` to
   `phase-ends/phase-<N>/`.
7. `PhaseEnd_Phase<N>.md` and `GenerationEnd_<G>.md`: the append-only build history.
   Assembled by scripts from the phase's task logs and summaries, the milestone verification,
   and the closer's recap.
8. `rules/` (one file per rule), `cookbook/` (one file per technique), `docs/ops/` (environment
   and build references).

Auto memory stays on. Claude Code's auto memory keeps one directory per repository under
`~/.claude/projects/<slug>/memory/`. The first 200 lines or 25 KB of the index load at
session start for every agent that reads `CLAUDE.md`. PA3 keeps it enabled and curates it at
every generation start: standing facts belong in `HOW_WE_WORK.md`, rules in `rules/`,
techniques in the cookbook. The memory index is kept small and current by a curator agent
rather than switched off. The memory directory lives in the repository (`.claude-state/memory/`) behind a
junction so a fresh machine restores it with `pa_install --project`.

---

## Roles

| Role | Agent | Model | Effort | Spawns | Tools |
|---|---|---|---|---|---|
| Planner (generation) | planner-gen | Opus 5.5 | medium | retrievers | — |
| Planner (phase) | planner-phase | Opus 5.5 | medium | retrievers | — |
| Router | pa-session | Sonnet 5 | medium | experts, planners, critic, review, discuss, curator | — |
| Expert (default) | expert-opus55 | Opus 5.5 | medium | coders, retrievers | — |
| Expert (hard, `effort: high`) | expert-fable | Fable 5.1 | medium | coders, retrievers | — |
| Coder (default) | coder-opus55 | Opus 5.5 | medium | retriever-code | — |
| Retriever (code) | retriever-code | Haiku 4.5 | — | — | Read, Grep, Glob, Write |
| Retriever (digest) | retriever-digest | Sonnet 5 | medium | — | Read, Grep, Glob, Write |
| Retriever (web) | retriever-web | Sonnet 5 | medium | — | WebSearch, WebFetch, Write |
| Critic | critic | Fable 5.1 | medium | — | — |
| Review | review | Fable 5.1 | medium | retriever-code, retriever-digest | — |
| Discussion | discuss, discuss-high, discuss-max | Opus 5.5 | medium, high, max | — | — |
| Memory curator | memory-curator | Fable 5.1 | medium | — | — |

Models and efforts are pinned per agent file; the developer never touches `/effort` or
`/model`. The cost structure follows from the pinning: Opus 5.5 is the default builder and
expert (coders, the planners since 3.9.5 and the medium-effort expert), Fable 5.1 the thinker for hard tasks (
the critic, the hard-tier expert), Haiku the cheapest lookup (code retrievers), and
Sonnet the middle ground (the router, digest/web retrievers). The router runs
on Sonnet because it is purely mechanical: it reads a seed, picks the next task, spawns an
expert and waits. Judgment lives in the subagents. The phase planner marks `effort: high` only
when a task's done-when rests on a judgment no test can arbitrate (a design decision, a harness
probe, a proof read from evidence), never for size or importance; at most one task in five per
phase plan, each high mark's judgment named in the plan's Rationale.

The maximum spawn depth is three: router (0), expert (1), coder (2), retriever-code (3). The
depth is set explicitly in the environment (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3`) so a
harness default change cannot silently break it. A coder's retriever is a leaf by construction
(no `Agent` tool) so the chain cannot grow further.

The expert delegation rule: a coder for any build or test loop, more than about twenty lines,
or more than one file. The expert may make one edit of twenty lines or fewer itself with one
verification run. Two coder failures with different causes return
`blocked`.

Every role reads a file over `guard.whole_read_chars` (20,000 chars by default) by range only:
a Read without `offset`/`limit` on such a file is denied, and the denial carries the file's
outline inline (what `tools/outline.py <path>` prints: header block, symbols with their intent
lines, section comments; capped at 60 lines, Markdown at depth 2) and ends "Read the ranges you
need with offset and limit". Retrievers get the same map without a shell; the served outline
writes the same `outline` credit note as the script. The audit's `whole_reads` row mirrors the
same threshold. A retriever's report, when written, lands first at
`research/pending/<agent>-<slug>.md`; the retriever returns `REPORT: pending/<file>`, and the file
is adopted into `R<N>-<nnn>.md` by `tools/research_add.py adopt`, run at the expert's hand, at
`task_log.py finish`, and at `phaseend_index.py assemble`.

---

## Lifecycle

The launcher (`tools/launch.py`) checks the project's state and determines the mode:

- No `GENERATION_PLAN.md`, or all its phases closed: planner-gen mode.
- Generation open, no approved phase plan: planner-phase mode.
- `REPLAN.md` present: planner-phase in replan mode.
- `REVIEW.md` present: review mode.
- Otherwise: the router loop.

In planner mode the router spawns a planner (generation or phase) which explores only through
retrievers, drafts into `.run/`, and returns a summary. The router puts the summary in front
of the developer through the question picker: approve, show the full plan first, or send
changes back. On approval the router writes the plan with `plan_edit.py from-draft`, stamps
it with `plan_edit.py approve`, records ratified rules, and commits. From that point
`plan_edit.py` is the only writer of the plan file; a `PreToolUse` hook denies any other
write to it.

In the router loop, at every task boundary the router consumes `INBOX.md` if the developer
left notes, asks `plan_edit.py next` for the next runnable task, stamps the statusline and
spawns the expert in the background. Then it ends its turn; the harness wakes it when the
expert returns. While an expert runs, the statusline shows the task, the agent, the context
and the time elapsed. The expert reads the plan's Context, Interfaces and Cookbook sections,
its own task entry, and only the summaries the entry names. It works, spawns coders and
retrievers as needed, and returns its contract.

When an expert returns `done`, the router marks the task done
and goes to the next task. When it returns `question` or `blocked`, the router sends the
question to the critic. A `handoff` (context crossed the 350,000-token threshold) triggers
a respawn: the expert writes `TASK_PROGRESS.md`, commits and returns; the router respawns
the same agent with the progress file in its brief. The respawn archives the file to
`logs/T<n>.progress<k>.md` when done.

When `plan_edit.py next` prints `NONE`, the router asks the developer once whether anything
else should come first, then spawns the phase closer (`expert-opus55`).
The closer runs `phaseend_index.py verify`, which executes every `verified by:` clause of the
milestone and prints GREEN or RED per clause. It writes `RECAP.md`, promotes techniques from
`generalizable:` lines to the cookbook, commits, and returns `MILESTONE: green` or `red`. On
green the router assembles, lints and archives the phase, commits, and gives the developer
the recap with the agent-runs table. The session ends with `Relaunch: /clear then go`.

The carry audit rides on the close. `phaseend_index.py assemble` runs `pa_ledger.py audit --phase`,
which walks the phase's transcripts once (zero tokens, repeatable) and writes its tables into the
PhaseEnd's Audit section: reads by file and role, writes by file, results by kind, whole-plan and
tool-source reads, spilled results read whole, noise, carry per request against the previous phase,
and candidates promised beside measured. Rows past the thresholds become `- flag:` lines; the seed
then carries `Audit flag:` and the router spawns the auditor (Fable, medium effort) before the next
planner. The auditor judges tables, never transcripts: one verdict per flagged row (tool, split,
guard, script-fix, leave), project-level fixes applied through coders, harness-level ones written as
`Tool candidate:` lines in `phase-ends/current/AUDIT.md` with what the tool does, what the model did
instead, the occurrences, the saving on the credit formula and the build size. The planner copies them
into its return; the developer ratifies each with one picker question at plan approval; a built tool
earns the savings credit automatically and the next audit prints measured beside promised.

At a generation boundary, `genend_index.py assemble` builds the `GenerationEnd` from the
phase-end files. The next `claude` opens in planner-gen mode and the generation-start ceremony
(the memory curator, then the auditor over the closing generation's last PhaseEnd) runs before the
new planner drafts.

Every `/discuss [model] [effort]` session that proceeds writes a discussion record with a `Status:` tag
(`open`, `executed`, `planned:<phase> T<k>`, `deferred:<phase>`, `dropped`, `failed`) and an
index line; the phase's discussion lines fold into the PhaseEnd on archive, and cumulative
history lives in `phase-ends/DISCUSSION_INDEX.md`. A record's `## Deferred` lines and an
inbox's `later:` lines both flow into the PhaseEnd's `## Deferred` block, so a later planner
either plans the item as a task or leaves it deferred with one line of reason.

Every configured MCP server adds its instruction block to every session, whether or not the
task at hand needs it; `doctor` lists the servers found in `~/.claude.json` and the project's
`.mcp.json`, and the standing advice is to disable the ones a project does not use.

---

## Contracts

Every role returns a fixed-format contract of at most 300 tokens. Nothing else. The contracts
are the system's only inter-agent communication; they carry status, pointers and one-line
recommendations, never pasted content.

Expert brief (router to expert):
```
TASK: T3 — <title>
PLAN: phase-ends/current/PHASE_PLAN.md
HOW: HOW_WE_WORK.md
LOGS TO READ: phase-ends/current/tasks/T1.md, tasks/T2.md
CODER: opus55 | none              EFFORT: high | medium
PROGRESS: <path>                   (respawn only)
NOTE: <developer text or critic's WHY>   (only when present)
DONE WHEN: <from the task entry>
RETURN: the expert contract, ≤300 tokens, nothing else.
```

Expert return:
```
STATUS: done | blocked | question | handoff | review
LOG: phase-ends/current/tasks/T3.md
CTX: 142k
COMMIT: abc123 | none
MILESTONE: green | red | n/a       (phase-end only)
RECOMMENDED: <one line>
QUESTION: <one paragraph; only if question>
```

Coder brief (expert to coder):
```
CODER TASK: T3.c1 — <one line>
CHANGE: <what to implement; files and functions; ≤8 lines>
INTERFACES: <named entries from the plan's ## Interfaces>
CONSTRAINTS: <2–4 lines>
BUILD/TEST: <exact commands; what green means>
DONE WHEN: <machine-checkable>
LOG: phase-ends/current/logs/T3.c1.md
RETURN: the coder contract, ≤300 tokens.
```

Coder return:
```
STATUS: done | partial | blocked
CHANGED: path:lines, …            (≤6 entries)
VERIFIED: <command -- result>
LOG: phase-ends/current/logs/T3.c1.md
CTX: 96k
COMMIT: def456 | none
DEVIATIONS: <one line | none>
BLOCKER: <one paragraph; only if partial/blocked>
```

Retriever brief: `QUESTION · FOR · SCOPE · CAP · REPORT yes|auto · KNOWN`. Return: the
answer only, first line `REPORT: R24-007` when a report was written. Code retrievers return
signatures with `file:line`, call sites one per line, shared state and nearby gotchas, up to
40 lines.

Critic brief: the plan sections, task index, named summaries, the question, the
recommendation, and a tier guess. Return: `DECISION (continue | edit | needs-developer) ·
TIER · EDITS (plan_edit.py lines) · WHY (3 lines) · BRIEF (needs-developer only, ends
"Recommended: ...")`.

---

## Replan tiers and the critic

Changes are graded by scope and routed to the agent with the right authority:

- Approach within the task: the expert decides and notes it under Deviations.
- A developer's inbox item that only adds within the milestone: the router applies the edit and
  adds a Changes entry.
- Any plan change an expert proposes, additive or not: the critic decides. It returns `continue`
  (carry on), `edit` (exact `plan_edit.py` commands the router runs and commits) or
  `needs-developer` (a one-paragraph brief with a recommendation the router puts to the
  developer in the question picker).
- Structural (milestone or scope change): the developer, through `REPLAN.md`. The next
  `launch.py --seed-only` starts a planner-phase pass that archives the old plan and writes a
  new one for approval.

The critic exists because the expert's context is deliberately narrow. An expert knows its own
task, not that another task tests the same thing from the other side. When an expert hits a
wall it will sometimes recommend removing the wall. The critic sees the full task index, the
milestone, and the plan's rationale, and can judge whether the recommendation protects the
milestone or undermines it. Cost: about $0.50 per invocation.

---

## Discussion, questions, notifications

The developer is never interrogated. Agents give recommendations, not questions; when a
decision is genuinely the developer's (risk appetite, product intent, money, scope), the
recommendation states consequences and ends with a recommended answer.

Three channels reach the developer:

1. The question picker (plan approval, review decisions, the critic's `needs-developer`, a
   planner's Developer-decides items): interactive, in the terminal.
2. Plain text at the end of a turn (the close question, and anything the router must ask
   outside the picker).
3. `/discuss [model] [effort]` (the discuss agent): the developer opens a read-only thinking session with
   Opus 5.5 at medium (opt-in: fable or sonnet, high or max; `/thoughts` an alias for one release)
   in its own terminal view. `Edit` and `Write` are denied while the discussion
   flag is set. When the developer says `proceed`, the agent writes the record under
   `discussions/` and returns the decisions as exact plan edits for the router to apply.

A note for a running expert is typed into the expert's own view (its context stays); the
router relays nothing and relaunches only on `stop:` or `relaunch:`. `INBOX.md` takes
anything for the next task boundary.

Notifications: at the default `toast: waiting` level (`.claude/pa.json`; the others are `all`
and `off`) a Windows toast fires only when something waits on the developer: a `Stop` that ends
on a question (the toast carries its text), a permission prompt or a picker question (Claude
Code reports both as `permission_prompt`), a new `REVIEW.md` or `REPLAN.md`, a finished
discussion, and a turn stopped by an error. Phone pushes come from Claude Code itself: with
Remote Control on and its `/config` push switches set (the machine install turns them on
unless you set them yourself), permission prompts and picker questions reach the Claude app.
The toast child gets a console without a window (`CREATE_NO_WINDOW`) so there is no flash and
no focus theft.

---

## Mode 1 intake

3.0 change: Gate 2 no longer prompts for `/effort max`. Generation happens in `planner-gen`
at medium effort; the model and effort are pinned by the agent file.

In 3.0 the intake and the constitution happen in a plain session (`claude --agent plain`) or
in Claude Chat with the chat kit; that session writes the outputs of the next chapter itself.
The governed loop starts at the first bare `claude` after `pa_install.py --project`; an
intake agent is a later item.

Conversational, not an interrogation. Weave questions into the discussion; track silently
against the intake checklist. Do not generate the constitution during brainstorming, no matter
how much detail accrues.

Intake checklist (all 12, silently tracked):

1. The elevator pitch — what is this, in two sentences?
2. Motivation — why build it; is it a rewrite/successor of something?
3. Who is the developer — background, skill level, solo or team? (feeds `who-is-dev`)
4. Tech stack — chosen or open; existing constraints?
5. Hard constraints — platform, deadlines, budget, licenses, privacy/closed-source posture?
6. Core features — the non-negotiable heart.
7. Stretch features — wanted, not required.
8. Dream features — someday/maybe (Parking Lot material).
9. Known risks and pain points — what worries them; what failed before?
10. Definition of success — what does "it works" observably look like? (feeds milestones)
11. Multi-user/deployment reality — who else touches it, where does it run?
12. Resource management — data, compute, storage, external services, costs.

Gate 1, the structured review (when the developer says "ready"): do not generate yet. Present
recommended phase ordering with reasoning, standard features they missed, nice-to-haves worth
considering, scope-creep warnings, methodology concerns, architecture suggestions with
reasoning, and risk flags. Then ask what to adjust.

Gate 2, the generation confirmation (after they confirm the review): generation is Tier-1
work. In 3.0 the planner-gen agent runs at medium effort by its agent file; the developer is
not asked to toggle anything. The intake session writes everything listed in the next
chapter; the first bare `claude` afterwards enters planner-gen mode.

---

## Generating the constitution

3.0 change: the outputs list is updated. The registry is replaced by individual files under
`rules/`; effort and model are pinned per agent file, nothing is mapped per phase; the
cookbook is generated as `cookbook/` entries and the ops reference as `docs/ops/` topics.
`CLAUDE.md` is about 190 tokens of pointers with two default fail-safes, written by the
installer (item 3 below).

Phase design principles (you design the phases, not the developer):

- Core before periphery. Data before processing. Foundation before features. Safety before
  action. Observation before optimization.
- Every phase produces something runnable and testable. No planning-only or models-only phases.
- Every phase ends with an explicit Milestone line: an observable, machine-checkable outcome
  ("`make check` green", "the level loads and the character walks", "round-trips 100 files
  byte-identical") — the gate that the closer will hold the phase to.
- Validation is a dedicated phase, not an afterthought.
- Enhancement layers are toggleable, feature-flagged modules added one at a time and measured
  before the next.
- Size phases to the developer (see Skill-level handling): beginners get fewer, chunkier
  phases; advanced developers get fine-grained ones.

Domain phase-ladder starting points (adapt, do not copy):

- Game/game-tool: foundation and skeleton, data formats and I/O, core domain model, core
  mechanic vertical slice, content layers, UI shell, integration, validation, polish,
  packaging.
- Backend/API: skeleton and config, data layer, domain model, first vertical endpoint, auth
  and safety, remaining endpoints, integration, validation, observability, deployment.
- Desktop app: skeleton and DI shell, data/persistence, core engine, minimal UI, feature
  layers, import/export, validation, polish/packaging.
- Automation/pipeline: skeleton and config, source connectors, transform core, dry-run
  end-to-end, guarded write path, scheduling, observability, validation, hardening.
- RE/decompilation/format: environment and oracle setup, deterministic extraction pipeline,
  ground-truth maps, byte-exact verification gate, first verified unit, scale-out with a
  knowledge flywheel, the hard tail via an escalation ladder, integration/packaging.

Generation design principles:

- Generations are evolutionary leaps, not version numbers. A new generation only when a
  fundamentally new capability requires the prior generation stable and proven, and there is
  a hard dependency between them.
- The constitution's roadmap covers Gen 1 in phase detail; later generations get a Generation
  Map sketch and a Future Generations section, not premature phase lists.

Generation outputs (the complete set):

1. `PROJECT_CONTEXT.md` on the skeleton — every section, with the Quick Reference Card, Key
   Decisions, Build Roadmap with machine-checkable Milestones, Parking Lot.
2. `rules/G*.md` — the project's domain rules as individual files with an `INDEX.md`, seeded
   from the project's oracles, definition-of-done gate, data constraints and environment
   constraints.
3. `CLAUDE.md` is written by the installer (about 190 tokens of pointers and two default
   fail-safes: never push, the milestone gate is the arbiter); nothing regenerates it, the
   developer edits the fail-safe lines by hand if the project's `G*` rules justify others.
4. `docs/ops/` — environment, build/run/test commands, version pins as known at generation,
   one file per topic with an `INDEX.md`.
5. `cookbook/INDEX.md` — the initial techniques, one file per entry, named for the project's
   recurring craft.
6. `HOW_WE_WORK.md` — the card, generated from environment detection (`pa/install/detect.py environment()`), the
   developer interview and the constitution's opening and Quick Reference Card. It is one file, sliced per role
   (`{{PY}} tools/card.py slice <role>` prints only the sections whose heading tags name that role) and capped
   (`card.max_chars`, checked by `{{PY}} tools/card.py check`); the archive enforces the cap at phase end and
   nothing is ever appended to it — a standing decision or an environment fact is routed to a rule, a product doc, a
   Tools-table row or `docs/ops/` instead.
7. `HOW_WE_WORK.md ## Developer` filled from intake item 3 and the skill-level assessment
   (no memory is written); it is one of the sections the `router` and `planner` slices carry beyond the shared ones.

---

## Rule detection

3.0 change: "propose at PhaseEnd" becomes "the expert writes `Rule candidate:` in the task
summary; the phase planner adjudicates."

| Signal | Example | Likely rule type |
|---|---|---|
| The developer corrects a tool/framework behavior you assumed | "maspsx's default is not latest" | Pin/make-explicit rule |
| The same mistake happens twice in one phase | Two stale-build false diffs | Verification rule |
| The developer says "never do X" / "always do Y" | "never push", "always dry-run first" | Process rule |
| A wiring step gets missed | Registration forgotten | Checklist rule |
| An assumption proves wrong in practice | "the archive is per-day" was not | Provenance/verification rule |
| A strong workflow preference emerges | Per-task commits after the log | Process rule + memory seed |
| A costly gotcha with a cheap guard | The enum-zero config trap | Cookbook entry first; rule if it recurs |

When proposing: state it specifically ("always Y when Z", not "be careful"), one-line
justification, developer approves or modifies or rejects — never add unilaterally. Some
things are deviations to record, not rules. Techniques go to the cookbook, norms of conduct
to the rules.

A rule enters the package's seed only when it has recurred across projects and fits in one
sentence of conduct; otherwise it stays a project rule (a `G*` rule or a per-phase `R` rule).
A sharper form of an existing seed rule becomes one clause in that rule, never a new id;
techniques never enter the seed.

---

## Migration

Migration replaces 2.0's Mode 4. The alternate-heading sweep rule is kept: when consolidating
rules from an old project, sweep every rule-bearing heading, not just the literal "Rules Added
This Phase." Older phases carry rules under alternate headings ("Key Rules Confirmed",
"Architecture/Design Decisions", "Key technical decisions"); a consolidator keying only on the
canonical heading silently misses them.

PA3 migration is one engine (`pa/install/migrate.py`) and three thin layout modules. The engine
handles git mv from a mapping table, heading-level splits, retiring, generating and committing.
The layouts differ only in their mapping tables and their `HOW_WE_WORK` sources. The installer
detects the project shape and selects the layout automatically.

The three paths:

1. Stock 2.0 (`pa/install/stock20.py`): a project with `CLAUDE.md`, `RULES_REGISTRY.md` and
   `phase-ends/` in the standard 2.0 layout. The registry is split by its `###` headings into
   individual files under `rules/`. The cookbook is split by its `##` headings into `cookbook/`
   entries. The ops-setup document is split by its `##` headings into `docs/ops/` (giant
   sections split again at `###` if over 400 lines). The effort map, the old templates and
   the old `docs/project-architect.md` are retired. `CURRENT_PHASE.md` becomes a partial log.
   PhaseEnds stay in place; a `LEGACY_INDEX.md` is generated. The PA2 `SessionEnd` backup
   hook is removed from settings; no copy is kept, the harness holds transcripts for ten
   years (`cleanupPeriodDays`).
   The interphase sorts after the last closed phase and before the next.

2. 1.x-shaped (`pa/install/onex.py`): a project whose `Project Context Markdowns` (or
   similar) folder holds PhaseEnds with underscore separators, plan/verdict/prereg documents
   mixed in, a large rules registry under a non-standard name, and no `.claude/settings.json`
   (or an empty one). PhaseEnds are renamed in place (`_` to `.` between version tokens) and
   `sort -V` checked. Non-phase documents (plans, verdicts, prereg, checkpoints, audits) move
   to `docs/research-archive/` with a generated index, linked from `RESEARCH_INDEX.md`'s
   `## Legacy` section. The rules registry is split from its own headings into `rules/`. The
   project context becomes `PROJECT_CONTEXT.md` (verbatim, static). Settings are written
   fresh from the project snippet. The interphase label is proposed from the newest phase
   family; `--interphase` overrides.

3. Overlay kit (`pa/install/overlay.py`): a project with a `DIGEST.md`, R-numbered rules
   above 100, a kit folder with its own agents and commands, and a large cookbook with a
   separate index. Rules come from the DIGEST's rule section and from the kit's
   `registry-E*.md` templates. The DIGEST is frozen in place with a header line. The cookbook
   is streamed into `cookbook/` entries with index rows taken from the existing
   `cookbook-index.md` when its rows match the headings. The kit folder and its agents are
   untouched; name collisions with PA3 agents fail the install and name the collision. Kit
   agents that do not collide are listed in `HOW_WE_WORK` as extra experts the plan may name.
   Existing hooks in `.claude/settings.json` are appended to, never replaced. The interpreter
   is `python3` when the project runs on POSIX.

Every path follows the same runbook discipline:

- Dry run first. `--dry-run` prints the full mapping table (`git mv` lines, split counts,
  retire lines, unmapped files) and writes nothing. The developer reviews the table and
  confirms once.
- One confirmation. After the dry run, the install runs with one confirmation prompt per
  phase of the install. No per-file prompts.
- `git mv` only. Every move preserves history. An untracked source is moved as a plain file
  and noted. `git log --follow` on a moved file shows the pre-move commit.
- `docs/retired/` for everything leaving the load order. Nothing is deleted.
- Never push. The developer pushes when satisfied.
- The migration's first planning step is the curator. When a project already has a memory
  directory, the memory curator runs before the planner drafts the first phase, demoting
  generation-specific memories into a linked archive and keeping the index small.

The resume pointer (`.run/install-state.json`) records the last completed step. A crash or
interrupt is followed by a rerun that resumes after the last good step. A second run with
everything done prints SKIP for every step.

---

## Knowledge

PA3 captures knowledge through four indexed stores, each managed by a script that enforces the
format and keeps the index current.

Rules (`rules/INDEX.md`, one file per rule): `tools/rules_add.py add` writes the file and
appends the index line. Rules are proposed at phase end (`Rule candidate:` in a task summary),
adjudicated by the phase planner, and never added unilaterally. A superseded rule keeps its
file with a `superseded-by:` line; the index marks it.

Cookbook (`cookbook/INDEX.md`, one file per technique): `tools/cookbook_add.sh --title ... --tags
... --file ...` writes the entry and appends the index. Planners and experts grep the index
by tag before recurring work; they never read it whole (the index can grow past 400 lines in
a large project). A technique that recurs across projects is stronger evidence that it is
general.

Research (`research/INDEX.md`, one file per report): `tools/research_add.py new ... | index
<id>` writes the report and appends the index. Retriever findings land here; experts cite
report ids and never read the bodies. The index carries the title, the date and the scope.

Skills (`.claude/skills/`): `tools/skill_add.py` writes a skill file from a workflow gotcha.
Skills are named per agent and listed in the agent file's `skills:` frontmatter. They load
into the system prompt of every agent that names them.

The flywheel: every task ends with a full log (`logs/T<n>.md`) and a summary
(`tasks/T<n>.md`, at most 150 lines). The next task reads summaries only. Gotchas are
marked `generalizable:` (promoted to the cookbook at phase end) or `workflow:` (promoted to a
skill). Decisions are marked `binding:` (promoted to `HOW_WE_WORK.md` at phase end). The
proportionality rule applies: no features, abstractions, options or tests beyond the change.
A one-time fetch is a fetch, not a downloader.

The generation-start ceremony: when a generation closes and the next one opens, a
memory-curator agent (Fable 5.1 at medium effort) runs before the planner drafts. It reads
every memory file, the cookbook and rules indexes, and `HOW_WE_WORK.md`. It demotes
generation-specific state into archive files: memories into `memory/gen<N>.md`, cookbook
entries into `cookbook/gen<N>/`, rules into `rules/gen<N>/`, and superseded standing decisions
into `docs/retired/HOW_WE_WORK.gen<N>.md`. It commits the result and returns a recap. The
demotion pattern is always: append content to an archive under a generation directory, remove
the pointer from the main index, and keep one link line in the main index pointing to the
archive. Active demoted rules keep their main-index line, marked `demoted:gen<N>`.

The four subcommands of `tools/curate.py` (`memory`, `cookbook`, `rules`, `how-we-work`) each
support `--dry-run`. The `how-we-work --report` subcommand lists trim candidates by date and
token budget; `--retire` moves them into the generation archive. The pa-session detects the
ceremony via a `curate:` line in the seed (written by `launch.py` when a `GenerationEnd`
exists) and spawns the curator in the foreground before the planner.

The curator's pre-flight is `pa_ledger.py doctor --sizes`: the sizes of the governed files
(`CLAUDE.md`, each skill, `HOW_WE_WORK.md`, the memory index, the cookbook and rules indexes)
and the current expert seed against their targets. Over a target is a WARN the curator acts
on; only a memory index past the harness's own cap fails. The closer's `## Audit` section in
every PhaseEnd records the expert seed per phase, its growth over the previous phase, and
the same sizes.

Standing decisions of 3.1 through 3.3 that change how a project is run:

- The measured saving is what a vanilla Claude Code session would have paid to carry what the
  helpers kept out: one 1h cache write per result per session, re-reads at the read price, the
  carried results rewritten only at a real idle gap over an hour, bounded by the parent model's
  window, minus every helper run's seed carry. One rule at every scope (run, session, window,
  project, account). The statusline reads it in the subscriber's units (percent of the 5h and
  7d windows, weeks of allowance at the user's own live exchange rate, "N% fewer tokens than
  vanilla, lasts Mx longer" per scope). Every input is the user's own ledger, nothing is
  calibrated from anyone else's runs; the modeled replay stays in `report` as a cross-check.
- The meter weighs model families differently: a Fable dollar consumed the 5h window about
  ten times faster than an Opus dollar (as of 2026-09-20). The fit carries per-family weights
  and the "percentage of window" figures are summed per family.
- One ledger per machine, written only by its own hooks. Other roots are read in copy mode,
  never over the WSL file boundary. The fit, the window samples and the window savings read
  the union on the sampler's change path and in the CLI; the hooks' rebuild and the render
  stay local.
- A project's memories live in its repository (`.claude-state/memory/`) behind a junction
  from the harness's memory folder, so `pa_install --project` on a fresh machine restores
  them. Never delete a memory; demote it to a linked
  generation archive. Only the index costs context.
- The generation-start ceremony (curator, demotions, trim, seed audit, `doctor --sizes`) runs
  before the planner at every generation boundary.

---

## Skill-level handling

3.0 change: the developer profile lands in `HOW_WE_WORK.md` under `## Developer` rather than
only in a memory. No change to the levels.

- Beginner: explain more, simpler architectures, fewer and chunkier phases, warn plainly when
  scope is over-ambitious. Expand the explain-before-coding depth.
- Intermediate: explain the non-obvious; standard patterns; normal phase granularity.
- Advanced: brief reasoning, respect their calls, fine-grained phases, argue only when the
  methodology stakes are real.
- When in doubt, ask. Record the profile in `HOW_WE_WORK.md` at install or generation so
  every future session starts calibrated.

---

## Sunset list and changelog

### Sunset list (mechanisms removed from 2.0)

Mid-phase rules check and re-read every four tasks. Session-start rule recitation. Printing
checkpoint blocks; the blocks themselves. "Checkpoint CURRENT_PHASE every turn" and
`CURRENT_PHASE.md` with its template. The effort map, the tier prompts, the "drop back down"
reminders, the context buckets and reset bands, and the statusline-injection checkpoint hook.
Token-lean SSP block and digest subagent. Duplicated fail-safe rule blocks. TaskCreate after
approval, task-list rebuild on resume, and the Tasks-tool setting. Feedback memories as
corrections and the memory index. "Explain before coding" (replaced by: one line when
deviating). The "no truncating reads / spend freely" clause (keeping "never state a
percentage, never hurry"). Long commit messages. Inline one-liners. "Present results in chat"
(replaced by the task log and `REVIEW.md`). Verbatim rule printing.

### Changelog 2.0 to 3.0

| Area | 2.0 | 3.0 |
|---|---|---|
| Entry point | `SETUP.md` path A/B/C | `pa_install.py --root` + `--project` |
| Rules store | One `RULES_REGISTRY.md` (recited every session) | `rules/` one file per rule; grep the index |
| Session startup | Rule recitation + checkpoint restore | Seed file (`launch.py --seed-only`) |
| Effort and models | Developer toggles `/effort`; effort map per phase | Pinned per agent file; the developer never touches `/effort` |
| Execution | One task, confirmation, plan-mode + Max | One task per fresh context; two gates per phase, autonomy between |
| State recovery | `CURRENT_PHASE.md` (one large file, reloaded every turn) | `TASK_PROGRESS.md` (written at handoff, archived when done) |
| Knowledge stores | One cookbook file, one registry, one ops file | `cookbook/`, `rules/`, `docs/ops/` with indexes and flywheel scripts |
| Agent state | `.claude-state/` (repo-contained) | Same, plus the memory junction and the curator |
| Phase close | PhaseEnd (append-only) + the developer closes | `phaseend_index.py verify` then `assemble`, `lint`, `archive` |
| Cost visibility | None | Usage ledger, statusline with savings and pace, `pa_ledger.py report` |
| Migration paths | Mode 4 (one protocol) | Three layout modules: stock 2.0, 1.x-shaped, overlay kit |

What is measured (the acceptance metrics): average context per expert request at most 150k
tokens; expert seed at most 40k; retriever seed at most 6k; router context at phase end at
most 40k; zero prefix rewrites not at session start; zero Tasks-tool attachments; cost per
request at most $0.10; developer interruptions limited to the critic's `needs-developer` plus
the milestone gates.

---

## Appendix: the chat kit

The package still works for brainstorming in Claude Chat without Claude Code. Attach three
files to a new chat: this document (`project-architect-3.0.md`), the constitution skeleton
(`PROJECT_CONTEXT.skeleton.md`) and the rules seed (`rules-seed/INDEX.seed.md`). The AI runs
Mode 1 above and generates the constitution as a markdown artifact with the rules and
protocols embedded. That output is migration-ready: when the project later moves to Claude
Code, the installer's migration path picks it up.

The dispatch section from 2.0 shrinks to one rule: if you are Claude Code and a
`.claude/pa.json` exists, the installed system governs and this document is the reference
specification. If you are Claude Chat with this document attached, run the modes from this
document. Never mix the postures.
