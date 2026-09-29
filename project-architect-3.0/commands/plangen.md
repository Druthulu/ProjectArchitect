---
description: Request a planner-gen session at the next launch
allowed-tools: Bash({{PY}} tools/launch.py:*)
disable-model-invocation: true
---
!`{{PY}} tools/launch.py --request planner-gen`
A planner-gen session is queued (`.run/next_mode`). Do not plan here. If an expert is running, let it finish or `TaskStop` it and set its task back to `next` via plan_edit.py. Then run `{{PY}} tools/launch.py --seed-only` and enter the planning mode it names, in this session.
