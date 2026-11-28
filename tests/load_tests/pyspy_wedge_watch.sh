#!/usr/bin/env bash
# Watch SkyPilot executor workers for the cancellation-poisoned deadlock
# fingerprint: N provisioning threads parked at logging._acquireLock while
# no thread holds the lock (owner is the idle main thread — reentrant
# RLock; see generators/launch_cancel_generator.py for the mechanism).
#
# Run this INSIDE the api-server pod (it needs to see the executor worker
# processes and their /proc):
#   kubectl exec -n sky-tenant-<t> deploy/<release>-api-server -c skypilot-api -- \
#     bash -s < tests/load_tests/pyspy_wedge_watch.sh
# or copy it in and run it. Installs py-spy if missing.
#
# Env knobs:
#   INTERVAL   seconds between scans (default 20)
#   THRESHOLD  min threads at _acquireLock to call it a wedge (default 8)
#   DUMPDIR    where to write full dumps on a hit (default /tmp/sky-wedge)
set -uo pipefail

INTERVAL="${INTERVAL:-20}"
THRESHOLD="${THRESHOLD:-8}"
DUMPDIR="${DUMPDIR:-/tmp/sky-wedge}"
mkdir -p "$DUMPDIR"

if ! command -v py-spy >/dev/null 2>&1; then
  echo "[watch] installing py-spy..." >&2
  pip install --quiet py-spy 2>/dev/null || pip install --quiet --user py-spy
fi
PYSPY="$(command -v py-spy || echo "$HOME/.local/bin/py-spy")"

echo "[watch] interval=${INTERVAL}s threshold=${THRESHOLD} dumpdir=${DUMPDIR}"
echo "[watch] watching for executor-worker RLock wedges; Ctrl-C to stop"

worker_pids() {
  # Long-lived spawn'd executor workers name their main thread
  # 'SkyPilot:executor:long:<pid>'. Fall back to any python running the
  # executor module.
  ps -eo pid,comm,args 2>/dev/null | \
    grep -iE 'SkyPilot:executor|sky.server.requests.executor|spawn_main' | \
    grep -v grep | awk '{print $1}' | sort -u
}

scan_pid() {
  local pid="$1"
  local dump
  dump="$($PYSPY dump --pid "$pid" 2>/dev/null)" || return 1
  local n
  n="$(printf '%s' "$dump" | grep -c '_acquireLock')"
  # Is any thread actually inside acquire and past it (holding)? The
  # fingerprint is: many waiters, and the main thread NOT in _acquireLock
  # (it owns the lock reentrantly and is idle elsewhere, e.g. pool.next).
  local mainwait
  mainwait="$(printf '%s' "$dump" | grep -A2 'MainThread' | grep -c '_acquireLock')"
  if [[ "$n" -ge "$THRESHOLD" ]]; then
    local ts stamp
    ts="$(date -u +%H:%M:%SZ)"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    echo "[watch $ts] *** PID $pid: $n threads at _acquireLock " \
         "(main-thread-in-acquire=$mainwait) — WEDGE FINGERPRINT ***"
    local out="$DUMPDIR/wedge_${pid}_${stamp}.txt"
    $PYSPY dump --pid "$pid" --locals > "$out" 2>/dev/null
    echo "[watch $ts]     full --locals dump: $out"
    # One native dump too (shows if anything is in uv_spawn/native fork).
    $PYSPY dump --pid "$pid" --native > "${out%.txt}.native.txt" 2>/dev/null || true
    return 0
  fi
  # Non-wedge status line (only when there's some lock traffic).
  if [[ "$n" -gt 0 ]]; then
    echo "[watch $(date -u +%H:%M:%SZ)] pid $pid: $n at _acquireLock (below threshold)"
  fi
  return 1
}

while true; do
  pids="$(worker_pids)"
  if [[ -z "$pids" ]]; then
    echo "[watch $(date -u +%H:%M:%SZ)] no executor workers found yet"
  else
    for pid in $pids; do
      scan_pid "$pid" || true
    done
  fi
  sleep "$INTERVAL"
done
