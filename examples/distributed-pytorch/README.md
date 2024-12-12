# Distributed Training with PyTorch

This example demonstrates how to run distributed training with PyTorch using SkyPilot.

**The example is based on [PyTorch's official minGPT example](https://github.com/pytorch/examples/tree/main/distributed/minGPT-ddp)**

## Using distributed training


The following command spawn 2 nodes with 1 L4 GPU each. 

`sky launch -c train.yaml`

In the [train.yaml](./train.yaml), we use `torchrun` to launch the training and set the arguments for distributed training using environment variables provided by SkyPilot.

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



## Using `rdvz`
