# PA3 changes

One plain-English section per phase, newest first, written at the phase close as `## <phase> — <title>`. When it announces an update, the router reads the sections newer than the phase a project last recorded and tells the developer what changed in those words.

## 3.15.2 — The discussion in its own view, the savings on the right account

- A discuss agent now answers you in its own view, in plain text, and returns to the router only after `/proceed`, with its decisions and the record it wrote. Claude Code nudges background agents to hand their result back after every reply; PA3 now refuses a discuss agent's hand-back until the record file it names exists, so a discussion no longer ends after its first reply. If a hand-back without a record still reaches the router, the router passes it on to you word for word.
- The installer adds the hand-back to the tools PA3's safety hook watches (the PreToolUse matcher in your user settings); the next update or install refresh rewrites that matcher for you.
- Known cost: while a discussion is open, Claude Code's own nudges to hand back cost about three to five extra requests per discuss reply, each at the agent's current context size. This is accepted for now: Claude Code's switch for hand-back applies to every agent at once, so it stays on until there is one per agent.
- After the update, the first session start finishes the earlier account repair. The 3.15 repair moved a session's turns back to the right account but left the record of the old account switch as it was, so the next recalculation (the 3.15.1 repair ran one) filed those turns, and their savings, under the cancelled account again. The repair now corrects that record too, and the savings follow it, once, after backing up the ledger (step `3.15.2/savings-account`).
- A project's savings under an account no longer include a weekly or five-hour window that has already ended. Before, an account with no spend in the current week could carry an old window's large negative saving into the project's total (seen on WSL: minus $442 under the cancelled account).

## 3.15.1 — The past cold starts and both machines' numbers

