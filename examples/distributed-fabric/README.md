# Distributed Training with Lightning Fabric

This example demonstrates how to run distributed language model training using [Lightning Fabric](https://lightning.ai/docs/fabric/stable/) and SkyPilot.

> **The example is based on the official [Lightning Fabric language model](https://github.com/Lightning-AI/pytorch-lightning/tree/master/examples/fabric/language_model) training script.**

## Overview

This guide shows how to launch distributed training with Lightning Fabric on the cloud, leveraging SkyPilot's built-in distributed environment management.

The following command will launch a distributed training job using 2 nodes, each with 2 L4 GPU:

```bash
sky launch -c fabric-lm train.yaml
```

In train.yaml, we use fabric run to launch the Lightning Fabric training script, leveraging SkyPilot environment variables for seamless distributed setup:

```yaml
run: |
    cd pytorch-lightning/examples/fabric/language_model
    MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
    fabric run \
      --accelerator cuda \
      --devices auto \
      --num-nodes $SKYPILOT_NUM_NODES \
      --node-rank $SKYPILOT_NODE_RANK \
      train.py
```



## Scaling Up
To scale up the training, simply increase your resource requirements—SkyPilot and Lightning Fabric will handle the rest:

For example, to launch 4 nodes with 4 L4 GPUs each:

```bash
sky launch -c fabric-lm fabric-lm.yaml --num-nodes 4 --gpus L4:4 --cpus 8+
```

Increasing --cpus helps prevent bottlenecks during multi-GPU training.

