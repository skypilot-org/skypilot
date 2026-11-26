#!/usr/bin/env bash
# Prepare a stock nvidia/cuda:13.x image for SkyRL: tools it lacks, and CUDA
# forward compatibility on hosts whose driver is older than the image's CUDA.
set -euo pipefail
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)   # root images (nvidia/cuda) have no sudo
# libnuma: SkyRL workers pin NUMA affinity via ctypes; without the library,
# find_library() returns None and the first libnuma call crashes the worker.
if ! command -v git > /dev/null || ! command -v curl > /dev/null || \
   ! python3 -m pip --version > /dev/null 2>&1 || ! ldconfig -p | grep 'libnuma\.so' > /dev/null; then
  $SUDO apt-get update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git curl python3 python3-pip python3-venv libnuma1 > /dev/null
fi
# SkyRL main needs CUDA 13 (driver >= 580). The image ships the 580 user-mode
# driver in /usr/local/cuda/compat; put it first only on older drivers (e.g.
# 570 on some clusters). On a newer driver it must stay off the search path.
# --id=0, not `| head -1`: with pipefail, head closing the pipe on an 8-GPU
# node kills nvidia-smi with SIGPIPE (exit 141).
DRIVER=$(nvidia-smi --id=0 --query-gpu=driver_version --format=csv,noheader)
if [ "${DRIVER%%.*}" -lt 580 ] && [ -e /usr/local/cuda/compat/libcuda.so.1 ]; then
  echo /usr/local/cuda/compat | $SUDO tee /etc/ld.so.conf.d/00-cuda-compat.conf > /dev/null
  $SUDO ldconfig
  echo "driver $DRIVER: enabled CUDA forward compat ($(ls /usr/local/cuda/compat/libcuda.so.*.*))"
else
  echo "driver $DRIVER: CUDA forward compat not needed"
fi
