#!/bin/bash
# Light workload: read-only operations only.
# Tests API server read concurrency without provisioning overhead.

set -uo pipefail

UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)-$$"}

bench_run() {
    local op_name="$1"; shift
    echo "##BENCH_START ${op_name} $(date +%s.%N)"
    local rc=0
    "$@" || rc=$?
    echo "##BENCH_END ${op_name} ${rc} $(date +%s.%N)"
    return $rc
}

# Run several rounds of read-only operations
for i in $(seq 1 5); do
    bench_run "sky_status_${i}" sky status
    bench_run "sky_jobs_queue_${i}" sky jobs queue
done

echo "##BENCH_WORKLOAD_DONE"
