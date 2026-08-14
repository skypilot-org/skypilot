#!/bin/bash

sudo apt update
sudo apt install -y build-essential

echo "Installing GRID driver..."
GRID_DRIVER_VERSION="570.237"
GRID_DRIVER_URL="https://download.microsoft.com/download/5e213ec5-834f-4b0a-87f7-772751353b06/NVIDIA-Linux-x86_64-${GRID_DRIVER_VERSION}-grid-azure.run"
GRID_DRIVER_FILE="NVIDIA-Linux-x86_64-${GRID_DRIVER_VERSION}-grid-azure.run"
GRID_DRIVER_SHA256="1a529dd4d173ba3b36f3c284bb54fce5dd39c9e757856980b8ae0b7798e7764b"

wget -nv "$GRID_DRIVER_URL" -O "$GRID_DRIVER_FILE"
echo "$GRID_DRIVER_SHA256  $GRID_DRIVER_FILE" | sha256sum --check --strict
sudo chmod +x "$GRID_DRIVER_FILE"
sudo sh "$GRID_DRIVER_FILE" --silent --disable-nouveau

echo "Set vGPU Licensing Daemon config..."
sudo cp /etc/nvidia/gridd.conf.template /etc/nvidia/gridd.conf
sudo sed -i '/^FeatureType=0/s/^/# /' /etc/nvidia/gridd.conf
echo "IgnoreSP=FALSE" | sudo tee -a /etc/nvidia/gridd.conf
echo "EnableUI=FALSE" | sudo tee -a /etc/nvidia/gridd.conf

echo "Installing CUDA toolkit..."
CUDA_TOOLKIT_URL="https://developer.download.nvidia.com/compute/cuda/12.2.0/local_installers/cuda_12.2.0_535.54.03_linux.run"
CUDA_TOOLKIT_FILE="cuda_12.2.0_535.54.03_linux.run"
wget -nv $CUDA_TOOLKIT_URL -O $CUDA_TOOLKIT_FILE
sudo sh $CUDA_TOOLKIT_FILE --silent --toolkit --override

# Set environment variables
echo 'export PATH=$PATH:/usr/local/cuda-12.2/bin' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/cuda-12.2/lib64' >> ~/.bashrc
source ~/.bashrc

# Verify installations
rm -f "$GRID_DRIVER_FILE" "$CUDA_TOOLKIT_FILE"
nvidia-smi
