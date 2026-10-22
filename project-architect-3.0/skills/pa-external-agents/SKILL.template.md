---
name: pa-external-agents
description: External coders in a PA 3.3 project — a task's coding on an OpenRouter or local model through tools/pa_ext.py, while experts, retrievers and Claude stay on the subscription. The setup, the check, the project settings, the plan's coder field and the troubleshooting.
---
# External coders

An external coder is our `pa-coder` running on another provider's model, in its own headless Claude Code process,
started by the task's expert with `{{PY}} tools/pa_ext.py run`. Only the coding moves: experts, retrievers, the
planner and you stay on Claude.

What it keeps from an in-house coder:
- the same instructions and house rules, and the same allow and deny rules;
- PA3's guards, its journal entries and its cards ("External coder · T4.c1", naming the model).

What differs:
- Its lookups go through `{{PY}} tools/pa_ext.py ask "<question>"`. The mod spawns `retriever-code` (Haiku, on the
  subscription) and the answer comes back.
- Corrections reach it at its next tool break.
- It reads only the project and system folders, writes only inside the project, and has no web: its web needs go to
  the expert, which has the web retriever (an in-house coder has none either).
- A command no allow rule covers goes to PA3's judge instead of auto mode, which cannot run on another provider's
  model (see "Learning from each run").
- Its tokens are cash on the provider's key. The statusline's External line shows them; they never count as
  subscription usage.

## Setting up (once per machine, per backend)
1. The developer creates a key on openrouter.ai with a **credit limit**. It is never pasted into chat: anything typed
   here stays in the conversation log.
2. They run the setup in a normal terminal, never through `!` (it reads the key with hidden typing):
   - Windows: double-click `%USERPROFILE%\.claude\pa3-src\project-architect-3.0\setup-external.cmd`;
   - Linux and WSL: `sh ~/.claude/pa3-src/project-architect-3.0/setup-external.sh`.

   Its questions are the URL (Enter for OpenRouter), a name, the key and the model ids (e.g.
   `deepseek/deepseek-v4.1-flash-1017`). It tests each model and saves nothing on a failure.

   Which model: DeepSeek V4.1 Flash is the one PA3 tested end to end and recommends. GLM-5.3 Flash passed easy and
   medium tasks at max for about three times DeepSeek's price, but most of its hardest attempts ran out of time and
   its hosts were unreliable. Qwen3.8 Flash passed nearly everything it finished, at 6 to 11 times DeepSeek's price
   per task, because its only host barely caches. The wiki's External coders page has the numbers.
3. The setup then lists each model's hosts, best first: fp8 or better before fp4, ranked by a typical coder run's
   cost. It names the recommended host and the guardrail line.
   - The developer adds a **guardrail** on the key at openrouter.ai, allowing that host. Claude Code cannot choose
     the host, so without the guardrail OpenRouter spreads requests over 20 or more hosts. Some cost twice as much,
     and some serve fp4 copies that code worse.
   - One guardrail covers a whole key. When two models are best on different hosts, use **one key per model** (one
     config each, e.g. `openrouter-deepseek` and `openrouter-glm`).
4. The setup's result reaches you at the developer's next message. Explain a failure from the table below. On a
   success, run `{{PY}} tools/pa_ext.py check` and show its lines, then go on to the project's settings.

The key lives only in `~/.claude/pa3-ext/<config>/settings.json` on that machine. A project that runs in WSL needs
the setup run in WSL too.

## The project's settings (yours to write, from the conversation)
Ask the developer per config and per model what each is for, then write exactly that with `{{PY}} tools/pa_ext.py set`:
- `--coder <config>/<alias>`: the default coder for tasks the plan does not name (`--coder in-house` removes it);
- `--lanes <config>=4`: how many run at once per backend;
- `--max-coders <n>`;
- `--escalate in-house`: who takes over a failed run;
- `--effort max`: the external coders' reasoning effort. The default is high (`--effort default` returns to it). Max
  is for the hardest work, where DeepSeek solved one more task in ten for about half as much again, and for GLM,
  which fails easy tasks at high;
- `--use off`: not used in this project;
- `--remind off`: stops the session-start reminder.

`{{PY}} tools/pa_ext.py set --show` prints the block. Never edit pa.json's `external` by hand; the developer may.

Then write the how and when, in the developer's words, as a section of HOW_WE_WORK.md of at most three lines, e.g.:

    ## External coders <!-- roles: claude expert planner -->
    Cracking waves: openrouter-deepseek/deepseek replaces the in-house coder; a failed run escalates in-house.
    Design and judgment tasks stay in-house.

Updates never touch that section or pa.json's `external`.

## In plans
A task's `coder:` names `<config>/<alias>`, `in-house` or `none`. With no `coder:`, pa.json's default applies. The
planner recommends one per task with a reason in `note:`, and the developer changes any of them in the plan view.

