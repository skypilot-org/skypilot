# Deploy slurm on VMs

## Deploy

```bash
cd examples/slurm_cloud_deploy
sky launch -c slurm-cluster --workdir . deploy.yaml
```


## Test

```bash
ssh slurm-cluster
sbatch ~/sky_workdir/run.sh
```