- After the update, the first session start corrects past main-session turns that the old ledger counted as fresh starts. Before 3.15, the first turn of each batch of the main session's turns had no gap recorded, so a router that went cold between batches never showed as a cache rewrite. The repair recomputes each such turn's gap, cold start and rewrite from its transcript (or from the ledger's own rows when the transcript is gone), then rebuilds savings, fits and the summary, once, after backing up the ledger.
- The Project and Account lines now count both machines every time. Before, some summary rebuilds (those started by hooks) read only the local ledger and others read both, so the account's percent-per-dollar rate and the project's share of the weekly meter jumped between two values; on WSL one project showed 150 % of the week. Every rebuild now reads the ledgers of the other machines named in the ledger config.
- A project's share of an account's meter is now its share of that account's whole spend on every machine. Spend on models with no fitted weight of their own is valued at the window's average rate on both sides of the share, instead of being dropped from the total.
- When the other machine's ledger cannot be read (it is off, or was copied mid-write), a rebuild tries once more, then keeps the previous account numbers rather than showing this machine's alone, and logs `summary_extra_root_failed` in `hooks.log`. A copy that fails the database's integrity check is discarded instead of read.

## 3.15 — The cache and account fixes

- A session no longer moves to another account when PA3 cannot tell who is signed in. PA3 now reads the account straight from Claude Code's own `.claude.json` and asks the `claude` command only when that file has no email. Before, a failed lookup (seen in WSL after a sign-in token refresh) fell back to the ledger's configured default, which could be a cancelled account, and filed the session's cost under it; the statusline then showed the session as 333 % of the week.
- The session line shows `?` instead of a percentage when the session's cost is larger than everything the ledger holds for its account in that window, which only happens when costs were filed under the wrong account.
- After an update, the first session start repairs past data the new version knows how to fix, once, in the background, after backing up the ledger. The first repair re-files sessions that a failed lookup moved to another account; you see one short notice when it is done. A pattern it cannot fix safely becomes a warning in `pa_ledger.py doctor`, with the command that fixes it.
- Summary rebuilds no longer time out against each other. A rebuild now does its work first and holds the summary lock only to write, and a slower, older rebuild never overwrites a newer one. On a large ledger the old way failed whenever a hook and the statusline rebuilt at the same time.
- Experts now keep their cache for an hour instead of five minutes. An expert spends most of its life waiting on its coders, and one hour of cache costs less than rewriting the expert's whole context after every five-minute pause. Each expert has a five-minute twin (`expert-opus55-5m`, `expert-fable-5m`) for projects whose experts rarely wait; `tools/expert_ttl.py` measures a project's past experts and tells the planner which to use.
- The keep-warm warmer decides per agent how many pings a five-minute cache is worth, from that agent's context size, its model and the router's cost to relay each ping, instead of a fixed limit. When a ping would cost more than the rewrite it saves, it sends none. The phase audit now counts the router's relay and re-arm turns in the warmer's cost.
- The router can no longer silently miss the warmer's pings. The instruction to start relaying them is now the second line of the router's seed, and the router starts relaying whenever it enters router mode. A ping nobody relays within two minutes is logged and shown to the router with the instruction to start. The phase audit shows how many pings went undelivered.
- The ledger now sees a cache rewrite at the start of each batch of the main session's turns. Before, the first turn of every batch was counted as a fresh start, so a router that went cold between batches did not show as a rewrite.
- On Windows, the installer links the usage ledger of every WSL installation it finds, and a re-run links one that appeared since, so both machines' costs show together. In WSL it prefers the Windows user's own folder.

## 3.14.2 — The hotfix

- A usage ledger created by an early build now gets the column a later build added, the next time a hook, the installer or the ledger CLI opens it. Before, such a ledger could be marked up to date without that column; every summary rebuild then failed, and the statusline lost its Project and Account lines and part of its Pace and Session lines (seen on a WSL machine for three days).
- `hooks.log` now says why a summary rebuild failed (`summary_rebuild_failed {"error": "..."}`). Before, it logged only the event name, which is how the failure above went unnoticed.
- Experts no longer try to continue a coder that has finished. Claude Code's own text offers SendMessage for that, but agents started by other agents don't have it, so the call failed and the expert started a new coder anyway. The expert now starts the new coder straight away, with a brief naming the previous coder's commit and log.

## 3.14.1 — The discussion stays open, the toasts say something

- Desktop toasts from a project in WSL now show their title and message. Before, PA3 handed the text to Windows with a setting that only works in the other direction (Windows to WSL), so every toast from WSL read just "Project Architect".
- The router no longer takes each reply of a discuss agent as the end of the discussion. Claude Code marks a background agent done after every reply, and your next message in its view picks it up again. The router now treats only the return that carries the record (after `/proceed`) as the end and stays quiet on every other reply.
- The keep-warm ping's one-character reply from a discuss agent no longer raises a "discussion turn ended" toast.
- The router no longer crashes at launch in a project that has a PhaseEnd named like `PhaseEnd_Phase3_5.md` (common after a 2.0 migration) next to numbered ones like `37.5`. The launcher now sorts PhaseEnds with the same rule as the rest of PA3.
- The PhaseEnd's seed-size line "previous phase" now names the phase that really came before: phases were sorted as text, so 3.10 came before 3.9.

## 3.14 — The migrated memories, finished

- A project upgraded from 2.0 no longer turns old 2.0 habits into rules. Memories such as "capture knowledge before a fresh session" or "plain-English recaps" describe practices PA3 already covers, or replaced, so the memory curator now archives them instead, and its recap lists them under "superseded by PA3". Nothing is deleted: their full text stays in the archive.
- A project migrated on an earlier 3.x release is curated once more at its next session, so its kept memories get the same check. The router asks for it once; when the curator is done, the project records that its memories were sorted (`memory_routed` in `.claude/pa.json`) and is never asked again. This now works in the middle of a phase too, at the next task boundary, not only at a generation start.
- A project with no memories to sort is never asked.
- The version check at a phase close now expects one version bump per agent per task: several commits of the same task that change an agent count as one change. Before, each commit had to bump the version again. (This landed just after 3.13 was published.)

## 3.13 — The router's reach and the migrated memories

- The auditor now runs at every generation start. The router's instructions called for it there, but the router was not allowed to start it, so every attempt failed with "Agent type 'auditor' not found". A test now fails whenever the router's instructions name an agent it cannot start.
- The memory curator is required at every generation start. Before it runs, the router asks you once and says it is required; you can run it now or pause, not skip it.
- A project upgraded from 2.0 has its old memories moved where PA3 keeps such things: standing facts into HOW_WE_WORK.md, rules into the project's rules, techniques into the cookbook and environment facts into docs/ops/. The rest are archived, and the curator's recap and the router tell you the archive's path. Nothing is deleted: the full text of every memory stays in the archive.

## 3.12 — The Sonnet 5.5 upgrade

- The router and all three retrievers now run on Sonnet 5.5 at medium effort. The router and the document and web retrievers moved from Sonnet 5, the code retriever from Haiku 4.5. The experts and the coder are unchanged.
- Each move was measured first: the agent was benched on its old model and on Sonnet 5.5, with nothing else different. The router passed 14 of its 15 tasks on Sonnet 5.5 against 13 on Sonnet 5, and cost $0.77 against $0.88. The document retriever answered all 28 of its tasks against 27, for $1.44 against $1.54 and in fewer turns. The code retriever matched Haiku 4.5 in accuracy and cost and answered in about 9 seconds instead of 16. Sonnet 5.5 costs the same per token as Sonnet 5; the web retriever moved with the others on that, since the bench has no web questions to measure it with.
- An update moves a project still on the old default: if its settings carry the router model PA3 shipped before (Sonnet 5 at medium effort), they now say Sonnet 5.5. A model you chose yourself is kept.
- The bench has new baselines for the four moved agents, published with the other results. Its grader now reads citations written as a list, such as `file.py:45,56`, which Sonnet 5.5 writes; before, only the first line number counted, and a correct answer could fail.
- A rule for model upgrades: when a new version of a model comes out, every agent on the old version is benched against it and moves only on the developer's word, and the upgrade is finished only when the wiki and the docs name the old model in history alone. The wiki's Bench page describes it.
- Publishing now checks the wiki's roles table against the agents themselves: a model or effort in the table that differs from what the agent runs stops the wiki from being published, naming the row and both values.

## 3.11 — The publish

- Project Architect 3.0 is public at version 3.11: the public repository now holds the package, the README and the bench results. Version 2.0 is retired and no longer distributed.
- A short README and a wiki describe 3.0: installing it, the roles, a day in the life, every line of the statusline with what its white (used) and blue (saved) figures mean, why the two savings figures are never added together, the bench, the parallel speedups PA3 chose not to take, and the frequently asked questions.
- Installing is simpler. Double-click `install.cmd` on Windows; it asks nothing and keeps its window open until you have read the result. A repository is set up from the one line PA3 shows when you open it in Claude Code (the path already filled in), by double-clicking `setup-project.cmd` and picking the folder, or from a terminal, where `pa_install.py --project` asks for the folder.
- Your plan's renewal day is never guessed. PA3 asks for it once per account, pointing to the Claude app's Settings > Billing page, and again when you sign in to an account it does not know; until then the month figures show a dash. `pa_ledger.py accounts renewal <email> <day>` sets or changes it.
- INSTALL.md matches the package again: 17 agents, six commands and 32 seed rules; the install scripts' own clone of the package, through which updates arrive; exactly what the settings merges add; the short interview at a repository install.
- Text files now check out with LF line endings on every system. Before, a fresh clone on Windows, including the installer's own clone, came out with CRLF endings, the package's tests failed on eight counts there, and a new project's CLAUDE.md was written with CRLF. The whole test suite now passes on a plain clone of the public repository.
- The weekly bench publishes each result to the public repository's `bench/results/`, with session identifiers replaced before it goes public.
- Updates keep each project's copy of the methodology, `docs/project-architect.md`, current like the agents and tools: an untouched copy is updated, and an edited one is merged when it can be, else replaced with yours kept under `.claude/pa3-upgrade/`.
- The package holds only what you need to install and use PA3: its design and build records and its own analysis scripts are no longer part of it. A repository set up by an earlier release can delete its `tools/analysis/` folder.
- Smaller repairs: the "runs in another session" line leaves your session as soon as it starts one of the phase's tasks.

## 3.10.6 — The tiers

- PA3 now knows your account's tier (Max 20x, Max 5x or Pro). It reads it from the credentials the usage poller already opens and remembers it per account. `pa_ledger.py accounts tier <email> max20|max5|pro|clear` overrides it.
- Each tier has a preset: the model and effort level for each of the six managed agents. Max 20x keeps today's setup exactly. On Max 5x and Pro the hard expert (expert-fable) is left out. At every session start, the agents are brought back in line with your tier's preset, and edits you made to an agent's body are kept. INSTALL.md and README.md describe the three presets; the `ladder` setting in pa.json overrides them.
- On Max 5x and Pro, the planning tools refuse to mark a task `effort: high`, because no hard expert exists there.
- When the five-hour window reaches 90 %, the router pauses at the next task boundary and schedules its own resume just after the window resets (`status.py window-gate`). The statusline shows "paused until HH:MM" while it waits.
- Every bench result records its preset. `bench.py plan|run --preset` selects one and skips the expert-fable arm on Max 5x and Pro. The critic and review agents each gained a light suite.
- Rule H14: a doctor or health probe that replays a hook never changes the project it runs in.

## 3.10.5 — The session fixes

- The savings figures now say what they are. The Project line shows your own account's saved share in its own week, the Pace line prices what you paid and what you saved from the same table, and every "weeks" figure converts each week's spend at that week's own rate instead of today's.
- `pa_ledger.py report` prints, per account, the tokens kept out of your context (counted), the modeled carry at the 1M window with the 450k and 200k figures beside it, and the replay on its own line, never added together. A model family the weekly fit cannot separate borrows a labelled rate instead of counting as zero.
- The guards cover shell reads: `cat`, `sed`, `head` or `tail` on a file over the size cap or on a spilled result is denied unless narrowed, like a whole Read. A second whole Read of a file you already have, unchanged, is denied up to three times.
- A phase archive runs the moment the milestone is green and the lint passes; the "anything else before I close?" question comes once, before the closer starts. The archive refuses while your inbox holds an unconsumed note, clears the phase owner, and regenerates the ops index.
- The package carries its version, 3.11, in one file; the installer, the update check, doctor, the report, the managed record and the bench all read it, and an update is offered when the version changes.
- `statusline.fewer_numbers: true` in the ledger config gives a leaner statusline; a definitions page (`docs/statusline-definitions.md`) names every figure and its kind.
- Smaller repairs: the bench never writes a README above a results folder; outline.py prints UTF-8 on Windows; the installer removes the env value that re-enabled the Task tools; the commit script never commits a half-written log; the "runs in another session" line needs an open phase.
- The phase close recalculates the router's session before it writes the PhaseEnd, so the Agent-runs table shows final savings per expert. A coder or retriever shows "-" there by design: the column counts what a run saved by delegating, and a leaf run delegates nothing.

## 3.10 — The repairs and the upgrade proof

- Updates now install themselves. When a new PA3 is available, the router applies it at the start of your session, before any work, and then tells you in one message what changed. Set `upgrade: ask` in `.claude/pa.json` if you would rather be asked first.
- Your own edits to PA3 files survive an update. A file you never changed is simply replaced; a file you and PA3 both changed is merged; a tool that cannot be merged takes the new version and your copy is saved next to it under `.claude/pa3-upgrade/`; an agent or skill that cannot be merged is merged by a helper agent, or yours is kept.
- Agent versions now read like `3.10.7`: the phase of the last change, then how many times the agent has ever changed.
- Hooks finish faster, and `doctor` warns when one is slow.
- Deferred ideas and inbox notes are no longer lost between phases: each one is listed when the next phase is planned and must be placed, postponed or dropped.
- Your settings file keeps only the values you set yourself, so a model change in PA3 reaches you without a stale copy overriding it.
- An update that was found but not yet applied is offered again at the next session start; the once-a-day limit now applies only to the network check.
- Smaller fixes: the phase archive keeps its usage records, the bench reads its results from the right place, the update check stays quiet for bench-only commits, and PA3's agent files are always written in the line format Claude Code can load.
