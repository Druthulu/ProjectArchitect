# SETUP.md — Project Architect 2.0 Installer

> **For the human:** copy the `project-architect-2.0/` folder into your project's root, open Claude Code there, and say: **"Read SETUP.md and do it."** Expect a handful of permission prompts (file writes; a few under `~/.claude`) — approve them, or choose "allow edits this session" when offered. Everything else below is for the agent.

---

## §0 — Execution contract (agent: read this first, follow it exactly)

1. **You are Claude Code, running from the target project's root**, with this package at `./project-architect-2.0/` (if the folder is elsewhere, resolve `PKG=<its path>` once and use it throughout; all snippets below assume `PKG="./project-architect-2.0"`). Quote every path — several contain spaces.
2. **If you are in plan mode:** your plan is exactly *"execute SETUP.md §1–§9 in order."* Present that, get approval, execute. **Do not redesign, reorder, or 'improve' these steps** — they encode hard-won fixes (version-sorted load order, alternate-heading rule sweeps, placeholder classes) that a redesign would lose.
3. **Copy templates verbatim.** Files are materialized with `cp`, then placeholders are filled with the Edit tool. **Never retype a template's body from memory** — paraphrase drift is the most likely silent failure. Placeholders come in two classes: **copy-time** (`{{PROJECT_NAME}}`, `{{INSTALL_DATE}}`, names/taglines — fill during §3–§5) and **generation-time** (`{{PER_PHASE_MAP}}`, `{{PINNED_CONTEXT}}`, ops build/run/test commands, `{{DOMAIN_FAILSAFES}}`, `{{GENERATED_CONSTRAINTS}}`, `{{ENVIRONMENT}}` — fill during §8). The §9 audit catches any leftovers.
4. **Every step self-verifies** (the ✓ line). If a verification fails: STOP, report exactly what failed, and wait — do not improvise around it.
5. **Idempotent:** every step begins with an existence check and a SKIP branch — re-running this file after a crash is safe and resumes where it left off (the §2 checklist is the resume pointer).
6. Git: commits add **only the files this installer created or moved, by explicit path** (never `git add -A` — the target repo may have unrelated dirty state). **No `Co-Authored-By`/AI-attribution trailers. Never push** — the developer pushes.
7. The developer answers questions once at §1 (path choice) and once at §5 (the dev interview); the generation/migration step (§8) is interactive by design. Everything else runs without prompts.

---

## §1 — Detect the install path & confirm

