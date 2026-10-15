# partner keys (3.27 T6): the first Agent call's subagent_type and its `[effort <level>]` hint (pa-claude.md, mod/pa-watch/hooks/ladder.ts), or no Agent call

p01: {"no_agent": true, "end_text_any": ["T3"]} — status question: answer from the running-now note, no spawn
p02: {"no_agent": true, "end_text_any": ["14"]} — short factual question: one lookup, answered itself
p03: {"no_agent": true, "end_text_any": ["?"]} — unclear request: ask the developer, no spawn
p04: {"subagent_type": "pa-planner"} — start a phase: brief pa-planner
p05: {"subagent_type": "pa-thinker", "effort": ["low", "medium"]} — small thinker question: low or medium
p06: {"subagent_type": "pa-thinker", "effort": ["max"]} — "think hard": thinker at max
p07: {"subagent_type": "pa-thinker", "effort": ["deep"]} — deep-thinker asked by name: pa-thinker [effort deep]
p08: {"subagent_type": "pa-thinker", "effort": ["max"]} — hard question, deep-thinker not named: max, never deep
p09: {"subagent_type": "retriever-code"} — broad codebase lookup: retriever-code
p10: {"subagent_type": "pa-coder"} — a change larger than ~10 tool calls: pa-coder
