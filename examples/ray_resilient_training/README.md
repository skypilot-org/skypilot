# Resilient Ray training on SkyPilot

This example keeps a multi-node Ray training job alive through head and worker
failures. Dynamic Node Sets provide fast failover to warm standby capacity:
only the failed pod is replaced, healthy workers keep running, and training
resumes without a full-cluster restart. This minimizes training downtime
during failures.

> [!NOTE]
> This example uses **Frontier Trainer** for Dynamic Node Set recovery, which
> is available on the **[SkyPilot Platform](https://docs.skypilot.ai/en/latest/skypilot-platform.html)**.

## Requirements

- A Kubernetes cluster with two available GPUs.
- A CSI storage class that supports `ReadWriteOncePod` volumes and can reattach
  a volume to a replacement head pod. Set `storage_class_name` in
  `gcs-volume.yaml` if the cluster's default class is unsuitable.

## Architecture

The workload is a SkyPilot Job Group with two resource shapes:

- **`ray-head`** is the primary task. It runs the Ray GCS and driver on CPU,
  with zero logical Ray CPUs and GPUs so actors are scheduled only on workers.
- **`ray-workers`** runs two GPU replicas. Each replica joins the head and
  contributes one GPU to the Ray cluster.

The driver creates one named, detached Ray actor per GPU. Actors use
`max_restarts=-1` and `max_task_retries=-1`, so Ray reconstructs the actor
whose worker disappeared and retries its synthetic rollout. The other actor
continues on its original worker. Each rollout runs a fixed number of GPU
batches and waits for every batch to finish, making retried computation
deterministic and preventing asynchronous GPU work from accumulating past the
configured batch count.

The head stores GCS metadata in Ray's embedded RocksDB backend on a persistent
SkyPilot volume. It also checkpoints the last completed application step to
the same volume. A replacement head reopens that state, reattaches to the
detached actors, and resumes the driver. When resuming from a completed step,
the driver requires every named actor to exist so failed reattachment cannot
silently create a new training topology.

## Run it

From the SkyPilot repository root, create the GCS volume once:

```bash
sky volumes apply examples/ray_resilient_training/gcs-volume.yaml
```

Launch the Job Group:

```bash
sky jobs launch examples/ray_resilient_training/ray-resilient-training.yaml
```

Use the returned job ID to follow the driver:

```bash
sky jobs logs <job-id> ray-head
```

Each completed step prints both actors' worker hostname, process ID, Ray node
ID, and random `incarnation` value.

## Exercise recovery

Delete one `ray-workers` pod while a step is running:

```bash
kubectl delete pod <ray-worker-pod>
```

SkyPilot creates a replacement worker, its raylet joins the existing cluster,
and Ray reconstructs the lost actor. The next completed step shows a new
`incarnation` for that actor and the original value for the healthy actor.

Delete the `ray-head` pod to exercise GCS recovery:

```bash
kubectl delete pod <ray-head-pod>
```

The replacement head mounts the same volume and restarts Ray against the
job-specific RocksDB directory. Worker raylets wait up to 600 seconds for the
GCS endpoint to return, and the driver resumes after its last completed step.

## Configuration

`NUM_GPU_WORKERS` on `ray-head` must equal `ray-workers.num_nodes`. The example
uses two `L4:1` workers. Change both values together to scale the worker fleet,
and change `resources.accelerators` to use another GPU type.

The published Ray GPU image does not include PyTorch, so the worker `setup`
installs it. For shorter replacement times, build an image containing both Ray
and PyTorch and use it for `ray-workers`.

## NVLink domain-aware placement

For GB300 NVL72 clusters,
`ray-nvl72-resilient-training.yaml` extends the same recovery design across two
NVLink domains. It runs 32 Ray actors on 32 four-GPU workers and creates two
detached placement groups with 16 bundles each. `STRICT_PACK` on
`ray.io/gpu-domain` keeps each group in one domain, while `PACK` on
`ray.io/node-id` gives Ray flexibility within that domain.

An NVL72 domain has 18 four-GPU nodes. Using 16 workers per placement group
leaves up to two nodes in each domain as standby capacity. When one worker is
replaced, Ray preserves the placement group's domain assignment and
reconstructs the lost actor. Healthy actors continue their current rollouts.

Before launching this variant:

- Configure topology-aware scheduling to place the workers as two 16-node
  slices keyed by the `nvidia.com/gpu.clique` Kubernetes Node label.
- Confirm that every GPU node has a non-empty `nvidia.com/gpu.clique` label.
  NVIDIA GPU Feature Discovery normally creates this label.
- Create the same `ray-gcs-rocksdb` volume used by the basic example.

The worker task gets its Kubernetes Node name through the Downward API, reads
the node's clique label through the Kubernetes API, and registers the value as
the Ray node label `ray.io/gpu-domain`.

Launch the variant from the repository root:

```bash
sky jobs launch \
  examples/ray_resilient_training/ray-nvl72-resilient-training.yaml
```

The program reserves all 128 Ray GPU resources but runs a CPU-only synthetic
rollout. Replace `RolloutWorker.rollout()` in `train_nvl72.py` with the
application's GPU work. Delete one worker pod during a rollout to exercise
in-domain replacement; the other actors continue on their existing workers.

Ray marks topology strategy scheduling as alpha. See Ray's
[topology strategy documentation](https://docs.ray.io/en/latest/ray-core/scheduling/placement-group.html#alpha-topology-strategy-scheduling)
for the current API and fault-tolerance semantics.

Ray marks the embedded RocksDB backend as alpha. RocksDB stores Ray cluster
metadata, not model weights or mutable actor state. Real training code should
checkpoint application state to its own durable volume or object store and
make retried work idempotent.

See Ray's [GCS fault-tolerance documentation](https://docs.ray.io/en/latest/ray-core/fault_tolerance/gcs.html#fault-tolerance-gcs-rocksdb)
for details about the embedded RocksDB backend.
