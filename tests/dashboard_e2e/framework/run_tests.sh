#!/usr/bin/env bash
# Run a dashboard e2e suite in two phases against a server started by
# run_server.sh (its exports must be in the environment):
#
#   1. every non-exclusive test, in parallel (-n $E2E_WORKERS, default 8);
#   2. exclusive tests, which assert global state, one at a time with the
#      server to themselves.
#
#   framework/run_tests.sh [suite dir] [pytest args...]
#
# Set E2E_TWIST_REPORT to a file to collect every run's verdict (JSON lines).
# Set E2E_HEADED=1 to drive a real, headed Chromium; without a display it runs
# under xvfb-run. Set E2E_SCREENSHOT_ALL=1 to keep a full-page screenshot of
# every test under test-results/, not only failures.
set -uo pipefail
FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_DIR="$(dirname "$FRAMEWORK_DIR")"
if [[ $# -gt 0 && -d "$1" ]]; then
  SUITE_DIR="$(cd "$1" && pwd)"
  shift
fi
cd "$SUITE_DIR"
WORKERS="${E2E_WORKERS:-8}"
PYTEST=(python -m pytest)
if [[ "${E2E_HEADED:-0}" == "1" ]]; then
  PYTEST+=(--headed)
  [[ -z "${DISPLAY:-}" ]] && PYTEST=(xvfb-run -a "${PYTEST[@]}")
fi

"${PYTEST[@]}" -n "$WORKERS" -m 'not exclusive' -W ignore -rs "$@"
parallel=$?
"${PYTEST[@]}" -m exclusive -W ignore -rs "$@"
exclusive=$?
# 5 = no tests collected: fine when -k selects only one phase.
[[ $parallel -eq 5 ]] && parallel=0
[[ $exclusive -eq 5 ]] && exclusive=0
exit $(( parallel || exclusive ))
