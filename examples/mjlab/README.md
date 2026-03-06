# mjlab: GPU-Accelerated Reinforcement Learning with MuJoCo

[mjlab](https://github.com/mujocolab/mjlab) is a GPU-accelerated reinforcement learning framework that combines Isaac Lab's manager-based API with MuJoCo Warp. It provides composable building blocks for RL environment design, with minimal dependencies and direct access to native MuJoCo data structures.

With SkyPilot, you can easily launch mjlab training jobs on cloud GPUs (Lambda Cloud, AWS, GCP, etc.) with automatic provisioning and teardown.

## Prerequisites

Install SkyPilot with your preferred cloud:

```bash
pip install "skypilot[lambda]"    # For Lambda Cloud
# or
pip install "skypilot[aws]"       # For AWS
# or
pip install "skypilot[all]"       # For all clouds
```

Verify your cloud credentials:

```bash
sky check
```

## Training

Launch a training job on a cloud GPU:

```bash
sky launch examples/mjlab/train.yaml \
  --env TASK=Mjlab-Velocity-Flat-Unitree-G1
```

This will:
1. Provision a GPU instance on the cloud.
2. Install mjlab and its dependencies.
3. Run the training job.
4. Automatically terminate the instance after 5 minutes of idle time.

### Choose a different GPU

```bash
sky launch examples/mjlab/train.yaml --gpus H100:1    # 1x H100
sky launch examples/mjlab/train.yaml --gpus A100:8    # 8x A100
sky launch examples/mjlab/train.yaml --gpus A10:1     # 1x A10 (cheaper)
```

Both task files pass `--gpu-ids all`, so multi-GPU instances
automatically use distributed training. When requesting more than one
GPU, consider scaling `MAX_ITERATIONS` down proportionally.

### Override training parameters

Every variable in the YAML `envs` block can be overridden from the command line:

```bash
sky launch examples/mjlab/train.yaml \
  --env TASK=Mjlab-Velocity-Flat-Unitree-Go1 \
  --env NUM_ENVS=8192 \
  --env MAX_ITERATIONS=10000
```

## Using Docker

For a fully reproducible environment, use the Docker-based task:

```bash
sky launch examples/mjlab/train-docker.yaml \
  --env TASK=Mjlab-Velocity-Flat-Unitree-G1
```

This pulls the pre-built Docker image from GHCR instead of installing from source.

## W&B Logging

To log training metrics to Weights & Biases, make sure you have logged in locally:

```bash
pip install wandb
wandb login
```

The SkyPilot task files mount `~/.netrc` onto the remote instance so that `wandb` authenticates automatically.
