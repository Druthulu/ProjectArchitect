#!/usr/bin/env bash
# The PA 3.2 live feed launcher (3.23 T13d): the router plugin's manifest monitor `feed`, and the router's own restart
# through the Monitor tool (`--dir`), run this. It execs pa_feed.py under the project's Python, so the monitor's process
# is Python and a kill reaches it. With the mod off it idles silently forever (a monitor that exits sends
# an end notice that wakes the model in a session the router does not answer); with no Python it prints one error
# record for the router, then idles (3.23 T13d.c2). An idle loop ends once the Claude Code process `$CLAUDE_PID` is gone,
# checked every 30 s (3.23 T13d.c6b), every 300 s in an inert session (no pa.json or no `mod: on`; 3.24 T8: the mod loads
# in every session, so the idle feed costs next to nothing); without CLAUDE_PID it idles forever.
#   pa_feed.sh <project> [--dir <feed folder>]
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$1"
shift

claude_alive() {
  if command -v tasklist >/dev/null 2>&1; then
    # Windows (Git Bash): CLAUDE_PID is a Windows pid, which kill -0 does not see.
    tasklist //FI "PID eq $CLAUDE_PID" //NH 2>/dev/null | grep -qw "$CLAUDE_PID"
  else
    kill -0 "$CLAUDE_PID" 2>/dev/null
  fi
}

# idle [seconds between checks, default 30]
idle() {
  if [ -z "${CLAUDE_PID:-}" ]; then
    while :; do sleep 3600; done
  fi
  while claude_alive; do sleep "${1:-30}"; done
  exit 0
}

pa="$project/.claude/pa.json"
[ -f "$pa" ] || idle 300
# pa.json is flat JSON; a key's string value, '' when absent.
key() {
  sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$pa" | head -n 1
}
[ "$(key mod)" = "on" ] || idle 300

python=()
p="$(key python)"
if [ -n "$p" ] && "$p" --version >/dev/null 2>&1; then
  python=("$p")
else
  for c in python3 python "py -3"; do
    # shellcheck disable=SC2086
    if $c --version >/dev/null 2>&1; then
      read -r -a python <<<"$c"
      break
    fi
  done
fi
if [ ${#python[@]} -eq 0 ]; then
  # 3.23 T13d.c2: the mod is on, so the router answers this one record: the feed cannot start, no more restarts.
  printf '%s\n' '{"pa":"feed","error":"no python"}'
  idle
fi

exec "${python[@]}" -I -X utf8 "$here/pa_feed.py" --project "$project" "$@"
