# Test Fixtures

Trimmed copies of real Project Architect 2.0 repositories, used by `test_install_project.py`
to verify layout detection and (in future phases) migration.

## Fixtures

### stock20 — TerrainDiffusion7DTD
- **Source:** `Z:/Storage/git/TerrainDiffusion7DTD`
- **Copy date:** 2026-09-20
- **Layout:** stock Project Architect 2.0 (`RULES_REGISTRY.md` + `phase-ends/`)
- **What was trimmed:** RULES_REGISTRY.md reduced from ~400 lines / 40+ rules to ~70 lines / 12 rules
  across P/H/G/R sections with real ids; docs/ops-setup.md reduced from ~1500 lines to ~30 lines
  keeping `## Version pins`, `## Environment` (with `### Build` and `### Test`), and
  `## Build / run / test`; docs/7dtd-modding-cookbook.md reduced from ~100 lines / 4 sections to
  3 sections; memory files carry frontmatter from the real ones but invented harmless content;
  all PhaseEnd files reduced to heading + date + short summary; settings.local.json allowlist
  reduced to 3 entries. No secrets, tokens, or absolute home paths.

### onex — Vantage
- **Source:** `Z:/Storage/git/Vantage`
- **Copy date:** 2026-09-20
- **Layout:** 1.x-shaped Project Architect (`Project Context Markdowns/` folder)
- **What was trimmed:** Rules registry reduced from ~2400 lines to ~60 lines keeping 8+ headlines
  including a Section D block; CURRENT_PHASE.md reduced to the checkpoint block with HARNESS FACTS;
  all PhaseEnd files reduced to heading + date + short summary; Plan/Verdict/Checkpoint docs
  reduced to heading + 2-line summary; settings.local.json kept to 5 lines.
  No secrets, tokens, or absolute home paths.
- **T5.c3 additions:** `CURRENT_PHASE.template.md` (D7 template retire),
  `Vantage_Gen4_X_Spec.md` (D9 gen-prefixed research), `Vantage_Product_Thesis.md`
  (D9 stay-word kept), `docs/Vantage_Cookbook.md` with 2 `##` (D11 cookbook split),
  `docs/Vantage_Ops_Setup.md` with 2 `##` (D11 ops split).

### overlay — bfm-decomp
- **Source:** `//wsl.localhost/Ubuntu-24.04/home/you/bfm-decomp`
- **Copy date:** 2026-09-20
- **Layout:** overlay kit (`decomp-architect/` folder + `DIGEST.md` + R-rules past 100)
- **What was trimmed:** DIGEST.md reduced from ~475 lines to ~50 lines keeping R1-R3 and R101-R103
  headings; matching-cookbook.md reduced from ~700KB / 100+ sections to 4 sections; all PhaseEnd
  files reduced to heading + date + short summary; decomp-architect templates kept as stubs
  with real headings; settings.json carries the real Ghidra MCP SessionStart/SessionEnd hooks.
  No secrets, tokens, or absolute home paths.
