# PA3 changes

One plain-English section per phase, newest first, written at the phase close as `## <phase> — <title>`. When it announces an update, Claude reads the sections newer than the phase a project last recorded and tells the developer what changed in those words.

## 3.28.7 — The model line shows the session's work

- In a 3.2 session the model line's context figure (always `0/1M` there, since the main session never calls a model) becomes the session's work, such as `4.3M/∞`: every token that went into the contexts of Claude and all its agents, each counted once, with an agent type's shared starting prompt counted only once. Other sessions keep the usual `<context>/<window>`.

## 3.28.6 — Claude no longer gets the old update command

- When the mod finds a new release mid-session, Claude hears only the version and its title, never the old "run pa3_update" instruction, so it cannot start a second install beside the mod's.

## 3.28.5 — The statusline says when an update is installing

- While a chosen update installs, the statusline reads "installing · please wait" (not "installed") and Claude says once that it is installing; "installed" shows only after the install finishes.

## 3.28.4 — No stray windows on Windows

- PA3's background steps (an update, the ledger's recalculation, a notification) no longer open an empty console window on Windows.

## 3.28.3 — Updates while paused

- A chosen update installs while a task is paused, with no expert or coder working, instead of waiting for the task to end.
- Asking Claude for an update you already chose is accepted, not refused.
- An update installed at a session start, or already loaded by a restart, gives the "installed and loaded" line instead of asking for another restart.
- The restart line always has a blank line above it.

## 3.28.2 — The restart after an update stands out

- When an update is installed, Claude puts the restart on its own bold line ("To load it: /exit, then claude --continue (not /clear)"), coloured green with "(not /clear)" in red in its card, and the statusline shows "(not /clear)" in red.
- After that restart, Claude first says "Project Architect <version> is installed and loaded. Ready to continue work.", so the old restart line above doesn't look like a loop.

## 3.28.1 — Fixes from the first projects on 3.2

- The statusline shows the titles of queued tasks again when their plan line names no status.
- The seed warning shows only when an expert starts above 40k tokens; it no longer fires when a seed merely grew.
- The planner's name shows in the planners' purple in the activity band, as its cards do.
- A plan change you ask for during a phase goes in at the next task boundary, and Claude tells you in chat what changed. The plan view opens only for a phase's first plan and the generation plan.
- Claude's questions appear the moment Claude asks them, even while a task runs; answering one leaves the running task alone.
- Installing a new phase plan keeps every finished task finished.
- Only your messages hold the queue while you talk, and the hold ends 15 minutes after you last spoke; the mod's own messages to Claude (a plan installed, an update finished) don't extend it.
- When Claude Code restarts, updates or the terminal closes while a task runs, the task's expert picks up by itself at the next start, and Claude tells you in one line. Only your own stop (Ctrl+C) waits for your go.
- The mod's own commits get five minutes instead of one, so a large repo's commit isn't cut off. If one is cut off anyway, the mod clears the leftover git lock (only when no other git command is running), tries once more, and tells Claude in one line if that fails too.
- A long command (a bake, a backtest, a batch run) runs as a Claude Code background shell: the coder or expert hands back "WAITING: shell …" and stops instead of sitting in a polling tool call, and picks up by itself when the shell ends, reporting with SendMessage.
- While such a shell runs you can see it: a line under its task on the statusline ("◐ shell bake_rest.sh · running 9m 05s · coder waiting", ✗ if it failed), a row under its agent in the band, and one line from Claude saying what is running and that it picks up by itself.
- Every waiting agent is kept warm by its size: a ping just before its cache lapses (55 minutes for Claude, experts and planners; 4.5 for coders), more pings for a small context and fewer for a large one (up to 5 and 12). Any real message starts the count over. Claude stays warm even while a background command of its own is still running.
- Nothing is sent to an agent whose cache has gone cold: a fresh agent of the same kind takes the message, Claude seeded from the journal, an expert from its progress and earlier reports, a coder from its brief and notes.
- An agent at its context limit finishes the change in hand until it builds and its tests pass, commits only working code, saves any unfinished edit as a patch beside its progress notes for the next agent, and hands back a plain "handoff".
- A running session checks for a new release every 8 hours, as well as at each start and resume. When it finds one, Claude asks once per version, on your phone too: "Install after the current task" or "Not now (I'm away or busy)". Work keeps going while you decide, and no answer counts as "Not now"; later you can just tell Claude you're back and want it.
- An update you choose to install goes in once the current task finishes, with no new task started; a toast and a push notification say it's done, and Claude asks you to type /exit, then claude --continue (not /clear) to load it. A green line above the model line shows the state: "available · installs at your next restart" or "installed · /exit, then claude --continue", with "hotfix" or "feature update" by the version number.
- Claude makes the small plan changes you ask for itself. A real replan goes to a fresh planner, given the plan, the replan notes and the earlier planner's notes.

