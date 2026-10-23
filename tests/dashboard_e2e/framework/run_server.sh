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

export SKY_RUNTIME_DIR="$RUNTIME_DIR"
export SKYPILOT_API_SERVER_LOCAL_PORT="$PORT"
export SKYPILOT_GLOBAL_CONFIG="$RUNTIME_DIR/config.yaml"
export SKYPILOT_SERVER_PLUGINS_CONFIG="${E2E_PLUGINS_CONFIG:-$SUITE_DIR/plugins.yaml}"
# The suite's own modules first (its server_twists must win over this
# suite's file of the same name), then framework.server_plugin.
export PYTHONPATH="$SUITE_DIR:$(dirname "$FRAMEWORK_DIR")${PYTHONPATH:+:$PYTHONPATH}"
if [[ -z "${E2E_SERVER_TWIST_MODULES:-}" && -f "$SUITE_DIR/server_twists.py" ]]; then
  export E2E_SERVER_TWIST_MODULES=server_twists
fi
export SKYPILOT_DISABLE_USAGE_COLLECTION=1
export SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=1

if [[ "${1:-start}" == "stop" ]]; then
  sky api stop
  exit 0
fi

mkdir -p "$RUNTIME_DIR"
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

ADMIN_HASH="$(python -c 'import sys; from passlib.context import CryptContext; print(CryptContext(["bcrypt"]).hash(sys.argv[1]))' "$ADMIN_PW")"
export ENABLE_BASIC_AUTH=true
export ENABLE_SERVICE_ACCOUNTS=true
export SKYPILOT_INITIAL_BASIC_AUTH="$ADMIN_USER:$ADMIN_HASH"

START=$(date +%s)
sky api start --deploy >"$RUNTIME_DIR/start.log" 2>&1 || {
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

cat <<EOF
export E2E_BASE_URL=http://127.0.0.1:$PORT
export E2E_RUNTIME_DIR=$RUNTIME_DIR
export SKY_RUNTIME_DIR=$RUNTIME_DIR
export SKYPILOT_GLOBAL_CONFIG=$SKYPILOT_GLOBAL_CONFIG
export E2E_ADMIN_USER=$ADMIN_USER
export E2E_ADMIN_PW=$ADMIN_PW
EOF
