#!/usr/bin/env bash
# run.sh -- the house pattern for long or noisy commands (D11).
#
#   run.sh <name> [--tail N | --grep PAT] [--timeout S] -- <cmd...>
#   run.sh --bg <name> -- <cmd...>        detached; writes .run/logs/<name>.pid
#   run.sh --wait <name> [--max 3000]     poll the pid every 30 s, then print the tail
#
# Output goes to .run/logs/<name>.log; the caller sees at most 40 lines.
set -u

TAILN=40
GREPPAT=""
TIMEOUT=""
MODE="fg"
NAME=""

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

is_windows() { case "$(uname -s 2>/dev/null || echo unknown)" in MINGW*|MSYS*|CYGWIN*) return 0;; *) return 1;; esac; }

alive() {
  if is_windows; then
    powershell -NoProfile -NonInteractive -Command \
      "if (Get-Process -Id $1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" \
      >/dev/null 2>&1
  else
    kill -0 "$1" 2>/dev/null
  fi
}

[ $# -eq 0 ] && usage 1
case "$1" in -h|--help) usage 0;; --bg) MODE="bg"; shift;; --wait) MODE="wait"; shift;; esac
[ $# -eq 0 ] && refuse "no <name>"
NAME="$1"; shift
case "$NAME" in ""|*/*|*\\*|*" "*|--*) refuse "<name> must be a bare token (got '$NAME')";; esac

MAXWAIT=3000
while [ $# -gt 0 ]; do
  case "$1" in
    --tail) TAILN="${2:-40}"; shift 2;;
    --grep) GREPPAT="${2:-}"; shift 2;;
    --timeout) TIMEOUT="${2:-}"; shift 2;;
    --max) MAXWAIT="${2:-3000}"; shift 2;;
    --) shift; break;;
    *) refuse "unknown option '$1' (commands go after --)";;
  esac
done

ROOT="$(find_root)"
LOGDIR="$ROOT/.run/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/$NAME.log"
PIDF="$LOGDIR/$NAME.pid"
EXITF="$LOGDIR/$NAME.exit"
REL=".run/logs/$NAME.log"
[ "$TAILN" -gt 39 ] 2>/dev/null && TAILN=39

report() {
  local code="$1"
  local n; n="$(wc -l < "$LOG" 2>/dev/null | tr -d ' ')"
  echo "exit=$code log=$REL lines=${n:-0}"
  if [ -n "$GREPPAT" ]; then
    grep -n -- "$GREPPAT" "$LOG" 2>/dev/null | head -n "$TAILN"
  else
    tail -n "$TAILN" "$LOG" 2>/dev/null
  fi
}

credit_note() {
  local log_bytes tail_bytes kept
  log_bytes=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
  log_bytes=${log_bytes:-0}
  if [ -n "$GREPPAT" ]; then
    tail_bytes=$(grep -n -- "$GREPPAT" "$LOG" 2>/dev/null | head -n "$TAILN" | wc -c | tr -d ' ')
  else
    tail_bytes=$(tail -n "$TAILN" "$LOG" 2>/dev/null | wc -c | tr -d ' ')
  fi
  tail_bytes=${tail_bytes:-0}
  kept=$((log_bytes - tail_bytes))
  [ "$kept" -lt 0 ] && kept=0
  local PY="${PA_PYTHON:-python}"
  command -v "$PY" >/dev/null 2>&1 || PY="python"
  command -v "$PY" >/dev/null 2>&1 || PY="python3"   # Linux ships python3 only
  "$PY" "$(dirname "$0")/_credit.py" runsh --sub "$NAME" --kept-chars "$kept" 2>/dev/null || true
}

case "$MODE" in
fg)
  [ $# -eq 0 ] && refuse "no command after --"
  : > "$LOG"
  if [ -n "$TIMEOUT" ] && command -v timeout >/dev/null 2>&1; then
    timeout -k 5 "$TIMEOUT" "$@" >"$LOG" 2>&1; code=$?
  else
    "$@" >"$LOG" 2>&1; code=$?
  fi
  report "$code"
  credit_note
  exit "$code"
  ;;
bg)
  [ $# -eq 0 ] && refuse "no command after --"
  WRAP="$LOGDIR/$NAME.cmd.sh"
  rm -f "$EXITF" "$PIDF"
  {
    echo '#!/usr/bin/env bash'
    printf 'cd %q\n' "$ROOT"
    is_windows || printf 'echo $$ > %q\n' "$PIDF"
    printf '%q ' "$@"
    # stdin from /dev/null: a prompt must not stall a detached run (was: open stdin; 3.9.6 T1, C0043)
    printf '> %q 2>&1 < /dev/null\n' "$LOG"
    printf 'echo $? > %q\n' "$EXITF"
  } > "$WRAP"
  chmod +x "$WRAP" 2>/dev/null
  if is_windows; then
    BASHEXE="$(cygpath -w "$(command -v bash)" 2>/dev/null || echo bash)"
    pid="$(powershell -NoProfile -NonInteractive -Command \
      "(Start-Process -FilePath '$BASHEXE' -ArgumentList '$WRAP' -WindowStyle Hidden -PassThru).Id" \
      2>/dev/null | tr -d '\r ')"
    [ -z "$pid" ] && refuse "Start-Process did not return a pid"
    echo "$pid" > "$PIDF"
  else
    if command -v setsid >/dev/null 2>&1; then
      setsid nohup bash "$WRAP" >/dev/null 2>&1 </dev/null &
    else
      nohup bash "$WRAP" >/dev/null 2>&1 </dev/null &
    fi
    pid=$!
    i=0
    while [ ! -s "$PIDF" ] && [ $i -lt 20 ]; do sleep 0.1; i=$((i + 1)); done
    [ -s "$PIDF" ] || echo "$pid" > "$PIDF"
    pid="$(tr -d '\r ' < "$PIDF")"
  fi
  echo "bg=$NAME pid=$pid log=$REL"
  echo "wait: tools/run.sh --wait $NAME --max $MAXWAIT"
  exit 0
  ;;
wait)
  [ -f "$PIDF" ] || refuse "no $REL pid file (start it with run.sh --bg $NAME)"
  pid="$(tr -d '\r ' < "$PIDF")"
  [ -n "$pid" ] || refuse "empty pid file for $NAME"
  waited=0
  while alive "$pid"; do
    if [ "$waited" -ge "$MAXWAIT" ]; then
      echo "still running after ${waited}s (pid $pid) log=$REL"
      tail -n "$TAILN" "$LOG" 2>/dev/null
      exit 0
    fi
    sleep 30
    waited=$((waited + 30))
  done
  code="$(tr -d '\r ' < "$EXITF" 2>/dev/null)"
  [ -n "$code" ] || code="?"
  report "$code"
  credit_note
  [ "$code" = "?" ] && exit 0
  exit "$code"
  ;;
esac
