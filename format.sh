#!/usr/bin/env bash
# Fast formatter using Ruff (replaces YAPF, isort, pylint)
#
# Usage:
#    # Do work and commit your work.

#    # Format files that differ from origin/master.
#    bash format.sh

#    # Commit changed files with message 'Run formatter'
#
# Ruff is 10-100x faster than the traditional tools it replaces.

# Cause the script to exit if a single command fails
set -eo pipefail

# this stops git rev-parse from failing if we run this from the .git directory
builtin cd "$(dirname "${BASH_SOURCE:-$0}")"
ROOT="$(git rev-parse --show-toplevel)"
builtin cd "$ROOT" || exit 1

# Check tool versions
RUFF_VERSION=$(ruff --version | awk '{print $2}')
MYPY_VERSION=$(mypy --version | awk '{print $2}')
BLACK_VERSION=$(black --version | head -n 1 | awk '{print $2}')

# params: tool name, tool version, required version
tool_version_check() {
    if [[ $2 != $3 ]]; then
        echo "Wrong $1 version installed: $3 is required, not $2."
        exit 1
    fi
}

tool_version_check "ruff" "$RUFF_VERSION" "$(grep "^ruff==" requirements-dev.txt | cut -d'=' -f3)"
tool_version_check "mypy" "$MYPY_VERSION" "$(grep mypy requirements-dev.txt | cut -d'=' -f3)"
tool_version_check "black" "$BLACK_VERSION" "$(grep black requirements-dev.txt | cut -d'=' -f3)"

# Directories to format/lint
DIRS=(sky tests examples llm docs)

# Get merge base for changed file detection
MERGEBASE="$(git merge-base origin/master HEAD 2>/dev/null || echo "")"

# IBM-specific code still uses Black
BLACK_INCLUDES=(
    'sky/skylet/providers/ibm'
)

echo '=== SkyPilot Black (IBM-specific code) ==='
black "${BLACK_INCLUDES[@]}"

echo '=== SkyPilot Ruff Linter ==='
# Ruff check with auto-fix (replaces pylint + isort)
if [[ "$1" == '--files' ]]; then
    # Format specific files
    ruff check --fix "${@:2}"
elif [[ "$1" == '--all' ]]; then
    # Format all files
    ruff check --fix "${DIRS[@]}"
else
    # Format only changed files
    if [[ -n "$MERGEBASE" ]]; then
        changed_files=$(git diff --name-only --diff-filter=ACM "$MERGEBASE" -- '*.py' '*.pyi' 2>/dev/null || true)
        if [[ -n "$changed_files" ]]; then
            echo "$changed_files" | xargs ruff check --fix
        else
            echo 'Ruff linter skipped: no Python files changed.'
        fi
    else
        # No merge base, format all
        ruff check --fix "${DIRS[@]}"
    fi
fi

echo '=== SkyPilot Ruff Formatter ==='
# Ruff format (replaces yapf + isort formatting)
if [[ "$1" == '--files' ]]; then
    ruff format "${@:2}"
elif [[ "$1" == '--all' ]]; then
    ruff format "${DIRS[@]}"
else
    if [[ -n "$MERGEBASE" ]]; then
        changed_files=$(git diff --name-only --diff-filter=ACM "$MERGEBASE" -- '*.py' '*.pyi' 2>/dev/null || true)
        if [[ -n "$changed_files" ]]; then
            echo "$changed_files" | xargs ruff format
        else
            echo 'Ruff formatter skipped: no Python files changed.'
        fi
    else
        ruff format "${DIRS[@]}"
    fi
fi

echo '=== SkyPilot Ruff: Done ==='

# Run mypy (type checking - cannot be replaced by Ruff)
echo '=== SkyPilot mypy ==='
mypy $(cat tests/mypy_files.txt) --cache-dir=/dev/null

# Lint and format the dashboard
echo "=== SkyPilot Dashboard linting and formatting ==="
if ! npm -v > /dev/null 2>&1 || ! node -v > /dev/null 2>&1; then
    echo "npm or node is not installed, please install them first"
    # Don't fail the script if npm or node is not installed
else
    npm --prefix sky/dashboard install
    npm --prefix sky/dashboard run lint
    npm --prefix sky/dashboard run format
    echo "SkyPilot Dashboard linting and formatting: Done"
    echo
fi

if ! git diff --quiet &>/dev/null; then
    echo 'Reformatted files. Please review and stage the changes.'
    echo 'Changes not staged for commit:'
    echo
    git --no-pager diff --name-only

    exit 1
fi
