#!/bin/bash
# Managed-jobs-biased load workload for the SkyPilot load-test framework.
#
# basic.sh's `jobs` phase is thin: one short job per thread, cancelled before
# it really runs, with `sky jobs queue`/`logs` hit once. That stresses the
# jobs *endpoints* but barely exercises the controller/queue under a standing
# population. This workload instead:
#   1. launches a small population of managed jobs per thread (async submit),
#   2. hammers the read paths that degrade first (`sky jobs queue` / `logs`)
#      while the population is live,
#   3. cancels everything (RETURN trap, runs even on early exit).
#
# Concurrent managed jobs across a run is roughly
#     workers x threads_per_worker x BENCHMARK_JOBS_PER_THREAD.
# Each job provisions its own cluster, so that same number is also the
# data-plane footprint: N concurrent jobs needs ~N x BENCHMARK_JOB_CPUS of
# schedulable CPU on the launch target. Size the target accordingly.
#
# Per-op timing comes from the ##BENCH_START/END markers (parsed by
# shell_generator.py); op names are sky_jobs_launch / sky_jobs_queue /
# sky_jobs_logs / sky_jobs_cancel.
#
# Knobs (env, all optional):
#   BENCHMARK_JOBS_PER_THREAD    jobs launched per iteration        (default 2)
#   BENCHMARK_JOB_DURATION_S     per-job run length, seconds        (default 180)
#   BENCHMARK_JOBS_POLLS         queue/logs poll rounds while live  (default 6)
#   BENCHMARK_JOBS_POLL_GAP_S    sleep between poll rounds, seconds (default 5)
#   BENCHMARK_JOB_CPUS           per-job cpus                       (default 1)
#   BENCHMARK_JOB_MEM_GB         per-job memory, GB                 (default 2)
#   BENCHMARK_CLOUD              infra for the jobs (--infra)       (default kubernetes)
#   BENCHMARK_UNIQUE_ID          name scope (set by the harness)
#
# BENCHMARK_JOB_CPUS must stay >= 1: a normal managed job provisions a real
# cluster whose Ray runtime needs a full CPU, so sub-1 requests never come up.
# (Sub-1 values only work on the bare-pod reuse-mode path, which this workload
# deliberately does not exercise — we want the stock managed-jobs controller.)
#
# NOTE: intentionally NOT `set -e`. Under load some ops are EXPECTED to fail
# (429s, launch-slot exhaustion, transient timeouts); we record them via the
# bench markers and keep going rather than aborting the thread. bench_run
# always returns 0. The RETURN trap still cancels every launched job.
set -uo pipefail

UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)-$$"}
CLOUD=${BENCHMARK_CLOUD:-"kubernetes"}
JOBS_PER_THREAD=${BENCHMARK_JOBS_PER_THREAD:-2}
JOB_DURATION_S=${BENCHMARK_JOB_DURATION_S:-180}
JOBS_POLLS=${BENCHMARK_JOBS_POLLS:-6}
POLL_GAP_S=${BENCHMARK_JOBS_POLL_GAP_S:-5}
JOB_CPUS=${BENCHMARK_JOB_CPUS:-1}
JOB_MEM_GB=${BENCHMARK_JOB_MEM_GB:-2}

# ── helper: time one op, record its exit code, never abort the workload ──
bench_run() {
    local op_name="$1"; shift
    echo "##BENCH_START ${op_name} $(date +%s.%N)"
    local rc=0
    "$@" || rc=$?
    echo "##BENCH_END ${op_name} ${rc} $(date +%s.%N)"
    return 0
}

names=()
cleanup() {
    if [ "${#names[@]}" -gt 0 ]; then
        for n in "${names[@]}"; do
            # Bound cancel so a wedged controller can't stall teardown.
            bench_run "sky_jobs_cancel" timeout 120 sky jobs cancel -n "$n" -y
        done
    fi
}
trap cleanup RETURN

# ── 1. build a small standing population (async submit; don't block) ──
for i in $(seq 1 "$JOBS_PER_THREAD"); do
    JOB="bench-mjob-${UNIQUE_ID}-${i}"
    names+=("$JOB")
    bench_run "sky_jobs_launch" \
        sky jobs launch -y --async -n "$JOB" --infra "$CLOUD" \
        --cpus "$JOB_CPUS" --memory "$JOB_MEM_GB" \
        "echo start ${JOB}; for s in \$(seq 1 ${JOB_DURATION_S}); do echo \$s; sleep 1; done; echo done"
done

# ── 2. hammer the read paths while the population is live ─────────────
#     `sky jobs queue` is the path that degrades first under load; `sky jobs
#     logs` exercises the controller log-stream hookup. `logs` is bounded by
#     `timeout` so a slow/absent stream can't stall the thread.
for _ in $(seq 1 "$JOBS_POLLS"); do
    bench_run "sky_jobs_queue" sky jobs queue
    bench_run "sky_jobs_logs" timeout 60 sky jobs logs -n "${names[0]}" --no-follow
    sleep "$POLL_GAP_S"
done

# ── 3. cancel everything (RETURN trap) ───────────────────────────────
echo "##BENCH_WORKLOAD_DONE"
