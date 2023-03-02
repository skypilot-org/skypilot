#!/bin/bash

conda activate resnet
conda env list

cd pytorch-distributed-resnet
num_nodes=`echo "$SKYPILOT_NODE_IPS" | wc -l`
master_addr=`echo "$SKYPILOT_NODE_IPS" | head -n1`
echo MASTER_ADDR $master_addr
python -m torch.distributed.launch --nproc_per_node=1 \
--nnodes=$num_nodes --node_rank=${SKYPILOT_NODE_RANK} --master_addr=$master_addr \
--master_port=8008 resnet_ddp.py --num_epochs 20
