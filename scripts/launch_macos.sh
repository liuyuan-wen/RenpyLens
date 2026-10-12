#!/bin/bash

renpylens_action=$1
shift
cd "$(dirname "$0")/.." || exit 1
renpylens_python=

try_python() {
    [ -n "$renpylens_python" ] && return
    [ -x "$1" ] || return
    if "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        renpylens_python=$1
    fi
}

pause_terminal() {
    if [ -t 0 ]; then
        read -r -p 'Press Enter to close...' _
    fi
}

# Use a full interpreter path to select a specific environment.
if [ -n "${RENPYLENS_PYTHON:-}" ]; then
    try_python "$RENPYLENS_PYTHON"
else
    for renpylens_candidate in \
        "$PWD/.venv/bin/python" "$PWD/venv/bin/python" \
        "${VIRTUAL_ENV:-}/bin/python" "${CONDA_PREFIX:-}/bin/python"; do
        try_python "$renpylens_candidate"
    done
    for renpylens_root in \
        "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" \
        /opt/miniconda3 /opt/anaconda3 /opt/homebrew/Caskroom/miniconda/base \
        /opt/homebrew/Caskroom/miniforge/base; do
        try_python "$renpylens_root/envs/renpylens/bin/python"
        try_python "$renpylens_root/bin/python"
    done
    for renpylens_command in python3 python; do
        try_python "$(command -v "$renpylens_command")"
    done
    # Finder launches may have a shorter PATH than an interactive shell.
    for renpylens_candidate in \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        try_python "$renpylens_candidate"
    done
fi

if [ -z "$renpylens_python" ]; then
    echo '[ERROR] Python 3.10+ was not found.'
    echo 'Create .venv in this project, or set RENPYLENS_PYTHON to a Python executable.'
    pause_terminal
    exit 1
fi

echo "Python: $renpylens_python"
if [ "$renpylens_action" = build ]; then
    "$renpylens_python" build.py "$@"
else
    "$renpylens_python" src/main.py "$@"
fi
renpylens_exit_code=$?

if [ "$renpylens_exit_code" -ne 0 ]; then
    echo "[ERROR] RenpyLens $renpylens_action failed. Exit code: $renpylens_exit_code"
fi
if [ "$renpylens_action" = build ] || [ "$renpylens_exit_code" -ne 0 ]; then
    pause_terminal
fi
exit "$renpylens_exit_code"
