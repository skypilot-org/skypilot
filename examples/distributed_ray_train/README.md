# Distributed Ray Training with SkyPilot

This example shows how to launch distributed Ray jobs with SkyPilot.

## Important: Ray Runtime Best Practices

SkyPilot uses Ray internally on port 6380 for cluster management. So when running your own Ray applications, you need to start a separate Ray
cluster on a different port (e.g. 6379 is the default) to avoid conflicts. Do not use `ray.init(address="auto")` as it would connect to
SkyPilot’s internal cluster, causing resource conflicts.

## Setting Up Your Ray Cluster

SkyPilot provides a [start_cluster.sh](https://github.com/skypilot-org/skypilot/blob/master/sky_templates/ray/start_cluster.sh) script that sets up a Ray cluster for your workloads. Simply call it in your task's `run` commands:

```bash
~/sky_templates/ray/start_cluster.sh
```

Under the hood, this script automatically:
- Installs `ray` if not already present
- Starts the head node (rank 0) and workers on all other nodes
- Waits for the head node to be healthy before starting workers
- Ensures all nodes have joined before proceeding

> **Tip**: The script uses SkyPilot's environment variables (`SKYPILOT_NODE_RANK`, `SKYPILOT_NODE_IPS`, `SKYPILOT_NUM_NODES`, `SKYPILOT_NUM_GPUS_PER_NODE`) to coordinate the distributed setup. See [Distributed Multi-Node Jobs](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html) for more details.

## Customizing the Ray Cluster

Customize the Ray cluster by setting environment variables before calling `start_cluster.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `RAY_HEAD_PORT` | `6379` | Ray head node port (must differ from SkyPilot's 6380) |
| `RAY_DASHBOARD_PORT` | `8265` | Ray dashboard port (must differ from SkyPilot's 8266) |
| `RAY_DASHBOARD_HOST` | `127.0.0.1` | Dashboard host (set to `0.0.0.0` to expose externally) |
| `RAY_DASHBOARD_AGENT_LISTEN_PORT` | `null` | Optional dashboard agent listen port |
| `RAY_HEAD_IP_ADDRESS` | `null` | Optional head node IP address override |
| `RAY_CMD_PREFIX` | `null` | Optional command prefix (e.g., `uv run`) |

## Managing the Ray Cluster

Stop your Ray cluster with:

```bash
~/sky_templates/ray/stop_cluster.sh
```

Do not use `ray stop` directly, as it may interfere with SkyPilot's cluster management.

To restart, simply run `start_cluster.sh` again. The script detects if Ray is already running and skips startup if the cluster is healthy.

## Running the Example

```bash
# Download the training script
wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/distributed_ray_train/train.py

# Launch on a cluster
sky launch -c ray-train ray_train.yaml
```
