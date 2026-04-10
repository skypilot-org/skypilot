#!/bin/bash
# Basic workload: launch → exec → logs → queue → status → stop → start → down
#                 → jobs launch → jobs queue → jobs logs
#
# Emits ##BENCH_START / ##BENCH_END markers for per-operation timing.
# The benchmark worker parses these to produce structured results.

set -uo pipefail

UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)-$$"}
CLOUD=${BENCHMARK_CLOUD:-"kubernetes"}
THREAD_ID=${BENCHMARK_THREAD_ID:-"0"}
REPEAT_ID=${BENCHMARK_REPEAT_ID:-"0"}

CLUSTER="bench-${UNIQUE_ID}"
JOB="bench-${UNIQUE_ID}"

# ── helper ────────────────────────────────────────────────────────────
bench_run() {
    local op_name="$1"; shift
    echo "##BENCH_START ${op_name} $(date +%s.%N)"
    local rc=0
    "$@" || rc=$?
    echo "##BENCH_END ${op_name} ${rc} $(date +%s.%N)"
    return $rc
}

# ── cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    sky down $CLUSTER -y 2>/dev/null || true
    sky jobs cancel -n $JOB -y 2>/dev/null || true
}
trap cleanup EXIT

# ── workload ──────────────────────────────────────────────────────────

# Prepare a small workdir for file mount testing
workdir=$(mktemp -d)
dd if=/dev/zero of=${workdir}/file.txt bs=1024 count=1024 2>/dev/null

bench_run "sky_launch" \
    sky launch -y -c $CLUSTER --infra $CLOUD --cpus 2+ --memory 4+ \
    --workdir ${workdir} \
    'echo "hello from $(hostname)"'

bench_run "sky_exec" \
    sky exec -c $CLUSTER 'echo "exec done" && sleep 1'

bench_run "ssh" \
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $CLUSTER 'echo "ssh ok"'

bench_run "sky_logs" \
    sky logs $CLUSTER --no-follow

bench_run "sky_queue" \
    sky queue $CLUSTER

bench_run "sky_status" \
    sky status

bench_run "sky_stop" \
    sky stop $CLUSTER -y

bench_run "sky_start" \
    sky start $CLUSTER -y

bench_run "sky_down" \
    sky down $CLUSTER -y

bench_run "sky_jobs_launch" \
    sky jobs launch -y -n $JOB --infra $CLOUD --cpus 2+ --memory 4+ \
    'for i in $(seq 1 30); do echo "$i"; sleep 0.2; done'

# Give the job a moment to be submitted
sleep 5

bench_run "sky_jobs_queue" \
    sky jobs queue

bench_run "sky_jobs_logs" \
    sky jobs logs -n $JOB --no-follow || true

bench_run "sky_jobs_cancel" \
    sky jobs cancel -n $JOB -y || true

rm -rf ${workdir}
echo "##BENCH_WORKLOAD_DONE"
