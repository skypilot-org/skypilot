#!/bin/bash

conda activate resnet
cd pytorch-distributed-resnet
num_nodes=`echo "$SKY_NODE_IPS" | wc -l`
master_addr=`echo "$SKY_NODE_IPS" | sed -n 1p`
echo MASTER_ADDR $master_addr
python3 -m torch.distributed.launch --nproc_per_node=1 \
--nnodes=$num_nodes --node_rank=${SKY_NODE_RANK} --master_addr=$master_addr \
--master_port=8008 resnet_ddp.py --num_epochs 20
