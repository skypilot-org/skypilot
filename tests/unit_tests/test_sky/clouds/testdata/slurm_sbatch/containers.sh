#!/bin/bash
#SBATCH --job-name=test-cluster
#SBATCH --output=/home/testuser/.sky_provision/slurm-%j.out
#SBATCH --error=/home/testuser/.sky_provision/slurm-%j.out
#SBATCH --nodes=1
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task=4
#SBATCH --mem=16384M
#SBATCH --gres=gpu:A100:2
#SBATCH --time=7-00:00:00

# Cleanup function to remove cluster dirs on job termination.
cleanup() {
    saved_exit=$?
    # Prevent the keeper from restarting Skylet during cleanup.
    rm -f "/tmp/test-cluster/.sky/skylet_start"
    echo "Terminating Skylet..."
    if [ -f "/tmp/test-cluster/.sky/skylet_pid" ]; then
        kill $(cat "/tmp/test-cluster/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --overlap --nodes=1 --ntasks-per-node=1 enroot remove -f pyxis_test-cluster 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --overlap --nodes=1 rm -rf /tmp/test-cluster
    # A stop publishes the snapshot manifest before cancellation. Keep the
    # logs referenced by the jobs database that start will restore.
    if [ -f /home/testuser/.sky_snapshots/test-cluster/manifest.json ]; then
        find /home/testuser/.sky_clusters/test-cluster -mindepth 1 -maxdepth 1 \
            ! -name sky_logs \
            -exec rm -rf -- {} +
    else
        rm -rf -- /home/testuser/.sky_clusters/test-cluster
    fi
    exit $saved_exit
}
# Run cleanup on any exit, including container init failures.
trap cleanup EXIT
# On SIGTERM (job cancellation via scancel), exit 0 so cleanup treats
# it as a graceful shutdown rather than propagating an error code.
trap 'exit 0' TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p /home/testuser/.sky_clusters/test-cluster/sky_logs /home/testuser/.sky_clusters/test-cluster/sky_workdir /home/testuser/.sky_clusters/test-cluster/.sky
# Create sky runtime directory on each node.
srun --nodes=1 mkdir -p /tmp/test-cluster/.sky
# Marker file to indicate we're in a Slurm cluster.
srun --nodes=1 touch /tmp/test-cluster/.sky/.sky_slurm_cluster
# Store proctrack type for task executor to read.
echo 'cgroup' > /home/testuser/.sky_clusters/test-cluster/.sky_proctrack_type
# Suppress login messages.
touch /home/testuser/.sky_clusters/test-cluster/.hushlogin
srun --nodes=1 mkdir -p /tmp/ccache_$(id -u)
CONTAINER_START=$SECONDS
echo "[container] Initializing test-cluster on all nodes"
rm -rf /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done
mkdir -p /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done
CONTAINER_PIDS=()
srun --overlap --job-name=sky-container-keeper --unbuffered --nodes=1 --ntasks-per-node=1 --container-image='nvcr.io#nvidia/pytorch:24.01-py3' --container-name=test-cluster:create --container-mounts="/home/testuser:/home/testuser,/tmp/ccache_$(id -u):/var/cache/ccache,/tmp/test-cluster/.sky:/tmp/test-cluster/.sky" --container-remap-root --no-container-mount-home --container-writable bash -c 'set -e
echo "[container-init] Starting..."
INIT_START=$SECONDS
apt-get update
apt-get install -y ca-certificates rsync curl git wget fuse
echo '"'"'alias sudo=""'"'"' >> ~/.bashrc
echo "[container-init] Packages installed in $((SECONDS - INIT_START))s"
touch /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done/$SLURM_PROCID && sleep infinity' &
CONTAINER_PIDS+=("$!")
while true; do
  for container_pid in "${CONTAINER_PIDS[@]}"; do
    if ! kill -0 "$container_pid" 2>/dev/null; then
      wait "$container_pid"
      container_rc=$?
      if [ "$container_rc" -eq 0 ]; then container_rc=1; fi
      echo "[container] ERROR: Container initialization failed with exit code $container_rc."
      exit "$container_rc"
    fi
  done
  shopt -s nullglob
  ready_markers=(/home/testuser/.sky_clusters/test-cluster/.sky_container_init_done/*)
  num_ready=${#ready_markers[@]}
  if [ "$num_ready" -ge "1" ]; then break; fi
  sleep 1
done
srun --overlap --unbuffered --nodes=1 --ntasks-per-node=1 bash -c 'global_target=pyxis_test-cluster
job_target="pyxis_${SLURM_JOB_ID}_"test-cluster
for ((attempt = 1; attempt <= 30; attempt++)); do
    container_pid=
    while read -r name pid rest; do
        if [ "$name" = "$global_target" ] || [ "$name" = "$job_target" ]; then
            container_pid=$pid
        fi
    done < <(enroot list -f)
    case "$container_pid" in
        '"'"''"'"'|*[!0-9]*) ;;
        *)
            if kill -0 "$container_pid" 2>/dev/null; then
                exit 0
            fi
            ;;
    esac
    sleep 1
done
echo "[container] ERROR: Container is not running as $global_target or $job_target." >&2
exit 1
' || exit 1
echo "[container] Ready in $((SECONDS - CONTAINER_START))s"
printf '%s\n' nvcr.io/nvidia/pytorch:24.01-py3 > /home/testuser/.sky_clusters/test-cluster/.sky_slurm_container
touch /home/testuser/.sky_clusters/test-cluster/.sky_sbatch_ready

# Host-side keeper step that starts skylet and restarts it if it dies.
SKY_HEAD_NODE=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)
( while true; do srun --overlap --jobid=$SLURM_JOB_ID --nodes=1 --ntasks=1 --job-name=sky-skylet-keeper --nodelist=$SKY_HEAD_NODE bash -c 'while true; do if [ -f /tmp/test-cluster/.sky/skylet_start ]; then HOME=/home/testuser/.sky_clusters/test-cluster bash /tmp/test-cluster/.sky/skylet_start; fi; sleep 5; done'; sleep 5; done ) &
wait -n "${CONTAINER_PIDS[@]}"
