#!/usr/bin/env bash
# Start (or stop) an isolated API server for a dashboard e2e suite.
#
#   framework/run_server.sh start > env.sh   # prints exports once healthy
#   framework/run_server.sh stop
#
# Inputs (all optional):
#   E2E_SUITE_DIR            suite directory (default: tests/dashboard_e2e)
#   E2E_PLUGINS_CONFIG       plugins.yaml (default: $E2E_SUITE_DIR/plugins.yaml)
#   E2E_SERVER_TWIST_MODULES modules that register server twists
#   E2E_RUNTIME_DIR          server state dir (default: a fresh temp dir)
#   E2E_WARMUP_REQUESTS      requests that warm the workers (default: E2E_WORKERS, or 8)
#   E2E_PORT, E2E_KUBECONFIG, E2E_ADMIN_USER, E2E_ADMIN_PW
#
# State lives in $E2E_RUNTIME_DIR, so the server never touches ~/.sky. Tests
# that seed the DB must use the same SKY_RUNTIME_DIR, which the exports set.
set -euo pipefail

FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_DIR="${E2E_SUITE_DIR:-$(dirname "$FRAMEWORK_DIR")}"
PORT="${E2E_PORT:-46611}"
RUNTIME_DIR="${E2E_RUNTIME_DIR:-$(mktemp -d /tmp/sky-e2e.XXXXXX)}"
ADMIN_USER="${E2E_ADMIN_USER:-e2eadmin}"
ADMIN_PW="${E2E_ADMIN_PW:-e2e-admin-pw-not-secret}"
mkdir -p "$RUNTIME_DIR"

export SKY_RUNTIME_DIR="$RUNTIME_DIR"
# Some server subprocesses (e.g. consolidation-mode jobs controllers) resolve
# paths from ~ rather than SKY_RUNTIME_DIR; give the server its own home so
# they can never read or launch the machine's real ~/.sky state.
export HOME="$RUNTIME_DIR"
export SKYPILOT_API_SERVER_LOCAL_PORT="$PORT"
export SKYPILOT_GLOBAL_CONFIG="$RUNTIME_DIR/config.yaml"
export SKYPILOT_SERVER_PLUGINS_CONFIG="${E2E_PLUGINS_CONFIG:-$SUITE_DIR/plugins.yaml}"
# The suite's own modules first (its server_twists must win over this
# suite's file of the same name), then framework.server_plugin.
export PYTHONPATH="$SUITE_DIR:$(dirname "$FRAMEWORK_DIR")${PYTHONPATH:+:$PYTHONPATH}"
# Default: every server_twists*.py in the suite (one file per area, so
# people adding twists in different areas do not edit the same file).
if [[ -z "${E2E_SERVER_TWIST_MODULES:-}" ]]; then
  modules=()
  for f in "$SUITE_DIR"/server_twists*.py; do
    [[ -f "$f" ]] && modules+=("$(basename "$f" .py)")
  done
  E2E_SERVER_TWIST_MODULES="$(IFS=,; echo "${modules[*]:-}")"
  export E2E_SERVER_TWIST_MODULES
fi
export SKYPILOT_DISABLE_USAGE_COLLECTION=1
export SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=1

# PIDs listening on $PORT: lsof where it exists, else ss (minimal images).
_listeners() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true
  else
    echo "run_server.sh: need lsof or ss to find the server on port $PORT" >&2
    exit 1
  fi
}

if [[ "${1:-start}" == "stop" ]]; then
  sky api stop >/dev/null 2>&1 || true
  # `sky api stop` finds the server through SKY_RUNTIME_DIR; a server started
  # with another runtime dir is stopped through its port instead. Signal the
  # whole process group: killing only the listener leaves its executors and
  # their ports behind, and the next start fails on them.
  for pid in $(_listeners); do
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ -n "$pgid" ]] && kill -TERM -- "-$pgid" 2>/dev/null || true
  done
  for _ in $(seq 1 10); do
    [[ -z "$(_listeners)" ]] && break
    sleep 1
  done
  for pid in $(_listeners); do
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ -n "$pgid" ]] && kill -KILL -- "-$pgid" 2>/dev/null || true
  done
  # Consolidation-mode jobs controllers start their own sessions, so neither
  # the port nor the process group reaches them; they outlive the server.
  # Find them by the runtime dir they inherited (Linux /proc only).
  if [[ -n "${E2E_RUNTIME_DIR:-}" && -d /proc ]]; then
    for env in /proc/[0-9]*/environ; do
      if { tr '\0' '\n' < "$env"; } 2>/dev/null | grep -qx "SKY_RUNTIME_DIR=$RUNTIME_DIR"; then
        pid="${env#/proc/}"; pid="${pid%/environ}"
        [[ "$pid" != "$$" ]] && kill -KILL "$pid" 2>/dev/null || true
      fi
    done
  fi
  exit 0
