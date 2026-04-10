#!/bin/bash
# Basic workload organised into independent phases that can be shuffled.
#
# Phases:
#   cluster  — launch → exec → ssh → logs → queue → status → stop → start → down
#   jobs     — jobs launch → jobs queue → jobs logs → jobs cancel
#
# Set BENCHMARK_PHASE_ORDER to a comma-separated list to control execution
# order (default: "cluster,jobs").  The benchmark worker shuffles this per
# thread so that concurrent workers hit different API endpoints.
#
# Each phase manages its own resources and cleanup.
# Emits ##BENCH_START / ##BENCH_END markers for per-operation timing.

set -uo pipefail

UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)-$$"}
CLOUD=${BENCHMARK_CLOUD:-"kubernetes"}
THREAD_ID=${BENCHMARK_THREAD_ID:-"0"}
REPEAT_ID=${BENCHMARK_REPEAT_ID:-"0"}

# ── helper ────────────────────────────────────────────────────────────
bench_run() {
    local op_name="$1"; shift
    echo "##BENCH_START ${op_name} $(date +%s.%N)"
    local rc=0
    "$@" || rc=$?
    echo "##BENCH_END ${op_name} ${rc} $(date +%s.%N)"
    return $rc
}

# ── phase: cluster lifecycle ─────────────────────────────────────────
phase_cluster() {
    local CLUSTER="bench-cl-${UNIQUE_ID}"
    local workdir
    workdir=$(mktemp -d)
    dd if=/dev/zero of="${workdir}/file.txt" bs=1024 count=1024 2>/dev/null

    # Ensure cleanup on return (both success and failure)
    trap "sky down $CLUSTER -y 2>/dev/null || true; rm -rf ${workdir}" RETURN

    bench_run "sky_launch" \
        sky launch -y -c $CLUSTER --infra $CLOUD --cpus 2+ --memory 4+ \
        --workdir "${workdir}" \
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
}

# ── phase: managed jobs ──────────────────────────────────────────────
phase_jobs() {
    local JOB="bench-job-${UNIQUE_ID}"

    trap "sky jobs cancel -n $JOB -y 2>/dev/null || true" RETURN

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
}

# ── execute phases in the requested order ────────────────────────────
IFS=',' read -ra PHASES <<< "${BENCHMARK_PHASE_ORDER:-cluster,jobs}"
for phase in "${PHASES[@]}"; do
    "phase_${phase}"
done

echo "##BENCH_WORKLOAD_DONE"
