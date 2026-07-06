#!/usr/bin/env bash
# backup-claude-state.sh — Project Architect 2.0 (rule H8: repo-self-contained Claude state)
#
# Sweeps this project's Claude Code state from ~/.claude/projects/<slug>/ into
# <repo>/.claude-state/ so the repo is fully self-contained (committed while private).
#
# Modes:
#   (no args)   Manual / phase-boundary sweep: copy all transcripts + any legacy
#               memory dir; print a summary. Run before every phase-end commit.
#   --hook      SessionEnd-hook mode: read the hook JSON from stdin, copy that
#               session's transcript first, then do the same sweep. ALWAYS exits 0
#               so it can never block a session from ending.
#   --dest DIR  Override the destination (testing only; default <repo>/.claude-state).
#
# Never deletes anything, never overwrites a newer file (cp -u). Portable across
# Linux / WSL / git-bash (coreutils only — no rsync dependency).

set -u
HOOK_MODE=0
DEST_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --hook) HOOK_MODE=1 ;;
        --dest) shift; DEST_OVERRIDE="${1:-}" ;;
        *) echo "backup-claude-state: unknown arg '$1'" >&2; [ "$HOOK_MODE" = 1 ] && exit 0 || exit 2 ;;
    esac
    shift
done

fail() { echo "backup-claude-state: $*" >&2; [ "$HOOK_MODE" = 1 ] && exit 0 || exit 1; }

# --- Locate repo root and destination ---
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || ROOT="$(pwd)"
DEST="${DEST_OVERRIDE:-$ROOT/.claude-state}"
mkdir -p "$DEST/transcripts" || fail "cannot create $DEST"

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
copied=0

# --- Hook mode: grab this session's transcript path from the hook's stdin JSON ---
HOOK_SRC=""
if [ "$HOOK_MODE" = 1 ]; then
    input="$(cat 2>/dev/null || true)"
    HOOK_SRC="$(printf '%s' "$input" | tr -d '\r\n' \
        | grep -oP '"transcript_path"\s*:\s*"\K(\\.|[^"\\])*' | head -1 \
        | sed -e 's/\\\\/\//g' -e 's/\\\//\//g')"
    [ -z "$HOOK_SRC" ] && [ -n "${CLAUDE_SESSION_TRANSCRIPT_PATH:-}" ] && HOOK_SRC="$CLAUDE_SESSION_TRANSCRIPT_PATH"
    if [ -n "$HOOK_SRC" ] && [ -f "$HOOK_SRC" ]; then
        out="$(cp -uv "$HOOK_SRC" "$DEST/transcripts/" 2>/dev/null)" && [ -n "$out" ] && copied=$((copied+1))
    fi
fi

# --- Find this project's dir under ~/.claude/projects ---
# Primary: Claude Code's slug transform (non-alphanumerics -> '-').
slug="$(printf '%s' "$ROOT" | sed 's/[^A-Za-z0-9]/-/g')"
PROJ_DIR="$CONFIG_DIR/projects/$slug"
# Fallback 1: the hook transcript's parent dir IS the project dir.
if [ ! -d "$PROJ_DIR" ] && [ -n "$HOOK_SRC" ]; then
    PROJ_DIR="$(dirname "$HOOK_SRC")"
fi
# Fallback 2: newest projects/* dir containing a transcript whose "cwd" is this repo.
if [ ! -d "$PROJ_DIR" ] && [ -d "$CONFIG_DIR/projects" ]; then
    PROJ_DIR="$(grep -rls "\"cwd\":\"$ROOT\"" "$CONFIG_DIR/projects" --include='*.jsonl' 2>/dev/null \
        | head -1 | xargs -r dirname)"
fi
[ -d "${PROJ_DIR:-/nonexistent}" ] || { echo "backup-claude-state: no ~/.claude project dir found for $ROOT (nothing to sweep yet)"; exit 0; }

# --- Sweep 1: all transcripts (*.jsonl, recursive, PRESERVING relative paths — sub-agent
# transcripts in per-session subdirs can share basenames, so flattening would collide) ---
while IFS= read -r -d '' rel; do
    d="$DEST/transcripts/$(dirname "$rel")"
    mkdir -p "$d" 2>/dev/null
    out="$(cp -uv "$PROJ_DIR/$rel" "$d/" 2>/dev/null)" && [ -n "$out" ] && copied=$((copied+1))
done < <(cd "$PROJ_DIR" && find . -name '*.jsonl' -type f -printf '%P\0' 2>/dev/null)

# --- Sweep 2: legacy memory dir (pre-autoMemoryDirectory) -> memory-imported/ for manual merge ---
if [ -d "$PROJ_DIR/memory" ] && [ "$(readlink -f "$PROJ_DIR/memory" 2>/dev/null)" != "$(readlink -f "$DEST/memory" 2>/dev/null)" ]; then
    mkdir -p "$DEST/memory-imported"
    cp -ru "$PROJ_DIR/memory/." "$DEST/memory-imported/" 2>/dev/null \
        && echo "backup-claude-state: legacy ~/.claude memory copied to .claude-state/memory-imported/ (merge into .claude-state/memory/ manually)"
fi

total_local=$(find "$DEST/transcripts" -name '*.jsonl' -type f 2>/dev/null | wc -l)
echo "backup-claude-state: swept $PROJ_DIR -> $DEST (files copied/updated this run: $copied; transcripts now held: $total_local)"
exit 0
