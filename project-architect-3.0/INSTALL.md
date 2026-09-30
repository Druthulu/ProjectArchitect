# Installing Project Architect 3.0

Project Architect 3.0, version 3.14.1 (the version is the phase that published it).

Two commands: the first runs **once per machine** (once per Claude config dir), the second
**once per repository** you want PA3 to drive. Both are idempotent — a second run writes
nothing (each step reports `SKIP` or zero files written). Needs Python 3.12+ (standard library only), git and
Claude Code. PA3 supports the latest Claude Code only: keep it updated; there is no fallback for an
older version's missing features (the router's scheduled resume assumes `CronCreate`); a
version named anywhere in these docs is the one a fact was checked on, not a supported range.
Run them from this folder, with the interpreter the hooks should use.

## 1. Per machine — `install.cmd` / `install.sh`, or `python pa_install.py --root`

Double-click `install.cmd` on Windows, or run `./install.sh` on WSL, Linux or macOS, once per
machine (once per Claude config dir). A double-clicked `install.cmd` shows a short banner and
waits for Enter at the end so the result can be read; run from a terminal, it does not wait.
Each finds a Python 3.12+ interpreter on `PATH`, prints which one it
picked, and runs `pa_install.py --root` with every argument passed through unchanged
(`--dry-run`, `--yes`, `--config-dir …`, …): `install.cmd` tries `py -3`, `python`, `python3`,
then installs one with `winget install --id Python.Python.3.12` if none qualifies; `install.sh`
tries `python3`, `python`, then `sudo apt-get install -y python3` if none qualifies and `apt-get`
is on `PATH` (otherwise it prints the install command for the platform and exits 1). Then each
clones the package (the public repository, or `--source <url|path>`) into `pa3-src` under the
Claude config dir, or pulls that clone when it exists, and runs the clone's `pa_install.py --root`;
without git on `PATH` it runs the installer beside it with `--no-clone`. Updates arrive through
that clone, so with git the script alone is enough. Neither script does anything else — the
installer itself stays plain Python. Already know which interpreter should run the hooks? Call it
directly instead: `python pa_install.py --root`.

Copies `pa/` and the entry scripts to `~/.claude/pa3/`, creates the usage ledger at
`~/.claude/usage-ledger/` (SQLite + `config.json`), and merges `settings/user.snippet.json`
into `~/.claude/settings.json` **after backing that file up** (`settings.json.bak-<date>`):
the Task tools off (`todoFeatureEnabled: false`, the one forced value), the eleven PA hooks and
the PA statusline (asked, default replace; the old one is kept), and — only where you have not
set them yourself — longer bash timeouts, auto-compact off, commit and PR attribution off, and
notifications and Remote Control on; a `CLAUDE_CODE_ENABLE_TODO_TOOLS` env value that would
re-enable the Task tools is removed and reported. `model` and `modelSettings` are never touched;
a non-PA hook is never removed, except 2.0's `ctx_guard.sh` and `ctx90.sh` entries (their scripts
are renamed `*.retired`).

Transcript policy: transcripts are never copied into a project — they stay under the Claude
config dir, and PA3 only ever reads them in place. The user settings snippet sets
`cleanupPeriodDays` to 3650, raise-only (an existing higher value is never lowered). Migrating
from Project Architect 2.0 removes its `SessionEnd` backup hook — PA3 keeps no parallel copy.

It asks nothing. A label for your account (`--label EMAIL=LABEL`) and other machines'
ledgers to include in reports (`--extra-root DIR`; under WSL the Windows ledger is found and
added on its own) are flags. It ends with two reminders (turn the **Remote Control** push
toggles on in `/config` if you want questions on your phone, and turn **recaps OFF**: they
are billed and never show up in a transcript) and the next step: open a repository in Claude
Code, or double-click `setup-project.cmd`.

Your plan's renewal day is never guessed. The first session with an account whose day PA3
does not know shows one line: read the renewal date in the Claude app under Settings >
Billing, then set it with `python ~/.claude/pa3/pa_ledger.py accounts renewal <email> <day>`
(the line carries the exact command; inside Claude Code prefix it with `!`). Until then the
month figures on the statusline show `—`. Signing in to another account mid-session asks
again for that account; `accounts renewal <email> clear` forgets a day, and `--renewal-day
EMAIL=N` sets one at install.

