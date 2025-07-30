#!/bin/bash

# This script installs NVIDIA drivers and CUDA toolkit for GCP's custom kernels
# GCP kernels often require Ubuntu's patched drivers instead of NVIDIA's repository drivers
export DEBIAN_FRONTEND=noninteractive

# CRITICAL FIX: Add error handling to prevent Packer cleanup failures
# The original script failed because Packer couldn't clean up temporary files
set -e
trap 'cleanup' EXIT

cleanup() {
    # Clean up any temporary files to prevent Packer cleanup errors
    rm -f cuda-keyring_1.1-1_all.deb 2>/dev/null || true
    echo "Cleanup completed"
}

echo "=== NVIDIA Driver Installation for GCP Kernel $(uname -r) ==="

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    echo "Detected ARM architecture: $ARCH"
    ARCH_PATH="arm64"
else
    echo "Detected x86_64 architecture"
    ARCH_PATH="x86_64"
fi

# CRITICAL FIX: Install GCC 12 for kernel 6.8+ compatibility
# GCP's kernel 6.8.0-1033-gcp was built with GCC 12, but NVIDIA drivers expected GCC 11
echo "=== Installing GCC 12 for kernel compatibility ==="
sudo apt-get update
sudo apt-get install -y gcc-12 g++-12 build-essential

# CRITICAL FIX: Properly set GCC 12 as default compiler
# This ensures DKMS builds drivers with the same GCC version as the kernel
sudo update-alternatives --remove-all gcc 2>/dev/null || true
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 11 || true
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 12
sudo update-alternatives --set gcc /usr/bin/gcc-12

# Export for current session and DKMS
export CC=/usr/bin/gcc-12
export CXX=/usr/bin/g++-12

echo "Active GCC version: $(gcc --version | head -1)"

# CRITICAL FIX: Use Ubuntu's patched drivers instead of NVIDIA's repository drivers
# GCP's custom kernel 6.8.0-1033-gcp is incompatible with NVIDIA's generic drivers
# Ubuntu patches their drivers specifically for GCP kernels
echo ""
echo "=== Installing Ubuntu's NVIDIA Drivers (patched for GCP kernels) ==="
sudo apt-get install -y ubuntu-drivers-common

# Show available drivers
echo "Available GPU drivers:"
sudo ubuntu-drivers devices

# Install recommended drivers automatically
echo "Installing recommended NVIDIA drivers..."
sudo ubuntu-drivers autoinstall

echo ""
echo "=== Installing CUDA Toolkit from NVIDIA Repository ==="
# Only install CUDA toolkit from NVIDIA - drivers come from Ubuntu
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/${ARCH_PATH}/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# Install CUDA toolkit without drivers
sudo apt-get install -y cuda-toolkit-12-4

echo ""
echo "=== Installing cuDNN ==="
# https://docs.nvidia.com/deeplearning/cudnn/latest/installation/linux.html#installing-on-linux
sudo apt-get install -y libcudnn8 libcudnn8-dev

echo ""
echo "=== Verifying Installation ==="
echo "Kernel: $(uname -r)"
echo "Active GCC: $(gcc --version | head -1)"
echo ""
echo "Installed NVIDIA packages:"
sudo dpkg -l | grep -i nvidia | head -10 || echo "No NVIDIA packages found"

# Set up environment for CUDA
echo ""
echo "=== Setting up CUDA environment ==="
# Add CUDA to system-wide profile
echo 'export PATH="/usr/local/cuda/bin:$PATH"' | sudo tee -a /etc/profile
echo 'export LD_LIBRARY_PATH="/usr/local/cuda/lib64:$LD_LIBRARY_PATH"' | sudo tee -a /etc/profile

echo ""
echo "=== Installation Complete ==="
echo "✅ Ubuntu NVIDIA drivers installed (compatible with GCP kernel)"
echo "✅ CUDA Toolkit 12.4 installed"
echo "✅ cuDNN installed"
echo "✅ Environment configured"
echo ""
echo "Note: System may need reboot for drivers to fully activate"
