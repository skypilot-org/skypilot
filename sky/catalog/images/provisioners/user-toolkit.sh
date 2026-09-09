#!/bin/bash
# This script installs popular toolkits for users to use in the base environment.

eval "$(~/miniconda3/bin/conda shell.bash hook)"
conda activate base
pip install numpy
pip install pandas

if [ "$AZURE_GRID_DRIVER" = 1 ]; then
    # Keep the PyTorch runtime aligned with this image's CUDA 12.2 toolkit.
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
fi
