# Distributed Ray Training with SkyPilot

This example shows how to launch distributed Ray jobs with SkyPilot.

## Important: Ray Runtime Best Practices

SkyPilot uses Ray internally on port 6380 for cluster management, so always start your own Ray cluster on a different port (e.g. 6379 is the default) when running Ray workloads on SkyPilot. Don't use `ray.init(address="auto")` as it would connect to SkyPilot's internal cluster, causing resource conflicts.

The example in `ray_train.yaml` demonstrates the correct approach:
1. Start Ray head node on rank 0
2. Start Ray workers on other ranks
3. Connect to your own Ray cluster, not SkyPilot's internal one

## Running the Example

```bash
# Download the training script
wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/distributed_ray_train/train.py

# Launch on a cluster
sky launch -c ray-train ray_train.yaml

```