Useful switches: `--dry-run` (print the plan, write nothing) · `--yes` (take every default) ·
`--python /usr/bin/python3` (WSL, or whichever interpreter the hooks should use) ·
`--config-dir ~/.claude-vantage` (a second config dir: run it again) · `--hooks off` ·
`--no-statusline` · `--allow-stale` (install even when the source clone could not be updated;
without it an offline clone prints `WARN clone offline: …` and stops before writing anything).

## 2. Per repository — `python pa_install.py --project <repo>`

Three ways in, one install:

- **Inside Claude Code.** Open the repository in Claude Code. At every start there, until
  the repository is set up, PA3 shows one line with the repository's path filled in; type it
  (it starts with `!`, so it runs without a permission prompt), then restart Claude Code
  (`/exit`, then `claude`). The same message carries a `--ignore` line for a repository you
  want left alone; `"setup_offer": "off"` in `~/.claude/usage-ledger/config.json` turns the
  offer off on this machine.
- **By double-click (Windows).** Double-click `setup-project.cmd` beside `install.cmd` and
  pick the folder, or drop the folder onto it.
- **From a terminal.** `python pa_install.py --project <repo>`; without a folder it asks
  which one (default: the current folder).

It prints an inventory of the repository and one confirmation (skipped from the `!` line,
which runs with `--yes`), then:

- **bootstrap** — `.run/` (gitignored), `phase-ends/` with `current/{tasks,logs,research,
  discussions}` and their indexes, `.claude/pa.json`, a `.gitignore` block;
- **files** — the 17 agents (16 on Max 5x and Pro, where expert-fable is not installed), the
  `project-architect` skill and the six commands into `.claude/`, the tools into `tools/`, plus
  `templates/`, the 32 seed rules into `rules/`, `cookbook/INDEX.md` and
  `docs/project-architect.md` (every copy is byte-compared; an edited rule or cookbook index is
  listed and left alone, and the managed files follow the classes below);
- **CLAUDE.md and settings** — a ≤300-token `CLAUDE.md` (an older one is moved to
  `docs/retired/CLAUDE.pre-pa3.md` with `git mv`), `HOW_WE_WORK.md` from the template and the
  interview below, the memory stub, and `settings/project.snippet.json` merged into
  `.claude/settings.json`: the `pa-session` agent as the entry, a default model and effort
  (only where the project has none), permissions and env added, auto-compact, recaps and the
  Task tools off — **no hooks**: those are per machine, from step 1;
- **memory link** — creates `.claude-state/memory/` in the repo with a `MEMORY.md`
  index, then junctions (Windows) or symlinks (elsewhere) `~/.claude/projects/<slug>/memory`
  to point there so Claude Code's auto-memory reads and writes go to the repo. If a real
  `memory/` directory already existed under the projects dir, its files are moved into the
  repo (a name collision keeps both, the incoming file renamed with the machine tag);
- **close** — the manifest, then `git add` by explicit path and one commit,
  `chore: install Project Architect 3.0`, with no trailer. It never pushes.

It asks for one line describing the project (or pass `--tagline`; the default is the
`**Project:**` line of your `PROJECT_CONTEXT.md` when there is one), and for confirmation
(skipped by `--yes`, by `--dry-run`, and when stdin is not a terminal). At a terminal it also
asks the card's short interview: your name, experience, domain, autonomy posture, notification
channel and rhythm for `HOW_WE_WORK.md`, and your plan tier (max20, max5 or pro; default max5).

Useful switches: `--dry-run` · `--yes` · `--name NAME` · `--tagline TEXT` · `--python EXE` ·
`--no-commit` · `--force` (overwrite project-edited copies of the packaged files).

Re-running it on an installed repository is the **upgrade path** for the agents, the skill,
the commands, `tools/`, `templates/` and `docs/project-architect.md`. Each managed file falls
in one of these classes: unchanged → nothing; changed by you only → kept; upstream-changed
(yours untouched, including a file equal to any past package version) → updated and recorded
in `.claude/pa3-managed.json`;
changed by both → merged with `git merge-file` when it merges cleanly; else, for `tools/`,
commands, `templates/` and the methodology doc, replaced by upstream with yours saved as
`.claude/pa3-upgrade/<rel>.yours`; for agents and skills, a conflict: upstream staged as
`.claude/pa3-upgrade/<rel>.upstream` with a line in `.claude/pa3-upgrade/UPGRADE.md`, which the
router hands to an upgrade expert. `.claude/pa.json` `upgrade: auto|ask` (default `auto`):
`ask` makes the session-start update offer wait for `update pa3`. `--force` overwrites all;
`--dry-run` writes nothing. It touches nothing else — not the plan, the rules, the cookbook,
`HOW_WE_WORK.md` or `CLAUDE.md`.