fi

# Never mistake another server for ours: tests would seed one DB while the
# browser reads another.
if [[ -n "$(_listeners)" ]]; then
  echo "port $PORT is already in use; stop that server first:" \
       "E2E_PORT=$PORT $0 stop" >&2
  exit 1
fi

cat > "$SKYPILOT_GLOBAL_CONFIG" <<'EOF'
allowed_clouds:
  - kubernetes
jobs:
  controller:
    consolidation_mode: true
EOF
# Never let the server see the developer's real clusters. CI points this at a
# kind cluster; locally it defaults to an empty kubeconfig.
export KUBECONFIG="${E2E_KUBECONFIG:-$RUNTIME_DIR/kubeconfig}"
touch "$KUBECONFIG"
# A runner that is itself a Kubernetes pod mounts a service-account token, and
# SkyPilot would offer the cluster it runs on as an "in-cluster" context.
# Tests only see the contexts in KUBECONFIG.
export SKYPILOT_ALL_KUBERNETES_CONTEXTS_INCLUDES_IN_CLUSTER=false
# Consolidation mode sizes its jobs-controller pool to the machine (27
# processes of ~290 MB on 16 CPUs). The suite never runs a managed job; one
# controller keeps the server realistic without eating the machine.
export SKYPILOT_SERVER_JOBS_CONTROLLERS="${SKYPILOT_SERVER_JOBS_CONTROLLERS:-1}"

ADMIN_HASH="$(python -c 'import sys; from sky.server.common import crypt_ctx; print(crypt_ctx.hash(sys.argv[1]))' "$ADMIN_PW")"
export ENABLE_BASIC_AUTH=true
export ENABLE_SERVICE_ACCOUNTS=true
export SKYPILOT_INITIAL_BASIC_AUTH="$ADMIN_USER:$ADMIN_HASH"

START=$(date +%s)
# --deploy (the default, as in production) sizes worker pools to the whole
# machine. Several servers sharing one machine set E2E_SERVER_DEPLOY=0 for the
# small local pools, or they oversubscribe it.
DEPLOY_FLAG=(--deploy)
[[ "${E2E_SERVER_DEPLOY:-1}" == "0" ]] && DEPLOY_FLAG=()
sky api start ${DEPLOY_FLAG[@]+"${DEPLOY_FLAG[@]}"} >"$RUNTIME_DIR/start.log" 2>&1 || {
  cat "$RUNTIME_DIR/start.log" >&2
  tail -50 "$RUNTIME_DIR/.sky/api_server/server.log" >&2 || true
  exit 1
}
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q healthy; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/api/health" | grep -q healthy
echo "# server healthy after $(( $(date +%s) - START ))s" >&2

# Healthy is not warm: request workers start on their first request and each
# takes several seconds to import, so the first test's page could wait 20s for
# its table and time out. Send a burst of jobs-queue requests, one per test
# worker, and wait for every result before handing the server to the tests.
WARM=$(date +%s)
python - "http://127.0.0.1:$PORT" "${E2E_WARMUP_REQUESTS:-${E2E_WORKERS:-8}}" <<'PY'
import concurrent.futures
import sys

import requests

base, count = sys.argv[1], int(sys.argv[2])


def warm(_):
    r = requests.post(f'{base}/jobs/queue/v2',
                      json={'all_users': True, 'skip_finished': False},
                      timeout=60)
    r.raise_for_status()
    request_id = r.headers['X-Skypilot-Request-ID']
    requests.get(f'{base}/api/get', params={'request_id': request_id},
                 timeout=120).raise_for_status()


with concurrent.futures.ThreadPoolExecutor(count) as pool:
    list(pool.map(warm, range(count)))
PY
echo "# request workers warm after $(( $(date +%s) - WARM ))s" >&2

cat <<EOF
export E2E_BASE_URL=http://127.0.0.1:$PORT
export E2E_RUNTIME_DIR=$RUNTIME_DIR
export SKY_RUNTIME_DIR=$RUNTIME_DIR
export SKYPILOT_GLOBAL_CONFIG=$SKYPILOT_GLOBAL_CONFIG
export E2E_ADMIN_USER=$ADMIN_USER
export E2E_ADMIN_PW=$ADMIN_PW
EOF
