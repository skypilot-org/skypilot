# SLURM-like NFS Mounts on Kubernetes with SkyPilot

This example demonstrates how to implement SLURM-like NFS mounts on Kubernetes with SkyPilot, where a user's shared NFS directory is mounted as their home directory. This pattern is commonly used in HPC clusters with SLURM, allowing users to access their data and configurations across compute nodes.

Use this [task.yaml](./task.yaml) as a starting point to mount an NFS directory as the home directory for your SkyPilot tasks and add run/setup/other sections as needed.

## How It Works

This task.yaml file:
1. Mounts a [shared NFS volume](https://docs.skypilot.co/en/latest/reference/kubernetes/kubernetes-setup.html#kubernetes-setup-volumes) at `/mnt/nfs` in the SkyPilot cluster
2. Uses the `NFS_USERNAME` environment variable passed during `sky launch` to determine the home directory to mount in the task. It assumes the NFS follows a `<root>/$NFS_USERNAME/` directory structure for home directories, but this can be changed by modifying the `setup` section.
3. Copies SSH keys and updates `.bashrc` to ensure the correct home directory is used.

## Usage

1. Update the `/path/to/nfs/on/host/machine` in `task.yaml` to the NFS path on your host nodes.
2. Launch the task with SkyPilot and pass the `NFS_USERNAME` environment variable:
```bash
sky launch -c nfs_cluster examples/nfs_slurm/task.yaml --env NFS_USERNAME=<your_nfs_username>
```
3. Your SkyPilot task will now use the NFS directory as its home directory.

## Limitations

* Does not support custom .bashrc files present in the user's shared NFS directory.