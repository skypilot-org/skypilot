#!/bin/bash

# This script installs the latest CUDA driver and toolkit version that is compatible with all GPU types.
# For CUDA driver version, choose the latest version that works for ALL GPU types.
#   GCP: https://cloud.google.com/compute/docs/gpus/install-drivers-gpu#minimum-driver
#   AWS: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-nvidia-driver.html
#
# We install the open-kernel-module flavor of the driver. NVIDIA Blackwell
# data-center GPUs (B200, RTX PRO 6000 Blackwell Server) require the open
# kernel module; the proprietary kernel module is unsupported on these cards.
# The open module also works on every previously supported GPU (Turing and
# later), so a single image continues to cover all GPU types.
#
# Driver / toolkit pinning: NVIDIA 580 branch (open) + CUDA 13.0.

set -euxo pipefail
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

# Install gcc-12 and make it the default. The DKMS rebuild of the NVIDIA
# kernel module on GCP's 6.8.x kernels requires gcc >= 12 because the kernel
# Makefile passes `-ftrivial-auto-var-init=zero`, which gcc-11 (Ubuntu 22.04
# default) does not support. Without this step the module silently fails to
# build and the resulting image has no working driver. Kernel headers must
# also be present so DKMS can build against the running kernel.
sudo apt-get install -y gcc-12 g++-12 "linux-headers-$(uname -r)"
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 60 \
    --slave /usr/bin/g++ g++ /usr/bin/g++-12

# Download architecture-specific CUDA keyring package
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/${ARCH_PATH}/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# Make sure CUDA toolkit and driver versions are compatible:
# https://docs.nvidia.com/deploy/cuda-compatibility/index.html
# Current State: Driver 580.x (open) + CUDA 13.0.
sudo apt-get install -y nvidia-driver-580-open
sudo apt-get install -y cuda-toolkit-13-0
# Install cuDNN
# https://docs.nvidia.com/deeplearning/cudnn/latest/installation/linux.html#installing-on-linux
sudo apt-get install -y libcudnn9-cuda-13
sudo apt-get install -y libcudnn9-dev-cuda-13

if [ "$ARCH_PATH" = "arm64" ]; then
    sudo apt-get install -y nvidia-modprobe
fi

# Cleanup
rm cuda-keyring_1.1-1_all.deb
