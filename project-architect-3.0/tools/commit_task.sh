#!/usr/bin/env bash
# commit_task.sh <TaskId> "<one line <=100 chars>" [paths...]
#
# Stages the explicit paths plus the task-owned areas only: phase-ends/current/{tasks,logs,
# research,discussions,RECAP.md,TASK_PROGRESS.md} and .claude-state/memory. Never `git add -u`:
# a dirty HOW_WE_WORK.md (the card: staged only when passed), PHASE_PLAN.md, GENERATION_PLAN.md,
# cookbook/, rules/ or code file is committed only when passed explicitly (the router commits its own plan edits:
# commit_task.sh router "<what>" phase-ends/current/PHASE_PLAN.md). The message gets the
# task id prefix unless it already starts with it. Untracked files outside those areas are
# listed and left alone unless passed explicitly (another coder's new files never block).
# Never `git add -A`, never pushes, never --amend, never adds a trailer.
set -u

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

refuse() { echo "refused: $*"; exit 1; }

find_root() {
  local d="$PWD"
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    [ -f "$d/.claude/pa.json" ] && { echo "$d"; return; }
    d="$(dirname "$d")"
  done
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

[ $# -eq 0 ] && usage 1
case "$1" in -h|--help) usage 0;; esac
[ $# -lt 2 ] && refuse "usage: commit_task.sh <TaskId> \"<one line>\" [paths...]"

TASK="$1"; shift
MSG="$1"; shift

case "$MSG" in
  *"
"*) refuse "commit messages are one line (got $(printf '%s' "$MSG" | wc -l) newlines)";;
esac
[ -z "$MSG" ] && refuse "empty message"
[ "${#MSG}" -gt 100 ] && refuse "message is ${#MSG} chars (max 100)"
case "$MSG" in
  "$TASK:"*|"$TASK "*) FULL="$MSG";;
  *) FULL="$TASK: $MSG";;
esac

ROOT="$(find_root)"
cd "$ROOT" || refuse "cannot cd to $ROOT"
git rev-parse --git-dir >/dev/null 2>&1 || refuse "$ROOT is not a git repository"

PE="phase-ends"
if [ -f .claude/pa.json ]; then
  V="$(sed -n 's/.*"phase_ends_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' .claude/pa.json | head -n 1)"
  [ -n "$V" ] && PE="$V"
fi
AUTO="$PE/current/tasks $PE/current/logs $PE/current/research $PE/current/discussions $PE/current/RECAP.md $PE/current/TASK_PROGRESS.md .claude-state/memory"  # was also HOW_WE_WORK.md (3.9 T1)

EXPLICIT="$*"
BAD=""
while IFS= read -r u; do
  [ -z "$u" ] && continue
  ok=0
  for a in $AUTO; do
    case "$u" in "$a"|"$a"/*) ok=1;; esac
  done
  for p in $EXPLICIT; do
    p="${p%/}"
    case "$u" in "$p"|"$p"/*) ok=1;; esac
  done
  [ "$ok" -eq 0 ] && BAD="$BAD$u
"
done <<EOF
$(git ls-files --others --exclude-standard)
EOF

if [ -n "$BAD" ]; then
  # was: refused (exit 1) -- 3.3 T1: parallel coders share one tree, so another task's
  # new files must not block this commit; they are listed and left alone.
  echo "note: untracked files outside the auto-staged areas are not committed (pass yours explicitly):"
  printf '%s' "$BAD" | head -n 20
fi

for p in "$@"; do
  if [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
    git -c core.safecrlf=false add -- "$p" || refuse "git add $p failed"
  else
    refuse "no such path: $p"
  fi
done
for a in $AUTO; do
  [ -e "$a" ] && { git -c core.safecrlf=false add -- "$a" || refuse "git add $a failed"; }
done

# fix-13: an assembled log or summary that still holds an authored placeholder is never
# committed; it is left in the tree unstaged and named, so another task's commit is not
# blocked by it and it cannot ride along half-written (it happened twice on 2026-09-27).
for f in $(git diff --cached --name-only -- "$PE/current/logs" "$PE/current/tasks"); do
  if [ -f "$f" ] && grep -qE '\{\{AUTHORED:[A-Za-z_]+\}\}' -- "$f"; then   # the exact placeholder, not prose about it
    git reset -q -- "$f" || refuse "git reset $f failed"
    echo "left out: $f (unfilled {{AUTHORED}} placeholder; fill it before committing)"
  fi
done

if git diff --cached --quiet; then
  refuse "nothing staged for $TASK"
fi

git -c core.safecrlf=false commit -q -m "$FULL" || refuse "git commit failed"
SHA="$(git rev-parse --short HEAD)"
N="$(git -c core.safecrlf=false show --stat --oneline HEAD | tail -n 1)"
echo "$SHA  $FULL"
echo "$N"
exit 0
