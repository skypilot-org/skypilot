# Slurm Cluster with NFS Home Directories

This directory contains configuration files to deploy a Slurm cluster on cloud resources with shared NFS home directories. You can deploy multiple SkyPilot clusters that act as different partitions in a single Slurm cluster.

## File Overview

- `deploy.yaml`: Complete Slurm cluster setup with NFS in a single step
- `add-users.yaml`: User creation and management with NFS home directories (idempotent - safe to run multiple times)

## Step 1: Deploy Primary Slurm Controller Cluster

Deploy the primary Slurm cluster with NFS:

```bash
sky launch -c slurm examples/slurm_cloud_deploy/deploy.yaml
```

This creates a complete cluster with:
- A controller node
- Worker node(s) (default: 2 nodes, use `--num-nodes` to change)
- Slurm configuration with partitions named after the cluster (`slurm-cpu` and `slurm-gpu` if GPUs are requested)
- NFS server on the controller
- NFS client configuration on all workers
- Shared `/mnt` directory across all nodes
- GPU support (if GPUs are requested)

## Step 2: Add Additional SkyPilot Clusters as Slurm Partitions

After deploying the primary controller cluster, you can add secondary clusters as Slurm partitions in the same region


```bash
REGION=<region in `sky status -v`>
# Use internal IP of the controller to make sure the secondary cluster can
# connect to the controller, with the internal network.
export CONTROLLER_IP=$(ssh slurm 'hostname -I' | awk '{print $1}')

# Find the SSH key SkyPilot uses for the controller cluster
export SSH_KEY=$(cat $(grep -A3 "Host slurm" ~/.sky/generated/ssh/slurm | grep IdentityFile | head -n 1 | awk '{print $2}'))

# Launch the secondary cluster with the controller IP and existing SSH key
sky launch -c slurm-compute1 --region $REGION examples/slurm_cloud_deploy/deploy.yaml \
  --env CONTROLLER_IP \
  --env CONTROLLER_SSH_PRIVATE_KEY
```

This will:
- Deploy a new SkyPilot cluster named `slurm-compute1`
- Use the existing SSH key to connect to the primary controller
- Connect it to the existing Slurm controller using the provided IP
- Create new partitions named `slurm-compute1-cpu` and `slurm-compute1-gpu` (if GPUs are requested)
- Mount the same NFS shared directory from the controller

You can add as many additional clusters as needed, each with different hardware configurations:

```bash
# Add a GPU-focused partition
sky launch -c slurm-compute2 --region $REGION examples/slurm_cloud_deploy/deploy.yaml \
  --gpus A100:4 \
  --env CONTROLLER_IP=$CONTROLLER_IP \
  --env CONTROLLER_SSH_PRIVATE_KEY=$SSH_KEY
```

## Step 3: Adding Users

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
sky exec slurm examples/slurm_cloud_deploy/add-users.yaml --env USERS="carol dave eve"
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

## Using the Slurm Cluster with Multiple Partitions

After setup is complete:

1. Users can log in to any node and have access to their home directory
2. Files in `/mnt` are shared across all nodes in all partitions
3. Submit jobs to specific partitions with `--partition=<cluster-name>-cpu` or `--partition=<cluster-name>-gpu`
4. Monitor jobs with `squeue`, `sinfo`, etc.

Example job submissions:
```bash
# Submit to the primary controller's CPU partition
sbatch --partition=slurm-cpu my_job.sh

# Submit to a secondary cluster's CPU partition
sbatch --partition=slurm-compute1-cpu my_job.sh

# Use GPUs on a specific partition
sbatch --partition=slurm2-gpu --gres=gpu:4 my_gpu_job.sh

# Submit to any available partition (will use the default partition)
sbatch my_job.sh
```

## GPU Support

If your clusters have GPUs:
- A cluster-specific GPU partition will be automatically created (e.g., `slurm-gpu`)
- Request GPUs with `--gres=gpu:N` where N is the number of GPUs.

Example GPU job submissions:
```bash
# Use GPUs on the primary controller partition
sbatch --partition=slurm-gpu --gres=gpu:4 my_job.sh

# Use GPUs on a secondary partition
sbatch --partition=slurm-compute1-gpu --gres=gpu:2 my_job.sh
```

## Security Notes

- For production use, change the default passwords for created users
- Consider using SSH keys instead of passwords for authentication
- For better security, restrict sudo access appropriately

## Quick Start Example with Multiple Partitions

```bash
# Clone the SkyPilot repository if you haven't already
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot/examples/slurm_cloud_deploy

# Launch the primary controller cluster (with L4 GPUs)
sky launch -c slurm deploy.yaml

# Get the controller IP and SSH key
CONTROLLER_IP=$(sky status --ip slurm)
SSH_KEY_PATH=$(grep -A3 "Host slurm" ~/.sky/ssh_config | grep IdentityFile | awk '{print $2}')

# Add a CPU-focused compute partition
sky launch -c slurm-compute deploy.yaml --cpus 8 \
  --env CONTROLLER_IP=$CONTROLLER_IP \
  --env SSH_KEY_PATH=$SSH_KEY_PATH

# Add a GPU-focused partition (with A100 GPUs)
sky launch -c slurm2 deploy.yaml --accelerators A100:4 \
  --env CONTROLLER_IP=$CONTROLLER_IP \
  --env SSH_KEY_PATH=$SSH_KEY_PATH

# Add users
sky exec slurm add-users.yaml

# Connect to the controller cluster
ssh slurm

# Check the partitions
sinfo

# Create and submit a test job to the controller's CPU partition
cat > ~/test_job.sh << EOF
#!/bin/bash
#SBATCH --job-name=test-cpu
#SBATCH --output=test-cpu-%j.out
#SBATCH --partition=slurm-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
hostname
sleep 10
echo "CPU job completed successfully!"
EOF

# Create and submit a test job to the GPU partition
cat > ~/test_gpu_job.sh << EOF
#!/bin/bash
#SBATCH --job-name=test-gpu
#SBATCH --output=test-gpu-%j.out
#SBATCH --partition=slurm2-gpu
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
hostname
nvidia-smi
sleep 10
echo "GPU job completed successfully!"
EOF

chmod +x ~/test_job.sh ~/test_gpu_job.sh
sbatch ~/test_job.sh
sbatch ~/test_gpu_job.sh
squeue  # Check job status
```

