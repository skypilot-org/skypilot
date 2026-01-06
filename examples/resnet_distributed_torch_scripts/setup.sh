#!/bin/bash
[ -d pytorch-distributed-resnet ] || git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
cd pytorch-distributed-resnet

uv venv ~/resnet --python 3.7 --seed
source ~/resnet/bin/activate
# SkyPilot's default image on AWS/GCP has CUDA 11.6 (Azure 11.5).
pip install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113

mkdir -p data
mkdir -p saved_models
cd data
wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
tar -xvzf cifar-10-python.tar.gz
