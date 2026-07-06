# Ops & Setup — {{PROJECT_NAME}}

> **Evolvable reference (docs/ layer). Last revised {{INSTALL_DATE}}.** The single document a fresh session — or a fresh machine after an OS reinstall — rebuilds the working environment from. **Keep it current in the SAME change as any tooling/env/hook/path change** (registry H7); it rots silently otherwise. Mark not-yet-verified items `# TODO:` and fill them the next time you run the command.

## Version pins

| Component | Version | Notes / why pinned |
|---|---|---|
| Language/runtime | {{RUNTIME}} | |
| Build tool | | |
| Test runner | | |
| Key dependencies | | |
| {{TOOL}} | | |

## Environment

- **OS / shell:** {{ENV_OS}}
- **Project root:** {{PROJECT_ROOT}}
- **Machine notes:** [cores/RAM if it affects concurrency; CI or no-CI; reinstall cadence — anything that shapes ops decisions]

## Build / run / test

```
# build
{{BUILD_CMD}}
# run
{{RUN_CMD}}
# test gate (the machine-checkable milestone gate — registry M1/P9)
{{TEST_CMD}}
```

- **The gate:** [what "green" means — modes if any (fast smoke vs full), the pinned known-failures baseline if the suite has pre-existing reds, when the slow suite is required].

## Claude Code integration (this project's agent wiring)

- **Statusline:** `~/.claude/statusline.sh` (model · effort · context · rate limits) — global; re-copy from the package on a fresh machine.
- **Effort baseline:** `effortLevel: "xhigh"` in `~/.claude/settings.json` (Max/Ultracode are session-only — see `docs/effort-map.md`).
- **Agent state (H8):** memory at `.claude-state/memory/` via `autoMemoryDirectory` in **`.claude/settings.local.json`** — *this is an absolute path and is machine-local; on a fresh machine, re-point it at `<this repo's absolute path>/.claude-state/memory`.* Transcripts: the `SessionEnd` hook in `.claude/settings.json` copies them into `.claude-state/transcripts/`; `tools/backup-claude-state.sh` sweeps stragglers (run at every phase boundary).
- **MCP / hooks:** {{MCP_HOOKS}} *(any project MCP servers, session hooks, and their lifecycle — e.g. a service a SessionStart hook boots.)*

## Tooling inventory

| Tool | Location | Purpose |
|---|---|---|
| `tools/backup-claude-state.sh` | `tools/` | sweep agent state → `.claude-state/` (H8) |
| {{TOOL_ROW}} | | |

## Backup & git posture

- Commit cadence: per completed task, after updating `CURRENT_PHASE.md` (P4); the developer pushes (H6); no AI-attribution trailers (H5).
- Generated/regenerable outputs are gitignored; decision records are committed (phase-close gitignore discipline).
- `.claude-state/` is committed while the repo is private; **excluded from any public mirror** (transcripts capture full tool output).
- {{BACKUP_NOTES}}

## Fresh-machine restore checklist

1. Clone the repo to a fast local filesystem.
2. Install the pinned toolchain (Version pins table above).
3. Copy `project-architect-2.0/statusline/statusline.sh` → `~/.claude/statusline.sh`; merge the settings snippet into `~/.claude/settings.json`.
4. Re-point `.claude/settings.local.json`'s `autoMemoryDirectory` at this clone's absolute `.claude-state/memory` path.
5. Run the test gate to confirm the environment is live.
6. {{RESTORE_EXTRAS}}
