# Resilient PyTorch training with torchft on SkyPilot

This example keeps a multi-node PyTorch training job training through node
failures, with no restart from a checkpoint. Dynamic Node Sets replace only the
failed pod, and [torchft](https://github.com/meta-pytorch/torchft) lets the
healthy trainers keep stepping at a smaller world size while the replacement
copies the model and optimizer state from one of them. This minimizes training
downtime during failures.

> [!NOTE]
> This example uses **Frontier Trainer** for Dynamic Node Set recovery, which
> is available on the
> **[SkyPilot Platform](https://docs.skypilot.ai/en/latest/skypilot-platform.html)**.

## Requirements

- A Kubernetes cluster with four available GPUs. The manifests request `H100`;
  change `resources.accelerators` to use another GPU type.
- Pods that can reach each other by pod IP, and can reach GitHub and PyPI:
  every trainer, including a replacement, clones this example and installs
  torchft when it starts.

## Architecture

The workload is a SkyPilot Job Group with two resource shapes:

- **`lighthouse`** runs the torchft lighthouse on CPU. The lighthouse tracks
  which trainers are alive through heartbeats and decides the membership of
  every training step.
- **`trainers`** is the primary task. It runs four GPU replicas, and each
  replica is one torchft *replica group* holding a full copy of the model.

Every step, the replica groups average their gradients through torchft's
fault-tolerant all-reduce and commit the step together. The recovery path has
two halves:

- **SkyPilot** replaces the pod. `job_recovery.strategy: DYNAMIC_NODE_SET`
  recreates only the lost trainer and leaves the others running.
  `min_nodes: 1` keeps the job `RUNNING` as long as one trainer is alive, so
  the job is never torn down while a copy of the weights still exists.
- **torchft** keeps training. The healthy replica groups drop the lost one from
  the next quorum and continue. When the replacement starts, it joins a quorum,
  copies the model and optimizer state from a healthy group over the training
  network, and takes part in the following step.

`train.py` trains a small MLP against a fixed synthetic target so the loss
shows whether a replacement really received the trained weights: its first
logged loss matches the other trainers instead of starting over.

## Run it

From the SkyPilot repository root, launch the Job Group:

```bash
sky jobs launch \
  examples/torchft_resilient_training/torchft-resilient-training.yaml
```

Use the returned job ID to follow the trainers:

```bash
sky jobs logs <job-id> trainers
```

Every tenth step prints the loss and the number of replica groups that took
part in it. A change in membership prints a line such as
`replica groups 4 -> 3`.

## Exercise recovery

Delete one `trainers` pod while the job is training:

```bash
kubectl delete pod <trainers-pod>
```

The other trainers pause for a few seconds until the lighthouse stops hearing
from the lost one, then continue as three replica groups. SkyPilot creates a
replacement pod, which joins as a fourth group at the current step. The job
stays `RUNNING` and `sky jobs queue` shows no recovery.

Delete two or three trainers at once to go further. The remaining trainers
keep training, and each replacement joins as it comes up.

## Configuration

`trainers.num_nodes` sets the number of replica groups, and
`resources.accelerators` the GPU type.

`MIN_REPLICAS` is how many replica groups a step needs before it is committed.
Set it on both tasks. It is a training choice, separate from `min_nodes`: with
fewer groups in a step, a smaller batch stands behind each update. With
`MIN_REPLICAS` above one, training pauses when fewer groups are alive and
resumes from the live weights when replacements join.

`TIMEOUT_SECONDS` bounds how long the trainers wait on an all-reduce with a
group that has just died. Together with the lighthouse's five-second heartbeat
timeout it sets the longest pause after a failure.

The cross-trainer all-reduce uses Gloo over TCP, which reconfigures quickly
after a failure. Pass `--process-group nccl` to `train.py` to use NCCL instead.

## Several GPUs per node

`torchft-multi-gpu-resilient-training.yaml` runs two trainers with two GPUs
each. The GPUs of one trainer form one replica group: `train.py` starts one
process per GPU, the same GPU index of each trainer runs the fault-tolerant
all-reduce, and the GPUs of one trainer then average their gradients among
themselves over NCCL. Inside a pod NCCL connects the GPUs directly, over
NVLink where the node has it. Between pods it uses the pod network.

```bash
sky jobs launch \
  examples/torchft_resilient_training/torchft-multi-gpu-resilient-training.yaml
```

A replica group fails as a unit: when one of its processes dies, the pod exits,
SkyPilot replaces it, and both GPUs of the replacement recover from the other
trainer. Set `NCCL_DEBUG: INFO` to print the transport NCCL picks for every
connection.

In this variant every GPU holds a full copy of the model. To shard a large
model across the GPUs of a replica group with FSDP, see
[torchtitan's torchft integration](https://github.com/pytorch/torchtitan/blob/main/docs/torchft.md).

## Limits

- The lighthouse is a single coordinator. Training pauses while it is
  unavailable.
- The live trainers hold the only copy of the weights. Real training code
  should still write periodic checkpoints to durable storage, for the case
  where every trainer is lost at once.
- torchft is under active development. This example pins `torchft==0.3.1`.

## Acknowledgements

The design follows torchft's
[fault-tolerant DDP example](https://github.com/meta-pytorch/torchft/blob/main/train_ddp.py)
and
[Fault Tolerant Llama: training with 2000 synthetic failures every ~15 seconds and no checkpoints on Crusoe L40S](https://pytorch.org/blog/fault-tolerant-llama-training-with-2000-synthetic-failures-every-15-seconds-and-no-checkpoints-on-crusoe-l40s/).