## 3.28 — The release

- This is the release a project on 3.15 updates to. The update brings every change in the sections from 3.24 to 3.28; the 3.24 section describes the move to PA 3.2.
- PA3 looks for a new release every time a session starts, and every time you resume a session in a project with the mod on. `pa_ledger.py doctor` warns about updates only when a newer release is out.
- `pa_ledger.py report --since <date>` counts the savings of the sessions that started on or after the date.
- The README and the wiki describe PA 3.2, and the wiki's savings cross-check is re-run on today's models.

## 3.27 — The model studies and the benches

- The retrievers run Haiku 5.5 at medium on every plan, instead of Sonnet 5.5. On the bench they passed about as often, at a small fraction of the cost.
- Pro gets its own models: the expert runs Opus 5.5 at low, the coder Sonnet 5.5 at medium and the thinker Opus 5.5 at medium, and Claude starts on Sonnet 5.5 at medium. A `--project` run moves a starting model PA3 shipped for another plan; one you set yourself stays.
- When you ask Claude to think hard, or ask a hard question, Claude runs its thinker at max effort.
- The bench (`/bench`) runs on today's agents and on any model, and now also benches Claude itself and the web retriever.
- The price table drops the long-context tier: Claude 4.6 and later bill the whole 1M window at standard rates. Stored costs never used it, so nothing is re-priced.

## 3.26 — Verify and fix

- A task that waits keeps its cache warm: the warming ping is answered with one `.`, and the resume clock counts from the last ping, so a held expert resumes warm after hours.
- Claude can answer a warming ping itself without being refused.
- The expert's brief asks for the plain report (done, question or blocked, then free text).
- Claude says what is running now while it routes, and after a review is released.
- A second waiting toast within a minute of the first is skipped.
- The expert reads the research its own task names.
- Claude picks the thinker's effort for each question (`[effort <level>]`).

## 3.25 — Looser and smaller

- The phase close needs no agent. When you say yes at the close question, Claude hands its phase report to the mod, which saves it as the phase recap (and the generation recap at a generation end) and closes the phase: it assembles the record, commits, archives and tells you to push. Nothing re-checks the milestone after your yes.
- Experts and coders report in plain words. A report starts with done, question or blocked and the rest is free text; the mod reads the commits from git. Older reports still read.
- A plan task needs only three parts: its id, a title and what done means. Everything else (files, a verify command, effort, review and the rest) is optional, and a task summary no longer needs a `Verified:` line. The milestone is a plain sentence, and a command can go with it.
- One agent per role: `pa-expert`, `pa-coder` and `pa-thinker` replace the per-model agents. A task's effort picks the expert's model from your tier, and every tier now has a high-effort expert. The deep thinker is the thinker spawned at its deep row, still only when you ask for it by name. An upgrade removes the old agent files.
- A generation plan's phase line may carry `planner-effort: <level>` to run that phase's planner at a lower effort.
- Agents may write memories again; the rules, the card and the `CLAUDE.md` line no longer forbid it. Claude still keeps the project's memories in `.claude-state/memory/`.
- The rules, templates and docs stop describing what is gone: the closing agent, the close lint and the milestone verifier, the strict report block, the required task fields and the old agent names. Seed rule P7 (the close verifier) is removed.

## 3.24 — PA 3.2

- PA 3.2 is now the only PA3. You work in one session and talk with one partner, Claude. When you approve a plan, its tasks run inside that same session: one expert per task, with its coders and retrievers, while you keep talking with Claude. There is no router session any more.
- The installer now installs the mod, the plugin that runs the plan's tasks. The machine install registers it as a user-scope plugin from a local marketplace in `~/.claude/pa3/mod` (`pa@pa3` and `pa-watch@pa3`, in your user settings), so a bare `claude` loads it. It is active in PA3 projects only; in any other folder it does nothing.
- Upgrading a project onto 3.2 is the same command as before (`pa_install.py --project <repo>`, or the update at session start). The upgrade:
  - turns the mod on (`mod: on` in `.claude/pa.json`);
  - removes the old loop's agents and commands, and saves a copy of any you had edited as `.claude/pa3-upgrade/<path>.yours`, with a note in `.claude/pa3-upgrade/UPGRADE.md`;
  - replaces the model pins PA3 used to ship (the `pa-session` agent, the Sonnet model and its effort entries) with Claude's starting model and effort, Opus 5.5 at medium; any value you set yourself is kept and reported;
  - refreshes the lines of `CLAUDE.md` and the seed rules you never edited;
  - marks a project that has memories for the curator's first-run sort.
