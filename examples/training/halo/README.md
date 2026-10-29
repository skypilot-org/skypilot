# Halo: post-train a Hugging Face model on any cloud

[Halo](https://github.com/whitecircle/halo) trains models in their native Hugging Face format with faster kernels and lower peak memory. This example uses SkyPilot to provision one H100, pull Halo's public Hopper image, and fine-tune Qwen3-4B with LoRA.

## Launch

Install SkyPilot and configure at least one cloud:

```bash
pip install "skypilot[aws,gcp,kubernetes]"
sky check
```

From this directory, launch the training job:

```bash
sky launch -c halo halo.yaml
```

The example runs for 20 steps and writes checkpoints to `~/sky_workdir/checkpoints`. To run complete training, remove `max_steps` from `train.yaml` and set `num_train_epochs`.

Useful commands:

```bash
sky logs halo
rsync -Pavz halo:~/sky_workdir/checkpoints ./checkpoints
sky down halo
```

## Change the hardware

The public image used here targets Hopper GPUs. `H100:1` lets SkyPilot select any available H100 provider. Set an infrastructure or region without changing the training command:

```bash
sky launch -c halo halo.yaml --infra aws/us-east-2
```

For B200 or B300, change the accelerator and use `public.ecr.aws/whitecircle/halo:blackwell`.

See the [Halo overview](https://whitecircle.com/halo) and [repository](https://github.com/whitecircle/halo) for other training methods and multi-GPU configs.
