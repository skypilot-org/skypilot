#!/usr/bin/env bash
# Prove each source twist: apply the patch, restart the server, check that the
# tests guarding it fail for the reason the twist names, then revert.
#
#   framework/run_src_twists.sh [suite dir]
#
# A twist with id src-<x> lives at <suite>/twist_patches/<x>.patch, written
# against the repository root that contains the suite. Patches touching
# dashboard or plugin frontend sources rebuild the bundles first, with the
# command in $E2E_FRONTEND_BUILD (default: rebuild the OSS dashboard), which
# can read the patch's path from $E2E_SRC_PATCH. A suite
# that needs its own server setup names its start script in $E2E_RUN_SERVER
# (same start/stop arguments and exports as framework/run_server.sh).
set -uo pipefail
FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_DIR="$(cd "${1:-$(dirname "$FRAMEWORK_DIR")}" && pwd)"
REPO_ROOT="$(git -C "$SUITE_DIR" rev-parse --show-toplevel)"
BUILD="${E2E_FRONTEND_BUILD:-npm --prefix sky/dashboard run build}"
RUN_SERVER="${E2E_RUN_SERVER:-$FRAMEWORK_DIR/run_server.sh}"
ENV_FILE="$(mktemp)"
PYTEST=(python -m pytest)
if [[ "${E2E_HEADED:-0}" == "1" ]]; then
  PYTEST+=(--headed)
  [[ -z "${DISPLAY:-}" ]] && PYTEST=(xvfb-run -a "${PYTEST[@]}")
fi

shopt -s nullglob
status=0
# E2E_SRC_SHARD=k/N runs every N-th patch, starting at the k-th (1-based), so
# several runners can share the patches. E2E_SHARD (test sharding) must not
# apply here: it could deselect the very test that guards a patch.
unset E2E_SHARD
patches=("$SUITE_DIR"/twist_patches/*.patch)
if [[ -n "${E2E_SRC_SHARD:-}" ]]; then
  if [[ ! "$E2E_SRC_SHARD" =~ ^([0-9]+)/([0-9]+)$ ]] ||
     (( BASH_REMATCH[1] < 1 || BASH_REMATCH[1] > BASH_REMATCH[2] )); then
    echo "E2E_SRC_SHARD=$E2E_SRC_SHARD must be k/N with 1 <= k <= N" >&2
    rm -f "$ENV_FILE"
    exit 2
  fi
  k="${BASH_REMATCH[1]}"; n="${BASH_REMATCH[2]}"
  mine=()
  for i in "${!patches[@]}"; do
    (( i % n == k - 1 )) && mine+=("${patches[$i]}")
  done
  patches=(${mine[@]+"${mine[@]}"})
fi

# Whether a patch changes code that is built into a bundle: the OSS dashboard,
# or a plugin's frontend in a suite that loads plugins. --numstat prints
# "added<TAB>deleted<TAB>path"; match the path from its start, so a patch to,
# say, tests/dashboard_e2e/ does not trigger a rebuild.
touches_frontend() {
  # Collect the paths first: under pipefail, grep -q exiting on the first
  # match can make the writer fail with SIGPIPE.
  local paths
  paths="$(git -C "$REPO_ROOT" apply --numstat "$1" | cut -f3)"
  grep -qE '^(sky/dashboard/|[^/]+/[^/]+/frontend/)' <<< "$paths"
}

# The patch in the working tree right now. Whatever ends the run (a failure,
# Ctrl-C, a cancelled CI job), the trap stops the server and takes it out, so
# the checkout never keeps a deliberate regression.
applied=""
revert() {
  [[ -z "$applied" ]] && return 0
  local patch="$applied"
  applied=""
  E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" stop >/dev/null 2>&1 || true
  git -C "$REPO_ROOT" apply --ignore-whitespace -R "$patch"
  if touches_frontend "$patch"; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
}
cleanup() {
  revert
  E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" stop >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for patch in ${patches[@]+"${patches[@]}"}; do
  id="src-$(basename "$patch" .patch)"
  export E2E_SRC_PATCH="$patch"
  echo "::group::$id"
  if ! git -C "$REPO_ROOT" apply --ignore-whitespace "$patch"; then
    # The patch is a one-line regression written against the current code; a
    # refactor of that code on master moves its context.
    echo "$id: twist_patches/$(basename "$patch") does not apply to the" \
         "current source. Re-create it on master: make the same one-line" \
         "regression, then git diff -U1 <file> > twist_patches/$(basename "$patch")" \
         "(see 'Twists' in tests/dashboard_e2e/README.md)." >&2
    status=1
    continue
  fi
  applied="$patch"
  if touches_frontend "$patch"; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
  E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" stop >/dev/null 2>&1 || true
  if E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" start > "$ENV_FILE"; then
    # shellcheck disable=SC1090
    (source "$ENV_FILE" && cd "$SUITE_DIR" && E2E_SRC_TWIST="$id" \
      "${PYTEST[@]}" -W ignore -rs -k "$id") || status=1
  else
    status=1
  fi
  revert
  echo "::endgroup::"
done
exit $status
