#!/bin/bash

# Script to fetch logs for all failed jobs in the queue
# Usage: ./fetch_failed_job_logs.sh

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
' | while read -r job_id status; do
    if [ -n "$job_id" ] && [ -n "$status" ]; then
        echo "Fetching job id $job_id controller logs, status: $status"
        sky jobs logs --controller "$job_id"
        echo "---"
    fi
done
