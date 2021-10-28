#!/usr/bin/env bash
# YAPF formatter, adapted from ray.
#
# Usage:
#    # Do work and commit your work.

#    # Format files that differ from origin/master.
#    bash format.sh

#    # Commit changed files with message 'Run yapf.'
#
#
# YAPF + Clang formatter (if installed). This script formats all changed files from the last mergebase.
# You are encouraged to run this locally before pushing changes for review.

# Cause the script to exit if a single command fails
set -eo pipefail

ver=$(yapf --version)
if ! echo $ver | grep -q 0.27.0; then
    echo "Wrong YAPF version installed: 0.27.0 is required, not $ver"
    exit 1
fi

# this stops git rev-parse from failing if we run this from the .git directory
builtin cd "$(dirname "${BASH_SOURCE:-$0}")"

ROOT="$(git rev-parse --show-toplevel)"
builtin cd "$ROOT" || exit 1

# # Add the upstream remote if it doesn't exist
# if ! git remote -v | grep -q upstream; then
#     git remote add 'upstream' 'https://github.com/ray-project/ray.git'
# fi

YAPF_VERSION=$(yapf --version | awk '{print $2}')

# # params: tool name, tool version, required version
tool_version_check() {
    if [[ $2 != $3 ]]; then
        echo "WARNING: We use $1 $3, You currently are using $2. This might generate different results."
    fi
}

tool_version_check "yapf" $YAPF_VERSION "0.27.0"

# Only fetch master since that's the branch we're diffing against.
# git fetch upstream master || true

YAPF_FLAGS=(
    '--style' "$ROOT/.style.yapf"
    '--recursive'
    '--parallel'
)

YAPF_EXCLUDES=(
    # '--exclude' 'python/build/*'
)

# Format specified files
format() {
    yapf --in-place "${YAPF_FLAGS[@]}" "$@"
}

# Format files that differ from main branch. Ignores dirs that are not slated
# for autoformat yet.
format_changed() {
    # The `if` guard ensures that the list of filenames is not empty, which
    # could cause yapf to receive 0 positional arguments, making it hang
    # waiting for STDIN.
    #
    # `diff-filter=ACM` and $MERGEBASE is to ensure we only format files that
    # exist on both branches.
    MERGEBASE="$(git merge-base origin/master HEAD)"

    if ! git diff --diff-filter=ACM --quiet --exit-code "$MERGEBASE" -- '*.py' &>/dev/null; then
        git diff --name-only --diff-filter=ACM "$MERGEBASE" -- '*.py' | xargs -P 5 \
             yapf --in-place "${YAPF_EXCLUDES[@]}" "${YAPF_FLAGS[@]}"
    fi

}

# Format all files, and print the diff to stdout for travis.
format_all() {
    yapf --diff "${YAPF_FLAGS[@]}" "${YAPF_EXCLUDES[@]}" test python
}

# format **/*.py

## This flag formats individual files. --files *must* be the first command line
## arg to use this option.
if [[ "$1" == '--files' ]]; then
   format "${@:2}"
   # If `--all` is passed, then any further arguments are ignored and the
   # entire python directory is formatted.
elif [[ "$1" == '--all' ]]; then
   format_all
else
   # Format only the files that changed in last commit.
   format_changed
fi

if ! git diff --quiet &>/dev/null; then
    echo 'Reformatted files. Please review and stage the changes.'
    echo 'Changes not staged for commit:'
    echo

    git --no-pager diff --name-only

    exit 1
fi
