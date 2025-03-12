#!/bin/bash

# This script installs the latest CUDA driver and toolkit version that is compatible with all GPU types.
# For CUDA driver version, choose the latest version that works for ALL GPU types.
#   GCP: https://cloud.google.com/compute/docs/gpus/install-drivers-gpu#minimum-driver
#   AWS: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-nvidia-driver.html
export DEBIAN_FRONTEND=noninteractive

# Detect architecture
ARCH=$(uname -m)

if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    echo "Detected ARM architecture: $ARCH"
    ARCH_PATH="arm64"
else
    echo "Detected x86_64 architecture"
    ARCH_PATH="x86_64"
fi

# Download architecture-specific CUDA keyring package
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/${ARCH_PATH}/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# Make sure CUDA toolkit and driver versions are compatible: https://docs.nvidia.com/deploy/cuda-compatibility/index.html
# Current State: Driver Version 535.183.06 and CUDA Version 12.2
sudo apt-get install -y cuda-drivers-535
sudo apt-get install -y cuda-toolkit-12-4

# Install cuDNN
# https://docs.nvidia.com/deeplearning/cudnn/latest/installation/linux.html#installing-on-linux
sudo apt-get install libcudnn8
sudo apt-get install libcudnn8-dev

# Cleanup
rm cuda-keyring_1.1-1_all.deb
