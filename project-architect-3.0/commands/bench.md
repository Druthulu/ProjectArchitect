---
description: Agent bench — pull the public series, compare the installed agents, report; run only when offered or asked
allowed-tools: Bash({{PY}} tools/bench.py:*), Bash({{PY}} ~/.claude/pa3/pa_ledger.py bench:*)
argument-hint: [full|canary|<agent>]
disable-model-invocation: true
---
!`{{PY}} tools/bench.py pull`
!`{{PY}} tools/bench.py compare`
!`{{PY}} ~/.claude/pa3/pa_ledger.py bench report`

The three blocks above are the pull (public results into the ledger, `source=public`; from `--source`, else
`bench.source` in `.claude/pa.json`, else `<clone>/bench/results`), the compare (one line per
installed agent: `<name> <verdict> [why]`) and the report (per arm key, newest first). Show the developer the compare
lines and the report lines that concern them, outcome first, in plain sentences. Then, by verdict:
- `match` — nothing to do: this agent body, model and effort already have a series.
- `no-series` — nothing to compare; say so.
- `lightly-altered` — the developer's own light edit of a PA3 agent; never overwritten, never run unasked.
- `run-offered` — offer a run: "Say `run bench <agent>` to run `{{PY}} tools/bench.py run <agent>`." A run costs
  five-hour window points; `bench.py run` refuses over `bench.window_max_pct` unless `--force`.
- `update` — a newer PA3 version of the agent scores at least as well. Offer it in the session-start wording:
  "Say `update pa3` to run `git -C <clone> pull --ff-only`, `{{PY}} <clone>/project-architect-3.0/pa_install.py
  --root --yes`, then `{{PY}} <clone>/project-architect-3.0/pa_install.py --project <root> --yes` (refreshes this
  project: your edits kept, conflicts staged in `.claude/pa3-upgrade/`), then restart." (`<clone>`:
  `~/.claude/pa3-src`; `<root>`: this project's root, forward slashes).

Run `{{PY}} tools/bench.py run $ARGUMENTS` (empty = the light suite) only on a `run-offered` or `update` verdict the
developer accepted, or on their explicit word (`/bench full`, `/bench canary`, `/bench <agent>` said as a request to
run). Never run on your own initiative; never pass `--force` unasked. After a run, print its per-arm lines, then tell
the developer to classify every failure before reading the numbers: `{{PY}} tools/bench.py audit --latest <suite>
--unaudited --show 20`, then per failure `audit --set <arm>/<id> agent|grader|harness --why "<text>"`; then the new
`bench report` lines. For a two-arm comparison of agents with the same role, offer
`{{PY}} tools/bench.py ab <agentA> <agentB> [--repeat k]` (k ≥ 2 measures the noise floor; costs k runs of each).

User marks on an agent's `version:` line (frontmatter):
- `+uN` (e.g. `version: 1+u1`) — the developer edited PA3's agent; bump N on each of their edits. A marked body within
  20 % of the PA3 base agent's lines is `lightly-altered`; a heavier edit is `run-offered` with the mark shown.
- `user/N` (e.g. `version: user/1`) — the developer's own agent; bump N on each edit. Never replaced by an update.
Unmarked agents belong to PA3: a changed body bumps `version:` in the same commit.
