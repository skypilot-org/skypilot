# Slurm Cluster with NFS Home Directories

This directory contains configuration files to deploy a Slurm cluster on cloud resources with shared NFS home directories.

## File Overview

- `deploy.yaml`: Complete Slurm cluster setup with NFS in a single step
- `add-users.yaml`: User creation and management with NFS home directories (idempotent - safe to run multiple times)

## Step 1: Slurm and NFS Deployment

Deploy the Slurm cluster with NFS in a single step:

```bash
sky launch -c slurm examples/slurm_cloud_deploy/deploy.yaml
```

This creates a complete cluster with:
- A controller node
- Worker node(s) (default: 2 nodes, use `--num-nodes` to change)
- Slurm configuration for job submission with resource sharing
- NFS server on the controller
- NFS client configuration on all workers
- Shared `/mnt` directory across all nodes
- GPU support (if GPUs are requested)

## Step 2: Adding Users

After the cluster is deployed with Slurm and NFS, add users with:

```bash
sky exec slurm examples/slurm_cloud_deploy/add-users.yaml
```

This script is fully idempotent, so it's safe to run multiple times:
- For new users: creates accounts with NFS home directories
- For existing users: verifies and updates settings if needed
- Provides a summary of which users were newly created vs updated

The script configures users with:
- Home directories on the NFS share (`/mnt/username`)
- Same user accounts across all nodes with consistent UIDs/GIDs
- SSH keys for passwordless access
- Sudo access (can be removed if needed)
- Default password = username (change for production use)

You can add more users at any time by running the `add-users.yaml` script again with different usernames.

### Customizing Users

You can specify which users to create or update by modifying the `USERS` environment variable:

```bash
sky launch --fast -c slurm examples/slurm_cloud_deploy/add-users.yaml --env USERS="carol dave eve"
```

By default, the script will create users "alice" and "bob" if no USERS variable is specified. You can call the script multiple times to add more users.

**Accessing the cluster as a user:**

1. Download the ssh key for a specific user from the cluster:
```bash
SLURM_USERNAME=alice
rsync -avz --rsync-path="sudo rsync" slurm:/mnt/$SLURM_USERNAME/.ssh/id_rsa ~/.ssh/slurm-$SLURM_USERNAME
```
2. SSH into the cluster as the user:
```bash
ssh -i ~/.ssh/slurm-$SLURM_USERNAME $SLURM_USERNAME@slurm
```

## Using the Cluster

After setup is complete:

1. Users can log in to any node and have access to their home directory
2. Files in `/mnt` are shared across all nodes
3. Submit jobs with `sbatch`, `srun`, etc.
4. Monitor jobs with `squeue`, `sinfo`, etc.

## GPU Support

If your cluster has GPUs:
- A "gpu" partition will be automatically created.
- Request GPUs with `--gres=gpu:N` where N is the number of GPUs.

Example job submissions:
```bash
# Use all GPUs on a single node
sbatch --partition=gpu --gres=gpu:4 my_job.sh

# Use 2 GPUs per node across both nodes (4 GPUs total)
sbatch --partition=gpu --nodes=2 --ntasks-per-node=1 --gres=gpu:2 my_job.sh

# Use all GPUs across all nodes (8 GPUs total)
sbatch --partition=gpu --nodes=2 --ntasks-per-node=1 --gres=gpu:4 my_job.sh

# For jobs that don't need GPUs, use the cpu partition
sbatch --partition=cpu my_cpu_job.sh
```

## Security Notes

- For production use, change the default passwords for created users
- Consider using SSH keys instead of passwords for authentication
- For better security, restrict sudo access appropriately

## Quick Start Example

```bash
# Clone the SkyPilot repository if you haven't already
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot/examples/slurm_cloud_deploy

# Launch the cluster with 2 nodes by default
sky launch -c slurm --workdir . deploy.yaml

# Add default users (alice and bob)
sky launch --fast -c slurm add-users.yaml

# Add more users later (won't affect existing users)
sky launch --fast -c slurm add-users.yaml --env USERS="dave eve"

# Connect to the cluster
ssh slurm

# Create and submit a test job
cat > ~/test_job.sh << EOF
#!/bin/bash
#SBATCH --job-name=test
#SBATCH --output=test-%j.out
#SBATCH --nodes=1
#SBATCH --ntasks=1
hostname
sleep 10
echo "Job completed successfully!"
EOF

chmod +x ~/test_job.sh
sbatch ~/test_job.sh
squeue  # Check job status
```

