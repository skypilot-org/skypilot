#!/bin/bash
#SBATCH --job-name=test-cluster-no-container
#SBATCH --output=.sky_provision/slurm-%j.out
#SBATCH --error=.sky_provision/slurm-%j.out
#SBATCH --nodes=1
#SBATCH --time=7-00:00:00
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G


# Cleanup function to remove cluster dirs on job termination.
cleanup() {
    # The Skylet is daemonized, so it is not automatically terminated when
    # the Slurm job is terminated, we need to kill it manually.
    echo "Terminating Skylet..."
    if [ -f "/tmp/test-cluster-no-container/.sky/skylet_pid" ]; then
        kill $(cat "/tmp/test-cluster-no-container/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --nodes=1 --ntasks-per-node=1 enroot remove -f pyxis_test-cluster-no-container 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --nodes=1 rm -rf /tmp/test-cluster-no-container
    rm -rf /home/testuser/.sky_clusters/test-cluster-no-container
    exit 0
}
trap cleanup TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p /home/testuser/.sky_clusters/test-cluster-no-container/sky_logs /home/testuser/.sky_clusters/test-cluster-no-container/sky_workdir /home/testuser/.sky_clusters/test-cluster-no-container/.sky
# Create sky runtime directory on each node.
srun --nodes=1 mkdir -p /tmp/test-cluster-no-container
# Marker file to indicate we're in a Slurm cluster.
touch /home/testuser/.sky_clusters/test-cluster-no-container/.sky_slurm_cluster
# Suppress login messages.
touch /home/testuser/.sky_clusters/test-cluster-no-container/.hushlogin

touch /home/testuser/.sky_clusters/test-cluster-no-container/.sky_sbatch_ready
sleep infinity
