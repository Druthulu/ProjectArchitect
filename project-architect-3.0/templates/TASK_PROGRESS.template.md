# TASK_PROGRESS — T<n> attempt <k>

<!-- Handoff only. Written when the handoff task is injected at the context threshold, then
     committed (commit_task.sh T<n> "handoff <k>"), then the run returns `handoff`. Verbose on
     purpose: the respawned agent has no memory of this run. The respawned run reads this file,
     then archives it at once to logs/T<n>.progress<k>.md (git mv) before continuing, so its
     own handoff never overwrites it (3.1: attempt 2 once overwrote attempt 1). -->

Task: T<n> <title>
Attempt: <k>   Agent: <agent file>   Ctx at handoff: <n>k   Commit: <sha>

## Done so far
- <what is finished and verified, one line each>

## In flight
- <file mid-edit> — <what state it is in, what is left>
- last commands + results: <name → .run/logs/<name>.log → verdict>

## Hypotheses rejected
- <hypothesis> — <evidence>

## Current hypothesis
<one paragraph: what is believed true and what would confirm it>

## Next 5 steps
1. <step>
2. <step>
3. <step>
4. <step>
5. <step>

## Gotchas
- <trap the next attempt would otherwise hit>

## State to carry verbatim
<exact strings, ids, paths, values the next attempt must not re-derive>

## Coder runs so far
- c<k> <coder> — <outcome> — logs/T<n>.c<k>.md

## Reports commissioned
- R<N>-<nnn> — <subject>
