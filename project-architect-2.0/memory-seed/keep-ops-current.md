---
name: keep-ops-current
description: Update docs/ops-setup.md in the SAME change as any tooling/env/hook/path change — it's the single doc a fresh session or fresh machine rebuilds from
metadata:
  type: feedback
---

`docs/ops-setup.md` is the single place a fresh session — or a fresh machine after an OS reinstall — reconstructs the working environment from (build/run/test commands, env vars, version pins, MCP/hook lifecycle, agent-state wiring, data paths, backup posture). **Update the relevant section in the SAME change as any tooling/env/hook/path change.**

**Why:** It rots silently otherwise — a completeness audit on a predecessor project found the equivalent file had drifted badly (hooks, pipeline, scripts, backup posture all undocumented). Keeping it current in-change prevents the rot. **Registry: H7.**

**How to apply:** When you install/upgrade tooling, add/rename a tool, change the build/test commands or pinned versions, add/modify a hook or MCP config, or move a path — update the relevant `ops-setup.md` section as PART of that task, and note it in `CURRENT_PHASE.md`. Mark not-yet-verified items `# TODO:`. See [[project-governance-system]].