- The first run in 3.2. When a project that already had memories enters 3.2 (from plain Claude Code, PA2 or PA3 3.15.x), the mod asks you once, together with your first message: the memory curator sorts those memories now, or you pause it. No plan is drafted or started until the sort is done or paused; a paused sort is asked again in a later session. The curator follows your policy, card first: your preferences go to the card's `## Developer` section and standing facts to its other sections, norms go to `rules/`, traps and techniques to the cookbook, and memory keeps only what fits none of these. Stale items and items that contradict the way PA3 works are archived. Nothing is deleted. Claude then tells you what was moved where.
- Claude always sees the card. Every fresh Claude starts with its slice of `HOW_WE_WORK.md`, and every expert's brief carries the expert slice, so neither has to go and read it. When you state a preference, Claude writes it to the card first.
- File edits show on screen. When Claude, an expert or a coder edits or creates a file, the session shows the change the way Claude Code itself does: `Updated <path> (+N -M)` or `Created <path> (N lines)`, then numbered lines, removed in red and added in green, in a note marked with the agent's name and color. Long diffs are cut at about 30 lines. Commands, reads and searches stay hidden. A coder's finish card lists the files its commit changed, with their counts, which also covers edits made by a script. The phone (Remote Control) shows the same diff.
- The phase close no longer stalls. The ledger recalc at the close used to hold the session for minutes; it now runs in the background, and the close goes on at once.
- At every session start in a PA3 project, the session-start hook checks the mod: the installed files, the plugin's registration, the Claude Code version, and whether the mod loaded last time. When something is broken it starts the repair at once: a reinstall of the release you have, or, under `upgrade: auto`, a newer release that fixes it. Its line says what broke and what was done, and Claude (or plain Claude Code, when the mod did not load) tells you in plain words. A repaired mod loads at the next start or at `/reload-plugins`. Below the Claude Code floor nothing is changed; the line says to update Claude Code.
- Updates install released versions only. `upgrade: auto`, the default again, installs a new release at session start, before any task runs; `upgrade: ask` asks you first. Nothing ever installs older or unreleased code.
- PA3 now needs Claude Code 2.1.292 or newer. Below that, the installer stops before changing anything and says to update Claude Code.
- The status line draws within a fraction of a second. It used to rebuild its summary before printing, which on a big ledger left it blank for minutes; the slow work now runs in the background. It refreshes every 5 seconds, so a `/model` or `/effort` change shows within seconds.
- The review picker no longer waits while Claude's thinker works. It used to treat any helper that was waiting as an open dialog.
- Ledger fixes:
  - each account's share of a project is now counted from that account's own figures;
  - a usage window that has ended no longer counts spend past its end;
  - a summary rebuild that cannot read the other machine's ledger now retries once and logs the failure, instead of showing this machine's numbers alone;
  - two repairs running at once can no longer both stamp the same step.
- Not supported any more:
  - PA3 3.15.x projects: run the upgrade above to move them to 3.2;
  - the `mod: off` mode: there is no fallback to the old loop.
- The phase-end recap is one plain-English paragraph, as long as the phase needs: what it set out to do and why, what it found or built, what it cost, what did not change, and what comes next. The generation recap reads the same way.

## 3.15.3 — Long sessions keep their savings

- A session open for more than two days keeps its saving on the Session line. PA3's summary kept an open session for 48 hours from its start instead of from its last turn, so a router busy for three days showed `-` for its saving once it passed 48 hours, and so did a session resumed more than two days after it started. A session now stays while it has had a turn in the last 48 hours, and the savings check in `pa_ledger.py doctor` counts sessions the same way.
- The end-of-turn hook no longer runs the savings repair itself. On a long session the repair recalculated the whole session and ran past the hook's 8-second limit, so the hook was stopped at the end of every turn: the `-` never cleared, the router was never told, and the hook's later steps (sweeping helper agents that finished without notice, showing the router a warmer ping it missed) were skipped. The hook now clears the warning once the saving is back, and otherwise starts the repair in the background, at most once every 15 minutes per session. The repair rebuilds the summary first and recalculates a session only when that is not enough.
- When a phase closes, the expert writing its recap now checks that every change you can see (an agent, a command, a default, a notice, the meaning of a statusline figure) came with its edit to the project's README, wiki, install notes or published docs, and has a coder fix a miss before the phase closes. PA3's own install notes now count the 19 agents 3.15 ships, and the statusline definitions describe the Project line's share of the meter as 3.15.1 computes it.
- After the update, a Session line that shows `-` for this reason gets its figures back within a minute.

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