1. Verify you are in a git repo (`git rev-parse --show-toplevel`); if not, ask the developer — recommend `git init` (the system's history/recovery model assumes git).
2. Inventory the repo (excluding `project-architect-2.0/` and `.git/`):
   - An existing project-context file (any `*ProjectContext*.md`, `*Project_Context*.md`, or similar) **or** any `PhaseEnd_*` files anywhere → **Path C — migrate/upgrade** (the common case).
   - Otherwise, meaningful source files exist → **Path B — existing code, no governance**.
   - Otherwise (effectively empty) → **Path A — new project**.
3. State the detected path, the evidence, and what will happen; **ask the developer to confirm** (one question). Record the answer.

✓ *Verify:* path chosen and confirmed; repo root resolved.

## §2 — Bootstrap the install as a phase (crash recovery)

1. `mkdir -p phase-ends/logs && touch phase-ends/logs/.gitkeep`
2. `cp "$PKG/templates/phase-ends-README.md" phase-ends/README.md` · `cp "$PKG/templates/CURRENT_PHASE.template.md" phase-ends/` · `cp "$PKG/templates/PhaseEnd.template.md" phase-ends/`
3. Create `phase-ends/CURRENT_PHASE.md` from the template. Phase name: Path A/B → **"Phase 0 — Governance install & constitution"**; Path C → **the migration interphase** — version-sorted **after the latest completed phase and before the next planned one** (e.g. latest 9.2, next-planned 9.3 → this phase is **9.2.5**; find the latest with `ls phase-ends/PhaseEnd_*.md | sort -V | tail -1` after §3's moves — provisional name now, finalize after §3). Task checkboxes = §3 through §9 of this file. Tick each as it completes — this is the crash-resume pointer.

✓ *Verify:* the five files exist; `CURRENT_PHASE.md` lists the install checkboxes.

## §3 — Governance file moves (Path C) / layout (all paths)

**Path C only:**
1. **Scan** for governance artifacts: old context file(s), `PhaseEnd_*` (any naming/location), checkpoint/lost-session artifacts, domain reference docs sitting in the repo root, legacy memories under `~/.claude/projects/<this repo's slug>/memory/`.
2. **Present the inventory + proposed mapping — one confirmation:** PhaseEnds → `phase-ends/` with separators normalized to dots (`Phase5_7` → `Phase5.7`); domain docs → `docs/`; junk/lost-session artifacts → `_migration/` (quarantine, never delete); old context → `<name>.md-old-dont-use`.
3. Execute via `git mv` (history preserved). Record the mapping table (it goes in the migration PhaseEnd).

✓ *Verify (Path C):* `ls phase-ends/PhaseEnd_*.md | sort -V` orders correctly — **an integer phase must precede its decimal sub-phases** (`Phase5.md` before `Phase5.5.md`; lexical order gets this wrong — if your listing tool shows `Phase5.md` after `Phase5.9.x.md`, that's display order, the LOAD order must be `sort -V`); `git log --follow` on one renamed file shows pre-move history; old context ends in `-old-dont-use`.

**All paths:** `mkdir -p docs tools .run .claude-state/memory .claude-state/transcripts`

## §4 — Docs layer, registry, tools, gitignore

1. `cp "$PKG/project-architect-2.0.md" docs/project-architect.md` *(the methodology survives package deletion)*
2. `cp` + fill **copy-time** placeholders (Edit tool; `{{PROJECT_NAME}}`, `{{INSTALL_DATE}}` = today, cookbook `{{DOMAIN}}` = the project's recurring craft):
   - `"$PKG/templates/effort-map.template.md"` → `docs/effort-map.md`
   - `"$PKG/templates/cookbook.template.md"` → `docs/<project>-cookbook.md` (name it for the domain)
   - `"$PKG/templates/ops-setup.template.md"` → `docs/ops-setup.md`
   - `"$PKG/templates/RULES_REGISTRY.seed.md"` → `RULES_REGISTRY.md`
3. `cp "$PKG/tools/backup-claude-state.sh" tools/ && chmod +x tools/backup-claude-state.sh`
4. Append to `.gitignore` (create if missing):
   ```
   # Project Architect 2.0
   .run/
   project-architect-2.0/
   ```
   *(`.claude-state/` is deliberately COMMITTED while the repo is private — it is the point of H8. Exclude it from any public mirror.)*

✓ *Verify:* the 5 installed files exist; `grep -c "{{PROJECT_NAME}}" RULES_REGISTRY.md docs/effort-map.md` returns 0 for each; `git check-ignore .run` succeeds.

## §5 — CLAUDE.md, agent-state wiring, memory seeding, dev interview

1. **CLAUDE.md:** `cp "$PKG/templates/CLAUDE.template.md" CLAUDE.md`, fill `{{PROJECT_NAME}}`/`{{PROJECT_TAGLINE}}`/`{{COOKBOOK_NAME}}`; leave the generation-time placeholders (`{{DOMAIN_FAILSAFES}}`, `{{GENERATED_CONSTRAINTS}}`, `{{ENVIRONMENT}}`, `{{DOMAIN_SESSION_START_EXTRAS}}`) for §8. *Path B/C with a pre-existing `CLAUDE.md`:* preserve its full content under a marked `## Pre-existing instructions (merged at PA 2.0 install)` section at the bottom; flag any conflicts to the developer.
2. **Project settings:** write `.claude/settings.json` (merge if it exists — add only missing keys):
   ```json
   { "hooks": { "SessionEnd": [ { "hooks": [ { "type": "command", "command": "bash tools/backup-claude-state.sh --hook", "timeout": 60 } ] } ] } }
   ```
   Write `.claude/settings.local.json` with the **absolute** repo path (machine-local; a fresh machine re-runs just this step — documented in ops-setup):
   ```json
   { "autoMemoryDirectory": "<ABSOLUTE-REPO-PATH>/.claude-state/memory" }
   ```
3. **Dev interview (fills `who-is-dev`):** ask the developer, offering defaults — experience level (beginner/intermediate/advanced), domain focus, working preferences (autonomy posture, recommendation-first options, breadth fan-out welcome?). Keep it to one compact question round.
4. **Seed memories:** `cp "$PKG/memory-seed/"*.md .claude-state/memory/` then Edit `who-is-dev.md` with the interview answers (delete its instruction comments) and fill `{{PROJECT_NAME}}` in `project-governance-system.md`. **Path C:** also run `bash tools/backup-claude-state.sh` now — it imports any legacy `~/.claude` memories to `.claude-state/memory-imported/` for manual merging (ratify still-true ones; discard the rest with the developer's OK).
5. State plainly: **seeded memories and the hook activate from the NEXT session** (settings are read at startup) — this is one reason §9 ends with a mandatory fresh session.

✓ *Verify:* `ls .claude-state/memory/*.md | grep -vc MEMORY.md` = 16 (+ any Path-C imports); `python3 -c "import json;json.load(open('.claude/settings.json'));json.load(open('.claude/settings.local.json'))"` passes; the `autoMemoryDirectory` value starts with `/` (or a drive letter) and ends in `/.claude-state/memory`; `who-is-dev.md` has no leftover `<!-- INSTALL INTERVIEW` comment.

## §6 — Global assets (statusline + user settings) — skip-if-present

1. **Statusline:** if `~/.claude/statusline.sh` does not exist → `cp "$PKG/statusline/statusline.sh" ~/.claude/statusline.sh` and `cmp` to verify. If it exists → `cmp -s` against the package copy; identical → SKIP; different → show the diff and ask (default: **keep the existing one** — it may be newer). Smoke-test either way:
   `printf '{"model":{"display_name":"Test"},"effort":{"level":"max"}}' | bash ~/.claude/statusline.sh` → non-empty output containing `Test`.
   *(Windows-native CC: the settings command must call git-bash's `bash.exe` by full path.)*
2. **User settings merge (`~/.claude/settings.json`):** back it up first (`cp ~/.claude/settings.json ~/.claude/settings.json.bak-<date>` if it exists). Consult `"$PKG/statusline/settings.snippet.json"` → `global` section. **Add a key only if absent; never overwrite an existing key; NEVER touch `model`.** Typical additions: `statusLine` block, `effortLevel: "xhigh"`, the read-only-git permissions allowlist. Validate the result parses as JSON.

✓ *Verify:* smoke-test passed; settings parse; report exactly which keys were added (often: none — that is success, not failure).

## §7 — Install checkpoint commit

1. Tick §2–§6 in `CURRENT_PHASE.md`.
2. Commit **by explicit path**: the created/moved governance files (`CLAUDE.md`, `RULES_REGISTRY.md`, `docs/…`, `phase-ends/…`, `tools/backup-claude-state.sh`, `.claude/settings.json`, `.claude-state/memory/…`, `.gitignore`, and Path C's moves/quarantine). Message: `chore: install Project Architect 2.0 governance` (no trailer). Do not push.

✓ *Verify:* `git status` shows no untracked governance files; the commit exists; `git log -1 --format=%B` contains no `Co-Authored-By`.

## §8 — Path execution (the interactive part — protocols live in `docs/project-architect.md`)

**All paths consult the corpus** (`"$PKG/corpus/README.md"` → the query guide): install stack-matching rules, generalize spirit-right/stack-wrong **kernels** (never domain bodies), record provenance on every adapted rule in registry §E.

- **Path A — new project:** run **Mode 1** (docs/project-architect.md): conversational 12-item intake → Gate 1 structured review → Gate 2 (🟡 Tier-1: prompt for `/effort max` and WAIT for the toggle) → generate ALL outputs per §Generation outputs: `PROJECT_CONTEXT.md` on `"$PKG/templates/PROJECT_CONTEXT.skeleton.md"` (machine-checkable Milestone per phase) · registry §E domain rules (oracles, the M1 gate, provenance) · CLAUDE.md generation-time placeholders · effort-map `{{PER_PHASE_MAP}}` · cookbook `{{PINNED_CONTEXT}}` · ops-setup content (build/run/test commands, env) · finalize memories.
- **Path B — existing code:** codebase recon FIRST (breadth-shaped — offer Ultracode/Workflow per E2, wait for the toggle) → present reality findings → abbreviated intake (what is this, what changed, what's next, constraints, success) → the two gates → generate as Path A, with the constitution documenting **reality as the baseline** (debts → Lessons Learned; roadmap = forward phases only).
- **Path C — migration:** run **Mode 4** steps 4–5 (docs/project-architect.md): consolidate rules into the registry — **sweep EVERY rule-bearing heading** ("Rules Added This Phase" AND "Key Rules Confirmed"/"Architecture Decisions"/"Key technical decisions"/similar); map general legacy rules onto seeded IDs, land project rules in §E (G-numbers), per-phase rules in §F (fresh R-sequence, origin phases noted), retire Chat-era mechanics rules with an adaptation note → generate the NEW constitution from old context + PhaseEnds + repo reality (abbreviated intake) → fill the generation-time placeholders (CLAUDE.md, effort-map, cookbook, ops-setup).

**Placeholder audit (F9 — required):** `grep -rn "{{" --include="*.md" .` — the ONLY acceptable hits are `docs/project-architect.md` (the reference spec) and `phase-ends/*.template.md`. Anything else = incomplete generation → fill it before proceeding.

✓ *Verify:* constitution exists with every skeleton section + Milestones; registry §E is non-empty (and §F populated on Path C); the placeholder audit is clean; the developer has confirmed the generated constitution (Gate 2's output review).

## §9 — Close the install phase (hard stop)

1. Write the PhaseEnd (per `phase-ends/PhaseEnd.template.md`): Path A/B → `PhaseEnd_Phase0.md`; Path C → the interphase number from §2 (e.g. `PhaseEnd_Phase9.2.5.md`), including the Path-C mapping table and anything flagged for the developer. End with the `## Plain-English Recap`.
2. `bash tools/backup-claude-state.sh` (the H8 sweep), then `git mv phase-ends/CURRENT_PHASE.md phase-ends/logs/PhaseLog_<N>.md`.
3. Commit by explicit path: `feat: Project Architect 2.0 installed — constitution generated` (Path C: `…— migrated + constitution regenerated`). Do not push — tell the developer to review and push.
4. Final message, in order: (a) the install manifest (the file list §7 committed + §9 added); (b) *"You may delete `project-architect-2.0/` from this repo (it's gitignored; keep the master copy elsewhere) — `docs/project-architect.md` carries the methodology"*; (c) *"Start a **fresh session** (memories + hooks activate at startup) and say **'Begin Phase 1'*** (Path C: *'Continue — next is Phase <N>'*)*"*; (d) the plain-English recap, last. Then **🛑 HARD STOP** — no previewing, no continuing.

✓ *Verify (before sending the final message):* PhaseEnd exists; `CURRENT_PHASE.md` is archived (not deleted); tree is clean except intentionally-uncommitted items; both commits present, trailer-free.

---

## Install manifest (what exists after a completed install)

```
CLAUDE.md                     RULES_REGISTRY.md            PROJECT_CONTEXT.md (generated §8)
docs/project-architect.md     docs/effort-map.md           docs/<project>-cookbook.md    docs/ops-setup.md
phase-ends/README.md          phase-ends/CURRENT_PHASE.template.md   phase-ends/PhaseEnd.template.md
phase-ends/PhaseEnd_Phase0.md (or the migration interphase)          phase-ends/logs/PhaseLog_<N>.md
tools/backup-claude-state.sh  .claude/settings.json        .claude/settings.local.json (machine-local)
.claude-state/memory/ (16 seeds + MEMORY.md)               .claude-state/transcripts/
.run/ (gitignored)            + Path C: phase-ends/PhaseEnd_*.md (migrated), *-old-dont-use, _migration/
```

## Troubleshooting

- **A `mcp`/oracle/service the project needs isn't reachable** → that's a Phase-1 concern, not an install blocker; note it in ops-setup `# TODO:`.
- **Verification failed mid-install** → fix or report; on resume, re-read `CURRENT_PHASE.md`'s checkboxes and continue from the first unticked step (§0.5).
- **The developer wants Chat-side use** → the Chat kit is `project-architect-2.0.md` + `templates/RULES_REGISTRY.seed.md` + `templates/PROJECT_CONTEXT.skeleton.md` (see the meta doc's dispatch section).
