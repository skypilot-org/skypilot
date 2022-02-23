#!/bin/bash
[ -d pytorch-distributed-resnet ] || git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
cd pytorch-distributed-resnet

conda activate resnet
if [ $? -eq 0 ]; then
    echo "conda env exists"
else
    echo "conda env does not exist"
    conda create -n resnet python=3.6 -y
    conda activate resnet
    pip3 install -r requirements.txt
fi

mkdir -p data
mkdir -p saved_models
cd data
wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
tar -xvzf cifar-10-python.tar.gz
