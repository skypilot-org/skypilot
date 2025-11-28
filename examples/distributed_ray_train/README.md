# Distributed Ray Training with SkyPilot

This example shows you how to launch distributed Ray jobs with SkyPilot.

## Setting Up Your Ray Cluster

SkyPilot provides templates for common workloads, such as Ray. The [~/sky_templates/ray/start_cluster](https://github.com/skypilot-org/skypilot/blob/master/sky_templates/ray/start_cluster) template is available on all SkyPilot clusters, and will set up a Ray cluster for your workloads. Simply call it in your task's `run` commands:

```bash
run: |
  ~/sky_templates/ray/start_cluster
```

Under the hood, this script automatically:

- Installs `ray` if not already present
- Starts the head node (rank 0) and workers on all other nodes
- Waits for the head node to be healthy before starting workers
- Ensures all nodes have joined before proceeding

> **Tip**: The script uses SkyPilot's environment variables (`SKYPILOT_NODE_RANK`, `SKYPILOT_NODE_IPS`, `SKYPILOT_NUM_NODES`, `SKYPILOT_NUM_GPUS_PER_NODE`) to coordinate the distributed setup. See [Distributed Multi-Node Jobs](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html) for more details.

## Customizing the Ray Cluster

Customize the Ray cluster by setting environment variables before calling `start_cluster`:

| Variable                          | Default     | Description                                            |
| --------------------------------- | ----------- | ------------------------------------------------------ |
| `RAY_HEAD_PORT`                   | `6379`      | Ray head node port (must differ from SkyPilot's 6380)  |
| `RAY_DASHBOARD_PORT`              | `8265`      | Ray dashboard port (must differ from SkyPilot's 8266)  |
| `RAY_DASHBOARD_HOST`              | `127.0.0.1` | Dashboard host (set to `0.0.0.0` to expose externally) |
| `RAY_DASHBOARD_AGENT_LISTEN_PORT` | `null`      | Optional dashboard agent listen port                   |
| `RAY_HEAD_IP_ADDRESS`             | `null`      | Optional head node IP address override                 |
| `RAY_CMD`                         | `ray`       | Ray command (e.g., `uv run ray`)                       |

## Managing the Ray Cluster

Stop your Ray cluster with:

```bash
~/sky_templates/ray/stop_cluster
```

Do not use `ray stop` directly, as it may interfere with SkyPilot's cluster management.

To restart, simply run `start_cluster` again. The script detects if Ray is already running and skips startup if the cluster is healthy.

## Running the Example

At the bottom of this page you can find a `ray_train.yaml` example. This YAML script provisions 4 L4 GPU nodes, spins up a Ray cluster on the requested infrastructure and then trains a distributed PyTorch model on the Fashion MNIST dataset.

Follow these steps to run the example:

1. Download the Python script that defines the PyTorch training sequence:

```bash
wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/distributed_ray_train/train.py
```

2. Launch your cluster:

```bash
sky launch -c ray-train --num-nodes 4 ray_train.yaml
```

You can use `sky stop` or `sky down` as usual to respectively stop or terminate your provisioned infrastructure.

To stop only the Ray cluster and keep your provisioned infrastructure online, use:

```bash
sky exec ray-train --num-nodes 4 'RAY_CMD=~/sky_workdir/.venv/bin/ray ~/sky_templates/ray/stop_cluster'
```

## Important: Ray Runtime Best Practices

SkyPilot uses Ray internally on port 6380 for cluster management. So when running your own Ray applications, you need to start a separate Ray cluster on a different port (e.g. 6379 is the default) to avoid conflicts. Do not use `ray.init(address="auto")` as it would connect to SkyPilot’s internal cluster, causing resource conflicts.
