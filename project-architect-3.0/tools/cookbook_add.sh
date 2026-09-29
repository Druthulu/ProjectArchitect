#!/usr/bin/env bash
# cookbook_add.sh --title "..." --tags a,b [--phase 24 --task T3] [--origin "..."] --file body.md
# cookbook_add.sh --find <term>
#
# Writes cookbook/C<nnnn>.md and one INDEX.md line:
#   C0187 | title | tags | 2026-09-12 | 24/T3 | origin
# Models look entries up with: grep -i <term> cookbook/INDEX.md, then Read the one file.
set -u

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
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

TITLE=""; TAGS=""; PHASE=""; TASK=""; FILE=""; ORIGIN=""; FIND=""
[ $# -eq 0 ] && usage 1
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage 0;;
    --title) TITLE="${2:-}"; shift 2;;
    --tags) TAGS="${2:-}"; shift 2;;
    --phase) PHASE="${2:-}"; shift 2;;
    --task) TASK="${2:-}"; shift 2;;
    --file) FILE="${2:-}"; shift 2;;
    --origin) ORIGIN="${2:-}"; shift 2;;
    --find) FIND="${2:-}"; shift 2;;
    *) refuse "unknown option '$1'";;
  esac
done

ROOT="$(find_root)"
CB="$ROOT/cookbook"
INDEX="$CB/INDEX.md"

if [ -n "$FIND" ]; then
  [ -f "$INDEX" ] || refuse "no cookbook/INDEX.md"
  grep -i -- "$FIND" "$INDEX" | head -n 38
  exit 0
fi

[ -n "$TITLE" ] || refuse "--title is required"
[ -n "$FILE" ] || refuse "--file <body.md> is required"
[ -f "$FILE" ] || refuse "no body file at $FILE"
case "$TITLE" in *"
"*) refuse "--title must be one line";; esac

PE="phase-ends"
if [ -f "$ROOT/.claude/pa.json" ]; then
  V="$(sed -n 's/.*"phase_ends_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/.claude/pa.json" | head -n 1)"
  [ -n "$V" ] && PE="$V"
fi
if [ -z "$PHASE" ] && [ -f "$ROOT/$PE/current/PHASE_PLAN.md" ]; then
  PHASE="$(sed -n '1s/^#[[:space:]]*Phase[[:space:]]*\([^[:space:]]*\).*/\1/p' "$ROOT/$PE/current/PHASE_PLAN.md")"
fi
[ -n "$PHASE" ] || PHASE="-"
WHERE="$PHASE"
[ -n "$TASK" ] && WHERE="$PHASE/$TASK"

mkdir -p "$CB"
n=1
TARGET=""
while [ "$n" -lt 10000 ]; do
  ID="$(printf 'C%04d' "$n")"
  P="$CB/$ID.md"
  if (set -C; : > "$P") 2>/dev/null; then TARGET="$P"; break; fi
  n=$((n + 1))
done
[ -n "$TARGET" ] || refuse "no free cookbook id below C9999"

DATE="$(date +%Y-%m-%d)"
{
  echo "# $ID — $TITLE"
  echo "tags: ${TAGS:--} · date: $DATE · phase/task: $WHERE · origin: ${ORIGIN:--}"
  echo
  cat "$FILE"
} > "$TARGET"

[ -f "$INDEX" ] || printf '# Cookbook -- one line per entry\n# id | title | tags | date | phase/task | origin\n' > "$INDEX"
LINE="$ID | $TITLE | ${TAGS:--} | $DATE | $WHERE | ${ORIGIN:--}"
echo "$LINE" >> "$INDEX"
PY="${PA_PYTHON:-python}"
command -v "$PY" >/dev/null 2>&1 || PY="python"
command -v "$PY" >/dev/null 2>&1 || PY="python3"   # Linux ships python3 only
"$PY" "$(dirname "$0")/_credit.py" note --path "$TARGET" --script cookbook_add.sh --whole 2>/dev/null || true
"$PY" "$(dirname "$0")/_credit.py" note --path "$INDEX" --script cookbook_add.sh --added ${#LINE} 2>/dev/null || true
echo "cookbook/$ID.md"
echo "$LINE"
exit 0
