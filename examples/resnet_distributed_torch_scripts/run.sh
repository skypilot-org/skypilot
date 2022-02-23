#!/bin/bash

conda activate resnet
cd pytorch-distributed-resnet
python3 -m torch.distributed.launch --nproc_per_node=1 \
--nnodes=${#SKY_NODE_IPS[@]} --node_rank=${SKY_NODE_RANK} --master_addr=${SKY_NODE_IPS[0]} \
--master_port=8008 resnet_ddp.py --num_epochs 20
