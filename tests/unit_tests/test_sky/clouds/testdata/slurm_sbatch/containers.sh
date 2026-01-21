#!/bin/bash
#SBATCH --job-name=test-cluster
#SBATCH --output=.sky_provision/slurm-%j.out
#SBATCH --error=.sky_provision/slurm-%j.out
#SBATCH --nodes=1
#SBATCH --time=7-00:00:00
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --gres=gpu:A100:2

# Cleanup function to remove cluster dirs on job termination.
cleanup() {
    # The Skylet is daemonized, so it is not automatically terminated when
    # the Slurm job is terminated, we need to kill it manually.
    echo "Terminating Skylet..."
    if [ -f "/tmp/test-cluster/.sky/skylet_pid" ]; then
        kill $(cat "/tmp/test-cluster/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --nodes=1 --ntasks-per-node=1 enroot remove -f pyxis_test-cluster 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --nodes=1 rm -rf /tmp/test-cluster
    rm -rf /home/testuser/.sky_clusters/test-cluster
    exit 0
}
trap cleanup TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p /home/testuser/.sky_clusters/test-cluster/sky_logs /home/testuser/.sky_clusters/test-cluster/sky_workdir /home/testuser/.sky_clusters/test-cluster/.sky
# Create sky runtime directory on each node.
srun --nodes=1 mkdir -p /tmp/test-cluster
# Marker file to indicate we're in a Slurm cluster.
touch /home/testuser/.sky_clusters/test-cluster/.sky_slurm_cluster
# Suppress login messages.
touch /home/testuser/.sky_clusters/test-cluster/.hushlogin
echo "Initializing container test-cluster on all nodes..."
rm -rf /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done
mkdir -p /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done
srun --overlap --label --unbuffered --nodes=1 --ntasks-per-node=1 --container-image='nvcr.io#nvidia/pytorch:24.01-py3' --container-name=test-cluster:create --container-mounts="/home/testuser:/home/testuser" --container-remap-root --no-container-mount-home --container-writable bash -c 'set -e
apt-get update
apt-get install -y ca-certificates rsync curl git wget fuse
cat > /usr/local/bin/sudo << '"'"'SUDO_EOF'"'"'
#!/bin/bash
exec "$@"
SUDO_EOF
chmod +x /usr/local/bin/sudo
touch /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done/$SLURM_PROCID && sleep infinity' &
while true; do
  num_ready=$(ls -1 /home/testuser/.sky_clusters/test-cluster/.sky_container_init_done 2>/dev/null | wc -l)
  if [ "$num_ready" -ge "1" ]; then
    break
  fi
  sleep 1
done
touch /home/testuser/.sky_clusters/test-cluster/.sky_slurm_container /home/testuser/.sky_clusters/test-cluster/.sky_sbatch_ready

wait
