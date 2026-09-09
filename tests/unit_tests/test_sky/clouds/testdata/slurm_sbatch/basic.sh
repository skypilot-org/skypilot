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
    # Unmount the storage MOUNT paths recorded by the mount keeper, so the
    # FUSE daemons killed with this allocation do not leave disconnected
    # mount points on the nodes. Runs through the container for container
    # clusters: that is the namespace the mounts live in. The primary
    # unmount happens in _cleanup_slurm_allocation while the allocation is
    # still quiet; this is the backstop for paths that skip it, so it runs
    # before the slower enroot cleanup.
    if [ -f /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/paths ]; then
        echo "Unmounting storage mounts..."
        srun --overlap --nodes=1 --ntasks-per-node=1 bash -c 'while read -r mount_path; do
    [ -n "$mount_path" ] || continue
    mount_path="${mount_path%/}"
    fusermount -uz "$mount_path" 2>/dev/null || fusermount3 -uz "$mount_path" 2>/dev/null || true
done < /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/paths
' || true
        echo "Unmounted storage mounts."
    fi
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
    srun --overlap --nodes=1 rm -rf /tmp/test-cluster-no-container
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
# On SIGTERM (job cancellation via scancel), exit 0 so cleanup treats
# it as a graceful shutdown rather than propagating an error code.
trap 'exit 0' TERM

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
# Storage-mount keeper: launches mount specs as persistent steps.
( mkdir -p /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts && touch /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/keeper_ready
  while true; do
    for spec in /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/spec-*.sh; do
      [ -e "$spec" ] || continue
      gen=${spec##*/spec-}; gen=${gen%.sh}
      [ -e /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/started-$gen ] && continue
      ( srun --overlap --job-name=sky-storage-mount-keeper --unbuffered --nodes=1 --ntasks-per-node=1 --kill-on-bad-exit=0 bash -c 'spec=$1
gen=${spec##*/spec-}
gen=${gen%.sh}
cd /home/testuser/.sky_clusters/test-cluster-no-container && export HOME="$PWD"
export SKY_RUNTIME_DIR="/tmp/test-cluster-no-container"
([ -f ~/.bashrc ] && source ~/.bashrc || true)
mkdir -p /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/logs
if bash "$spec" > /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/logs/storage_mounts-$SLURMD_NODENAME-$gen.log 2>&1; then
  touch /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/done-$gen/$SLURMD_NODENAME
  exec sleep infinity
else
  echo $? > /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/failed-$gen/$SLURMD_NODENAME
  exit 1
fi
' bash "$spec"
        mount_srun_rc=$?
        if [ "$mount_srun_rc" -ne 0 ]; then
          mkdir -p /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/failed-$gen
          echo "$mount_srun_rc" > /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/failed-$gen/_step
        fi ) &
      echo $! > /home/testuser/.sky_clusters/test-cluster-no-container/.sky/storage_mounts/started-$gen
    done
    sleep 2
  done ) &
sleep infinity
