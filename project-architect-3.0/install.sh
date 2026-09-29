#!/bin/sh
# install.sh -- WSL/Linux/macOS bootstrap: find (or install) a Python 3.12+
# interpreter, clone or pull the package source, then hand off every argument
# to pa_install.py --root.  No other logic lives here; see pa_install.py --help.
#
#   install.sh [--dry-run] [--yes] [--config-dir DIR] [--source URL] ...
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

if [ -z "$py" ] && command -v apt-get >/dev/null 2>&1; then
    echo "No Python 3.12+ interpreter found; installing python3 via apt-get..."
    sudo apt-get install -y python3
    if command -v python3 >/dev/null 2>&1 && check_version python3; then
        py=python3
    fi
fi

if [ -z "$py" ]; then
    echo "Could not find or install a Python 3.12+ interpreter." >&2
    echo "Install one, e.g. 'sudo apt-get install -y python3' (Linux) or 'brew install python@3.12' (macOS), then re-run this script." >&2
    exit 1
fi

echo "Using interpreter: $py"
case "$0" in
    */*) here=${0%/*} ;;
    *) here=. ;;
esac

# Parse --config-dir and --source from the arguments
config_dir=""
source_url=""
skip_next=""
for arg in "$@"; do
    if [ -n "$skip_next" ]; then
        case "$skip_next" in
            config-dir) config_dir="$arg" ;;
            source) source_url="$arg" ;;
        esac
        skip_next=""
        continue
    fi
    case "$arg" in
        --config-dir) skip_next="config-dir" ;;
        --source) skip_next="source" ;;
    esac
done
if [ -z "$config_dir" ]; then
    config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
fi

clone="$config_dir/pa3-src"

# Clone or pull when git is available
if command -v git >/dev/null 2>&1; then
    if [ -z "$source_url" ]; then
        source_url=$("$py" -c "import sys,os;sys.path.insert(0,'$here');from pa import config;print(config.DEFAULTS['install']['source'])" 2>/dev/null || echo "https://github.com/Druthulu/ProjectArchitect.git")
    fi
    if [ -d "$clone/.git" ]; then
        git -C "$clone" fetch --quiet >/dev/null 2>&1 || true
        git -C "$clone" pull --ff-only >/dev/null 2>&1 || true
    else
        git clone -c core.autocrlf=false "$source_url" "$clone" >/dev/null 2>&1 || true
    fi
    if [ -f "$clone/project-architect-3.0/pa_install.py" ]; then
        exec "$py" "$clone/project-architect-3.0/pa_install.py" --root "$@"
    fi
fi

if ! command -v git >/dev/null 2>&1; then
    echo "git not found on PATH; running the local installer without a clone."
fi
exec "$py" "$here/pa_install.py" --root --no-clone "$@"