### Account tiers: the three presets

PA3 reads your plan from the Claude Code login (`rateLimitTier` and `subscriptionType` in the
credentials file the usage poller already opens) and stamps it on your account in the ledger at
session start: Max 20x → `max20`, Max 5x → `max5`, Pro → `pro`; anything else (team, enterprise, an
unknown value) → the install interview's answer, `.claude/pa.json` `tier`, default `max5`. The tier
picks a preset for six agents; every other agent (router, planners, experts, coders, retrievers,
discuss, discuss-high, discuss-max) is the same on every tier.

| rung | agents | max20 | max5 | pro |
|---|---|---|---|---|
| hard | expert-fable | Fable 5.1 medium | not installed | not installed |
| judge | critic, review, auditor, memory-curator | Fable 5.1 medium | Fable 5.1 medium | Opus 5.5 medium |
| plain | plain | Fable 5.1 high | Fable 5.1 medium | Opus 5.5 medium |

On max5 and pro no plan task carries `effort: high` (there is no hard rung): the planner splits the
task instead and `tools/plan_edit.py` refuses the marking.

- **Managed lines.** The `model:` and `effort:` lines of these six agents are written by PA3 (install,
  update and every session start re-render them from the package file plus the preset); do not
  hand-edit them, the next session start puts the preset back.
- **Your own ladder.** To run one of the six on something else, set it in `.claude/pa.json`:
  `"ladder": {"critic": {"model": "claude-opus-5-5", "effort": "medium"}}`. The override wins over
  the preset and survives updates.
- **A stale tier.** After an upgrade or a downgrade of your plan, log out and back in to Claude Code
  (`/logout`, `/login`) so the credentials carry the new tier; the next session start re-stamps it.
- **Setting the tier by hand.** `python ~/.claude/pa3/pa_ledger.py accounts tier <email> max20|max5|pro`
  overrides the detected tier for that account (a later re-login never overwrites it);
  `accounts tier <email> clear` returns to detection. `accounts` lists each account's tier.
- **Two tiers, one repository.** The tier belongs to the login, not the repository: two accounts or
  two collaborators on different plans sharing a git-tracked `.claude/agents/` rewrite each other's
  lines, each until the next session start on the other tier.

Then start working: open a bare `claude` in the repo; the `pa-session` agent (set by `.claude/settings.json` `agent`) detects the state and takes it from there.

## Updating

