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

# Install GCC 12 and set as default compiler
# This is required because newer Ubuntu 22.04 kernels (6.5.0+ and 6.8.0+) are built with GCC 12,
# but Ubuntu 22.04 LTS defaults to GCC 11. Without GCC 12, NVIDIA DKMS driver compilation
# will fail with error: "unrecognized command-line option '-ftrivial-auto-var-init=zero'"
# This flag was introduced in GCC 12 and is not recognized by GCC 11.
echo "Installing GCC 12 to match kernel compiler version..."
sudo apt-get update
sudo apt-get install -y gcc-12 g++-12
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 100
sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-12 100

# Download architecture-specific CUDA keyring package
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/${ARCH_PATH}/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# Make sure CUDA toolkit and driver versions are compatible: https://docs.nvidia.com/deploy/cuda-compatibility/index.html
# Current State: Driver Version 535.183.06 and CUDA Version 12.2
if [ "$ARCH_PATH" = "arm64" ]; then
    sudo apt install -y nvidia-driver-535
    sudo apt install -y nvidia-modprobe
    sudo apt-get install -y cuda-toolkit-12-4
    sudo apt-get install libcudnn9-cuda-12
    sudo apt-get install libcudnn9-dev-cuda-12
else
    sudo apt-get install -y cuda-drivers-535
    sudo apt-get install -y cuda-toolkit-12-4
    # Install cuDNN
    # https://docs.nvidia.com/deeplearning/cudnn/latest/installation/linux.html#installing-on-linux
    sudo apt-get install libcudnn8
    sudo apt-get install libcudnn8-dev
fi

# Cleanup
rm cuda-keyring_1.1-1_all.deb
