# Distributed Training with PyTorch

This example demonstrates how to run distributed training with PyTorch using SkyPilot.

**The example is based on [PyTorch's official minGPT example](https://github.com/pytorch/examples/tree/main/distributed/minGPT-ddp)**.


## Overview

There are two ways to run distributed training with PyTorch:

1. Using normal `torchrun`
2. Using `rdvz` backend

The main difference between the two for fixed-size distributed training is that `rdvz` backend automatically handles the rank for each node, while `torchrun` requires the rank to be set manually.

SkyPilot offers convenient built-in environment variables to help you start distributed training easily.

### Using normal `torchrun`


The following command will spawn 2 nodes with 2 L4 GPU each:
```
sky launch -c train train.yaml
```

In [train.yaml](https://github.com/skypilot-org/skypilot/blob/master/examples/distributed-pytorch/train.yaml), we use `torchrun` to launch the training and set the arguments for distributed training using [environment variables](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html#skypilot-environment-variables) provided by SkyPilot.

```yaml
run: |
    cd examples/mingpt
    MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
    torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --master_addr=$MASTER_ADDR \
    --master_port=8008 \
    --node_rank=${SKYPILOT_NODE_RANK} \
    main.py
```

```yaml
run: |
   cd examples/mingpt
   MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
   torchrun \
   --nnodes=$SKYPILOT_NUM_NODES \
   --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
   --master_addr=$MASTER_ADDR \
   --master_port=8008 \
   --node_rank=${SKYPILOT_NODE_RANK} \
   main.py
```

Or, run the equivalent code using python SDK:
```bash
python sdk_scripts/train.py
```
```
python sdk_scripts/train.py
```


### Using `rdzv` backend

`rdzv` is an alternative backend for distributed training:

```
sky launch -c train-rdzv train-rdzv.yaml
```

In [train-rdzv.yaml](https://github.com/skypilot-org/skypilot/blob/master/examples/distributed-pytorch/train-rdzv.yaml), we use `torchrun` to launch the training and set the arguments for distributed training using [environment variables](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html#skypilot-environment-variables) provided by SkyPilot.

```yaml
run: |
    cd examples/mingpt
    MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
    echo "Starting distributed training, head node: $MASTER_ADDR"

    torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --rdzv_backend=c10d \
    --rdzv_endpoint=$MASTER_ADDR:29500 \
    --rdzv_id $SKYPILOT_TASK_ID \
    main.py
```

To run the equivalent code using python SDK, run
```
python sdk_scripts/train_rdzv.py
```

## Scale up

If you would like to scale up the training, you can simply change the resources requirement, and SkyPilot's built-in environment variables will be set automatically.

For example, the following command will spawn 4 nodes with 4 L4 GPUs each.

```
sky launch -c train train.yaml --num-nodes 4 --gpus L4:4 --cpus 8+
```

We increase the `--cpus` to 8+ as well to avoid the performance to be bottlenecked by the CPU.