## Learning from each run
Each external model and each codebase brings its own friction: DeepSeek chained `cd` into commands, MiniMax gave up
early, and bfm's coders needed its disassembler. PA3 heals it without waiting for the developer:
1. **The fence.** An external coder reads only the project and system folders and writes only inside the project
   (Claude Code's read fence and project-anchored edit rules). A folder outside the project its tools must read (bfm's
   Ghidra) is the developer's to allow: `{{PY}} tools/pa_ext.py set --dir <path>` (never a home, root or secrets
   folder); `--drop-dir` removes it.
2. **The judge.** Auto mode cannot run on another provider's model, so a command no rule allows goes to PA3's judge
   (Opus 5.5 at medium, on the subscription): it refuses anything that reaches the network (web needs go to the
   expert's web retriever) or a path outside the project, and otherwise decides as auto mode would, under PA3's policy
   for external coders. What it allows with a narrow rule (`Bash(objdump:*)`) is learned for the project and
   never judged again; a refusal is remembered too. `{{PY}} tools/pa_ext.py runs` lists what it learned; `set --forget
   <rule>` drops one.
3. **Learning mode.** A model new to the project runs one coder at a time there until a run comes back clean: no
   denial, no read-guard failure, a proper report, no note added after it (what the judge allowed or learned, and a
   sandbox collision between coders, don't count). Then it gets the project's lanes; a run that meets friction later
   puts it back to one at a time. A project with one lane sees no change.
4. **The check-in after each coder.** A finished external coder's report ends with `HEALTH` lines when it met friction
   (`{{PY}} tools/pa_ext.py health <label>` prints them for any run). The task's expert reads them at once, corrects the
   coders still running, adjusts its next brief, and writes down what later coders need with its evidence and date:
   `set --note "<text>"` (every external coder in this project) or `set --model-note <config>/<alias> "<text>"` (every
   coder on that model, in every project). Each note reaches you as a notice, `‹pa› FYI, nothing to ask: …`, with its
   undo (`set --drop-note <n>` or `set --drop-model-note <config>/<alias> <n>`): tell the developer in one line; ask
   nothing.
5. **What only the developer decides** reaches you as `EXT-HEALTH:` lines in an expert's report (a folder outside the
   project, the lanes, which work suits a model): tell the developer each in plain words with its evidence, and apply
   only what they approve, with `{{PY}} tools/pa_ext.py set` (`--dir`, `--lanes …`, `--effort`, or `--allow "<rule>"`
   for an allow rule they want in `.claude/settings.json`, which in-house agents get too).
6. After a phase closes, when external coders ran since the last review, the close notice asks you to start the
   `auditor` with `EXTERNAL REVIEW · PHASE <n>` (in the background, once the close is released). It reads every run
   since the last review against what is in force (`{{PY}} tools/pa_ext.py runs`: notes, learned rules, learning mode,
   settings): did each fix work, what still hurts, how the project should use its external coders. Its `EXT-HEALTH:`
   lines are handled as in step 5; an `EXT-SET:` line is a `{{PY}} tools/pa_ext.py set` change (`--effort max`,
   `--drop-note 2`, `--forget <rule>`, `--lanes …`), asked and applied the same way.

## Costs and safety
- The launcher pins every model setting to the chosen model, and each run reports any other model it saw (`LEAK:` on
  the report and in red on the statusline).
- Claude Code's own cost figure for these models is meaningless; the ledger prices them from its own rows.
- OpenRouter's key figures lag a few minutes.

## Troubleshooting
| What it says | Why | Fix |
|---|---|---|
| `401` | wrong or revoked key | new key, run the setup again (update the config) |
| `402` | no credit, or the key's limit reached | add credit or raise the limit at openrouter.ai |
| `404 … 0 endpoints … guardrail` | the key's guardrail allows neither this model nor its host | fix the guardrail, or test this model with its own key |
| `404` model not found | a wrong model id | copy the id from the model's OpenRouter page (`vendor/model`) |
| `429` or "Provider returned error" | the host is busy; Claude Code retried and gave up | pin a steadier host in the guardrail; the expert escalates the task meanwhile |
| `connection failed` | no network, or the local server is not running | start it; `check` again |
| `need bubblewrap and socat`, `bubblewrap is required` or `socat not installed` | Linux or WSL: the coder's commands run in Claude Code's sandbox, which keeps the key out, and it needs both | the developer runs `sudo apt-get install -y bubblewrap socat` in a terminal; `check` again |
| `blocked: … without a report` | the run died (host errors, a crash) | the expert escalates; the error is in the report |
| `blocked: the <config> lane is full` | that many runs are already going | wait for one to finish, or raise `--lanes` |
| `blocked: learning mode: …` | the model is new to the project and runs one coder at a time until a run comes back clean | the expert starts it when the running coder reports; nothing to do |
| `Refused by PA3: no network …` or `… is outside this project` | the judge's fixed rules | none: web needs go to the expert; a folder the tools must read is `set --dir` on the developer's word |
| `LEAK: requests ran on <id>` | a request named another model | stop external runs and tell the developer; check the key's activity at openrouter.ai |
| a command refused by PA3's judge | the judge found it against PA3's policy | the expert finds another route; `runs` lists what it refused, and `set --forget <key>` lets it be judged again |
