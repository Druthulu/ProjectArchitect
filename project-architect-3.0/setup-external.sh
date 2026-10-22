#!/usr/bin/env sh
# setup-external.sh -- set up the external coders on this machine (3.3 T2): find a Python 3.12+
# interpreter, then hand off to tools/pa_ext.py setup (asks for a backend URL, its key and model ids,
# tests every model, saves the config only when all of them answer).
set -eu

check_version() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

py=
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && check_version "$cand"; then
        py=$cand
        break
    fi
done

if [ -z "$py" ]; then
    echo "Could not find a Python 3.12+ interpreter." >&2
    echo "Install one, e.g. 'sudo apt-get install -y python3' (Linux) or 'brew install python@3.12' (macOS), then re-run this script." >&2
    exit 1
fi

exec "$py" "$(dirname "$0")/tools/pa_ext.py" setup "$@"