When the clone has a newer PA3, session start offers the update: `upgrade: auto` (the default)
runs it before the first task; `upgrade: ask` waits for you to say `update pa3`. Either way the
offer names one command, `<python> tools/pa3_update.py`, which pulls the clone, refreshes the
per-machine copy (`~/.claude/pa3`) and then refreshes the repository. The first install pre-approves
it at both levels (`~/.claude/settings.json` allows `Bash(<python> tools/pa3_update.py)`, the
project's `.claude/settings.json` allows `Bash(<python> tools/*)`). If a hand-edited
`settings.json` lost those rules and the update is denied, the session asks once; you can also run it
yourself at the prompt: `! <python> tools/pa3_update.py`.

Agents or skills you edited that do not merge are listed in `.claude/pa3-upgrade/UPGRADE.md`. Each
line is settled by appending ` | resolve: keep`, ` | resolve: upstream` or ` | resolve: merged: <path>`
(a merged file outside `.claude/`, by convention `.run/pa3-upgrade/<rel>`); the next
`tools/pa3_update.py` run applies them.

## Verify

`python ~/.claude/pa3/pa_ledger.py doctor` checks settings, interpreter, ledger and hook
latency: it WARNs a hook whose run() exceeds `doctor.hook_warn_ms` in
`~/.claude/usage-ledger/config.json` (2000 ms by default). `python pa_install.py --root --dry-run` should report `SKIP` for
every step after detection, and
`python tools/launch.py --dry-run` in the repo prints the session it would start.
`/bench` pulls the public bench series, compares each installed agent (`match`, `update`, `run-offered`,
`lightly-altered`, `no-series`) and runs only on your word. Agent versions are `<phase>.<N>` (every agent became
`3.10.N` at 3.10; a body change sets `<phase of the change>.<N+1>`, where N counts the agent's changes for its
lifetime and never resets: `3.10.5` changed in 4.2 becomes `4.2.6`, never `4.2.1`; the phase-end lint checks it),
compared as integer tuples. Mark your own
edits on the `version:` line: `3.10.1+u1` for an edited PA3 agent (never overwritten when light), `user/1` for your own.
When the interview is skipped (`--yes`, the `!` line, or no terminal), the `## Developer`
section of `HOW_WE_WORK.md` takes the interview's defaults: your git `user.name`, the folder
name as the domain, `advanced`, full autonomy inside an approved plan, `toast`. Edit that
section to change them.

## 3. Migrating a 2.0 project

Run a dry run first to see exactly what will move:

    python pa_install.py --project <repo> --dry-run

The output lists every `git mv`, split and retire, plus an `unmapped:` section naming any
markdown file the migration does not touch.  When it looks right, run the real migration:

    python pa_install.py --project <repo> --yes

The installer detects the stock 2.0 layout (`RULES_REGISTRY.md` + `phase-ends/`) and
migrates it in one commit: the registry is split into `rules/` (one file per `###` heading),
the cookbook into `cookbook/`, ops-setup into `docs/ops/`.  `CLAUDE.md`, effort-map, the phase
templates and `docs/project-architect.md` move to `docs/retired/`.  `CURRENT_PHASE.md`
becomes `phase-ends/logs/PhaseLog_<N>.partial.md` (where N is the next phase number), and
an interphase `PhaseEnd_Phase<N-1>.5.md` is generated with a summary table.
`HOW_WE_WORK.md`, `GENERATION_PLAN.md` and `LEGACY_INDEX.md` are written from templates.
The SessionEnd backup hook is removed from `.claude/settings.json` (the script stays in
place).  Nothing is deleted; everything retired is under `docs/retired/`.

Use `--interphase NAME` to override the interphase PhaseEnd label (default: "PA3 migration").

The **1.x layout** (a `Project Context Markdowns`-style folder holding registry, context and
PhaseEnds) keeps that folder as `phase_ends_dir` in `pa.json`; PhaseEnds are renamed in place,
research documents archived to `docs/research-archive/`, and the registry split into `rules/`.

The **overlay** layout (a `*-architect` or `*-kit` directory beside the project) follows the
same pattern: rules come from both `DIGEST.md` bullets and the kit's registry templates, ops
from `docs/SETUP.md` splits plus kit ops sections, and the kit's CLAUDE overlay constraints
feed `HOW_WE_WORK.md`; the kit folder itself is left untouched.

### What the real trees showed (proof on clones, 2026-09-21)

Run on local clones of a stock-2.0 repo (2,645 files), a 1.x-shaped one (58,115 files, 164
PhaseEnds) and an overlay one inside WSL (15,721 files, a 3.66 MB cookbook): each migration is
one commit and takes 5 to 78 seconds; a second run prints only SKIP lines.  Before you migrate for
real: commit or stash your working tree (the migration commits only its own paths); expect an
`unmapped:` list naming your project's own documents (README, design notes, maps), which stay
where they are; a 2.0 repo that tracks `.claude-state/transcripts/` keeps tracking it (PA3 adds
the ignore line and untracks nothing); the first `claude` after a migration opens generation
planning, because every legacy phase is closed.  On WSL, re-run the root install first so the
hooks match the package.

## If something misbehaves

`PA_HOOKS_OFF=1` makes every hook exit immediately; `PA_LEDGER_OFF=1` stops all ledger
writes. Both are environment variables — nothing needs reinstalling, and the ledger can be
rebuilt afterwards with `pa_ledger.py recalc --from transcripts`.

## Uninstall

Per machine: restore the newest `~/.claude/settings.json.bak-<date>` over `settings.json`,
then delete `~/.claude/pa3/` and the package clone `~/.claude/pa3-src/` (and
`~/.claude/usage-ledger/` if you want the cost history gone too). Per repository: `git revert`
the install commit, or delete the installed paths, and remove the `memory` link under
`~/.claude/projects/<slug>/` — nothing outside the repo holds project state.
