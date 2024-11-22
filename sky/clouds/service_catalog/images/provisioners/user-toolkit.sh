#!/bin/bash
# This script installs popular toolkits for users to use in the base environment.

eval "$(~/miniconda3/bin/conda shell.bash hook)"
conda activate base

export PATH=$PATH:$HOME/.local/bin
uv pip install numpy
uv pip install pandas

if [ "$AZURE_GRID_DRIVER" = 1 ]; then
    # Need PyTorch X.X.X+cu121 version to be compatible with older NVIDIA driver (535.161.08 or lower)
    uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
fi
