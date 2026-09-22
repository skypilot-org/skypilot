#!/bin/bash

# Script to fetch controller logs for recently-failed jobs in the queue.
# Usage: ./fetch_failed_job_logs.sh
#
# On a long-lived/shared API server the queue can contain many old failed
# jobs from unrelated runs. The queue is ordered newest-first, so we only
# fetch logs for the most recent failed jobs (the ones the failing test just
# created) and bound each fetch with a timeout, instead of serially fetching
# every failed job on the server.
MAX_FAILED_JOBS="${SKYPILOT_FETCH_FAILED_JOB_LOGS_LIMIT:-5}"
PER_LOG_TIMEOUT="${SKYPILOT_FETCH_FAILED_JOB_LOGS_TIMEOUT:-60}"

# First, display the full sky jobs queue -a -u output
echo "=== Job Queue ==="
job_queue_output=$(sky jobs queue -a -u)
echo "$job_queue_output"
echo ""

# Now process the output to find failed jobs
echo "$job_queue_output" | awk '
BEGIN {
    # Skip lines until we find the header
    header_found = 0
}
/^ID[[:space:]]+TASK/ {
    # Found the header line
    header_found = 1
    next
}
header_found && /^[0-9]+/ {
    # This is a job line (starts with a number)
    job_id = $1
    # Find the STATUS field (checking for all statuses in active and finished groups)
    # active: PENDING, RUNNING, RECOVERING, SUBMITTED, STARTING, CANCELLING
    # finished: SUCCEEDED, FAILED, CANCELLED, FAILED_SETUP, FAILED_PRECHECKS, FAILED_NO_RESOURCE, FAILED_CONTROLLER
    status = ""
    for (i = 1; i <= NF; i++) {
        if ($i ~ /^(PENDING|RUNNING|RECOVERING|SUBMITTED|STARTING|CANCELLING|SUCCEEDED|FAILED|CANCELLED|FAILED_SETUP|FAILED_PRECHECKS|FAILED_NO_RESOURCE|FAILED_CONTROLLER)/) {
            status = $i
            break
        }
    }

    # Only process jobs with status starting with FAILED
    if (status ~ /^FAILED/) {
        print job_id " " status
    }
}
' | head -n "$MAX_FAILED_JOBS" | while read -r job_id status; do
    if [ -n "$job_id" ] && [ -n "$status" ]; then
        echo "Fetching job id $job_id controller logs, status: $status"
        timeout "$PER_LOG_TIMEOUT" sky jobs logs --controller "$job_id" || \
            echo "(controller log fetch for job $job_id timed out or failed)"
        echo "---"
    fi
done

# ---- TEMP DIAGNOSTIC (CLAIMGATE) ----------------------------------------
# The jobs this investigation is about never reach FAILED: they sit in
# PENDING with submitted_at NULL, so the loop above fetches nothing for
# them. Everything below is about that state.

echo ""
echo "=== schedule_state distribution (job_info) ==="
python3 - <<'PYDIAG'
import glob, os, sqlite3
# Fixed shallow candidates only: a recursive glob over $HOME can hang for
# minutes here and nothing above bounds it.
cands = [os.path.expanduser('~/.sky/spot_jobs.db')]
cands += glob.glob(os.path.expanduser('~/runtimes/*/.sky/spot_jobs.db'))
seen = set()
for db in cands:
    if db in seen or not os.path.exists(db):
        continue
    seen.add(db)
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        ji = con.execute('select schedule_state, count(*) from job_info '
                         'group by schedule_state').fetchall()
        sp = con.execute('select status, count(*) from spot '
                         'group by status').fetchall()
        stuck = con.execute(
            'select spot_job_id, schedule_state from job_info '
            "where schedule_state = 'INACTIVE' limit 20").fetchall()
        print(f'{db}\n  job_info: {ji}\n  spot: {sp}\n  INACTIVE: {stuck}')
    except Exception as e:  # noqa: BLE001
        print(f'{db}: {type(e).__name__}: {e}')
PYDIAG

echo ""
echo "=== submit-job logs (where scheduler_set_waiting would fail) ==="
for f in ~/sky_logs/managed_jobs/submit-job-*.log; do
    [ -e "$f" ] || continue
    echo "--- $f ---"
    tail -n 40 "$f"
done

echo ""
echo "=== CLAIMGATE trace (all occurrences, not just the log tail) ==="
grep -h 'CLAIMGATE' ~/.sky/api_server/server.log 2>/dev/null | tail -n 120 \
    || echo "(no CLAIMGATE lines in server.log)"
