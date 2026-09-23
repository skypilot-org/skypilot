#!/bin/bash
#SBATCH --job-name=test-cluster-no-container
#SBATCH --output=/home/testuser/.sky_provision/slurm-%j.out
#SBATCH --error=/home/testuser/.sky_provision/slurm-%j.out
#SBATCH --nodes=1
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task=2
#SBATCH --mem=8192M

#SBATCH --time=7-00:00:00

# Cleanup function to remove cluster dirs on job termination.
cleanup() {
    saved_exit=$?
    # Prevent the keeper from restarting Skylet during cleanup.
    rm -f "/tmp/test-cluster-no-container/.sky/skylet_start"
    echo "Terminating Skylet..."
    if [ -f "/tmp/test-cluster-no-container/.sky/skylet_pid" ]; then
        kill $(cat "/tmp/test-cluster-no-container/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --overlap --nodes=1 --ntasks-per-node=1 enroot remove -f pyxis_test-cluster-no-container 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --overlap --nodes=1 rm -rf /tmp/test-cluster-no-container /tmp/test-cluster-no-container.exitcode."${SLURM_JOB_ID}"
    # A stop publishes the snapshot manifest before cancellation. Keep the
    # logs referenced by the jobs database that start will restore.
    if [ -f /home/testuser/.sky_snapshots/test-cluster-no-container/manifest.json ]; then
        find /home/testuser/.sky_clusters/test-cluster-no-container -mindepth 1 -maxdepth 1 \
            ! -name sky_logs \
            -exec rm -rf -- {} +
    else
        rm -rf -- /home/testuser/.sky_clusters/test-cluster-no-container
    fi
    exit $saved_exit
}
# Run cleanup on any exit, including container init failures.
trap cleanup EXIT
# Preserve the last submitted task's result for Slurm dependencies.
terminate() {
    if [ -f /tmp/test-cluster-no-container.exitcode."${SLURM_JOB_ID}" ]; then
        task_exit=$(cat /tmp/test-cluster-no-container.exitcode."${SLURM_JOB_ID}") || exit 1
    else
        task_exit=$(export SKY_RUNTIME_DIR=/tmp/test-cluster-no-container
if [ ! -f /tmp/test-cluster-no-container/.sky/jobs.db ]; then
    echo 0
else
$([ -x /usr/bin/env ] && echo /usr/bin/env || type -P env 2>/dev/null || echo /usr/bin/env) -u PYTHONPATH $([ -s ${SKY_RUNTIME_DIR:-$HOME}/.sky/python_path ] && cat ${SKY_RUNTIME_DIR:-$HOME}/.sky/python_path 2> /dev/null || command -v python3) - /tmp/test-cluster-no-container/.sky/jobs.db <<'SKY_JOB_EXIT_CODE'
import pathlib
import sqlite3
import sys

path = pathlib.Path(sys.argv[1])
code = 0
if path.exists():
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute(
            'SELECT status, exit_codes FROM jobs ORDER BY job_id DESC LIMIT 1'
        ).fetchone()
    if row is not None and row[0] != 'SUCCEEDED':
        codes = [int(value) for value in (row[1] or '').split(',') if value]
        code = next((value for value in codes if value != 0), 1)
        if code < 0:
            code = 128 - code
print(code)
SKY_JOB_EXIT_CODE
fi
) || exit 1
    fi
    exit "$task_exit"
}
trap terminate TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p /home/testuser/.sky_clusters/test-cluster-no-container/sky_logs /home/testuser/.sky_clusters/test-cluster-no-container/sky_workdir /home/testuser/.sky_clusters/test-cluster-no-container/.sky
# Create sky runtime directory on each node.
srun --nodes=1 mkdir -p /tmp/test-cluster-no-container/.sky
# Marker file to indicate we're in a Slurm cluster.
srun --nodes=1 touch /tmp/test-cluster-no-container/.sky/.sky_slurm_cluster
# Store proctrack type for task executor to read.
echo 'cgroup' > /home/testuser/.sky_clusters/test-cluster-no-container/.sky_proctrack_type
# Suppress login messages.
touch /home/testuser/.sky_clusters/test-cluster-no-container/.hushlogin

touch /home/testuser/.sky_clusters/test-cluster-no-container/.sky_sbatch_ready
# Host-side keeper step that starts skylet and restarts it if it dies.
SKY_HEAD_NODE=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)
( while true; do srun --overlap --jobid=$SLURM_JOB_ID --nodes=1 --ntasks=1 --job-name=sky-skylet-keeper --nodelist=$SKY_HEAD_NODE bash -c 'while true; do if [ -f /tmp/test-cluster-no-container/.sky/skylet_start ]; then HOME=/home/testuser/.sky_clusters/test-cluster-no-container bash /tmp/test-cluster-no-container/.sky/skylet_start; fi; sleep 5; done'; sleep 5; done ) &
sleep infinity
