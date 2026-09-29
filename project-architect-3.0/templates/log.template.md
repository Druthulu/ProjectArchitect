# T<n> full log — <agent> — <date>
<!-- assembled by task_log.py -->

<!-- Full record. Written once at the end of the task. No size cap, no tables, no tool-output
     dumps — name the run.sh log path instead (.run/logs/<name>.log). Nothing upstream reads
     this file: the router, the planner and the next expert read tasks/T<n>.md. A retriever-digest
     reads it on demand. Coder runs get their own file, logs/T<n>.c<k>.md, same shape. -->

## Timeline
- <time or step> — <what was done, what happened>

## Hypotheses rejected
{{AUTHORED:hypotheses}}

## Commands run
- <name> → .run/logs/<name>.log — <verdict in one line>

## Coder briefs sent
- c<k> coder-opus55 — <task line> → <return status>, log logs/T<n>.c<k>.md

## Retriever questions asked
- <question> → R<N>-<nnn>

## State worth keeping
{{AUTHORED:state}}
