# Slurm Cluster with NFS Home Directories

This directory contains configuration files to deploy a Slurm cluster on cloud resources with shared NFS home directories.

## File Overview

- `deploy.yaml`: Basic Slurm cluster setup
- `nfs-setup.yaml`: NFS server and client configuration
- `setup-users.yaml`: User creation with home directories on NFS

## Deployment Workflow

The deployment process consists of three sequential steps:

### 1. Deploy the Slurm Cluster

```bash
sky launch -c slurm_cluster examples/slurm_cloud_deploy/deploy.yaml
```

This creates a basic Slurm cluster with:
- A controller node
- Worker node(s)
- Slurm configuration for job submission
- GPU support (if GPUs are requested)

### 2. Set Up NFS

```bash
sky exec slurm_cluster examples/slurm_cloud_deploy/nfs-setup.yaml
```

This adds NFS functionality to the cluster:
- Installs NFS server on the controller node
- Sets up `/mnt` as the shared directory
- Configures all worker nodes as NFS clients
- Creates a template directory for new user homes

### 3. Create Users

```bash
sky exec slurm_cluster examples/slurm_cloud_deploy/setup-users.yaml
```

This creates users with homes on the NFS share:
- Creates the same users on all nodes
- Sets home directories to `/mnt/username`
- Sets up SSH keys for passwordless access
- Ensures consistent UIDs/GIDs across the cluster

## Customizing Users

You can specify which users to create by modifying the `users` environment variable:

```bash
sky exec slurm_cluster examples/slurm_cloud_deploy/setup-users.yaml --env users="carol dave eve"
```

## Using the Cluster

After setup is complete:

1. Users can log in to any node and have access to their home directory
2. Files in `/mnt` are shared across all nodes
3. Submit jobs with `sbatch`, `srun`, etc.
4. Monitor jobs with `squeue`, `sinfo`, etc.

## GPU Support

If your cluster has GPUs:
- A "gpu" partition will be automatically created
- Request GPUs with `--gres=gpu:N` where N is the number of GPUs
- Example: `sbatch --partition=gpu --gres=gpu:1 my_job.sh`

## Security Notes

- For production use, change the default passwords for created users
- Consider using SSH keys instead of passwords for authentication
- For better security, restrict sudo access appropriately

```bash
cd examples/slurm_cloud_deploy
sky launch -c slurm-cluster --workdir . deploy.yaml
```


## Test

```bash
ssh slurm-cluster
sbatch ~/sky_workdir/run.sh
```

