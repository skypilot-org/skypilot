#!/bin/bash
[ -d pytorch-distributed-resnet ] || git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
cd pytorch-distributed-resnet

conda activate resnet
if [ $? -eq 0 ]; then
    echo "conda env exists"
else
    echo "conda env does not exist"
    conda create -n resnet python=3.7 -y
    conda activate resnet
fi
# SkyPilot's default image on AWS/GCP has CUDA 11.6 (Azure 11.5).
pip install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113

mkdir -p data
mkdir -p saved_models
cd data
# CIFAR-10 is served from a SkyPilot-maintained GCS mirror because the
# upstream host (cs.toronto.edu) is often slow or unavailable. The md5
# check guards against truncated downloads (a truncated file can leave
# wget exiting 0, which would otherwise skip the fallback and fail in tar).
CIFAR_MD5=c58f30108f718f92721af3b95e74349a
cifar_ok() { echo "${CIFAR_MD5}  cifar-10-python.tar.gz" | md5sum -c --status; }
timeout 90s wget -c --quiet --timeout=30 --tries=2 https://storage.googleapis.com/skypilot-example-data/datasets/cifar-10-python.tar.gz || true
if ! cifar_ok; then
    rm -f cifar-10-python.tar.gz
    timeout 120s wget --quiet --timeout=30 --tries=2 https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
    cifar_ok
fi
tar -xzf cifar-10-python.tar.gz
